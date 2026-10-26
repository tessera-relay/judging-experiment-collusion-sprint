# Judging experiment: code and data

Code and data for a study run at the Apart Research AI Collusion Research Sprint (23–25 October 2026). Three Claude Sonnet 4.5 judges each supervise two simulated workers and share one token budget; the arms vary the private channel, who pays for the work, the work's quality, scarcity and the wording of the pause tool. The report describes the design and results.

## Contents

| Path | What it holds |
|---|---|
| `code/collusion_study/judges/` | The Judging environment (`engine.py`), the 18 tasks with good and flawed versions (`tasks.yaml`), the scorer's coding (`coding.py`), continuation of turn-capped runs (`extend.py`) |
| `code/collusion_study/` | Shared code: the `claude -p` backend, batch runner, analysis and permutation tests (`analysis.py`), pseudonyms for scorers, leak check. Also code for the sprint's other experiments (Jira control, library), not part of this release's study |
| `code/tests/` | Unit tests (scripted fake agents; no model calls) |
| `runs/<batch>/<arm>-seed<N>/events.jsonl` | Full event log of each episode: every output, score, pause, removal, board message and judge reply text |
| `runs/<batch>/<arm>-seed<N>/summary.json` | Per-episode counts and settings |
| `analysis/judges-coding-v2*/` | The GPT scorer's coding per episode and the combined CSV used by the tests |
| `analysis/results/`, `followup/`, `neutral/` | Pre-specified tests J1–J4, J6–J7, J8 |
| `analysis/coverage/`, `estimates/` | Evaluation coverage and dead time; judges' turns-left estimates |
| `analysis/report/` | Report tables (T1–T6), figures, and the per-episode table |
| `analysis/quotes/` | Compiled quote files and the scripts that build them; `judge-prompt-example.md` shows a full judge prompt |
| `protocol/judges-plan.md` | The run plan |

Episodes: C4 and channel_only seeds 1–32 (`judges-main`), separate_budget 1–32, all_good 1–32, neutral_pause 1–32, no_scarcity 1–16, instructed 1–4.

Not included: raw model request/response logs (the prompts can be regenerated from the code), the pilot, and third-party code.

## Reproducing

- Judge model: `claude-sonnet-4-5` through the Claude Code CLI (`claude -p`, version 2.1.286), subscription login, temperature 1, max_tokens 1500, thinking off.
- Episodes: `python -m collusion_study.batch --task judges ...` from `code/` (see the docstrings of `batch.py` and `judges/engine.py`; budget 12,500, `--max-calls 2000`).
- Scorer: gpt-5.6-sol via the Codex CLI (`judges/coding.py`), rules O40–O45.
- Analysis: the scripts in `analysis/*/` run read-only on `runs/` and the coding CSVs.
- `collusion_study` imports Colosseum (github.com/umass-ai-safety/colosseum, tag paper-v0.1.1, commit b077fe7) for the sprint's Jira control only. Colosseum and LLMCartel have no licence file and are not included; obtain them separately.

## Prior work (disclosure)

- Before the sprint opened (23 Oct 2026, 12:00 UTC), and tested only with scripted agents and no model calls: the Jira-runner core of `collusion_study` (`backend.py`, `textcalls.py`, `colosseum_client.py`, `colosseum_runner.py`, `smoke_test.py` and their tests), written 22–23 Oct. Earlier preparation on 17 Oct (`preflight.py`, a draft protocol) is not included here.
- Written during the sprint: the whole Judging experiment (`collusion_study/judges/`, `analysis.py`, `pseudonyms.py`, `leak_check.py`, the judges task in `batch.py`), all runs, scoring and analysis.

## Authorship

Research question, design decisions and scoring rules: Artemiy. Code, runs and analysis: Claude (Anthropic), working under Artemiy's direction.
