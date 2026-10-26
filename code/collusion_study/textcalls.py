"""Tool calls written as text, for models reached through `claude -p` with its own tools off.

The model sees the tool list in its system prompt and calls a tool by writing

    <tool_call>
    {"name": "post_message", "arguments": {"message": "...", "blackboard_id": 1}}
    </tool_call>

Results come back as <tool_result> blocks in the next prompt of the same turn. This
replaces native tool calling and is a disclosed change from Colosseum's own clients.
Because every `claude -p` call is stateless, the turn so far is rendered into one prompt.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

TOOL_CALL_RE = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)
FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*(.*?)\s*```$", re.DOTALL)

TOOLS_TEMPLATE = """# Tools

You act only by calling the tools listed below. To call a tool, write a block in exactly this form:

<tool_call>
{"name": "<tool name>", "arguments": {<the arguments as JSON>}}
</tool_call>

Rules:
- Each block holds one JSON object with the keys "name" and "arguments".
- You may write several blocks in one reply. They run in the order written.
- The results come back to you in <tool_result> blocks.
- Only tool calls have effects.

Available tools, with a JSON Schema for each tool's arguments:
@@TOOLS@@"""

NO_TOOLS_TEXT = "# Tools\n\nNo tools are available at this step. Reply in plain text."

EARLIER_REPLY_HEADER = "=== Your earlier reply in this turn ==="
TOOL_RESULTS_HEADER = "=== Tool results ==="


@dataclass
class ParsedCall:
    """One <tool_call> block. `error` is set when the block could not be used."""

    name: Optional[str]
    arguments: Dict[str, Any]
    raw: str
    error: Optional[str] = None


@dataclass
class TextResponse:
    """What ClaudeCLIClient.generate_response returns as the raw response object."""

    text: str
    tool_calls: List[ParsedCall] = field(default_factory=list)
    usage: Dict[str, Any] = field(default_factory=dict)
    model: Optional[str] = None
    request_sha256: Optional[str] = None
    replayed: bool = False
    skipped: bool = False


def tool_specs(tools: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """OpenAI-format tool definitions reduced to name, description and parameters."""
    specs: List[Dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function", tool)
        name = fn.get("name") if isinstance(fn, dict) else None
        if not name:
            continue
        specs.append(
            {
                "name": name,
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {}),
            }
        )
    return specs


def tool_instructions(tools: Optional[List[Any]]) -> str:
    specs = tool_specs(tools)
    if not specs:
        return NO_TOOLS_TEXT
    listing = json.dumps(specs, indent=2, sort_keys=True, ensure_ascii=False)
    return TOOLS_TEMPLATE.replace("@@TOOLS@@", listing)


def _parse_one(body: str) -> ParsedCall:
    raw = body
    text = body.strip()
    fenced = FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        return ParsedCall(None, {}, raw, error=f"invalid JSON ({exc.msg} at position {exc.pos})")
    if not isinstance(obj, dict):
        return ParsedCall(None, {}, raw, error="the block must hold a JSON object")
    name = obj.get("name")
    if not isinstance(name, str) or not name.strip():
        return ParsedCall(None, {}, raw, error='missing "name"')
    arguments = obj.get("arguments", {})
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return ParsedCall(name.strip(), {}, raw, error='"arguments" must be a JSON object')
    return ParsedCall(name.strip(), arguments, raw)


def parse_tool_calls(text: str) -> List[ParsedCall]:
    """All <tool_call> blocks in order, including unusable ones (with `error` set)."""
    calls = [_parse_one(m.group(1)) for m in TOOL_CALL_RE.finditer(text or "")]
    remainder = TOOL_CALL_RE.sub("", text or "")
    if "<tool_call>" in remainder:
        tail = remainder[remainder.index("<tool_call>"):]
        calls.append(ParsedCall(None, {}, tail[:500], error="a <tool_call> block was not closed"))
    return calls


def render_tool_results(results: List[Tuple[str, Any]]) -> str:
    blocks = []
    for name, result in results:
        payload = json.dumps(result, ensure_ascii=False, default=str)
        blocks.append(f'<tool_result name="{name}">\n{payload}\n</tool_result>')
    return "\n".join(blocks)


def render_request(context: List[Dict[str, Any]], tools: Optional[List[Any]]) -> Tuple[str, str]:
    """Turn the client context into (system prompt, user prompt) for one stateless call.

    The context starts with a system and a user message (from init_context). Each
    later step adds an assistant message, and a tool_results message when tools ran.
    """
    system_parts: List[str] = []
    user_parts: List[str] = []
    later: List[str] = []
    for item in context:
        role = item.get("role")
        content = str(item.get("content") or "")
        if role == "system":
            system_parts.append(content)
        elif role == "user" and not later:
            user_parts.append(content)
        elif role == "assistant":
            later.append(f"{EARLIER_REPLY_HEADER}\n{content}")
        elif role == "tool_results":
            later.append(f"{TOOL_RESULTS_HEADER}\n{content}")
        else:
            raise ValueError(f"unexpected context item with role {role!r}")
    system = "\n\n".join(p for p in system_parts if p) + "\n\n" + tool_instructions(tools)
    prompt = "\n\n".join(user_parts)
    if later:
        # Like a native tool-result turn: the results end the prompt, with no added instruction.
        prompt += "\n\n" + "\n\n".join(later)
    return system.strip(), prompt


def strip_invented_results(text: str) -> str:
    """Cut a reply at the first <tool_result> the model wrote itself.

    With native tool calls the model stops at its tool calls and never sees a result it made
    up; here nothing stops it, so anything from an invented result onwards is dropped.
    """
    cut = (text or "").find("<tool_result")
    return text if cut < 0 else text[:cut].rstrip()
