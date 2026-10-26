"""Neutral agent names for every GPT coder and scorer (decision O47).

The agents' first names are replaced by "Agent A" to "Agent F" before a transcript reaches a judge, both as message
authors and wherever a name appears inside a message. The letters are shuffled per episode with a seed of their own,
so the pair is not always A and B, and the same episode always gets the same letters. Answers that name agents are
mapped back to the real names before anything is saved, and the mapping is kept beside the codes.
"""
from __future__ import annotations

import random
import re
import string
from typing import Any, Dict, Iterable, List

# First names the agents and judges never get (review T4): model and company names would be masked as a family, or
# would tell the coder which family is meant, and the mask words themselves would collide with the pseudonyms.
RESERVED_NAMES = frozenset({"Claude", "Anthropic", "Sonnet", "Opus", "Haiku", "Openai", "Chatgpt", "Gemini",
                            "Agent", "Judge", "Family"})


def pseudonyms(names: Iterable[str], label: str, prefix: str = "Agent") -> Dict[str, str]:
    real = sorted({n for n in names if n})
    letters = list(string.ascii_uppercase[:len(real)])
    random.Random(f"pseudonyms-{prefix}-{label}" if prefix != "Agent" else f"pseudonyms-{label}").shuffle(letters)
    return {name: f"{prefix} {letter}" for name, letter in zip(real, letters)}


def _pattern(mapping: Dict[str, str]) -> "re.Pattern[str]":
    names = sorted(mapping, key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b")


def hide(text: str, mapping: Dict[str, str]) -> str:
    return _pattern(mapping).sub(lambda m: mapping[m.group(1)], text) if mapping else text


def hide_messages(messages: List[Dict[str, Any]], mapping: Dict[str, str]) -> List[Dict[str, Any]]:
    return [{**m, "agent": mapping.get(m["agent"], m["agent"]), "text": hide(m["text"], mapping)} for m in messages]


def reveal(name: str, mapping: Dict[str, str], prefix: str = "Agent") -> str:
    back = {v: k for k, v in mapping.items()}
    key = " ".join(name.split())
    return back.get(key, back.get(f"{prefix} {key}", name))  # "Agent C" or a bare "C"; anything else is kept as given


def reveal_text(text: str, mapping: Dict[str, str], prefix: str = "Agent") -> str:
    back = {v: k for k, v in mapping.items()}
    return re.sub(rf"\b{re.escape(prefix)} [A-Z]\b", lambda m: back.get(m.group(0), m.group(0)), text)
