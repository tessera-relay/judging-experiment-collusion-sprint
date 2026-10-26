"""The drift judge on planted transcripts with known labels (decision O36).

    python -m collusion_study.drift_planted --labels-only --out ../analysis/drift-planted
    python -m collusion_study.drift_planted --backend codex --judge-model gpt-5.6-sol --rule O30 --out ../analysis/drift-planted

--labels-only writes labels.md for Artemiy to check, and makes no call. Otherwise each transcript is coded in one
judge call, through the same code path as an episode (drift.code_messages), and the judge's codes are compared
with the planted labels: agreement, misses, false alarms, and the borderline messages listed one by one.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .backend import CODE_SHA256, CallLog, ClaudeCLIBackend, FakeBackend, ModelCaller
from .drift import DRIFT_SCHEMA, code_messages, scripted_coder
from .library.env import build_catalogue, load_design

PLANTED_FILE = Path(__file__).with_name("library") / "planted.yaml"


def load_planted(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    return yaml.safe_load(Path(path or PLANTED_FILE).read_text(encoding="utf-8"))["transcripts"]


def transcript_inputs(t: Dict[str, Any]) -> tuple:
    """The catalogue and messages the judge sees: no labels, no notes."""
    design = load_design()
    papers = [{"id": p["id"], "title": p["title"], "abstract": " ".join(str(p["abstract"]).split())}
              for p in build_catalogue(design, int(t["attractors"]), bool(t["control_paper"]))]
    messages = [{"id": f"M{i:03d}", "agent": m["agent"], "text": m["text"]} for i, m in enumerate(t["messages"], start=1)]
    return papers, messages


def labels_markdown(transcripts: List[Dict[str, Any]]) -> str:
    lines = ["# Planted transcripts: labels to check", "",
             "Each message is labelled with what Claude thinks your rule (O30) says. Please mark any label you disagree with.", ""]
    for t in transcripts:
        lines += [f"## {t['id']}: {t['title']}", ""]
        for i, m in enumerate(t["messages"], start=1):
            label = "**DRIFT**" if m["drift"] else "not drift"
            note = f" _({m['note']})_" if m.get("note") else ""
            lines.append(f"{i}. {label}{note}. {m['agent']}: \"{m['text']}\"")
        lines.append("")
    return "\n".join(lines)


def compare(transcripts: List[Dict[str, Any]], coded: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    rows = []
    for t in transcripts:
        for i, (m, c) in enumerate(zip(t["messages"], coded[t["id"]]), start=1):
            rows.append({"transcript": t["id"], "message": i, "planted": m["drift"], "judge": c["drift"],
                         "borderline": bool(m.get("note")), "judge_reason": c["reason"], "text": m["text"]})
    hit = sum(r["planted"] and r["judge"] for r in rows)
    return {
        "messages": len(rows),
        "agreement": sum(r["planted"] == r["judge"] for r in rows) / len(rows),
        "drift_planted": sum(r["planted"] for r in rows),
        "drift_found": hit,
        "misses": [r for r in rows if r["planted"] and not r["judge"]],
        "false_alarms": [r for r in rows if r["judge"] and not r["planted"]],
        "borderline": [r for r in rows if r["borderline"]],
        "rows": rows,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--labels-only", action="store_true")
    parser.add_argument("--backend", choices=["codex", "claude", "fake"], default="codex")
    parser.add_argument("--judge-model")
    parser.add_argument("--rule")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    transcripts = load_planted()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "labels.md").write_text(labels_markdown(transcripts), encoding="utf-8")
    if args.labels_only:
        print(json.dumps({"labels": str(out / "labels.md")}))
        return 0
    if not (args.judge_model and args.rule):
        parser.error("--judge-model and --rule are needed unless --labels-only")
    if args.backend == "fake":
        backend = FakeBackend(scripted_coder)
    elif args.backend == "claude":
        backend = ClaudeCLIBackend()
    else:
        from .judge import CodexCLIBackend

        backend = CodexCLIBackend(schema=DRIFT_SCHEMA)
    caller = ModelCaller(backend=backend, log=CallLog(out / "drift-planted-calls.jsonl"))
    coded = {}
    for t in transcripts:
        papers, messages = transcript_inputs(t)
        coded[t["id"]] = code_messages(papers, messages, caller, args.judge_model, f"planted-{t['id']}")
    result = compare(transcripts, coded)
    result.update(rule=args.rule, judge_model=args.judge_model, backend=args.backend, code_sha256=CODE_SHA256)
    (out / "drift-planted.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("messages", "agreement", "drift_planted", "drift_found")}
                     | {"misses": len(result["misses"]), "false_alarms": len(result["false_alarms"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
