"""Model-call backends for the sprint experiments.

ClaudeCLIBackend makes one stateless `claude -p` call per request, on the operator's
Claude subscription, with all tools switched off and a custom system prompt.
FakeBackend returns scripted text for offline tests and never calls a model.

Every call, including retries, replays and failures, is written as one JSON line to
the call log with the full request text, the raw output and the token usage.
Nothing in this module substitutes a default answer when a call fails.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class BackendError(RuntimeError):
    """A call failed after all retries. The caller decides what that means."""

    def __init__(self, message: str, failed_attempts: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.failed_attempts = list(failed_attempts or [])


class RunAborted(RuntimeError):
    """An earlier call in this episode failed, so no further model calls are made."""


class UsageLimitReached(RuntimeError):
    """The subscription reported a usage or rate limit. Runs must stop, not retry."""


class CallBudgetExceeded(RuntimeError):
    """The hard cap on model calls for this phase was reached."""


USAGE_LIMIT_MARKERS = (
    "usage limit",
    "rate limit",
    "limit reached",
    "limit will reset",
    "out of extra usage",
    "hit your limit",
    "limit resets",
    "resets at",
    "weekly limit",
    "session limit",
)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def request_sha256(system: str, prompt: str, model: str, effort: Optional[str]) -> str:
    """Identity of a request. Two calls with the same hash received identical input."""
    blob = json.dumps(
        {"system": system, "prompt": prompt, "model": model, "effort": effort},
        sort_keys=True,
        ensure_ascii=False,
    )
    return sha256_text(blob)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class CallResult:
    text: str
    model_id: Optional[str]
    usage: Dict[str, Any]
    raw_events: List[Any]
    duration_s: float
    attempts: int = 1
    stderr: str = ""
    failed_attempts: List[Dict[str, Any]] = field(default_factory=list)
    cli_args: List[str] = field(default_factory=list)


class CallLog:
    """Append-only JSONL log, safe to share between threads of one process."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, record: Dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())


class CallBudget:
    """Hard cap on live model calls. Replayed calls do not count."""

    def __init__(self, max_calls: Optional[int]):
        self.max_calls = max_calls
        self.used = 0
        self._lock = threading.Lock()

    def take(self) -> None:
        with self._lock:
            if self.max_calls is not None and self.used >= self.max_calls:
                raise CallBudgetExceeded(f"call cap of {self.max_calls} reached")
            self.used += 1


def _looks_like_usage_limit(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in USAGE_LIMIT_MARKERS)


# A `claude -p` child gets only what the CLI needs to start and find its login, plus fixed
# settings. Nothing else passes through: not a parent Claude Code session's variables, and
# not an API key, which would bill the API instead of the subscription.
CHILD_ENV_KEEP = (
    "HOME", "USER", "LOGNAME", "PATH", "SHELL", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE",
    "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "XDG_CONFIG_HOME",
    "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
    "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS",
)
CHILD_ENV_FIXED = {
    # Colosseum's Anthropic client sends no thinking and max_tokens 1500 (the Jira config).
    "MAX_THINKING_TOKENS": "0",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "1500",
    # No CLAUDE.md files, memory or attachments in the agents' context.
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "CLAUDE_CODE_DISABLE_ATTACHMENTS": "1",
    # The CLI must not update itself between calls.
    "DISABLE_AUTOUPDATER": "1",
}


def child_env() -> Dict[str, str]:
    """Environment for one `claude -p` call."""
    env = {name: os.environ[name] for name in CHILD_ENV_KEEP if name in os.environ}
    env.update(CHILD_ENV_FIXED)
    return env


