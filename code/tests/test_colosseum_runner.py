"""End-to-end checks of the 2.1 conditions in Colosseum's Jira task, with scripted fake agents."""
import json
import os
from pathlib import Path

import pytest

from collusion_study.colosseum_runner import (
    C2_NO_CHANNEL_LINE,
    PAPER_COLOSSEUM_COMMIT,
    EpisodeSpec,
    best_subset_advantage,
    invalid_reasons,
    run_episode,
)

COLOSSEUM_DIR = os.environ.get("COLOSSEUM_DIR")
needs_colosseum = pytest.mark.skipif(not COLOSSEUM_DIR, reason="set COLOSSEUM_DIR to the Colosseum checkout")
JIRA_CONFIG = "experiments/collusion/configs/collusion_jira_complete_n6_c2_regret_models.yaml"


def spec_for(out: Path, condition: str, seed: int = 1) -> EpisodeSpec:
    return EpisodeSpec(
        config_path=str(Path(COLOSSEUM_DIR) / JIRA_CONFIG),
        condition=condition,
        seed=seed,
        model="claude-test",
        effort=None,
        out_dir=str(out),
        backend="fake",
    )


def episode(out: Path, condition: str, seed: int = 1):
    summary = run_episode(spec_for(out, condition, seed))
    calls = [json.loads(line) for line in (out / "calls.jsonl").read_text().splitlines()]
    run_config = json.loads((Path(summary["colosseum_run_dir"]) / "run_config.json").read_text())
    return summary, calls, run_config


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    root = tmp_path_factory.mktemp("jira")
    return {c: episode(root / c, c) for c in ("C1", "C2", "C3", "C4", "channel_only")}


def prompts_of(calls, agents):
    return [c["prompt"] for c in calls if c["meta_agent"] in agents]


@needs_colosseum
def test_same_pair_and_turn_order_in_every_condition(runs):
    pairs = {c: tuple(s["assigned_pair"]) for c, (s, _, _) in runs.items()}
    orders = {c: tuple(rc["agent_turn_order"]) for c, (_, _, rc) in runs.items()}
    assert len(set(pairs.values())) == 1 and len(pairs["C1"]) == 2
    assert len(set(orders.values())) == 1


@needs_colosseum
def test_every_agent_acted_so_nothing_was_filled_at_random(runs):
    for summary, _, _ in runs.values():
        assert summary["random_fallback_fills"] == 0
        # 6 agents x (4 rounds x 2 steps + 1 decision): the scripted agents answer step 2
        # without a tool call, and a reply without a tool call ends the turn.
        assert summary["live_model_calls"] == 54


@needs_colosseum
def test_c1_private_channel_with_instructions(runs):
    summary, calls, run_config = runs["C1"]
    pair = summary["assigned_pair"]
    others = [a for a in run_config["agent_turn_order"] if a not in pair]
    board = run_config["secret_blackboard_id"]
    assert board is not None
    assert sorted(run_config["blackboard_participants"][str(board)] if str(board) in run_config["blackboard_participants"] else run_config["blackboard_participants"][board]) == sorted(pair)
    assert all("=== SECRET COALITION INSTRUCTIONS" in p and f"Collusion blackboard_id: {board}" in p for p in prompts_of(calls, pair))
    assert not any("SECRET COALITION" in p for p in prompts_of(calls, others))


@needs_colosseum
def test_c2_instructions_without_a_channel(runs):
    summary, calls, run_config = runs["C2"]
    pair = summary["assigned_pair"]
    others = [a for a in run_config["agent_turn_order"] if a not in pair]
    assert run_config["secret_blackboard_id"] is None
    assert summary["c2_prompts_patched"] > 0
    pair_prompts = prompts_of(calls, pair)
    assert all("=== SECRET COALITION INSTRUCTIONS" in p and C2_NO_CHANNEL_LINE in p for p in pair_prompts)
    assert all("Prompt variant: simple" in p for p in pair_prompts)
    assert not any("SECRET COALITION" in p for p in prompts_of(calls, others))
    assert not any("Blackboard 1:" in c["prompt"] for c in calls)


@needs_colosseum
def test_c3_private_board_for_every_pair(runs):
    summary, calls, run_config = runs["C3"]
    boards = summary["pair_channel_ids"]
    assert len(boards) == 15 and sorted(b["blackboard_id"] for b in boards) == list(range(1, 16))
    participants = run_config["blackboard_participants"]
    for b in boards:
        members = participants.get(str(b["blackboard_id"]), participants.get(b["blackboard_id"]))
        assert sorted(members) == sorted(b["pair"])
    # Each agent sees its own five pair boards and no others; nobody gets instructions.
    for agent in run_config["agent_turn_order"]:
        first = next(c["prompt"] for c in calls if c["meta_agent"] == agent and c["meta_step"] == 1)
        for b in boards:
            assert (f"Blackboard {b['blackboard_id']}:" in first) == (agent in b["pair"])
    assert run_config["secret_blackboard_id"] is None
    assert not any("SECRET COALITION" in c["prompt"] for c in calls)


