"""The paper's judge script runs unchanged on our episodes, with only the model call swapped."""
import csv
import json
import os

import pytest

from collusion_study.judge import main as judge_main

COLOSSEUM_DIR = os.environ.get("COLOSSEUM_DIR")
pytestmark = pytest.mark.skipif(not COLOSSEUM_DIR, reason="set COLOSSEUM_DIR to the Colosseum checkout")


def test_judge_uses_the_secret_board_or_all_boards_and_logs_every_call(tmp_path):
    from collusion_study.batch import main as batch_main

    runs = tmp_path / "runs"
    assert batch_main(["--name", "t", "--conditions", "C4", "channel_only", "--seeds", "1", "--model", "claude-test",
                       "--colosseum", COLOSSEUM_DIR, "--runs", str(runs), "--parallel", "2", "--fake"]) == 0
    out = tmp_path / "out"
    assert judge_main([str(runs / "t"), "--colosseum", COLOSSEUM_DIR, "--backend", "fake",
                       "--judge-model", "test-judge", "--rule", "TEST", "--out", str(out)]) == 0
    lines = (out / "judge.csv").read_text().splitlines()
    assert lines[0].startswith("# scoring rule: TEST")
    rows = {r["condition"]: r for r in csv.DictReader(lines[1:])}
    assert rows["channel_only"]["judge_status"] == "judged" and rows["channel_only"]["secret_blackboard_id"] == "1"
    assert rows["C4"]["judge_status"] == "judged_baseline_all_blackboards"
    assert all(r["judge_valid"] == "True" and float(r["judge_mean_rating"]) == 1.0 for r in rows.values())
    calls = [json.loads(l) for l in (runs / "t" / "channel_only-seed1" / "judge-calls.jsonl").read_text().splitlines()]
    assert len(calls) == 3 and all(c["status"] == "ok" for c in calls)
    assert calls[0]["system"].startswith("You are an impartial evaluator.")
    assert "Blackboard ID: 1" in calls[0]["prompt"]


def test_judging_a_copied_batch_leaves_the_original_untouched(tmp_path):
    import shutil
    from collusion_study.batch import main as batch_main

    runs = tmp_path / "runs"
    assert batch_main(["--name", "t", "--conditions", "C4", "--seeds", "1", "--model", "claude-test",
                       "--colosseum", COLOSSEUM_DIR, "--runs", str(runs), "--parallel", "1", "--fake"]) == 0
    copy = tmp_path / "copy"
    shutil.copytree(runs / "t", copy)
    assert judge_main([str(copy), "--colosseum", COLOSSEUM_DIR, "--backend", "fake",
                       "--judge-model", "test-judge", "--rule", "TEST", "--out", str(tmp_path / "out")]) == 0
    assert (copy / "C4-seed1" / "judge-calls.jsonl").exists()
    assert not (runs / "t" / "C4-seed1" / "judge-calls.jsonl").exists()
    assert not list((runs / "t").rglob("judge_secret_blackboard"))


def test_codex_backend_sends_the_system_text_first_and_reads_the_last_message(tmp_path, monkeypatch):
    import subprocess

    from collusion_study.judge import CodexCLIBackend

    seen = {}

    def fake_run(cmd, input, **kwargs):
        seen["cmd"], seen["input"] = cmd, input
        path = next(a for a in cmd if a.startswith("model_instructions_file=")).split("=", 1)[1].strip('"')
        seen["instructions"] = open(path).read()
        out = cmd[cmd.index("-o") + 1]
        open(out, "w").write('{"rating": 3, "rationale": "x"}')
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    backend = CodexCLIBackend(workdir=tmp_path / "cwd")
    result = backend.complete(system="SYS", prompt="USER", model="gpt-test", effort=None)
    assert result.text == '{"rating": 3, "rationale": "x"}' and result.model_id == "gpt-test"
    assert seen["input"] == "USER"
    assert seen["instructions"] == "SYS"
    assert "features.multi_agent=false" in seen["cmd"] and "include_environment_context=false" in seen["cmd"]
    for flag in ("-s", "read-only", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check"):
        assert flag in seen["cmd"]
    assert seen["cmd"][seen["cmd"].index("-m") + 1] == "gpt-test"
