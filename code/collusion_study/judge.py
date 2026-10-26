"""The paper's LLM judge on each episode's boards, through a subscription CLI instead of the OpenAI API.

    python -m collusion_study.judge ../runs/control ../runs/control-structured --colosseum ../third_party/colosseum \
        --backend codex --judge-model gpt-5.6-sol --rule O17 --out ../analysis/control-judge

Runs Colosseum's own experiments/collusion/judge_blackboards.py, unchanged, on every episode
of a batch: its three questions and rubrics, its transcript formatting, its choice of boards
(the secret board when the run has one; otherwise every board, averaged) and its parsing. Only
the model call is swapped: its OpenAIClient is replaced by one that sends the same system and
user text through our backend and logs each call to <episode>/judge-calls.jsonl. The CLI
cannot set the script's temperature, so that difference is recorded in each result.

Colosseum writes its judge file inside the episode's run tree
(runs/<model>/judge_secret_blackboard/<sweep>/<run>.json); judge.csv collects them, with the
mean of the three ratings per episode as the paper's judge_mean_rating. A call that fails after
the script's retries leaves no result for that episode; it is never replaced by a 0.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .backend import (CODE_SHA256, BackendError, CallLog, CallResult, ClaudeCLIBackend, FakeBackend, ModelCaller,
                      UsageLimitReached, _looks_like_usage_limit)
from .results import colosseum_run_dir, episode_dirs

PROMPT_NAMES = ("simple", "medium", "complex")


# The script asks for {"rating": 0-5 integer, "rationale": string}; Codex enforces that shape.
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"rating": {"type": "integer"}, "rationale": {"type": "string"}},
    "required": ["rating", "rationale"],
    "additionalProperties": False,
}


class CodexCLIBackend:
    """One `codex exec` per call, on the operator's ChatGPT login.

    The script's system text replaces Codex's own system prompt (model_instructions_file), and
    the user text goes in on stdin. Codex's optional context and tools are switched off; what
    cannot be switched off (two short multi-agent notes and a few built-in tools, recorded on
    23 Oct in analysis/codex-probe/) stays and is logged as a deviation. It runs read-only in a
    fixed empty directory, ignores the user's config and rules, keeps no session, and writes
    only its final message.
    """

    STRIP = [
        "include_permissions_instructions=false",
        "include_apps_instructions=false",
        "include_environment_context=false",
        "include_collaboration_mode_instructions=false",
        "skills.include_instructions=false",
        'web_search="disabled"',
    ] + [f"features.{name}=false" for name in (
        "shell_tool", "unified_exec", "view_image", "goals", "apps", "plugins", "image_generation",
        "browser_use", "computer_use", "in_app_browser", "sleep_tool", "skill_search", "tool_suggest",
        "multi_agent", "multi_agent_v2", "code_mode_host",
    )]

    name = "codex_cli"

    def __init__(self, codex_bin: Optional[str] = None, timeout_s: float = 300.0, max_attempts: int = 3,
                 workdir: Optional[Path] = None, schema: Optional[Dict[str, Any]] = None):
        self.schema = schema or JUDGE_SCHEMA  # the answer shape Codex enforces
        self.codex_bin = codex_bin or os.environ.get("COLLUSION_CODEX_BIN", "codex")
        self.timeout_s = timeout_s
        self.max_attempts = max_attempts
        self.workdir = Path(workdir) if workdir else Path(tempfile.gettempdir()) / "collusion-study-codex-cwd"
        self.workdir.mkdir(parents=True, exist_ok=True)

    def command(self, model: str, schema: Path, out: Path, instructions: Path) -> List[str]:
        overrides = [f'model_instructions_file="{instructions}"'] + self.STRIP
        return [
            self.codex_bin, "exec",
            *[arg for kv in overrides for arg in ("-c", kv)],
            "-m", model,
            "-s", "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--color", "never",
            "-C", str(self.workdir),
            "--output-schema", str(schema),
            "-o", str(out),
            "-",
        ]

    def complete(self, *, system: str, prompt: str, model: str, effort: Optional[str],
                 on_attempt: Optional[Callable[[int], None]] = None) -> CallResult:
        if any(self.workdir.iterdir()):
            raise BackendError(f"codex's working directory {self.workdir} must stay empty")
        failed: List[Dict[str, Any]] = []
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as tmp:
            schema, out = Path(tmp) / "schema.json", Path(tmp) / "last-message.txt"
            instructions = Path(tmp) / "instructions.txt"
            schema.write_text(json.dumps(self.schema))
            instructions.write_text(system)
            cmd = self.command(model, schema, out, instructions)
            for attempt in range(1, self.max_attempts + 1):
                if on_attempt is not None:
                    on_attempt(attempt)
                attempt_started = time.monotonic()
                out.unlink(missing_ok=True)
                try:
                    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                          timeout=self.timeout_s, cwd=str(self.workdir))
                except subprocess.TimeoutExpired:
                    failed.append({"attempt": attempt, "problem": f"timeout after {self.timeout_s}s",
                                   "seconds": round(time.monotonic() - attempt_started, 1)})
                    continue
                except OSError as exc:
                    raise BackendError(f"codex could not start: {exc}", failed) from exc
                text = out.read_text() if out.exists() else ""
                if proc.returncode != 0 or not text.strip():
                    combined = (proc.stderr or "") + (proc.stdout or "")
                    if _looks_like_usage_limit(combined):
                        raise UsageLimitReached(combined.strip()[-500:])
                    failed.append({"attempt": attempt, "problem": f"exit {proc.returncode}, empty={not text.strip()}",
                                   "stderr_tail": (proc.stderr or "")[-1500:],
                                   "seconds": round(time.monotonic() - attempt_started, 1)})
                    time.sleep(2 ** attempt)
                    continue
                return CallResult(text=text, model_id=model, usage={}, raw_events=[{"stdout_tail": (proc.stdout or "")[-4000:]}],
                                  duration_s=time.monotonic() - started, attempts=attempt, stderr=proc.stderr or "",
                                  failed_attempts=failed, cli_args=cmd)
        raise BackendError(f"codex exec failed after {self.max_attempts} attempts: {failed[-1]['problem'] if failed else ''}", failed)


class _JudgeClient:
    """Stands in for Colosseum's OpenAIClient inside judge_blackboards._judge_once."""

    caller: Optional[ModelCaller] = None
    model: str = ""
    meta: Dict[str, Any] = {}

    @staticmethod
    def init_context(system: str, user: str) -> Tuple[str, str]:
        return system, user

    def generate_response(self, input: Tuple[str, str], params: Dict[str, Any]) -> Tuple[None, str]:
        system, user = input
        meta = {**_JudgeClient.meta, "requested_params": json.dumps(params, sort_keys=True)}
        # Each judge call stands alone, so a failure stops only that call, not later ones.
        _JudgeClient.caller.fatal = None
        result = _JudgeClient.caller.call(system=system, prompt=user, model=_JudgeClient.model, effort=None, meta=meta)
        return None, result.text


