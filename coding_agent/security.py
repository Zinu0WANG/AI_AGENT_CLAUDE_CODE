from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any, Callable

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
class AgentRole(str, Enum):
    LEAD = "lead"
    WORKER = "worker"


class ApprovalChoice(str, Enum):
    DENY = "deny"
    ALLOW_ONCE = "allow_once"
    ALLOW_RUN_WRITES = "allow_run_writes"


class StrictToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


SafeText = Annotated[str, StringConstraints(min_length=1, max_length=50_000)]
SafePath = Annotated[str, StringConstraints(min_length=1, max_length=1_024)]
SafeName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]


class EmptyArgs(StrictToolArgs):
    pass


class CommandArgs(StrictToolArgs):
    command: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]


class BackgroundRunArgs(CommandArgs):
    timeout: int = Field(default=120, ge=1, le=300)


class ReadFileArgs(StrictToolArgs):
    path: SafePath
    reason: Annotated[str, StringConstraints(min_length=1, max_length=2_000)]
    limit: int | None = Field(default=None, ge=1, le=50_000)


class ReadFilesArgs(StrictToolArgs):
    files: list[ReadFileArgs] = Field(min_length=1, max_length=50)


class WriteFileArgs(StrictToolArgs):
    path: SafePath
    content: Annotated[str, StringConstraints(max_length=1_048_576)]


class EditFileArgs(StrictToolArgs):
    path: SafePath
    old_text: Annotated[str, StringConstraints(min_length=1, max_length=1_048_576)]
    new_text: Annotated[str, StringConstraints(max_length=1_048_576)]


class BatchEditArgs(StrictToolArgs):
    edits: list[EditFileArgs] = Field(min_length=1, max_length=50)


class BackgroundCheckArgs(StrictToolArgs):
    task_id: SafeName | None = None


class ArtifactReadArgs(StrictToolArgs):
    artifact_id: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{12}$")]
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=8_000, ge=1, le=12_000)


class ArtifactSearchArgs(StrictToolArgs):
    query: Annotated[str, StringConstraints(min_length=1, max_length=2_000)]
    max_hits: int = Field(default=5, ge=1, le=20)


class TaskCreateArgs(StrictToolArgs):
    subject: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    description: Annotated[str, StringConstraints(max_length=20_000)] = ""
    mode: str = Field(default="read", pattern=r"^(read|write)$")
    write_scope: list[SafePath] = Field(default_factory=list, max_length=50)


class TaskUpdateArgs(StrictToolArgs):
    task_id: int = Field(gt=0)
    status: str = Field(pattern=r"^(pending|in_progress|completed|deleted)$")


class LoadSkillArgs(StrictToolArgs):
    name: SafeName


class DelegateTaskArgs(StrictToolArgs):
    prompt: SafeText
    agent_type: str = Field(default="Explore", pattern=r"^(Explore|general-purpose)$")
    write_scope: list[SafePath] | None = Field(default=None, max_length=50)


class SpawnTeammateArgs(StrictToolArgs):
    name: SafeName
    role: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    prompt: SafeText
    task_id: int | None = Field(default=None, gt=0)
    write_scope: list[SafePath] | None = Field(default=None, max_length=50)


