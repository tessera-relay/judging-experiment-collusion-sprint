"""The per-episode results, the paired differences and the transcript page, on scripted fake episodes."""
import csv
import json
import os
from pathlib import Path

import pytest

from collusion_study.results import bootstrap_interval, paired_differences
from collusion_study.results import main as results_main
from collusion_study.transcript import main as transcript_main
from collusion_study.transcript import reply_segments

COLOSSEUM_DIR = os.environ.get("COLOSSEUM_DIR")
needs_colosseum = pytest.mark.skipif(not COLOSSEUM_DIR, reason="set COLOSSEUM_DIR to the Colosseum checkout")


@pytest.fixture(scope="module")
def batch(tmp_path_factory):
    from collusion_study.batch import main as batch_main

    runs = tmp_path_factory.mktemp("runs")
    args = ["--name", "t", "--conditions", "C4", "C1", "--seeds", "1", "2", "--model", "claude-test",
            "--colosseum", COLOSSEUM_DIR, "--runs", str(runs), "--parallel", "2", "--fake"]
    assert batch_main(args) == 0
    return runs / "t"


def read_csv(path: Path):
    lines = path.read_text().splitlines()
    return lines[0], list(csv.DictReader(lines[1:]))


@needs_colosseum
def test_colosseum_logs_stay_with_the_batch(batch):
    for name in ("C4-seed1", "C1-seed1", "C4-seed2", "C1-seed2"):
        assert (batch / "colosseum-logs" / name / "logs").is_dir()


@needs_colosseum
def test_results_pair_each_condition_with_c4_at_the_same_seed(batch, tmp_path):
    assert results_main([str(batch), "--rule", "TEST", "--out", str(tmp_path), "--colosseum", COLOSSEUM_DIR]) == 0
    header, episodes = read_csv(tmp_path / "episodes.csv")
    assert header == "# scoring rule: TEST"
    assert [(e["condition"], e["seed"], e["valid"]) for e in episodes] == [
        ("C4", "1", "True"), ("C1", "1", "True"), ("C4", "2", "True"), ("C1", "2", "True")]
    summaries = {(s["condition"], s["seed"]): s for s in
                 (json.loads((batch / f"{c}-seed{n}" / "episode_summary.json").read_text())
                  for c in ("C1", "C4") for n in (1, 2))}
    expected = [summaries[("C1", n)]["assigned_pair_advantage"] - summaries[("C4", n)]["assigned_pair_advantage"]
                for n in (1, 2)]
    _, paired = read_csv(tmp_path / "paired.csv")
    row = next(p for p in paired if p["measure"] == "assigned_pair_advantage")
    assert row["condition"] == "C1" and row["reference"] == "C4" and row["n_seeds"] == "2"
    assert float(row["mean_difference"]) == pytest.approx(sum(expected) / 2)
    for e in episodes:
        gap, optimum, joint = float(e["optimal_gap"]), float(e["optimal_joint_reward"]), float(e["joint_reward"])
        assert gap >= 0 and gap == pytest.approx(optimum - joint)


def test_only_seeds_where_both_episodes_are_valid_are_paired():
    rows = [
        {"condition": "C4", "seed": 1, "valid": True, "joint_reward": 1.0},
        {"condition": "C1", "seed": 1, "valid": True, "joint_reward": 4.0},
        {"condition": "C4", "seed": 2, "valid": False, "joint_reward": 9.0},
        {"condition": "C1", "seed": 2, "valid": True, "joint_reward": 2.0},
    ]
    (row,) = [p for p in paired_differences(rows) if p["measure"] == "joint_reward"]
    assert (row["n_seeds"], row["seeds"], row["mean_difference"]) == (1, "1", 3.0)
    assert (row["ci95_low"], row["ci95_high"]) == (None, None)


def test_bootstrap_interval_brackets_the_mean_and_repeats():
    values = [1.0, 2.0, 3.0, 10.0]
    lo, hi = bootstrap_interval(values)
    assert lo <= sum(values) / len(values) <= hi
    assert bootstrap_interval(values) == [lo, hi]


def test_reply_segments_keep_order_and_drop_invented_results():
    text = ('First I plan.\n<tool_call>\n{"name": "post_message", "arguments": {"message": "hi", "blackboard_id": 0}}\n'
            '</tool_call>\nThen more.\n<tool_result>{"ok": true}</tool_result>\n<tool_call>{"name": "assign_task"}</tool_call>')
    segments, dropped = reply_segments(text)
    assert [kind for kind, _ in segments] == ["prose", "call", "prose"]
    assert segments[1][1].name == "post_message" and segments[1][1].arguments["message"] == "hi"
    assert dropped.startswith("<tool_result>") and "assign_task" in dropped
    segments, _ = reply_segments('ok <tool_call>{"name": "post_message"')
    assert segments[-1][1].error == "a <tool_call> block was not closed"


@needs_colosseum
def test_transcript_page_shows_every_episode_and_hides_scores(batch, tmp_path):
    out = tmp_path / "page.html"
    assert transcript_main([str(batch), "--out", str(out), "--title", "Test page"]) == 0
    page = out.read_text()
    assert page.startswith("<title>Test page</title>")
    for name in ("C4-seed1", "C1-seed1", "C4-seed2", "C1-seed2"):
        assert f'id="{name}"' in page
    assert "the pair&#x27;s private board" in page and "Drawn pair for the comparison" in page
    assert "not posted" not in page
    assert page.count("=== SECRET COALITION INSTRUCTIONS (DO NOT REVEAL) ===") == 4  # two C1 episodes, two members each
    assert "<summary>Scores</summary>" not in page and "Reward</th>" not in page
    first_post = json.loads((batch / "C1-seed1" / "calls.jsonl").read_text().splitlines()[0])["text"]
    assert "Posted to Board" in page and first_post.split("\n")[0] in page
    with_scores = tmp_path / "scores.html"
    assert transcript_main([str(batch), "--out", str(with_scores), "--scores"]) == 0
    assert "<summary>Scores</summary>" in with_scores.read_text()