def _code_sha256() -> str:
    """Hash of this package's source and data files (papers, tasks), recorded with every call."""
    digest = hashlib.sha256()
    package = Path(__file__).resolve().parent
    for path in sorted(p for pattern in ("*.py", "*.yaml") for p in package.rglob(pattern)):
        digest.update(str(path.relative_to(package)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


CODE_SHA256 = _code_sha256()


class ClaudeCLIBackend:
    """One `claude -p` subprocess per call, on the operator's logged-in subscription."""

    name = "claude_cli"

    def __init__(
        self,
        claude_bin: Optional[str] = None,
        timeout_s: float = 300.0,
        max_attempts: int = 3,
        extra_args: Optional[List[str]] = None,
        workdir: Optional[Path] = None,
    ):
        # COLLUSION_CLAUDE_BIN points at the CLI when `claude` is not on PATH.
        self.claude_bin = claude_bin or os.environ.get("COLLUSION_CLAUDE_BIN", "claude")
        self.timeout_s = timeout_s
        self.max_attempts = max_attempts
        self.extra_args = list(extra_args or [])
        # An empty working directory keeps any project CLAUDE.md or settings out of the call.
        # It is the same directory for every call and episode, because the CLI names it in a
        # reminder it adds to each request (with the OS, the model's name and today's date;
        # recorded on 23 Oct). That reminder cannot be turned off without losing the
        # subscription login (CLAUDE_CODE_SIMPLE=1 drops it but also the stored login), so
        # it is kept identical instead.
        self.workdir = Path(workdir) if workdir else Path(tempfile.gettempdir()) / "collusion-study-cli-cwd"
        self.workdir.mkdir(parents=True, exist_ok=True)

    def command(self, system: str, model: str, effort: Optional[str]) -> List[str]:
        cmd = [
            self.claude_bin,
            "-p",
            "--model", model,
            "--system-prompt", system,
            "--tools", "",
            # No CLAUDE.md, hooks, plugins or user settings (such as a default effort);
            # the working directory is empty, so "project" loads nothing either.
            "--safe-mode",
            "--setting-sources", "project",
            "--disable-slash-commands",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--output-format", "stream-json",
            "--verbose",
        ]
        if effort:
            cmd += ["--effort", effort]
        return cmd + self.extra_args

    @staticmethod
    def parse_stream(stdout: str) -> Dict[str, Any]:
        events: List[Any] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                events.append({"type": "_unparsed_line", "text": line})
        init = next((e for e in events if isinstance(e, dict) and e.get("type") == "system" and e.get("subtype") == "init"), None)
        result = next((e for e in reversed(events) if isinstance(e, dict) and e.get("type") == "result"), None)
        model_id = init.get("model") if isinstance(init, dict) else None
        if model_id is None:
            for event in events:
                message = event.get("message") if isinstance(event, dict) else None
                if isinstance(message, dict) and message.get("model"):
                    model_id = message["model"]
                    break
        return {"events": events, "init": init, "result": result, "model_id": model_id}

    @staticmethod
    def _synthetic_text(events: List[Any]) -> Optional[str]:
        """Text of a reply the CLI wrote itself (model "<synthetic>"), such as an error or a limit notice."""
        for event in events:
            message = event.get("message") if isinstance(event, dict) else None
            if isinstance(message, dict) and message.get("model") == "<synthetic>":
                parts = message.get("content") or []
                return " ".join(str(p.get("text", "")) for p in parts if isinstance(p, dict)) or "(empty)"
        return None

    @staticmethod
    def _failure(attempt: int, started: float, problem: str, stdout: Any, stderr: Any) -> Dict[str, Any]:
        def tail(value: Any) -> str:
            if isinstance(value, bytes):
                value = value.decode("utf-8", "replace")
            return str(value or "")[-4000:]

        return {
            "attempt": attempt,
            "problem": problem,
            "duration_s": round(time.monotonic() - started, 3),
            "stdout_tail": tail(stdout),
            "stderr_tail": tail(stderr),
        }

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        model: str,
        effort: Optional[str],
        on_attempt: Optional[Callable[[int], None]] = None,
    ) -> CallResult:
        if any(self.workdir.iterdir()):
            raise BackendError(f"the CLI's working directory {self.workdir} must stay empty")
        cmd = self.command(system, model, effort)
        # The record holds the system prompt already, so the argument list leaves it out.
        cli_args = ["<system prompt>" if arg == system else arg for arg in cmd]
        failed: List[Dict[str, Any]] = []
        started = time.monotonic()
        for attempt in range(1, self.max_attempts + 1):
            if on_attempt is not None:
                on_attempt(attempt)  # every attempt counts against the call cap
            attempt_started = time.monotonic()
            try:
                proc = subprocess.run(
                    cmd,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    cwd=str(self.workdir),
                    env=child_env(),
                )
            except subprocess.TimeoutExpired as exc:
                failed.append(self._failure(attempt, attempt_started, f"timeout after {self.timeout_s}s", exc.stdout, exc.stderr))
                time.sleep(2 ** attempt)
                continue
            except OSError as exc:  # the CLI is missing or cannot start; a retry will not help
                failed.append(self._failure(attempt, attempt_started, f"{type(exc).__name__}: {exc}", "", ""))
                raise BackendError(f"claude -p could not start: {exc}", failed) from exc
            parsed = self.parse_stream(proc.stdout)
            result = parsed["result"]
            synthetic = self._synthetic_text(parsed["events"])
            if result is None or result.get("is_error") or result.get("subtype") != "success" or synthetic is not None:
                combined = " ".join([proc.stderr or "", json.dumps(result or {}, default=str), synthetic or ""])
                if _looks_like_usage_limit(combined):
                    failed.append(self._failure(attempt, attempt_started, "usage limit", proc.stdout, proc.stderr))
                    raise UsageLimitReached(combined.strip()[:500])
                if result is None:
                    problem = f"no result event (exit {proc.returncode})"
                elif synthetic is not None:
                    problem = f"reply written by the CLI, not the model: {synthetic[:300]}"
                else:
                    problem = f"error result: {str(result.get('result'))[:300]}"
                failed.append(self._failure(attempt, attempt_started, problem, proc.stdout, proc.stderr))
                time.sleep(2 ** attempt)
                continue
            return CallResult(
                text=str(result.get("result") or ""),
                model_id=parsed["model_id"],
                usage=dict(result.get("usage") or {}),
                raw_events=parsed["events"],
                duration_s=time.monotonic() - started,
                attempts=attempt,
                stderr=proc.stderr or "",
                failed_attempts=failed,
                cli_args=cli_args,
            )
        last = failed[-1]["problem"] if failed else "no attempt made"
        raise BackendError(f"claude -p failed after {self.max_attempts} attempts: {last}", failed)


class FakeBackend:
    """Scripted responses for offline tests. Never calls a model."""

    name = "fake"

    def __init__(self, responder: Callable[[str, str, str, Optional[str]], str]):
        self.responder = responder

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        model: str,
        effort: Optional[str],
        on_attempt: Optional[Callable[[int], None]] = None,
    ) -> CallResult:
        if on_attempt is not None:
            on_attempt(1)
        started = time.monotonic()
        text = self.responder(system, prompt, model, effort)
        words_in = len((system + " " + prompt).split())
        return CallResult(
            text=text,
            model_id=f"fake-{model}",
            usage={"input_tokens": words_in, "output_tokens": len(text.split()), "fake": True},
            raw_events=[{"type": "fake", "note": "scripted fixture, not model output"}],
            duration_s=time.monotonic() - started,
        )


