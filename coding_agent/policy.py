from __future__ import annotations

import re
import fnmatch
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class RiskLevel(str, Enum):
    READ = "read"
    WRITE = "write"
    DANGEROUS = "dangerous"
    L1 = "read"
    L2 = "write"
    L3 = "dangerous"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    risk: RiskLevel
    reason: str
    requires_approval: bool = False
    prohibited: bool = False


class ToolPolicy:
    DANGEROUS_PATTERNS = [
        (r"\b(rm|del|rmdir|remove-item)\b", "deletes files"),
        (r"\b(sudo|shutdown|reboot)\b", "changes host system state"),
        (r"\b(git\s+(push|reset|clean)|git\s+checkout\s+--)\b", "destructive or remote Git operation"),
        (r"\b(pip|npm|pnpm|yarn|uv|apt|brew|choco)\s+(install|add)\b", "installs dependencies"),
        (r"\b(curl|wget|invoke-webrequest|ssh|scp)\b", "uses the network"),
        (r"\|\s*(sh|bash|powershell|pwsh)\b", "pipes untrusted content to a shell"),
    ]
    READ_PREFIXES = (
        "git status", "git diff", "git log", "git show", "git branch", "git rev-parse",
        "rg ", "grep ", "find ", "ls", "dir", "pwd", "get-childitem", "get-content",
        "python -m py_compile", "python --version", "pytest --collect-only",
    )

    def __init__(self, workspace: Path, protected_read_patterns: list[str] | None = None,
                 protected_write_patterns: list[str] | None = None):
        self.workspace = workspace.resolve()
        self.protected_read_patterns = protected_read_patterns or []
        self.protected_write_patterns = protected_write_patterns or []

    @staticmethod
    def _matches(relative: str, patterns: list[str]) -> bool:
        normalized = relative.replace("\\", "/").strip("/")
        for pattern in patterns:
            candidate = pattern.replace("\\", "/")
            if candidate.startswith("./"):
                candidate = candidate[2:]
            candidate = candidate.lstrip("/")
            candidates = [candidate]
            if candidate.startswith("**/"):
                candidates.append(candidate[3:])
            if any(fnmatch.fnmatchcase(normalized, item) for item in candidates):
                return True
            if "/" not in candidate and fnmatch.fnmatchcase(Path(normalized).name, candidate):
                return True
        return False

    def resolve_path(self, raw: str) -> Path:
        if not raw or "\x00" in raw:
            raise ValueError("Invalid empty or NUL-containing path")
        candidate = (self.workspace / raw).resolve()
        try:
            candidate.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError(f"Path escapes workspace: {raw}") from exc
        return candidate

    def classify_path(self, raw: str, write: bool) -> PolicyDecision:
        try:
            resolved = self.resolve_path(raw)
        except ValueError as exc:
            return PolicyDecision(RiskLevel.DANGEROUS, str(exc), prohibited=True)
        relative = resolved.relative_to(self.workspace).as_posix()
        patterns = self.protected_write_patterns if write else self.protected_read_patterns
        if self._matches(relative, patterns):
            action = "write" if write else "read"
            return PolicyDecision(RiskLevel.DANGEROUS, f"protected path cannot be {action}: {relative}", prohibited=True)
        return PolicyDecision(RiskLevel.WRITE if write else RiskLevel.READ, "workspace file access")

    def classify_command(self, command: str) -> PolicyDecision:
        normalized = " ".join(command.strip().lower().split())
        if not normalized:
            return PolicyDecision(RiskLevel.DANGEROUS, "empty command", prohibited=True)
        for pattern, reason in self.DANGEROUS_PATTERNS:
            if re.search(pattern, normalized, re.IGNORECASE):
                return PolicyDecision(RiskLevel.DANGEROUS, reason, prohibited=True)
        if re.search(r"(^|\s)(\.\.[/\\]|[a-z]:[/\\]|/etc/|/home/|/root/|~[/\\])", normalized):
            return PolicyDecision(RiskLevel.DANGEROUS, "may access paths outside the workspace", prohibited=True)
        read_match = any(normalized == prefix.rstrip() or normalized.startswith(prefix) for prefix in self.READ_PREFIXES)
        if read_match and re.search(r"(;|&&|\|\||>|<|`|\$\()", normalized):
            return PolicyDecision(RiskLevel.WRITE, "compound command or shell redirection may change state")
        if read_match:
            return PolicyDecision(RiskLevel.READ, "recognized read-only command")
        return PolicyDecision(RiskLevel.WRITE, "command may change workspace state")
