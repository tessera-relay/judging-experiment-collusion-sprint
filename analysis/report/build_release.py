"""Build the shareable release folder for the Judging experiment (Artemiy's OK, 26 Oct 00:17 UTC). Copies only; nothing is published.
Kept out: third_party/ (no licence), private/, logs/, probes, the library experiment, raw CLI call logs (calls.jsonl, *-coding-calls.jsonl),
status.json, the pilot. summary.json is copied with its absolute out_dir replaced by the path inside the release."""
import json, shutil, re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
REL = ROOT / "release" / "judging-experiment"
if REL.exists():
    raise SystemExit(f"{REL} exists; move it aside first")
BATCHES = {"judges-main": ("C4", "channel_only"), "judges-separate-budget": ("separate_budget",), "judges-instructed": ("instructed",),
           "judges-all-good": ("all_good",), "judges-no-scarcity": ("no_scarcity",), "judges-neutral-pause": ("neutral_pause",)}
skip = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".DS_Store")
for d in ("collusion_study", "tests", "configs"):
    shutil.copytree(ROOT / "code" / d, REL / "code" / d, ignore=skip)
n = 0
for batch, arms in BATCHES.items():
    for arm in arms:
        for d in sorted((ROOT / "runs" / batch).glob(f"{arm}-seed*")):
            if not d.name.split("-seed")[1].isdigit():
                continue
            out = REL / "runs" / batch / d.name; out.mkdir(parents=True)
            shutil.copy2(d / "events.jsonl", out / "events.jsonl")
            s = json.load(open(d / "summary.json"))
            s["spec"]["out_dir"] = f"runs/{batch}/{d.name}"
            (out / "summary.json").write_text(json.dumps(s, indent=1))
            n += 1
for d in ("judges-coding-v2", "judges-coding-v2-followup", "judges-coding-v2-neutral", "results", "followup", "neutral", "coverage", "estimates", "report"):
    shutil.copytree(ROOT / "analysis" / d, REL / "analysis" / d, ignore=skip)
shutil.copytree(ROOT / "analysis" / "quotes", REL / "analysis" / "quotes", ignore=shutil.ignore_patterns("library-*", "__pycache__", ".DS_Store"))
(REL / "protocol").mkdir(); shutil.copy2(ROOT / "protocol" / "judges-plan.md", REL / "protocol" / "judges-plan.md")
shutil.copy2(Path(__file__).parent / "RELEASE-README.md", REL / "README.md")
pat = re.compile(r"/Users/|tesserarelay|@gmail|kamil", re.I)
hits = [str(p.relative_to(REL)) for p in REL.rglob("*") if p.is_file() and pat.search(p.read_text(errors="ignore"))]
size = sum(p.stat().st_size for p in REL.rglob("*") if p.is_file())
print(f"episodes {n}; files {sum(1 for p in REL.rglob('*') if p.is_file())}; {size / 1e6:.1f} MB; scan hits: {hits}")
