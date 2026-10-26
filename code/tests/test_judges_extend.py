"""Extending capped Judging episodes (O83): which episodes are picked, moved aside and continued."""
import json
import subprocess
import sys
from pathlib import Path

from collusion_study.judges import extend
from collusion_study.judges.engine import JudgesSpec, Settings, run_episode

CODE = Path(__file__).resolve().parents[1]


def _episode(batch, name, arm, seed, max_turns):
    run_episode(JudgesSpec(arm=arm, seed=seed, model="fake", effort=None, out_dir=str(batch / name),
                           settings=Settings(budget=3000, max_turns=max_turns), backend="fake"))


def _cli(*args):
    return subprocess.run([sys.executable, "-m", "collusion_study.judges.extend", *args], cwd=CODE,
                          env={"PYTHONPATH": str(CODE), "PATH": "/usr/bin:/bin"}, capture_output=True, text=True)


def test_only_capped_episodes_are_extended_and_the_originals_kept(tmp_path):
    batch = tmp_path / "judges-x"
    _episode(batch, "C4-seed1", "C4", 1, 4)            # capped
    _episode(batch, "C4-seed2", "C4", 2, None)         # ended on budget
    before = (batch / "C4-seed2" / "events.jsonl").read_text()
    r = _cli(str(batch), "--model", "fake", "--fake", "--parallel", "1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout.strip().splitlines()[-1]) == {"extended": 1, "states": {"ok": 1}}
    status = json.loads((batch / "C4-seed1" / "status.json").read_text())
    summary = json.loads((batch / "C4-seed1" / "summary.json").read_text())
    assert status["continued_from"].startswith(str(batch / "C4-seed1.capped-"))
    assert summary["end_reason"] == "budget" and summary["replayed_calls"] > 0
    assert len(list(batch.glob("C4-seed1.capped-*"))) == 1
    assert (batch / "C4-seed2" / "events.jsonl").read_text() == before
    again = _cli(str(batch), "--model", "fake", "--fake")
    assert json.loads(again.stdout.strip().splitlines()[-1])["extended"] == 0


def test_an_unfinished_extension_is_set_aside_and_restarted(tmp_path):
    batch = tmp_path / "judges-y"
    _episode(batch, "C4-seed1", "C4", 1, 4)
    out = batch / "C4-seed1"
    out.rename(batch / "C4-seed1.capped-20261025T000000")
    out.mkdir()
    (out / "status.json").write_text(json.dumps({"status": "failed", "error": "UsageLimitReached: x"}))
    assert extend.plan(out) == batch / "C4-seed1.capped-20261025T000000"
    assert not out.exists() and len(list(batch.glob("C4-seed1.failed-*"))) == 1


def test_a_model_mismatch_is_refused(tmp_path):
    batch = tmp_path / "judges-z"
    _episode(batch, "C4-seed1", "C4", 1, 4)
    r = _cli(str(batch), "--model", "claude-sonnet-4-5", "--fake")
    assert r.returncode != 0 and "was run with fake" in (r.stdout + r.stderr)


def _fake_cli(tmp_path, version):
    binary = tmp_path / "claude"
    binary.write_text(f"#!/bin/sh\necho '{version} (Claude Code)'\n")
    binary.chmod(0o755)
    return str(binary)


def _real_looking(batch, name):
    path = batch / name / "calls.jsonl"
    records = [json.loads(l) for l in path.read_text().splitlines()]
    for r in records:
        r["raw_events"] = [{"type": "system", "subtype": "init", "claude_code_version": "2.1.286"}]
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    status = json.loads((batch / name / "status.json").read_text())
    status["spec"]["model"] = "claude-sonnet-4-5"
    (batch / name / "status.json").write_text(json.dumps(status))


def test_another_installed_cli_version_refuses_before_anything_moves(tmp_path):
    batch = tmp_path / "judges-v"
    _episode(batch, "C4-seed1", "C4", 1, 4)
    _real_looking(batch, "C4-seed1")
    env = {"PYTHONPATH": str(CODE), "PATH": "/usr/bin:/bin", "COLLUSION_CLAUDE_BIN": _fake_cli(tmp_path, "2.1.300")}
    r = subprocess.run([sys.executable, "-m", "collusion_study.judges.extend", str(batch), "--model", "claude-sonnet-4-5"],
                       cwd=CODE, env=env, capture_output=True, text=True)
    assert r.returncode == 4 and "2.1.300" in r.stdout, r.stdout + r.stderr
    assert sorted(p.name for p in batch.iterdir() if "seed" in p.name) == ["C4-seed1"]
    env["COLLUSION_CLAUDE_BIN"] = _fake_cli(tmp_path, "2.1.286")
    ok = subprocess.run([sys.executable, "-m", "collusion_study.judges.extend", str(batch), "--model", "claude-sonnet-4-5",
                         "--dry-run"], cwd=CODE, env=env, capture_output=True, text=True)
    assert ok.returncode == 0 and ok.stdout.strip().endswith("C4-seed1"), ok.stdout + ok.stderr
