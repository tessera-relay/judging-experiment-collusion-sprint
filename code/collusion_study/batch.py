"""Run a batch of Jira episodes, one process per episode, a few at a time.

    python -m collusion_study.batch --name control --conditions C4 channel_only C1 --seeds 1 2 3 \\
        --model claude-sonnet-4-5 --colosseum ../third_party/colosseum --runs ../runs --parallel 3

Episodes run seed by seed, so the arms of one seed run side by side. Each episode gets
runs/<name>/<condition>-seed<seed>/ and a log in runs/<name>/logs/; Colosseum's own prompt logs go
to runs/<name>/colosseum-logs/<condition>-seed<seed>/, its working directory. Running the same command
again skips episodes that finished (ok or invalid) and reruns failed ones, after moving their
directory aside to <dir>.failed-<time>. A rerun resumes from the failed attempt's call log: its
successful calls are replayed in order and the episode goes on live from where it stopped
(--no-resume starts it afresh). Nothing is deleted. After a usage-limit stop no new
episode starts. runs/<name>/batch_status.json holds progress and validity only: no rewards or
advantages, so nobody sees results before the scoring rules are written.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from .backend import CODE_SHA256, utc_now
from .colosseum_runner import CONDITIONS
from .judges.engine import ARMS as JUDGE_ARMS

JIRA_CONFIG = "experiments/collusion/configs/collusion_jira_complete_n6_c2_regret_models.yaml"
CODE_DIR = Path(__file__).resolve().parents[1]


class Batch:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.root = Path(args.runs).resolve() / args.name
        self.colosseum = Path(args.colosseum).resolve()
        self.lock = threading.Lock()
        self.stopped: Optional[str] = None
        self.episodes: List[Dict[str, Any]] = [
            {"condition": c, "seed": s, "dir": str(self.root / f"{c}-seed{s}"), "state": "queued"}
            for s in args.seeds
            for c in args.conditions
        ]
        self.started = utc_now()

    def write_status(self) -> None:
        status = {
            "name": self.args.name,
            "started_utc": self.started,
            "updated_utc": utc_now(),
            "code_sha256": CODE_SHA256,
            "model": self.args.model,
            "config": self.args.config,
            "task": self.args.task,
            "prompt_variant": self.args.prompt_variant,
            "judges_budget": self.args.judges_budget,
            "judges_fixed_turns": self.args.judges_fixed_turns,
            "judges_no_scarcity_factor": self.args.judges_no_scarcity_factor,
            "stopped": self.stopped,
            "episodes": self.episodes,
        }
        tmp = self.root / "batch_status.json.tmp"
        tmp.write_text(json.dumps(status, indent=2))
        os.replace(tmp, self.root / "batch_status.json")

    def update(self, episode: Dict[str, Any], **fields: Any) -> None:
        with self.lock:
            episode.update(fields)
            self.write_status()

    def prepare(self, episode: Dict[str, Any]) -> bool:
        """False when the episode already finished; a failed or stale one is moved aside."""
        out = Path(episode["dir"])
        if not out.exists():
            return True
        status_path = out / "status.json"
        state = json.loads(status_path.read_text()).get("status") if status_path.exists() else None
        # An episode the usage limit cut short is rerun even if it finished as "invalid" (Colosseum can swallow the
        # failed call as an agent error and carry on): the limit, not the agents, made it invalid.
        if state in ("ok", "invalid") and not _hit_usage_limit(out):
            episode.update(state="skipped", previous_status=state)
            return False
        aside = out.with_name(f"{out.name}.failed-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
        out.rename(aside)
        episode["moved_aside"] = str(aside)
        return True

    def run_one(self, episode: Dict[str, Any]) -> None:
        if self.stopped:
            self.update(episode, state="not_started", note=f"batch stopped: {self.stopped}")
            return
        out = Path(episode["dir"])
        log_path = self.root / "logs" / f"{out.name}.log"
        if self.args.task == "judges":
            cmd = [
                sys.executable, "-m", "collusion_study.judges.engine",
                "--arm", episode["condition"],
                "--seed", str(episode["seed"]),
                "--model", self.args.model,
                "--out", str(out),
                "--max-calls", str(self.args.max_calls),
            ]
            if self.args.judges_budget:
                cmd += ["--budget", str(self.args.judges_budget)]
            if self.args.judges_fixed_turns:
                cmd += ["--fixed-turns", str(self.args.judges_fixed_turns)]
            if self.args.judges_no_scarcity_factor:
                cmd += ["--no-scarcity-factor", str(self.args.judges_no_scarcity_factor)]
        else:
            cmd = None
        cmd = cmd or [
            sys.executable, "-m", "collusion_study.colosseum_runner",
            "--config", str(Path(self.args.config).resolve()) if self.args.config else str(self.colosseum / JIRA_CONFIG),
            "--condition", episode["condition"],
            "--seed", str(episode["seed"]),
            "--model", self.args.model,
            "--prompt-variant", self.args.prompt_variant,
            "--out", str(out),
            "--max-calls", str(self.args.max_calls),
            "--colosseum", str(self.colosseum),
        ]
        if self.args.fake:
            cmd.append("--fake")
        # The judges engine has no replay: an interrupted judges episode starts again from the beginning.
        resume = self.resume_log(episode) if self.args.task != "judges" else None
        if resume is not None:
            cmd += ["--resume-from", str(resume)]
            episode["resumed_from"] = str(resume)
        env = dict(os.environ, PYTHONPATH=str(CODE_DIR))
        # Colosseum writes logs/ under its working directory, so each episode gets its own.
        workdir = self.root / "colosseum-logs" / out.name
        workdir.mkdir(parents=True, exist_ok=True)
        self.update(episode, state="running", started_utc=utc_now())
        began = time.monotonic()
        with open(log_path, "w", encoding="utf-8") as log:
            exit_code = subprocess.run(cmd, cwd=str(workdir), env=env, stdout=log, stderr=subprocess.STDOUT).returncode
        status_path = out / "status.json"
        status = json.loads(status_path.read_text()) if status_path.exists() else {}
        error = status.get("error")
        if _hit_usage_limit(out) and not self.stopped:
            self.stopped = f"usage limit in {out.name}"
        self.update(
            episode,
            state=status.get("status", "failed"),
            exit_code=exit_code,
            finished_utc=utc_now(),
            seconds=round(time.monotonic() - began, 1),
            live_model_calls=status.get("live_model_calls"),
            replayed_calls=status.get("replayed_calls"),
            failed_calls=status.get("failed_calls"),
            invalid_reasons=status.get("invalid_reasons"),
            error=error,
        )

    def resume_log(self, episode: Dict[str, Any]) -> Optional[Path]:
        """The interrupted attempt's call log, if it holds successful calls to replay."""
        if self.args.no_resume or not episode.get("moved_aside"):
            return None
        log = Path(episode["moved_aside"]) / "calls.jsonl"
        if any(json.loads(line).get("status") == "ok" for line in _lines(log) if line.strip()):
            return log
        return None

    def run(self) -> int:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "logs").mkdir(exist_ok=True)
        lock_path = self.root / "batch.lock"
        if lock_path.exists():
            pid = int(lock_path.read_text().strip() or 0)
            if pid and _alive(pid):
                sys.exit(f"batch {self.args.name} is already running (pid {pid})")
        lock_path.write_text(str(os.getpid()))
        try:
            todo = [e for e in self.episodes if self.prepare(e)]
            self.write_status()
            if self.args.dry_run:
                for e in todo:
                    print(e["condition"], e["seed"], e["dir"])
                return 0
            with ThreadPoolExecutor(max_workers=self.args.parallel) as pool:
                list(pool.map(self.run_one, todo))
        finally:
            lock_path.unlink(missing_ok=True)
        counts: Dict[str, int] = {}
        for e in self.episodes:
            counts[e["state"]] = counts.get(e["state"], 0) + 1
        print(json.dumps({"batch": self.args.name, "states": counts, "stopped": self.stopped}))
        return 0 if all(e["state"] in ("ok", "skipped") for e in self.episodes) else 3