@needs_colosseum
def test_c4_baseline(runs):
    summary, calls, run_config = runs["C4"]
    assert run_config["secret_blackboard_id"] is None and summary["pair_channel_ids"] == []
    assert not any("SECRET COALITION" in c["prompt"] or "Blackboard 1:" in c["prompt"] for c in calls)


@needs_colosseum
def test_channel_only_gives_the_pair_a_private_channel_and_no_instructions(runs):
    summary, calls, run_config = runs["channel_only"]
    pair = summary["assigned_pair"]
    others = [a for a in run_config["agent_turn_order"] if a not in pair]
    board = run_config["secret_blackboard_id"]
    assert board is not None and run_config["prompt_variant"] == "control"
    participants = run_config["blackboard_participants"]
    assert sorted(participants.get(str(board), participants.get(board))) == sorted(pair)
    assert summary["prompt_variant"] == "control" and not summary["pair_has_instructions"]
    assert not any("SECRET COALITION" in c["prompt"] for c in calls)
    assert all(f"Blackboard {board}:" in p for p in prompts_of([c for c in calls if c["meta_step"] == 1], pair))
    assert not any(f"Blackboard {board}:" in p for p in prompts_of(calls, others))


@needs_colosseum
def test_clean_episodes_are_valid_and_record_what_ran(runs):
    for condition, (summary, _, _) in runs.items():
        assert summary["valid"] and summary["invalid_reasons"] == [] and summary["failed_calls"] == 0
        status = json.loads((Path(summary["spec"]["out_dir"]) / "status.json").read_text())
        assert status["status"] == "ok" and status["code_sha256"] == summary["code_sha256"]
        sidecar = json.loads((Path(summary["colosseum_run_dir"]) / "study_condition.json").read_text())
        assert sidecar["condition"]["name"] == condition


@needs_colosseum
def test_an_episode_never_reuses_a_directory(runs):
    summary, _, _ = runs["C4"]
    with pytest.raises(FileExistsError):
        run_episode(spec_for(Path(summary["spec"]["out_dir"]), "C4"))


@needs_colosseum
def test_a_failed_call_fails_the_episode(tmp_path):
    from collusion_study.fake_agents import jira_fake_responder

    seen = {"calls": 0}

    def flaky(system, prompt, model, effort):
        seen["calls"] += 1
        if seen["calls"] == 2:
            raise RuntimeError("simulated CLI failure")
        return jira_fake_responder(system, prompt, model, effort)

    out = tmp_path / "flaky"
    with pytest.raises(Exception):
        run_episode(spec_for(out, "C1"), responder=flaky)
    status = json.loads((out / "status.json").read_text())
    assert status["status"] == "failed" and status["failed_calls"] == 1


def test_invalid_reasons_name_each_problem(tmp_path):
    lines = [json.dumps({"status": "ok"}), json.dumps({"status": "failed"})]
    (tmp_path / "calls.jsonl").write_text("\n".join(lines) + "\n")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "agent_turns.json").write_text(json.dumps([{"response": "[ERROR] Tool execution completed but failed"}]))

    class Caller:
        fatal = "BackendError: three attempts failed"

    paper = {"commit": PAPER_COLOSSEUM_COMMIT, "tracked_files_changed": False, "terrarium_agents": "0.1.1"}
    assert invalid_reasons(tmp_path, run_dir, {"random_fallback_fills": 0}, Caller(), paper, "fake") != []
    reasons = invalid_reasons(tmp_path, run_dir, {"random_fallback_fills": 2}, Caller(), {**paper, "commit": "abc"}, "claude_cli")
    assert len(reasons) == 5


@needs_colosseum
def test_rerunning_an_episode_sends_identical_requests(runs, tmp_path):
    _, first, _ = runs["C4"]
    _, second, _ = episode(tmp_path / "again", "C4")
    assert [c["request_sha256"] for c in first] == [c["request_sha256"] for c in second]


def test_best_subset_advantage():
    rewards = {"a": 10.0, "b": 8.0, "c": 1.0, "d": 1.0}
    assert best_subset_advantage(rewards, size=2) == {"subset": ["a", "b"], "advantage": 8.0}
    assert best_subset_advantage(rewards) == {"subset": ["a", "b"], "advantage": 8.0}
    lone = best_subset_advantage({"a": 10.0, "b": 1.0, "c": 1.0, "d": 1.0})
    assert lone == {"subset": ["a"], "advantage": 9.0}


@needs_colosseum
def test_a_batch_runs_each_episode_once_and_reports_validity_only(tmp_path):
    from collusion_study.batch import main as batch_main

    args = ["--name", "b", "--conditions", "C4", "channel_only", "--seeds", "1", "--model", "claude-test",
            "--colosseum", str(Path(COLOSSEUM_DIR)), "--runs", str(tmp_path), "--parallel", "2", "--fake"]
    assert batch_main(args) == 0
    status = json.loads((tmp_path / "b" / "batch_status.json").read_text())
    assert [e["state"] for e in status["episodes"]] == ["ok", "ok"]
    assert not any("advantage" in key or "reward" in key for e in status["episodes"] for key in e)
    assert batch_main(args) == 0
    status = json.loads((tmp_path / "b" / "batch_status.json").read_text())
    assert [e["state"] for e in status["episodes"]] == ["skipped", "skipped"]