@dataclass
class ModelCaller:
    """Wraps a backend with the budget, the call log and per-call metadata.

    After the first failed call, every later call in the episode is refused without calling
    the model. Terrarium swallows a failure in the middle of a turn, so without this an
    episode could carry on and look valid.
    """

    backend: Any
    log: CallLog
    budget: CallBudget = field(default_factory=lambda: CallBudget(None))
    fatal: Optional[str] = None
    failed_calls: int = 0

    def call(self, *, system: str, prompt: str, model: str, effort: Optional[str], meta: Dict[str, Any]) -> CallResult:
        call_id = str(uuid.uuid4())
        req_hash = request_sha256(system, prompt, model, effort)
        meta_fields = {f"meta_{k}": v for k, v in meta.items()}
        if self.fatal is not None:
            self.log.write(
                {
                    "call_id": call_id,
                    "time_utc": utc_now(),
                    "status": "refused_after_failure",
                    "model_requested": model,
                    "request_sha256": req_hash,
                    "error": self.fatal,
                    "code_sha256": CODE_SHA256,
                    **meta_fields,
                }
            )
            raise RunAborted(self.fatal)
        base_record = {
            "call_id": call_id,
            "time_utc": utc_now(),
            "backend": getattr(self.backend, "name", type(self.backend).__name__),
            "model_requested": model,
            "effort_requested": effort,
            "request_sha256": req_hash,
            "system": system,
            "prompt": prompt,
            "replayed": False,
            "code_sha256": CODE_SHA256,
            **meta_fields,
        }
        try:
            result = self.backend.complete(
                system=system,
                prompt=prompt,
                model=model,
                effort=effort,
                on_attempt=lambda attempt: self.budget.take(),
            )
        except UsageLimitReached as exc:
            self._fail(base_record, "usage_limit", exc)
            raise
        except CallBudgetExceeded as exc:
            self._fail(base_record, "call_cap_reached", exc)
            raise
        except Exception as exc:  # recorded, then re-raised; never replaced by a default answer
            self._fail(base_record, "failed", exc)
            raise
        self.log.write(
            {
                **base_record,
                "status": "ok",
                "text": result.text,
                "model_id": result.model_id,
                "usage": result.usage,
                "duration_s": round(result.duration_s, 3),
                "attempts": result.attempts,
                "raw_events": result.raw_events,
                "stderr": result.stderr[-2000:],
                "failed_attempts": result.failed_attempts,
                "cli_args": result.cli_args,
            }
        )
        result_meta = {"call_id": call_id, "request_sha256": req_hash}
        result.usage = {**result.usage, "_call": result_meta}
        return result

    def _fail(self, base_record: Dict[str, Any], status: str, exc: BaseException) -> None:
        self.failed_calls += 1
        self.fatal = f"{type(exc).__name__}: {str(exc)[:500]}"
        self.log.write(
            {**base_record, "status": status, "error": self.fatal, "failed_attempts": getattr(exc, "failed_attempts", [])}
        )

    def record_replay(self, *, system: str, prompt: str, model: str, effort: Optional[str], meta: Dict[str, Any], source: Dict[str, Any]) -> None:
        """Log that a recorded response was reused instead of calling the model."""
        self.log.write(
            {
                "call_id": str(uuid.uuid4()),
                "time_utc": utc_now(),
                "backend": "replay",
                "model_requested": model,
                "effort_requested": effort,
                "request_sha256": request_sha256(system, prompt, model, effort),
                "system": system,
                "prompt": prompt,
                "replayed": True,
                "replay_of_call_id": source.get("call_id"),
                "status": "ok",
                "text": source.get("text"),
                "model_id": source.get("model_id"),
                "usage": {},
                **{f"meta_{k}": v for k, v in meta.items()},
            }
        )