def _hit_usage_limit(out: Path) -> bool:
    status_path = out / "status.json"
    error = json.loads(status_path.read_text()).get("error") if status_path.exists() else None
    return "UsageLimitReached" in str(error) or any('"status": "usage_limit"' in line for line in _lines(out / "calls.jsonl"))


def _lines(path: Path) -> List[str]:
    return path.read_text().splitlines() if path.exists() else []


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", required=True, help="batch name; episodes go to <runs>/<name>/")
    parser.add_argument("--task", choices=["colosseum", "judges"], default="colosseum")
    parser.add_argument("--conditions", nargs="+", required=True, choices=sorted(set(CONDITIONS) | set(JUDGE_ARMS)),
                        help="Colosseum conditions, or judges arms with --task judges")
    parser.add_argument("--judges-budget", type=int, default=None, help="--task judges: the budget for three judges")
    parser.add_argument("--judges-fixed-turns", type=int, default=None,
                        help="--task judges, no_scarcity arm: the run ends after this many turns (engine default 8); judges are not told")
    parser.add_argument("--judges-no-scarcity-factor", type=int, default=None,
                        help="--task judges, no_scarcity arm: the budget is this many times --judges-budget (engine default 20)")
    parser.add_argument("--seeds", nargs="+", type=int, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--config", default=None, help="Colosseum-style config; default: the paper's Jira config")
    parser.add_argument("--prompt-variant", default="simple")
    parser.add_argument("--colosseum", required=True)
    parser.add_argument("--runs", required=True, help="the runs/ directory")
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--max-calls", type=int, default=None,
                        help="per episode; default 100 (the Jira and library tasks need at most 78 and 90) or 70 for --task judges "
                             "(at most 3 judges x 1 reply x 16 turns = 48, plus retries)")
    parser.add_argument("--fake", action="store_true", help="scripted fake agents; no model calls")
    parser.add_argument("--no-resume", action="store_true", help="rerun failed episodes from the start")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    allowed = JUDGE_ARMS if args.task == "judges" else CONDITIONS
    wrong = [c for c in args.conditions if c not in allowed]
    if wrong:
        parser.error(f"not a {args.task} condition: {wrong}")
    if args.max_calls is None:
        args.max_calls = 70 if args.task == "judges" else 100
    previous = Path(args.runs) / args.name / "batch_status.json"
    if previous.exists():
        # Episode folders are named by condition and seed only, so a batch name must keep one task, config, model,
        # prompt variant and judges budget, or a finished episode run with other settings would be skipped as done
        # and a resumed one would continue under settings it did not start with.
        before = json.loads(previous.read_text())
        now = {"config": args.config, "task": args.task, "model": args.model, "prompt_variant": args.prompt_variant,
               "judges_budget": args.judges_budget, "judges_fixed_turns": args.judges_fixed_turns,
               "judges_no_scarcity_factor": args.judges_no_scarcity_factor}
        old = {"config": before.get("config"), "task": before.get("task", "colosseum"), "model": before.get("model"),
               "prompt_variant": before.get("prompt_variant", args.prompt_variant),
               "judges_budget": before.get("judges_budget", args.judges_budget),
               "judges_fixed_turns": before.get("judges_fixed_turns", args.judges_fixed_turns),
               "judges_no_scarcity_factor": before.get("judges_no_scarcity_factor", args.judges_no_scarcity_factor)}
        if old != now:
            changed = {k: (old[k], now[k]) for k in now if old[k] != now[k]}
            parser.error(f"batch {args.name} was run with other settings {changed}; use a new --name")
    return Batch(args).run()


if __name__ == "__main__":
    sys.exit(main())