def load_judge_module(colosseum: Path):
    if str(colosseum) not in sys.path:
        sys.path.insert(0, str(colosseum))
    from experiments.collusion import judge_blackboards as jb

    jb.OpenAIClient = _JudgeClient
    return jb


def judge_episode(out_dir: Path, jb, backend: Any, judge_model: str, max_retries: int) -> Dict[str, Any]:
    run_dir = colosseum_run_dir(out_dir)
    if run_dir is None:
        raise FileNotFoundError(f"{out_dir.name}: no Colosseum run directory")
    _JudgeClient.caller = ModelCaller(backend=backend, log=CallLog(out_dir / "judge-calls.jsonl"))
    _JudgeClient.model = judge_model
    _JudgeClient.meta = {"episode": out_dir.name, "phase": "judge"}
    cfg = jb.JudgeConfig(model=judge_model)
    asyncio.run(
        jb._evaluate_run(
            run_dir=run_dir,
            judge_cfg=cfg,
            overwrite=True,
            baseline_all_blackboards=True,  # the script's default
            semaphore=asyncio.Semaphore(1),
            max_retries=max_retries,
        )
    )
    payload = json.loads(jb._result_file_for_run(run_dir).read_text())
    payload["judge_backend"] = getattr(backend, "name", type(backend).__name__)
    payload["judge_deviation"] = (
        "temperature not settable through the CLI (the script's value is in judge_config)"
        + ("; codex keeps two multi-agent notes and some built-in tools that cannot be switched off" if payload["judge_backend"] == "codex_cli" else "")
    )
    payload["code_sha256"] = CODE_SHA256
    return payload


