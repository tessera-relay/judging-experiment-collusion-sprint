"""The episode-level library collusion judge (O38): blind transcript, one call per question, drafts refused live."""
import json
import os
from pathlib import Path

import pytest

from collusion_study.backend import CallLog, FakeBackend, ModelCaller

COLOSSEUM_DIR = os.environ.get("COLOSSEUM_DIR")
CONFIGS = Path(__file__).resolve().parents[1] / "configs"


def test_a_live_run_refuses_draft_questions(tmp_path, monkeypatch):
    from collusion_study import library_judge

    assert library_judge.QUESTIONS_APPROVED  # O55
    monkeypatch.setattr(library_judge, "QUESTIONS_APPROVED", False)
    with pytest.raises(SystemExit):
        library_judge.main([str(tmp_path), "--judge-model", "gpt", "--rule", "O38", "--out", str(tmp_path / "o")])


@pytest.mark.skipif(not COLOSSEUM_DIR, reason="set COLOSSEUM_DIR to the Colosseum checkout")
def test_judge_reads_the_whole_conversation_blind(tmp_path):
    from collusion_study.colosseum_runner import EpisodeSpec, run_episode
    from collusion_study.library_judge import judge_episode, row

    out = tmp_path / "batch" / "channel_only-seed2"
    summary = run_episode(EpisodeSpec(config_path=str(CONFIGS / "library_a3.yaml"), condition="channel_only", seed=2,
                                      model="fake", effort=None, out_dir=str(out), backend="fake"))
    pair = summary["assigned_pair"]
    from collusion_study.library_judge import episode_pseudonyms

    mapping = episode_pseudonyms(out)
    assert len(mapping) == 6 and set(mapping.values()) == {f"Agent {c}" for c in "ABCDEF"}
    prompts = []

    def answer(system, prompt, model, effort):
        prompts.append(prompt)
        return json.dumps({"rating": 3, "rationale": f"{mapping[pair[0]]} led it", "agents": [mapping[pair[0]], "Nobody"]})

    caller = ModelCaller(backend=FakeBackend(answer), log=CallLog(out / "j.jsonl"))
    answers = judge_episode(out, ["collusion", "joint_action", "persuasion", "exclusivity"], caller, "fake")
    assert len(prompts) == 4
    for p in prompts[:3]:
        for leak in ("channel_only", "attractor", "Board", "pair", "computer_science"):
            assert leak not in p
        for name in mapping:  # O47: no real agent name reaches the judge
            assert name not in p
    assert "Board 2: members" in prompts[3]  # only the exclusivity question sees boards
    assert answers["collusion"]["agents"] == [pair[0], "Nobody"] and answers["collusion"]["rationale"] == f"{pair[0]} led it"
    r = row(out, answers)
    assert r["collusion_rating"] == 3 and r["collusion_pair1_members_named"] == 1 and r["collusion_pair2_members_named"] == 0
    assert r["collusion_unpaired_named"] == 1  # "Nobody"


def test_an_out_of_range_rating_is_never_scored(tmp_path):
    from collusion_study.backend import BackendError
    from collusion_study.library_judge import QUESTIONS, ask

    caller = ModelCaller(backend=FakeBackend(lambda s, p, m, e: json.dumps({"rating": 7, "rationale": "", "agents": []})),
                         log=CallLog(tmp_path / "j.jsonl"))
    with pytest.raises(BackendError):
        ask(QUESTIONS["collusion"], "text", caller, "fake", "ep")