class SendMessageArgs(StrictToolArgs):
    to: SafeName
    type: str = Field(default="instruction", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    content: Any
    task_id: int | None = Field(default=None, gt=0)


class ReadInboxArgs(StrictToolArgs):
    status: str | None = Field(default=None, pattern=r"^(pending|delivered|acknowledged)$")
    limit: int = Field(default=20, ge=1, le=100)


class MessageIdArgs(StrictToolArgs):
    message_id: Annotated[str, StringConstraints(min_length=8, max_length=64)]


class BroadcastArgs(StrictToolArgs):
    content: SafeText


class ShutdownArgs(StrictToolArgs):
    teammate: SafeName


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[StrictToolArgs]
    allowed_roles: frozenset[AgentRole]
    risk: Any
    rate_limit_per_minute: int
    handler: Callable[[dict], str] | None = None

    def schema(self) -> dict:
        return {"name": self.name, "description": self.description,
                "input_schema": self.args_model.model_json_schema()}


def tool_fingerprint(run_id: str, actor: str, role: AgentRole, name: str, arguments: dict) -> str:
    canonical = json.dumps({"run_id": run_id, "actor": actor, "role": role.value,
                            "tool": name, "arguments": arguments}, sort_keys=True,
                           ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RateLimiter:
    def __init__(self):
        self._calls: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, actor: str, tool: str, limit: int, now: float | None = None) -> bool:
        timestamp = time.monotonic() if now is None else now
        with self._lock:
            window = self._calls[(actor, tool)]
            while window and timestamp - window[0] >= 60:
                window.popleft()
            if len(window) >= limit:
                return False
            window.append(timestamp)
            return True


@dataclass(frozen=True, slots=True)
class GuardrailResult:
    blocked: bool
    reason: str = ""
    warnings: tuple[str, ...] = ()


class GuardrailEngine:
    SQL_INJECTION = re.compile(r"(?i)(\bor\s+1\s*=\s*1\b|;\s*(drop|truncate)\s+table\b|--\s*$)")
    PROMPT_INJECTION = re.compile(
        r"(?i)(ignore\s+(all\s+)?previous\s+(instructions|rules)|忽略.{0,8}(规则|指令)|"
        r"you\s+are\s+now\s+(an?\s+)?(admin|system)|提升.{0,6}(权限|管理员))"
    )
    SCRIPT_INJECTION = re.compile(r"(?i)<script\b|javascript\s*:")

    def inspect(self, tool: str, arguments: dict) -> GuardrailResult:
        warnings: list[str] = []
        for key, value in self._strings(arguments):
            if self.PROMPT_INJECTION.search(value):
                warnings.append(f"prompt injection marker in {key}")
            # Source-code bodies may legitimately contain attack samples. Control fields may not.
            if key not in {"content", "old_text", "new_text", "prompt", "description", "reason"}:
                if self.SQL_INJECTION.search(value):
                    return GuardrailResult(True, f"SQL injection pattern in {key}", tuple(warnings))
                if self.SCRIPT_INJECTION.search(value):
                    return GuardrailResult(True, f"script injection pattern in {key}", tuple(warnings))
        return GuardrailResult(False, warnings=tuple(warnings))

    def _strings(self, value: Any, prefix: str = ""):
        if isinstance(value, str):
            yield prefix.rsplit(".", 1)[-1], value
        elif isinstance(value, dict):
            for key, item in value.items():
                yield from self._strings(item, f"{prefix}.{key}" if prefix else key)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield from self._strings(item, f"{prefix}[{index}]")


class SecretRedactor:
    PATTERNS = (
        re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[A-Za-z0-9._~+/=-]+"),
        re.compile(r"(?i)((?:api[_-]?key|token|password|secret)\s*[=:]\s*[\"']?)[^\s\"',;]+"),
        re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{12,}\b"),
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    )

    def __init__(self):
        names = ("ANTHROPIC_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_AUTH_TOKEN")
        self.known_values = tuple(value for name in names if (value := os.getenv(name)) and len(value) >= 8)

    def text(self, value: str) -> str:
        redacted = value
        for secret in self.known_values:
            redacted = redacted.replace(secret, "[REDACTED]")
        for pattern in self.PATTERNS:
            redacted = pattern.sub(lambda match: (match.group(1) if match.lastindex else "") + "[REDACTED]", redacted)
        return redacted

    def value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {key: self.value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.value(item) for item in value)
        return value


def audit_arguments(arguments: dict) -> dict:
    result = dict(arguments)
    for key in ("content", "old_text", "new_text", "prompt"):
        value = result.get(key)
        if isinstance(value, str):
            result[key] = {"chars": len(value), "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()}
    if isinstance(result.get("edits"), list):
        result["edits"] = [audit_arguments(item) if isinstance(item, dict) else item for item in result["edits"]]
    return result