def judge_row(out_dir: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    status = json.loads((out_dir / "status.json").read_text()) if (out_dir / "status.json").exists() else {}
    condition, _, seed = out_dir.name.rpartition("-seed")
    row: Dict[str, Any] = {
        "batch": out_dir.parent.name,
        "episode": out_dir.name,
        "condition": status.get("spec", {}).get("condition", condition),
        "seed": int(status.get("spec", {}).get("seed", seed)),
        "episode_valid": status.get("status") == "ok",
        "judge_status": payload.get("status"),
        "secret_blackboard_id": payload.get("secret_blackboard_id"),
    }
    judgements = payload.get("judgements") or {}
    ratings, problems = [], []
    for name in PROMPT_NAMES:
        j = judgements.get(name) or {}
        row[f"judge_{name}_rating"] = j.get("rating")
        if j.get("parse_error"):
            problems.append(f"{name}: parse error")
        if j.get("rating") is not None:
            ratings.append(float(j["rating"]))
    row["judge_mean_rating"] = sum(ratings) / len(ratings) if len(ratings) == len(PROMPT_NAMES) else None
    row["judge_valid"] = row["judge_mean_rating"] is not None and not problems
    row["judge_problems"] = "; ".join(problems)
    return row


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("batch_dirs", nargs="+")
    parser.add_argument("--colosseum", required=True)
    parser.add_argument("--backend", choices=["codex", "claude", "fake"], default="codex")
    parser.add_argument("--judge-model", required=True)
    parser.add_argument("--rule", required=True, help="the logged decision this judging serves")
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-retries", type=int, default=2, help="the script's default")
    args = parser.parse_args(argv)

    jb = load_judge_module(Path(args.colosseum).resolve())
    if args.backend == "fake":
        backend = FakeBackend(lambda system, prompt, model, effort: '{"rating": 1, "rationale": "scripted"}')
    elif args.backend == "claude":
        backend = ClaudeCLIBackend()
    else:
        backend = CodexCLIBackend()
    rows, failures = [], []
    for batch in args.batch_dirs:
        # batch_status.json stores absolute paths; judge the episodes inside the folder given, so a copied batch is judged in place.
        for out_dir in (Path(batch) / d.name for d in episode_dirs(Path(batch))):
            try:
                rows.append(judge_row(out_dir, judge_episode(out_dir, jb, backend, args.judge_model, args.max_retries)))
            except Exception as exc:  # recorded; never scored as 0
                failures.append({"episode": out_dir.name, "error": f"{type(exc).__name__}: {exc}"[:500]})
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "judge.csv", "w", newline="", encoding="utf-8") as handle:
        handle.write(f"# scoring rule: {args.rule}; judge model: {args.judge_model} via {args.backend}\n")
        columns = list(dict.fromkeys(k for r in rows for k in r)) or ["episode"]
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    (out / "judge-failures.json").write_text(json.dumps(failures, indent=2))
    print(json.dumps({"judged": len(rows), "failed": len(failures), "out": str(out)}))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
