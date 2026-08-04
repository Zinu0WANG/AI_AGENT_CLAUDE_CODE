from __future__ import annotations

import ast
import fnmatch
import json
import subprocess
from collections import Counter
from pathlib import Path


LANGUAGES = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".rs": "Rust", ".go": "Go", ".java": "Java", ".md": "Markdown", ".yml": "YAML",
    ".yaml": "YAML", ".json": "JSON", ".toml": "TOML",
}
KEY_CONFIGS = {"pyproject.toml", "requirements.txt", "package.json", "Cargo.toml", "go.mod", ".agent.yml"}


def is_path_ignored(relative: str, patterns: list[str]) -> bool:
    """Match unanchored ignore patterns at the workspace root or any depth."""
    normalized = relative.replace("\\", "/").strip("/")
    if not normalized:
        return False
    parts = normalized.split("/")
    for raw_pattern in patterns:
        pattern = raw_pattern.replace("\\", "/")
        anchored = pattern.startswith("/")
        pattern = pattern.lstrip("/")
        directory = pattern[:-3].rstrip("/") if pattern.endswith("/**") else ""
        if directory and not any(character in directory for character in "*?["):
            if "/" not in directory:
                directory_parts = parts[:1] if anchored else parts[:-1]
                if directory in directory_parts:
                    return True
                continue
            candidates = [normalized] if anchored else [
                "/".join(parts[index:]) for index in range(len(parts) - 1)
            ]
            if any(candidate.startswith(directory + "/") for candidate in candidates):
                return True
            continue
        if fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(normalized + "/", pattern):
            return True
        if not anchored:
            for index in range(1, len(parts)):
                candidate = "/".join(parts[index:])
                if fnmatch.fnmatch(candidate, pattern) or fnmatch.fnmatch(candidate + "/", pattern):
                    return True
    return False


class RepoMap:
    def __init__(self, workspace: Path, ignore_patterns: list[str] | None = None, max_file_bytes: int = 250_000):
        self.workspace = workspace.resolve()
        self.ignore_patterns = ignore_patterns or []
        self.max_file_bytes = max_file_bytes
        self.cache_path = self.workspace / ".runs" / "repo-map-cache.json"

    def _ignored(self, relative: str) -> bool:
        return is_path_ignored(relative, self.ignore_patterns)

    def _python_symbols(self, path: Path) -> list[str]:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError):
            return []
        return [node.name for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]

    def build(self) -> dict:
        cached = {}
        if self.cache_path.exists():
            try:
                cached = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                cached = {}
        files, languages, entries = [], Counter(), {}
        for path in sorted(self.workspace.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.workspace).as_posix()
            if self._ignored(relative) or path.stat().st_size > self.max_file_bytes:
                continue
            stat = path.stat()
            fingerprint = f"{stat.st_size}:{stat.st_mtime_ns}"
            entry = cached.get("entries", {}).get(relative)
            if not entry or entry.get("fingerprint") != fingerprint:
                entry = {"fingerprint": fingerprint, "symbols": self._python_symbols(path) if path.suffix == ".py" else []}
            entries[relative] = entry
            files.append(relative)
            if path.suffix in LANGUAGES:
                languages[LANGUAGES[path.suffix]] += 1
        git_status = "unavailable"
        try:
            result = subprocess.run(["git", "status", "--short"], cwd=self.workspace, capture_output=True, text=True, timeout=5)
            git_status = result.stdout.strip() or "clean"
        except (OSError, subprocess.TimeoutExpired):
            pass
        data = {"files": files, "languages": dict(languages), "entries": entries, "git_status": git_status}
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data

    def render(self) -> str:
        data = self.build()
        lines = ["Repository map", f"Languages: {data['languages']}", f"Git: {data['git_status']}", "Files:"]
        for relative in data["files"]:
            symbols = data["entries"][relative].get("symbols", [])
            suffix = f"  symbols: {', '.join(symbols)}" if symbols else ""
            marker = " [config]" if Path(relative).name in KEY_CONFIGS else ""
            lines.append(f"- {relative}{marker}{suffix}")
        return "\n".join(lines)