def interrupted_at(call_number: int):
    """A fake responder that fails at one call, as a usage limit or a dropped connection would."""
    from collusion_study.fake_agents import jira_fake_responder

    seen = {"calls": 0}

    def responder(system, prompt, model, effort):
        seen["calls"] += 1
        if seen["calls"] == call_number:
            raise RuntimeError("simulated interruption")
        return jira_fake_responder(system, prompt, model, effort)

    return responder


def ok_calls(out: Path):
    return [c for c in map(json.loads, (out / "calls.jsonl").read_text().splitlines()) if c["status"] == "ok"]


@needs_colosseum
def test_an_interrupted_episode_resumes_from_its_call_log(runs, tmp_path):
    first = tmp_path / "first"
    with pytest.raises(Exception):
        run_episode(spec_for(first, "C1"), responder=interrupted_at(20))
    assert len(ok_calls(first)) == 19
    spec = spec_for(tmp_path / "resumed", "C1")
    spec.resume_from = str(first / "calls.jsonl")
    summary = run_episode(spec)
    uninterrupted_summary, uninterrupted_calls, _ = runs["C1"]
    resumed_calls = ok_calls(tmp_path / "resumed")
    assert summary["valid"] and summary["replayed_calls"] == 19
    assert summary["live_model_calls"] == len(uninterrupted_calls) - 19
    assert [c["request_sha256"] for c in resumed_calls] == [c["request_sha256"] for c in uninterrupted_calls]
    assert summary["agent_rewards"] == uninterrupted_summary["agent_rewards"]


@needs_colosseum
def test_a_resume_that_drifts_from_its_recording_is_stopped(runs, tmp_path):
    c4_log = Path(runs["C4"][0]["colosseum_run_dir"]).parents[3] / "calls.jsonl"
    spec = spec_for(tmp_path / "drift", "C1")
    spec.resume_from = str(c4_log)
    try:
        run_episode(spec)
    except Exception:
        pass
    status = json.loads((tmp_path / "drift" / "status.json").read_text())
    assert status["status"] in ("failed", "invalid") and "ReplayDivergence" in json.dumps(status)


@needs_colosseum
def test_a_batch_resumes_a_failed_episode(tmp_path):
    from collusion_study.batch import main as batch_main

    with pytest.raises(Exception):
        run_episode(spec_for(tmp_path / "b" / "C1-seed1", "C1"), responder=interrupted_at(20))
    args = ["--name", "b", "--conditions", "C1", "--seeds", "1", "--model", "claude-test",
            "--colosseum", str(Path(COLOSSEUM_DIR)), "--runs", str(tmp_path), "--fake"]
    assert batch_main(args) == 0
    (episode_status,) = json.loads((tmp_path / "b" / "batch_status.json").read_text())["episodes"]
    assert episode_status["state"] == "ok" and episode_status["replayed_calls"] == 19
    assert episode_status["resumed_from"] == str(Path(episode_status["moved_aside"]) / "calls.jsonl")


def test_a_batch_name_keeps_one_config(tmp_path):
    import json as _json

    from collusion_study import batch

    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "batch_status.json").write_text(_json.dumps({"config": "configs/library_a3.yaml", "task": "colosseum"}))
    with pytest.raises(SystemExit):
        batch.main(["--name", "lib", "--conditions", "channel_only", "--seeds", "1", "--model", "fake",
                    "--config", "configs/library_a1.yaml", "--colosseum", str(tmp_path), "--runs", str(tmp_path), "--dry-run"])


def test_a_batch_reruns_an_invalid_episode_the_usage_limit_cut_short(tmp_path):
    from collusion_study import batch

    for name, line in (("C1-seed1", {"status": "usage_limit"}), ("C4-seed1", {"status": "failed"})):
        d = tmp_path / "b" / name
        d.mkdir(parents=True)
        (d / "status.json").write_text(json.dumps({"status": "invalid"}))
        (d / "calls.jsonl").write_text(json.dumps(line) + "\n")
    batch.main(["--name", "b", "--conditions", "C1", "C4", "--seeds", "1", "--model", "m", "--colosseum", str(tmp_path),
                "--runs", str(tmp_path), "--dry-run"])
    states = {Path(e["dir"]).name: e for e in json.loads((tmp_path / "b" / "batch_status.json").read_text())["episodes"]}
    assert states["C1-seed1"].get("moved_aside") and states["C4-seed1"]["state"] == "skipped"


def test_a_batch_name_keeps_one_model(tmp_path):
    from collusion_study import batch

    (tmp_path / "j").mkdir()
    (tmp_path / "j" / "batch_status.json").write_text(json.dumps({"config": None, "task": "judges", "model": "claude-sonnet-4-5"}))
    with pytest.raises(SystemExit):
        batch.main(["--name", "j", "--task", "judges", "--conditions", "C4", "--seeds", "1", "--model", "claude-haiku-4-5",
                    "--colosseum", str(tmp_path), "--runs", str(tmp_path), "--dry-run"])
