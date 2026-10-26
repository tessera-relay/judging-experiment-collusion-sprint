"""Resuming the GPT coders after a usage limit.

Each coder writes one output file per episode. Running the same command again reuses a file whose settings match
(rule, coder model, backend, code hash and anything else passed in `same`), so episodes coded before the limit are
not sent again and keep the codes they got. A usage limit stops the loop: the remaining episodes are listed as not
attempted rather than each failing in turn.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


def reuse(path: Path, same: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The saved output at path, if it exists and every key in `same` matches; else None."""
    if not path.exists():
        return None
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return saved if all(saved.get(k) == v for k, v in same.items()) else None
