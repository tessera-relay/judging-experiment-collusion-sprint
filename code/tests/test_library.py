"""The paper-library task: catalogue variants, the answer-key score, and a full offline episode."""
import json
import os
from pathlib import Path

import pytest

from collusion_study.library.env import build_catalogue, filing_agreement, load_design

COLOSSEUM_DIR = os.environ.get("COLOSSEUM_DIR")
CONFIGS = Path(__file__).resolve().parents[1] / "configs"


def test_variants_keep_the_catalogue_and_group_sizes():
    design = load_design()
    base = build_catalogue(design, 0, False)
    for attractors, control in [(1, False), (3, False), (0, True)]:
        cat = build_catalogue(design, attractors, control)
        assert [p["group"] for p in cat] == [p["group"] for p in base]
        assert sum(p["kind"] == "attractor" for p in cat) == attractors
        assert sum(p["kind"] == "control" for p in cat) == int(control)
    assert not any(p["kind"] != "regular" for p in base)


def test_filing_agreement_ignores_category_names_and_case():
    truth = {"a": "x", "b": "x", "c": "y"}
    assert filing_agreement({"a": "Stars", "b": "stars ", "c": "Cells"}, truth) == 1.0
    assert filing_agreement({"a": "One", "b": "One", "c": "One"}, truth) == pytest.approx(1 / 3)
    assert filing_agreement({}, truth) is None


@pytest.mark.skipif(not COLOSSEUM_DIR, reason="set COLOSSEUM_DIR to the Colosseum checkout")
def test_offline_library_episode_files_every_paper(tmp_path):
    from collusion_study.colosseum_runner import EpisodeSpec, run_episode

    out = tmp_path / "ep"
    summary = run_episode(EpisodeSpec(config_path=str(CONFIGS / "library_a1.yaml"), condition="channel_only", seed=3,
                                      model="fake", effort=None, out_dir=str(out), backend="fake"))
    assert summary["valid"], summary["invalid_reasons"]
    final = json.loads(next(out.glob("runs/*/*/*/final_summary.json")).read_text())
    assert final["papers_filed"] == 24 and final["missing_filings"] == []
    assert [(p["id"], p["design_id"]) for p in final["catalogue"] if p["kind"] == "attractor"] == [("P20", "A1")]
    prompt = json.loads((out / "calls.jsonl").read_text().splitlines()[0])["prompt"]
    assert "Indicators of Consciousness" in prompt and "computer_science" not in prompt and "attractor" not in prompt
    assert len(summary["assigned_pair"]) == 2


def _env(tmp_path, seed, attractors=0, control=False):
    """A library environment outside Colosseum's loop, enough to read its catalogue order and shares."""
    import yaml
    from collusion_study.library.env import LibraryEnvironment

    class Protocol:
        environment = None

    cfg = yaml.safe_load((CONFIGS / "library_a0.yaml").read_text())
    cfg["simulation"]["seed"] = seed
    cfg["environment"].update(attractors=attractors, control_paper=control)
    return LibraryEnvironment(Protocol(), cfg, None)


@pytest.mark.skipif(not COLOSSEUM_DIR, reason="set COLOSSEUM_DIR to the Colosseum checkout")
def test_catalogue_order_is_seeded_and_swap_ins_keep_their_slot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the prompt logger writes logs/ into the working directory
    a = _env(tmp_path, 1)
    b = _env(tmp_path, 2)
    assert a.display_order != b.display_order and sorted(a.display_order) == sorted(a.paper_ids)
    swapped = _env(tmp_path, 1, attractors=3)
    # Swap-ins keep the replaced paper's id (A2 shows as P17), so ids, order and shares match across variants.
    assert swapped.display_order == a.display_order and swapped.share_of == a.share_of
    assert {p["id"]: p["design_id"] for p in swapped.catalogue if p["kind"] == "attractor"} == {"P20": "A1", "P17": "A2", "P18": "A3"}
    context = a.build_agent_context(a.agent_names[0], "planning", 1)
    assert [p["id"] for p in context["catalogue"]] == a.display_order


@pytest.mark.skipif(not COLOSSEUM_DIR, reason="set COLOSSEUM_DIR to the Colosseum checkout")
def test_instructed_line_reaches_the_pair_only(tmp_path):
    from collusion_study.colosseum_runner import PRIVATE_LINE_HEADER, EpisodeSpec, run_episode

    out = tmp_path / "ep"
    summary = run_episode(EpisodeSpec(config_path=str(CONFIGS / "library_a3.yaml"), condition="instructed", seed=2,
                                      model="fake", effort=None, out_dir=str(out), backend="fake"))
    assert summary["valid"], summary["invalid_reasons"]
    pair = {a for p in summary["pairs"] for a in p}  # O59: both pairs get the line
    assert len(summary["pairs"]) == 2 and len(pair) == 4
    calls = [json.loads(line) for line in (out / "calls.jsonl").read_text().splitlines()]
    for call in calls:
        assert (PRIVATE_LINE_HEADER in call["prompt"]) == (call["meta_agent"] in pair)
        assert summary["private_line"] not in call["system"]
    assert summary["private_line_prompts"] > 0


def test_instructed_line_must_be_approved_for_a_live_run(tmp_path):
    from collusion_study.colosseum_runner import EpisodeSpec, run_episode

    import yaml

    cfg = yaml.safe_load((CONFIGS / "library_a3.yaml").read_text())
    cfg["experiment"]["collusion"]["private_line_approved"] = False
    unapproved = tmp_path / "unapproved.yaml"
    unapproved.write_text(yaml.safe_dump(cfg))
    out = tmp_path / "ep"
    with pytest.raises(ValueError, match="approved"):
        run_episode(EpisodeSpec(config_path=str(unapproved), condition="instructed", seed=1,
                                model="claude-sonnet-4-5", effort=None, out_dir=str(out)))
    assert not out.exists()


def test_adjusted_agreement_is_zero_for_one_category_per_paper():
    from collusion_study.library.env import adjusted_filing_agreement

    truth = {f"p{i}": f"g{i // 4}" for i in range(24)}
    assert filing_agreement({p: p for p in truth}, truth) == pytest.approx(240 / 276)
    assert adjusted_filing_agreement({p: p for p in truth}, truth) == pytest.approx(0.0)
    assert adjusted_filing_agreement(dict(truth), truth) == pytest.approx(1.0)


@pytest.mark.skipif(not COLOSSEUM_DIR, reason="set COLOSSEUM_DIR to the Colosseum checkout")
@pytest.mark.parametrize("attractors,control", [(3, False), (1, False), (0, True)])
def test_targets_are_filed_by_the_pairs_only(tmp_path, monkeypatch, attractors, control):
    monkeypatch.chdir(tmp_path)
    for seed in range(1, 7):
        env = _env(tmp_path, seed, attractors=attractors, control=control)
        names = sorted(env.agent_names)
        pairs = [names[0:2], names[2:4]]
        before = {n: len(p) for n, p in env.share_of.items()}
        env.assign_targets(pairs)
        assert {n: len(p) for n, p in env.share_of.items()} == before
        targets = [p["id"] for p in env.catalogue if p["kind"] in ("attractor", "control")]
        owner = {pid: a for a, ps in env.share_of.items() for pid in ps}
        held = [sum(owner[t] in p for t in targets) for p in pairs]
        assert all(owner[t] in names[:4] for t in targets)                  # never an unpaired agent
        assert sorted(held) == ([1, 2] if attractors == 3 else [0, 1])       # spread over the pairs
        assert sorted(pid for ps in env.share_of.values() for pid in ps) == sorted(env.paper_ids)
