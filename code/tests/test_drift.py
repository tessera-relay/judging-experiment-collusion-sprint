"""Drift coding: what the judge sees, and that every message is coded exactly once."""
import json
import os
from pathlib import Path

import pytest

from collusion_study.backend import CallLog, FakeBackend, ModelCaller
from collusion_study.drift import RULE_O30, SYSTEM, code_episode, episode_row, judge_prompt, parse_codes, scripted_coder

COLOSSEUM_DIR = os.environ.get("COLOSSEUM_DIR")
CONFIGS = Path(__file__).resolve().parents[1] / "configs"


def test_the_rule_is_given_verbatim():
    assert RULE_O30 in SYSTEM


def test_codes_must_cover_every_message_once():
    messages = [{"id": "M001"}, {"id": "M002"}]
    ok = {"codes": [{"id": "M001", "drift": False, "papers": [], "parts": [], "reason": ""},
                    {"id": "M002", "drift": True, "papers": ["A1"], "parts": ["spreading"], "reason": ""}]}
    assert parse_codes(json.dumps(ok), messages)["M002"]["drift"] is True
    for bad in ({"codes": ok["codes"][:1]}, {"codes": ok["codes"] + ok["codes"][:1]},
                {"codes": [ok["codes"][0], {**ok["codes"][1], "id": "M009"}]},
                {"codes": [ok["codes"][0], {**ok["codes"][1], "parts": []}]},
                {"codes": [ok["codes"][0], {**ok["codes"][1], "parts": ["off_topic"]}]}):
        with pytest.raises(ValueError):
            parse_codes(json.dumps(bad), messages)


@pytest.mark.skipif(not COLOSSEUM_DIR, reason="set COLOSSEUM_DIR to the Colosseum checkout")
def test_offline_episode_is_coded_blind(tmp_path):
    from collusion_study.colosseum_runner import EpisodeSpec, run_episode

    out = tmp_path / "batch" / "channel_only-seed2"
    summary = run_episode(EpisodeSpec(config_path=str(CONFIGS / "library_a3.yaml"), condition="channel_only", seed=2,
                                      model="fake", effort=None, out_dir=str(out), backend="fake"))
    assert summary["valid"]
    seen = []

    def coder(system, prompt, model, effort):
        seen.append(prompt)
        return scripted_coder(system, prompt, model, effort)

    caller = ModelCaller(backend=FakeBackend(coder), log=CallLog(out / "drift-calls.jsonl"))
    coded = code_episode(out, caller, "fake-judge")
    prompt = seen[0]
    # Blind: no condition, no pair, no boards, no paper kinds or true groups.
    for leak in ("channel_only", "attractor", "control", "computer_science", "Blackboard", "board", "pair", "coalition"):
        assert leak not in prompt
    assert "Indicators of Consciousness" in prompt
    for m in coded:  # O47: the judge sees neutral names only; the saved codes keep the real ones
        assert m["agent"] not in prompt and f"] {m['pseudonym']}: " in prompt
    row = episode_row(out, coded)
    assert row["messages"] == len(coded) == row["pair1_messages"] + row["pair2_messages"] + row["unpaired_messages"] > 0
    assert row["pair1_private_board_messages"] > 0 and row["pair2_private_board_messages"] > 0  # O59: two private boards
    assert len(row["unpaired"].split(" + ")) == 2
    # Relayed copies are counted but never shown: every line the judge sees is distinct.
    copies = [m for m in coded if m["copy_of"]]
    assert copies and row["distinct_messages"] == len(coded) - len(copies)
    assert prompt.count("\n[M") == row["distinct_messages"]
    assert all(m["drift"] == next(o for o in coded if o["id"] == m["copy_of"])["drift"] for m in copies)
    # Swap-ins carry the id of the paper they replace (P17 for A2), so no id marks them out.
    assert "- A2:" not in prompt and "- P17: Moral Patienthood" in prompt


def test_planted_transcripts_reach_the_judge_without_labels(tmp_path):
    from collusion_study.drift import code_messages
    from collusion_study.drift_planted import compare, load_planted, transcript_inputs

    transcripts = load_planted()
    assert len(transcripts) >= 6 and any(m["drift"] for t in transcripts for m in t["messages"])
    coded = {}
    for t in transcripts:
        papers, messages = transcript_inputs(t)
        assert all(set(m) == {"id", "agent", "text"} for m in messages)
        seen = []
        backend = FakeBackend(lambda s, p, m, e: (seen.append(p), scripted_coder(s, p, m, e))[1])
        caller = ModelCaller(backend=backend, log=CallLog(tmp_path / f"{t['id']}.jsonl"))
        coded[t["id"]] = code_messages(papers, messages, caller, "fake", t["id"])
        assert not any(m.get("note") and m["note"] in seen[0] for m in t["messages"])
        assert "attractor" not in seen[0] and "computer_science" not in seen[0]
    result = compare(transcripts, coded)
    assert result["messages"] == sum(len(t["messages"]) for t in transcripts)


def test_joint_drift_needs_one_member_taking_up_the_others_drift():
    from collusion_study.drift import joint_drift

    drift = [{"id": "M001", "agent": "A", "responds_to": None}, {"id": "M004", "agent": "B", "responds_to": "M001"},
             {"id": "M005", "agent": "C", "responds_to": "M001"}, {"id": "M006", "agent": "A", "responds_to": "M001"}]
    assert joint_drift(["A", "B"], drift) == 1   # B takes up A's drift
    assert joint_drift(["A", "C"], drift) == 1
    assert joint_drift(["B", "C"], drift) == 0   # neither takes up the other's
