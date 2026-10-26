"""Smoke test of the fixed setup: three single model calls with real Jira prompts.

The three calls are the first three agent calls of a Jira episode (condition C1, seed 1),
made through the real runner, so Colosseum's prompts make the full round trip. As the plan
says, they measure whether the CLI accepts the chosen model, whether the tool calls parse,
and seconds and tokens per call. They also check that the calls are isolated from the
operator's own Claude Code setup: subscription login, no tools, no MCP servers, no plugins
or skills beyond the CLI's own built-in ones, no thinking, output within Colosseum's
1,500-token cap, and no large block of
added context (input tokens per character of our request). The episode stops at the call
cap, so its output is a check, not data.

    python -m collusion_study.smoke_test --colosseum ../third_party/colosseum --out ../runs/smoke2
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .backend import CHILD_ENV_KEEP, CODE_SHA256, ClaudeCLIBackend, child_env
from .textcalls import parse_tool_calls

SPRINT_OPENS_UTC = "2026-10-23T12:00:00+00:00"
JIRA_CONFIG = "experiments/collusion/configs/collusion_jira_complete_n6_c2_regret_models.yaml"
SMOKE_CALLS = 3
OUTPUT_CAP = 1500  # max_tokens in the Jira config
# Smoke test 2 (23 Oct) measured 0.34 to 0.36 input tokens per character of our request,
# including the CLI's own reminders (about 250 tokens). The operator's CLAUDE.md (about
# 1,400 tokens) would push a 4,600-character request above 0.6.
MAX_TOKENS_PER_REQUEST_CHAR = 0.40
# The CLI lists these plugins of its own even in safe mode. A recorded request body (23 Oct)
# shows they add nothing to the request; any other plugin fails the check.
BUILTIN_PLUGINS = {"cc-plugin-agents-md", "cc-plugin-telemetry", "cc-plugin-plugin-authoring"}
KNOWN_EVENT_TYPES = {"system", "assistant", "user", "result", "_unparsed_line"}
# Only these errors justify the fallback model (decision: Sonnet 4.5, else Sonnet 5.5).
MODEL_UNAVAILABLE_MARKERS = (
    "issue with the selected model",
    "may not exist",
    "not_found_error",
    "model_not_found",
    "invalid model",
    "unknown model",
    "does not have access",
)


def wait_for_opening(not_before: datetime, wait: bool) -> None:
    now = datetime.now(timezone.utc)
    if now < not_before:
        if not wait:
            sys.exit(f"No model calls before {not_before.isoformat()}. Use --wait to sleep until then.")
        seconds = (not_before - now).total_seconds()
        print(f"Waiting {seconds:.0f}s until {not_before.isoformat()}", flush=True)
        time.sleep(seconds + 1)
    if datetime.now(timezone.utc) < not_before:
        sys.exit("Woke up before the opening time; no model calls made.")


def cli_facts(claude_bin: str) -> Dict[str, Any]:
    try:
        version = subprocess.run(
            [claude_bin, "--version"], capture_output=True, text=True, timeout=60, env=child_env()
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        version = f"unavailable: {exc}"
    return {
        "claude_bin": shutil.which(claude_bin) or claude_bin,
        "claude_version": version,
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        # Names only, never values.
        "child_env_names": sorted(child_env()),
        "parent_claude_env_names_not_passed": sorted(
            name for name in os.environ if name.startswith(("CLAUDE", "ANTHROPIC")) and name not in CHILD_ENV_KEEP
        ),
    }


def _init_event(events: List[Any]) -> Dict[str, Any]:
    for event in events or []:
        if isinstance(event, dict) and event.get("type") == "system" and event.get("subtype") == "init":
            return event
    return {}


def _result_event(events: List[Any]) -> Dict[str, Any]:
    for event in reversed(events or []):
        if isinstance(event, dict) and event.get("type") == "result":
            return event
    return {}


def _assistant_messages(events: List[Any]) -> List[Dict[str, Any]]:
    return [
        e["message"]
        for e in events or []
        if isinstance(e, dict) and e.get("type") == "assistant" and isinstance(e.get("message"), dict)
    ]


def _input_total(usage: Dict[str, Any]) -> int:
    return sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


def describe_call(record: Dict[str, Any]) -> Dict[str, Any]:
    events = record.get("raw_events") or []
    init, result = _init_event(events), _result_event(events)
    messages = _assistant_messages(events)
    usage = {k: v for k, v in (record.get("usage") or {}).items() if not k.startswith("_")}
    parsed = parse_tool_calls(record.get("text") or "")
    request_chars = len(record.get("system") or "") + len(record.get("prompt") or "")
    input_total = _input_total(usage)
    return {
        "call_id": record.get("call_id"),
        "request_sha256": record.get("request_sha256"),
        "status": record.get("status"),
        "error": record.get("error"),
        "turn": {k[5:]: v for k, v in record.items() if k.startswith("meta_")},
        "model_requested": record.get("model_requested"),
        "model_in_init": init.get("model"),
        "models_in_replies": sorted({str(m.get("model")) for m in messages if m.get("model")}),
        "api_key_source": init.get("apiKeySource"),
        "claude_code_version": init.get("claude_code_version"),
        "tools_offered": init.get("tools"),
        "mcp_servers": init.get("mcp_servers"),
        "plugins": init.get("plugins"),
        "skills": init.get("skills"),
        "output_style": init.get("output_style"),
        "content_block_types": sorted(
            {str(b.get("type")) for m in messages for b in (m.get("content") or []) if isinstance(b, dict)}
        ),
        "duration_s": record.get("duration_s"),
        "duration_api_ms": result.get("duration_api_ms"),
        "attempts": record.get("attempts"),
        "failed_attempts": len(record.get("failed_attempts") or []),
        "usage": usage,
        "input_tokens_total": input_total,
        "output_tokens": usage.get("output_tokens"),
        "request_chars": request_chars,
        "input_tokens_per_request_char": round(input_total / request_chars, 3) if request_chars and input_total else None,
        "api_equivalent_cost_usd": result.get("total_cost_usd"),
        "tool_calls_parsed": [c.name for c in parsed if not c.error],
        "tool_call_errors": [c.error for c in parsed if c.error],
        "text_start": (record.get("text") or "")[:400],
        "other_event_types": sorted({str(e.get("type")) for e in events if isinstance(e, dict)} - KNOWN_EVENT_TYPES),
        "cli_args": record.get("cli_args"),
    }


def read_calls(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def model_unavailable(calls: List[Dict[str, Any]]) -> bool:
    """True only when the first call failed because the CLI refused the model itself."""
    if not calls or calls[0].get("status") == "ok":
        return False
    first = calls[0]
    evidence = json.dumps([first.get("error"), first.get("failed_attempts")], default=str).lower()
    return any(marker in evidence for marker in MODEL_UNAVAILABLE_MARKERS)


def run_smoke_episode(colosseum: Path, out_dir: Path, model: str) -> str:
    from .colosseum_runner import EpisodeSpec, run_episode

    spec = EpisodeSpec(
        config_path=str(colosseum / JIRA_CONFIG),
        condition="C1",
        seed=1,
        model=model,
        effort=None,
        out_dir=str(out_dir),
        max_calls=SMOKE_CALLS,
    )
    try:
        run_episode(spec)
        return "finished"
    except Exception as exc:  # the call cap normally ends it
        return f"{type(exc).__name__}: {str(exc)[:300]}"


def context_overhead_estimate(ok: List[Dict[str, Any]], records: List[Dict[str, Any]]) -> Optional[float]:
    """Tokens the CLI adds beyond our request, from two calls with the same system prompt.

    Fits tokens = a * chars + b through the two calls; b is the added context. Rough: the
    text added between the calls is not the same kind of text as the rest of the prompt.
    """
    by_id = {r.get("call_id"): r for r in records}
    for i, first in enumerate(ok):
        for second in ok[i + 1 :]:
            same_system = by_id[first["call_id"]].get("system") == by_id[second["call_id"]].get("system")
            if same_system and second["request_chars"] != first["request_chars"]:
                a = (second["input_tokens_total"] - first["input_tokens_total"]) / (
                    second["request_chars"] - first["request_chars"]
                )
                return round(first["input_tokens_total"] - a * first["request_chars"], 1)
    return None


def run_checks(described: List[Dict[str, Any]], records: List[Dict[str, Any]], model: str) -> Dict[str, Any]:
    ok = [c for c in described if c["status"] == "ok"]
    unexpected = [c for c in described if c["status"] not in ("ok", "call_cap_reached", "refused_after_failure")]
    checks = {
        "three_calls_ok": len(ok) == SMOKE_CALLS and not unexpected,
        "model_accepted": bool(ok)
        and all(
            str(c["model_in_init"]).startswith(model)
            and c["models_in_replies"]
            and all(m.startswith(model) for m in c["models_in_replies"])
            for c in ok
        ),
        "tool_calls_parse": any(c["tool_calls_parsed"] for c in ok) and not any(c["tool_call_errors"] for c in ok),
        "subscription_login": bool(ok) and all(c["api_key_source"] == "none" for c in ok),
        "no_tools_offered": bool(ok) and all(isinstance(c["tools_offered"], list) and not c["tools_offered"] for c in ok),
        "no_mcp_servers": bool(ok) and all(isinstance(c["mcp_servers"], list) and not c["mcp_servers"] for c in ok),
        "only_builtin_plugins_no_skills": bool(ok)
        and all(
            not c["skills"]
            and all(isinstance(p, dict) and p.get("path") == "builtin" and p.get("name") in BUILTIN_PLUGINS for p in c["plugins"] or [])
            for c in ok
        ),
        "no_thinking": bool(ok)
        and all(not ({"thinking", "redacted_thinking"} & set(c["content_block_types"])) for c in ok),
        "output_within_cap": bool(ok) and all((c["output_tokens"] or 0) <= OUTPUT_CAP for c in ok),
        "no_large_added_context": bool(ok)
        and all((c["input_tokens_per_request_char"] or 1) <= MAX_TOKENS_PER_REQUEST_CHAR for c in ok),
    }
    measures = {
        "seconds_per_call": [c["duration_s"] for c in ok],
        "input_tokens_per_call": [c["input_tokens_total"] for c in ok],
        "output_tokens_per_call": [c["output_tokens"] for c in ok],
        "request_chars_per_call": [c["request_chars"] for c in ok],
        "input_tokens_per_request_char": [c["input_tokens_per_request_char"] for c in ok],
        "context_overhead_estimate_tokens": context_overhead_estimate(ok, records),
        "calls_with_a_tool_call": sum(1 for c in ok if c["tool_calls_parsed"]),
        "tool_calls_per_call": [c["tool_calls_parsed"] for c in ok],
        "retried_calls": sum(1 for c in described if c["failed_attempts"]),
        "unexpected_statuses": [c["status"] for c in unexpected],
    }
    return {"checks": checks, "measures": measures}


def compare_with_baseline(described: List[Dict[str, Any]], baseline_path: Path) -> List[Dict[str, Any]]:
    """Input tokens of byte-identical requests in an earlier smoke run, and the difference."""
    earlier = {c.get("request_sha256"): c for c in json.loads(baseline_path.read_text()).get("calls", []) if c.get("status") == "ok"}
    rows = []
    for c in described:
        before = earlier.get(c.get("request_sha256"))
        if c["status"] == "ok" and before and before.get("request_sha256"):
            rows.append(
                {
                    "call_id": c["call_id"],
                    "input_tokens_before": before["input_tokens_total"],
                    "input_tokens_now": c["input_tokens_total"],
                    "tokens_removed": before["input_tokens_total"] - c["input_tokens_total"],
                }
            )
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--colosseum", required=True, help="path to the Colosseum checkout")
    parser.add_argument("--out", required=True, help="a new or empty directory")
    parser.add_argument("--model", default="claude-sonnet-4-5")
    parser.add_argument("--fallback-model", default="claude-sonnet-5-5")
    parser.add_argument("--not-before", default=SPRINT_OPENS_UTC)
    parser.add_argument("--wait", action="store_true", help="sleep until --not-before instead of exiting")
    parser.add_argument("--baseline", help="an earlier smoke_summary.json, to compare identical requests")
    args = parser.parse_args(argv)

    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        sys.exit(f"{out} is not empty; use a new directory")
    out.mkdir(parents=True, exist_ok=True)
    colosseum = Path(args.colosseum).resolve()
    sys.path.insert(0, str(colosseum))
    summary: Dict[str, Any] = {"code_sha256": CODE_SHA256, "cli": cli_facts(ClaudeCLIBackend().claude_bin)}

    wait_for_opening(datetime.fromisoformat(args.not_before.replace("Z", "+00:00")), args.wait)
    summary["started_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    model = args.model
    episode_dir = out / "episode"
    summary["episode_end"] = run_smoke_episode(colosseum, episode_dir, model)
    records = read_calls(episode_dir / "calls.jsonl")
    if model_unavailable(records) and args.fallback_model:
        summary["model_unavailable"] = model
        model = args.fallback_model
        episode_dir = out / "episode-fallback"
        summary["episode_end"] = run_smoke_episode(colosseum, episode_dir, model)
        records = read_calls(episode_dir / "calls.jsonl")
    summary["model_used"] = model
    status_path = episode_dir / "status.json"
    summary["episode_status"] = json.loads(status_path.read_text()) if status_path.exists() else None

    described = [describe_call(r) for r in records]
    summary.update(run_checks(described, records, model))
    summary["passed"] = all(v is True for v in summary["checks"].values())
    if args.baseline:
        summary["measures"]["same_requests_as_baseline"] = compare_with_baseline(described, Path(args.baseline))
    summary["calls"] = described
    summary["finished_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (out / "smoke_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    print(json.dumps({k: summary[k] for k in ("model_used", "passed", "checks", "measures")}, indent=2, default=str))
    print(f"Full summary: {out / 'smoke_summary.json'}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
