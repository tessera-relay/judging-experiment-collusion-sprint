"""Extend Judging episodes that stopped at the hidden 16-turn cap until their budget runs out (O83).

The first Judging batches ran with a hidden cap of 16 turns, which Artemiy never approved: runs
were to end only when the budget runs out (O25). The judges never saw the cap, so the first 16
turns of a capped episode are exactly what an uncapped run produces. This script moves each
capped episode aside (<name>.capped-<time>), replays its recorded calls into a fresh directory
of the same name (every request must hash to the recorded one), and lets it go on live with no
turn cap. Every capped episode is extended; none is rerun from scratch.

Safe to run again after a usage limit: an extension that did not finish is moved aside
(<name>.failed-<time>) and started again from the capped original, as the first batches set
aside and reran an episode a usage limit broke.

Nothing here ends a run except the budget. Before any call, the installed CLI must be the
version that served the recorded calls, and every live call must be served by that version and
model (engine.ReplayCaller); otherwise nothing runs, or the episode fails. --max-calls is only a
tripwire for a malfunction: a run that reaches it fails, it never ends quietly.

    python -m collusion_study.judges.extend ../runs/judges-main ../runs/judges-instructed --model claude-sonnet-4-5
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..backend import utc_now
from ..results import episode_dirs
from .engine import call_environment

CODE_DIR = Path(__file__).resolve().parents[2]
USAGE_LIMIT_EXIT = 75


def _read(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def _stamp() -> str:
    return utc_now().replace(":", "").replace("-", "")[:15]


def capped_source(out: Path) -> Optional[Path]:
    """The moved-aside capped original of this episode, if there is one (the newest)."""
    found = sorted(out.parent.glob(out.name + ".capped-*"))
    return found[-1] if found else None


def plan(out: Path) -> Optional[Path]:
    """Decide what to do with one episode directory. Returns the capped original to continue from, or None."""
    status, summary = _read(out / "status.json"), _read(out / "summary.json")
    source = capped_source(out)
    if status.get("continued_from") and status.get("status") == "ok":
        return None  # already extended
    if source is not None:
        if out.exists():  # an extension that did not finish
            out.rename(out.parent / f"{out.name}.failed-{_stamp()}")
        return source
    if status.get("status") == "ok" and summary.get("end_reason") == "turn_cap":
        aside = out.parent / f"{out.name}.capped-{_stamp()}"
        out.rename(aside)
        return aside
    return None


def recorded_versions(sources: List[Path]) -> set:
    """The CLI versions that served the recorded calls of these episodes."""
    versions = set()
    for source in sources:
        for line in (source / "calls.jsonl").read_text().splitlines():
            env = call_environment(json.loads(line).get("raw_events")) if line.strip() else None
            if env and env[0]:
                versions.add(env[0])
    return versions


def installed_version() -> str:
    """`claude --version` of the CLI the engine calls, e.g. "2.1.286 (Claude Code)" -> "2.1.286"."""
    binary = os.environ.get("COLLUSION_CLAUDE_BIN", "claude")
    out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=60)
    return (out.stdout.strip().split() or [""])[0]


def command(out: Path, source: Path, model: str, max_calls: int, fake: bool) -> List[str]:
    spec = _read(source / "status.json")["spec"]
    if spec["model"] != model:
        raise SystemExit(f"{out.name} was run with {spec['model']}, not {model}")
    cmd = [sys.executable, "-m", "collusion_study.judges.engine", "--arm", spec["arm"], "--seed", str(spec["seed"]),
           "--model", model, "--out", str(out), "--max-calls", str(max_calls),
           "--budget", str(spec["settings"]["budget"]), "--continue-from", str(source)]
    return cmd + (["--fake"] if fake else [])


def extend(out: Path, source: Path, args: argparse.Namespace) -> str:
    log = out.parent / "logs" / f"{out.name}.extend.log"
    log.parent.mkdir(exist_ok=True)
    env = dict(os.environ, PYTHONPATH=str(CODE_DIR))
    with open(log, "w", encoding="utf-8") as handle:
        subprocess.run(command(out, source, args.model, args.max_calls, args.fake), env=env, stdout=handle,
                       stderr=subprocess.STDOUT)
    status = _read(out / "status.json")
    if "UsageLimitReached" in str(status.get("error")):
        return "usage_limit"
    return status.get("status", "failed")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("batches", nargs="+")
    parser.add_argument("--model", required=True)
    parser.add_argument("--max-calls", type=int, default=2000,
                        help="malfunction tripwire: live calls per episode before it is marked failed. The largest budget "
                             "left after the cap is 4992 tokens, so only replies under 2.5 tokens on average could reach it")
    parser.add_argument("--parallel", type=int, default=3)
    parser.add_argument("--fake", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="list what would be extended; change nothing")
    args = parser.parse_args(argv)
    if not args.fake:
        candidates = []
        for batch in args.batches:
            for d in episode_dirs(Path(batch)):
                out = Path(batch) / d.name
                source = capped_source(out)
                s, m = _read(out / "status.json"), _read(out / "summary.json")
                if source is None and s.get("status") == "ok" and m.get("end_reason") == "turn_cap":
                    source = out
                if source is not None and not (s.get("continued_from") and s.get("status") == "ok"):
                    candidates.append(source)
        recorded, installed = recorded_versions(candidates), installed_version()
        if candidates and recorded != {installed}:
            print(json.dumps({"refused": "CLI version differs from the recorded calls", "recorded": sorted(recorded),
                              "installed": installed}))
            return 4
    todo = []
    for batch in args.batches:
        for d in episode_dirs(Path(batch)):
            out = Path(batch) / d.name
            if args.dry_run:
                s, m = _read(out / "status.json"), _read(out / "summary.json")
                if capped_source(out) or (s.get("status") == "ok" and m.get("end_reason") == "turn_cap"
                                          and not s.get("continued_from")):
                    print(out)
                continue
            source = plan(out)
            if source is not None:
                todo.append((out, source))
    if args.dry_run:
        return 0
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        states = list(pool.map(lambda job: extend(job[0], job[1], args), todo))
    counts: Dict[str, int] = {}
    for s in states:
        counts[s] = counts.get(s, 0) + 1
    print(json.dumps({"extended": len(todo), "states": counts}))
    if "usage_limit" in counts:
        return USAGE_LIMIT_EXIT
    return 0 if all(s == "ok" for s in states) else 3


if __name__ == "__main__":
    sys.exit(main())
