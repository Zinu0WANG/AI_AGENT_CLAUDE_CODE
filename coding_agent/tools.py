from __future__ import annotations

import difflib
import fnmatch
import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from .config import AgentConfig
from .context import RepoMap
from .context_management import ArtifactStore
from .events import EventStore
from .policy import PolicyDecision, RiskLevel, ToolPolicy
from .security import (
    AgentRole, ApprovalChoice, ArtifactReadArgs, ArtifactSearchArgs, BackgroundCheckArgs,
    BackgroundRunArgs, BatchEditArgs, CommandArgs, EditFileArgs, EmptyArgs, GuardrailEngine,
    LoadSkillArgs, RateLimiter, ReadFileArgs, ReadFilesArgs, TaskCreateArgs, TaskUpdateArgs,
    ToolSpec, WriteFileArgs, audit_arguments, tool_fingerprint,
)
from .state import TaskManager


ApprovalCallback = Callable[[str, dict, PolicyDecision], bool | ApprovalChoice | str]


class ToolRegistry:
    def __init__(self, workspace: Path, config: AgentConfig, events: EventStore,
                 approval_callback: ApprovalCallback | None = None, actor: str = "lead",
                 artifact_store: ArtifactStore | None = None, allowed_write_scope: list[str] | None = None,
                 role: AgentRole | str | None = None):
        self.workspace = workspace.resolve()
        self.config = config
        self.events = events
        security = config.tool_security
        self.policy = ToolPolicy(self.workspace, security.protected_read_patterns,
                                 security.protected_write_patterns)
        self.approval_callback = approval_callback
        self.actor = actor
        self.role = AgentRole(role or (AgentRole.LEAD if actor == "lead" else AgentRole.WORKER))
        self.artifact_store = artifact_store or ArtifactStore(events.run_dir, events)
        self.allowed_write_scope = allowed_write_scope
        self.tasks = TaskManager(self.workspace / ".tasks")
        self.repo_map = RepoMap(self.workspace, config.ignore_patterns, config.max_file_bytes)
        self._approved_for_run: set[RiskLevel] = set()
        self.guardrails = GuardrailEngine()
        self.rate_limiter = RateLimiter()
        self.specs = self._build_specs()
        self._before: dict[Path, bytes | None] = self._snapshot_workspace()
        self._background: dict[str, dict] = {}
        self._background_lock = threading.Lock()
        self._read_cache: dict[Path, tuple[tuple[int, int], str, set[int | None]]] = {}
        self.aborted = False

    def _snapshot_workspace(self) -> dict[Path, bytes | None]:
        snapshot = {}
        for relative in self.repo_map.build()["files"]:
            path = self.workspace / relative
            try:
                snapshot[path] = path.read_bytes()
            except OSError:
                continue
        return snapshot

    @property
    def schemas(self) -> list[dict]:
        return [spec.schema() for spec in self.specs.values() if self.role in spec.allowed_roles and not (
            self.role is AgentRole.WORKER and spec.risk is RiskLevel.WRITE and not self.allowed_write_scope
        )]

    def _build_specs(self) -> dict[str, ToolSpec]:
        lead, both = frozenset({AgentRole.LEAD}), frozenset(AgentRole)
        limits = self.config.tool_security
        rows = [
            ("bash", "Run a shell command after explicit human approval.", CommandArgs, lead, RiskLevel.DANGEROUS, limits.l3_rate_limit),
            ("read_file", "Read a workspace file and record why it was selected.", ReadFileArgs, both, RiskLevel.READ, limits.l1_rate_limit),
            ("read_files", "Read multiple independent workspace files in one tool round.", ReadFilesArgs, both, RiskLevel.READ, limits.l1_rate_limit),
            ("write_file", "Write a workspace file.", WriteFileArgs, both, RiskLevel.WRITE, limits.l2_rate_limit),
            ("edit_file", "Replace one exact occurrence in a workspace file.", EditFileArgs, both, RiskLevel.WRITE, limits.l2_rate_limit),
            ("batch_edit", "Atomically apply multiple exact replacements after validating every edit.", BatchEditArgs, both, RiskLevel.WRITE, limits.l2_rate_limit),
            ("repo_map", "Refresh and show the repository map.", EmptyArgs, both, RiskLevel.READ, limits.l1_rate_limit),
            ("background_run", "Run an approved command in a background thread.", BackgroundRunArgs, lead, RiskLevel.DANGEROUS, limits.l3_rate_limit),
            ("check_background", "Check one or all background commands.", BackgroundCheckArgs, lead, RiskLevel.READ, limits.l1_rate_limit),
            ("artifact_read", "Read a page from an externalized result.", ArtifactReadArgs, both, RiskLevel.READ, limits.l1_rate_limit),
            ("artifact_search", "Search externalized results by literal keyword.", ArtifactSearchArgs, both, RiskLevel.READ, limits.l1_rate_limit),
            ("task_create", "Create a persistent task.", TaskCreateArgs, lead, RiskLevel.WRITE, limits.l2_rate_limit),
            ("task_list", "List persistent tasks.", EmptyArgs, both, RiskLevel.READ, limits.l1_rate_limit),
            ("task_update", "Update a persistent task.", TaskUpdateArgs, lead, RiskLevel.WRITE, limits.l2_rate_limit),
            ("load_skill", "Load a local SKILL.md by name.", LoadSkillArgs, both, RiskLevel.READ, limits.l1_rate_limit),
        ]
        return {name: ToolSpec(name, description, model, roles, risk, limit)
                for name, description, model, roles, risk, limit in rows}

    def _decision(self, name: str, arguments: dict) -> PolicyDecision:
        if name in {"bash", "background_run"}:
            command_decision = self.policy.classify_command(arguments["command"])
            if command_decision.prohibited:
                return command_decision
            return PolicyDecision(RiskLevel.DANGEROUS, "raw shell execution requires human approval",
                                  requires_approval=True)
        if name in {"write_file", "edit_file"}:
            return self.policy.classify_path(arguments["path"], write=True)
        if name == "batch_edit":
            decisions = [self.policy.classify_path(edit.get("path", ""), write=True)
                         for edit in arguments.get("edits", [])]
            dangerous = next((item for item in decisions if item.risk is RiskLevel.DANGEROUS), None)
            return dangerous or PolicyDecision(RiskLevel.WRITE, "modifies multiple workspace files")
        if name == "read_file":
            return self.policy.classify_path(arguments["path"], write=False)
        if name == "read_files":
            decisions = [self.policy.classify_path(item.get("path", ""), write=False)
                         for item in arguments.get("files", [])]
            dangerous = next((item for item in decisions if item.risk is RiskLevel.DANGEROUS), None)
            return dangerous or PolicyDecision(RiskLevel.READ, "reads multiple workspace files")
        if name in {"task_create", "task_update"}:
            return PolicyDecision(RiskLevel.WRITE, "updates workspace task state")
        return PolicyDecision(RiskLevel.READ, "read-only agent operation")

    def _allowed(self, name: str, arguments: dict, decision: PolicyDecision) -> bool:
        if decision.prohibited:
            return False
        if decision.risk is RiskLevel.READ:
            return True
        explicit_only = decision.risk is RiskLevel.DANGEROUS or decision.requires_approval
        if self.config.approval_policy == "read_only":
            return False
        if not explicit_only and (self.config.approval_policy == "allow_write" or
                                  decision.risk in self._approved_for_run):
            return True
        fingerprint = tool_fingerprint(self.events.run_id, self.actor, self.role, name, arguments)
        self.events.emit("approval_requested", self.actor, {
            "tool": name, "arguments": audit_arguments(arguments), "risk": decision.risk.value,
            "reason": decision.reason, "fingerprint": fingerprint,
        })
        raw_choice = self.approval_callback(name, arguments, decision) if self.approval_callback else False
        if raw_choice is True:
            choice = ApprovalChoice.ALLOW_ONCE
        elif raw_choice is False or raw_choice is None:
            choice = ApprovalChoice.DENY
        else:
            try:
                choice = ApprovalChoice(raw_choice)
            except (TypeError, ValueError):
                choice = ApprovalChoice.DENY
        if choice is ApprovalChoice.ALLOW_RUN_WRITES and not explicit_only:
            self.approve_for_run(RiskLevel.WRITE)
        approved = choice in {ApprovalChoice.ALLOW_ONCE, ApprovalChoice.ALLOW_RUN_WRITES}
        self.events.emit("approval_resolved", self.actor, {
            "tool": name, "approved": approved, "choice": choice.value, "fingerprint": fingerprint,
        })
        return approved

    def approve_for_run(self, risk: RiskLevel = RiskLevel.WRITE) -> None:
        self._approved_for_run.add(risk)

    def authorize(self, name: str, arguments: dict, decision: PolicyDecision) -> bool:
        """Apply the same approval flow to runtime-managed tools such as delegation."""
        return self._allowed(name, arguments, decision)

    def execute(self, name: str, arguments: dict) -> str:
        if not isinstance(arguments, dict):
            return "Error: invalid tool request: arguments must be an object"
        spec = self.specs.get(name)
        if not spec:
            return "Error: invalid tool request: unknown tool"
        if self.role not in spec.allowed_roles:
            self.events.emit("tool_rbac_denied", self.actor, {"tool": name, "role": self.role.value})
            return f"Error: role {self.role.value} is not allowed to call {name}"
        if self.role is AgentRole.WORKER and spec.risk is RiskLevel.WRITE and not self.allowed_write_scope:
            self.events.emit("tool_rbac_denied", self.actor, {"tool": name, "role": self.role.value,
                                                              "reason": "missing write_scope"})
            return "Error: worker write operation requires write_scope"
        try:
            validated = spec.args_model.model_validate(arguments).model_dump(exclude_none=True)
        except ValidationError as exc:
            details = [{"field": ".".join(str(part) for part in error["loc"]), "type": error["type"]}
                       for error in exc.errors()[:10]]
            self.events.emit("tool_validation_failed", self.actor, {"tool": name, "errors": details})
            return f"Error: invalid tool request: {json.dumps(details, ensure_ascii=False)}"
        if name == "write_file" and len(validated["content"].encode("utf-8")) > self.config.tool_security.max_file_content_bytes:
            return "Error: invalid tool request: file content exceeds configured limit"
        guard = self.guardrails.inspect(name, validated)
        if guard.warnings:
            self.events.emit("tool_guardrail_warning", self.actor, {"tool": name, "warnings": guard.warnings})
        if guard.blocked:
            self.events.emit("tool_guardrail_denied", self.actor, {"tool": name, "reason": guard.reason})
            return f"Error: tool guardrail denied operation: {guard.reason}"
        arguments = validated
        fingerprint = tool_fingerprint(self.events.run_id, self.actor, self.role, name, arguments)
        self.events.emit("tool_requested", self.actor, {"tool": name, "arguments": audit_arguments(arguments),
                                                        "role": self.role.value, "fingerprint": fingerprint})
        if self.aborted:
            output = "Error: run aborted"
            self.events.emit("tool_finished", self.actor, {"tool": name, "ok": False, "output": output})
            return output
        decision = self._decision(name, arguments)
        if not self.rate_limiter.allow(self.actor, name, spec.rate_limit_per_minute):
            output = "Error: tool rate limit exceeded"
            self.events.emit("tool_rate_limited", self.actor, {"tool": name, "risk": decision.risk.value})
            return output
        scope_error = self._scope_error(name, arguments, decision)
        if scope_error:
            output = f"Error: write scope denied: {scope_error}"
            self.events.emit("tool_finished", self.actor, {"tool": name, "ok": False, "output": output, "risk": decision.risk.value})
            return output
        if not self._allowed(name, arguments, decision):
            output = f"Error: {decision.risk.value} operation denied: {decision.reason}"
            self.events.emit("tool_finished", self.actor, {"tool": name, "ok": False, "output": output, "risk": decision.risk.value})
            return output
        self.events.emit("tool_started", self.actor, {"tool": name, "risk": decision.risk.value,
                                                      "role": self.role.value, "fingerprint": fingerprint})
        try:
            output = self._dispatch(name, arguments)
            ok = not output.startswith("Error:")
        except Exception as exc:
            output, ok = f"Error: {exc}", False
        output = self.events.redact_text(output)
        limit = self.config.tool_security.max_tool_output_bytes
        if len(output.encode("utf-8")) > limit:
            output = output.encode("utf-8")[:limit].decode("utf-8", errors="ignore") + "\n[output truncated]"
        self.events.emit("tool_finished", self.actor, {"tool": name, "ok": ok, "output": output[:2000],
                                                       "fingerprint": fingerprint})
        return output

    def _scope_error(self, name: str, arguments: dict, decision: PolicyDecision) -> str | None:
        if self.allowed_write_scope is None or decision.risk is not RiskLevel.WRITE:
            return None
        if name in {"write_file", "edit_file", "batch_edit"}:
            paths = [arguments["path"]] if name != "batch_edit" else [edit.get("path", "") for edit in arguments.get("edits", [])]
            for raw_path in paths:
                relative = self.policy.resolve_path(raw_path).relative_to(self.workspace).as_posix()
                allowed = any(
                    fnmatch.fnmatchcase(relative, pattern.replace("\\", "/").lstrip("./")) or (
                        pattern.replace("\\", "/").lstrip("./").endswith("/**") and
                        relative.startswith(pattern.replace("\\", "/").lstrip("./")[:-3].rstrip("/") + "/")
                    ) for pattern in self.allowed_write_scope
                )
                if not allowed:
                    return f"{relative} is outside {self.allowed_write_scope}"
            return None
        if name in {"bash", "background_run"}:
            return "workers cannot execute shell commands"
        return None

    def _remember(self, path: Path) -> None:
        if path not in self._before:
            self._before[path] = path.read_bytes() if path.exists() else None

    def _atomic_write(self, path: Path, content: str) -> None:
        # Re-resolve immediately before replacement to reduce symlink/junction races.
        relative = path.relative_to(self.workspace).as_posix()
        resolved = self.policy.resolve_path(relative)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        temporary = resolved.with_name(f".{resolved.name}.agent-{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            if self.policy.resolve_path(relative) != resolved:
                raise ValueError("Path changed while preparing write")
            os.replace(temporary, resolved)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _command_environment() -> dict[str, str]:
        allowed = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
                   "LANG", "LC_ALL", "PYTHONPATH", "VIRTUAL_ENV", "APPDATA", "LOCALAPPDATA",
                   "PROGRAMDATA", "USERPROFILE"}
        return {key: value for key, value in os.environ.items() if key.upper() in allowed}

    def _run_command(self, command: str, timeout: int) -> subprocess.CompletedProcess:
        timeout = min(timeout, self.config.command_timeout, self.config.tool_security.max_command_timeout)
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(command, shell=True, cwd=self.workspace, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, env=self._command_environment(),
                                   creationflags=creationflags, start_new_session=os.name != "nt")
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               capture_output=True, check=False)
            else:
                os.killpg(process.pid, 15)
            process.communicate()
            raise
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    def _dispatch(self, name: str, arguments: dict) -> str:
        if name == "bash":
            result = self._run_command(arguments["command"], self.config.command_timeout)
            output = (result.stdout + result.stderr).strip() or "(no output)"
            self._read_cache.clear()
            return f"exit_code={result.returncode}\n{output}"
        if name == "read_file":
            return self._read_one(arguments)
        if name == "read_files":
            if not arguments.get("files"):
                return "Error: files must not be empty"
            return "\n\n".join(
                f"===== {item.get('path', '')} =====\n{self._read_one(item)}"
                for item in arguments["files"]
            )
        if name == "write_file":
            path = self.policy.resolve_path(arguments["path"])
            self._remember(path)
            self._atomic_write(path, arguments["content"])
            self._read_cache.pop(path, None)
            return f"Wrote {len(arguments['content'])} characters to {arguments['path']}"
        if name == "edit_file":
            path = self.policy.resolve_path(arguments["path"])
            self._remember(path)
            content = path.read_text(encoding="utf-8")
            if content.count(arguments["old_text"]) != 1:
                return f"Error: old_text must occur exactly once; found {content.count(arguments['old_text'])}"
            self._atomic_write(path, content.replace(arguments["old_text"], arguments["new_text"], 1))
            self._read_cache.pop(path, None)
            return f"Edited {arguments['path']}"
        if name == "batch_edit":
            return self._batch_edit(arguments.get("edits", []))
        if name == "repo_map":
            return self.repo_map.render()
        if name == "artifact_read":
            result = self.artifact_store.read(
                arguments["artifact_id"], arguments.get("offset", 0),
                arguments.get("limit", self.config.artifact_read_default_chars),
            )
            return json.dumps(result, ensure_ascii=False)
        if name == "artifact_search":
            result = self.artifact_store.search(
                arguments["query"], arguments.get("max_hits", self.config.artifact_search_max_hits),
            )
            return json.dumps(result, ensure_ascii=False)
        if name == "background_run":
            task_id = str(uuid.uuid4())[:8]
            with self._background_lock:
                self._background[task_id] = {"status": "running", "command": arguments["command"], "result": None}
            thread = threading.Thread(target=self._background_exec,
                                      args=(task_id, arguments["command"], arguments.get("timeout", self.config.command_timeout)),
                                      daemon=True, name=f"background-{task_id}")
            thread.start()
            return f"Background task {task_id} started"
        if name == "check_background":
            task_id = arguments.get("task_id")
            with self._background_lock:
                if task_id:
                    task = self._background.get(task_id)
                    return json.dumps(task, ensure_ascii=False) if task else f"Error: unknown background task {task_id}"
                return json.dumps(self._background, ensure_ascii=False)
        if name == "task_create":
            return self.tasks.create(arguments["subject"], arguments.get("description", ""),
                                     arguments.get("mode", "read"), arguments.get("write_scope", []))
        if name == "task_list":
            return self.tasks.list_all()
        if name == "task_update":
            return self.tasks.update(arguments["task_id"], arguments.get("status"))
        if name == "load_skill":
            skill_name = arguments["name"]
            if not re_safe_name(skill_name):
                return "Error: invalid skill name"
            matches = list((self.workspace / "skills").glob(f"**/{skill_name}/SKILL.md"))
            if not matches:
                matches = [p for p in (self.workspace / "skills").glob("**/SKILL.md") if p.parent.name == skill_name]
            return matches[0].read_text(encoding="utf-8") if matches else f"Error: unknown skill {skill_name}"
        return f"Error: unknown tool {name}"

    def _read_one(self, arguments: dict) -> str:
        path = self.policy.resolve_path(arguments["path"])
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        cached = self._read_cache.get(path)
        limit = arguments.get("limit")
        same_view = bool(cached and cached[0] == signature and limit in cached[2])
        self.events.emit("context_selected", self.actor, {
            "path": arguments["path"], "reason": arguments["reason"], "cached": same_view,
        })
        if same_view:
            return f"File unchanged; reuse the previous result for {arguments['path']}."
        if cached and cached[0] == signature:
            text, views = cached[1], cached[2]
            views.add(limit)
        else:
            text, views = path.read_text(encoding="utf-8"), {limit}
        self._read_cache[path] = (signature, text, views)
        lines = text.splitlines()
        if limit and len(lines) > limit:
            return "\n".join(lines[:limit] + [f"... ({len(lines) - limit} more lines)"])
        return text

    def _batch_edit(self, edits: list[dict]) -> str:
        if not edits:
            return "Error: edits must not be empty"
        staged: dict[Path, str] = {}
        display_paths: dict[Path, str] = {}
        for edit in edits:
            path = self.policy.resolve_path(edit["path"])
            content = staged.get(path)
            if content is None:
                content = path.read_text(encoding="utf-8")
            count = content.count(edit["old_text"])
            if count != 1:
                return f"Error: {edit['path']}: old_text must occur exactly once; found {count}"
            staged[path] = content.replace(edit["old_text"], edit["new_text"], 1)
            display_paths[path] = edit["path"]
        for path, content in staged.items():
            self._remember(path)
            self._atomic_write(path, content)
            self._read_cache.pop(path, None)
        return f"Applied {len(edits)} edits across {len(staged)} files: " + ", ".join(display_paths.values())

    def _background_exec(self, task_id: str, command: str, timeout: int) -> None:
        try:
            result = self._run_command(command, timeout)
            output = (result.stdout + result.stderr).strip() or "(no output)"
            state = {"status": "completed" if result.returncode == 0 else "failed",
                     "command": command, "result": f"exit_code={result.returncode}\n{output}"[:50_000]}
        except Exception as exc:
            state = {"status": "failed", "command": command, "result": str(exc)}
        with self._background_lock:
            self._background[task_id] = state
        self.events.emit("tool_finished", self.actor, {"tool": "background_run", "task_id": task_id,
                         "ok": state["status"] == "completed", "output": state["result"][:2000]})

    def diff(self) -> str:
        for relative in self.repo_map.build()["files"]:
            path = self.workspace / relative
            self._before.setdefault(path, None)
        chunks = []
        for path, before in sorted(self._before.items(), key=lambda item: str(item[0])):
            after = path.read_bytes() if path.exists() else None
            if before == after:
                continue
            relative = path.relative_to(self.workspace).as_posix()
            if _is_binary(before) or _is_binary(after):
                old_name = f"a/{relative}" if before is not None else "/dev/null"
                new_name = f"b/{relative}" if after is not None else "/dev/null"
                chunks.append(f"Binary files {old_name} and {new_name} differ\n")
                continue
            old_lines = (before or b"").decode("utf-8").splitlines(keepends=True)
            new_lines = (after or b"").decode("utf-8").splitlines(keepends=True)
            chunks.extend(difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{relative}", tofile=f"b/{relative}"))
        return "".join(chunks) or "No agent changes."

    def run_quality_gates(self) -> tuple[bool, str]:
        results, passed = [], True
        for kind, commands in (("lint", self.config.lint_commands), ("test", self.config.test_commands)):
            for command in commands:
                try:
                    decision = self.policy.classify_command(command)
                    executable_prefixes = (sys.executable.lower(), f'"{sys.executable.lower()}"')
                    uses_current_python = command.strip().lower().startswith(executable_prefixes)
                    if decision.prohibited and not (uses_current_python and
                                                    decision.reason == "may access paths outside the workspace"):
                        results.append(f"[{kind}] {command}\nError: denied by tool policy: {decision.reason}")
                        passed = False
                        continue
                    quality_decision = PolicyDecision(RiskLevel.WRITE, "runs a configured quality gate")
                    if not self._allowed(f"quality_gate:{kind}", {"command": command}, quality_decision):
                        results.append(f"[{kind}] {command}\nError: approval denied")
                        passed = False
                        continue
                    result = self._run_command(command, self.config.command_timeout)
                    output = (result.stdout + result.stderr).strip()
                    results.append(f"[{kind}] {command}\nexit_code={result.returncode}\n{output}")
                    passed = passed and result.returncode == 0
                except subprocess.TimeoutExpired:
                    results.append(f"[{kind}] {command}\nError: timeout")
                    passed = False
        summary = "\n\n".join(results) if results else "No quality gates configured; structural run completed."
        self.events.emit("validation_finished", self.actor, {"passed": passed, "summary": summary[:5000]})
        return passed, summary


def re_safe_name(value: str) -> bool:
    return bool(value and value.replace("-", "").replace("_", "").isalnum() and len(value) <= 64)


def _is_binary(content: bytes | None) -> bool:
    if content is None:
        return False
    if b"\x00" in content:
        return True
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False
