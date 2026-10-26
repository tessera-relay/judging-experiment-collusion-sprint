"""Search a batch for e-mail addresses before anything from it is shared (decision O37).

    python -m collusion_study.leak_check ../runs/library-a3 ../runs/library-a1

The CLI puts the account's e-mail address into every agent's context. This lists every e-mail address that
appears in any text file of the batch (call logs, boards, summaries, coder outputs, the logs Colosseum writes), by
file and episode; binary files are skipped (review T6).
It prints counts and file names only, never the addresses themselves, and exits 1 if any were found.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def scan(batch: Path) -> Dict[str, int]:
    hits: Dict[str, int] = {}
    for path in sorted(batch.rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data[:8192]:  # binary
            continue
        n = len(EMAIL.findall(data.decode("utf-8", errors="replace")))
        if n:
            hits[str(path.relative_to(batch))] = n
    return hits


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("batch_dirs", nargs="+")
    args = parser.parse_args(argv)
    report = {b: scan(Path(b)) for b in args.batch_dirs}
    print(json.dumps({"files_with_addresses": report}, indent=2))
    return 1 if any(report.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
