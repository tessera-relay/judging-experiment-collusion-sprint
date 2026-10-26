"""The library's one environment tool, file_papers, registered under the environment's class name."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set


class LibraryTools:
    """Tools for the paper library (execution phase only, as Jira's assign_task)."""

    def __init__(self, blackboard_manager):
        self.blackboard_manager = blackboard_manager

    def get_tool_names(self) -> Set[str]:
        return {"file_papers"}

    def get_tools(self, phase: str) -> List[Dict[str, Any]]:
        if phase != "execution":
            return []
        return [
            {
                "type": "function",
                "function": {
                    "name": "file_papers",
                    "description": "File every paper in your share under a category of the agreed scheme. Call once.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "assignments": {
                                "type": "object",
                                "description": "Maps each of your paper ids to a category name, e.g. {\"P03\": \"Astronomy\"}.",
                                "additionalProperties": {"type": "string"},
                            }
                        },
                        "required": ["assignments"],
                    },
                },
            }
        ]

    def handle_tool_call(
        self,
        tool_name: str,
        agent_name: str,
        arguments: Dict[str, Any],
        phase: Optional[str] = None,
        iteration: Optional[int] = None,
        env_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if tool_name != "file_papers":
            return {"error": f"Library environment does not support tool: {tool_name}"}
        if not env_state:
            return {"status": "failed", "reason": "Environment state not provided"}
        if agent_name in (env_state.get("filed") or {}):
            return {"status": "failed", "reason": f"{agent_name} has already filed"}
        share = list((env_state.get("share_of") or {}).get(agent_name) or [])
        assignments = arguments.get("assignments")
        if not isinstance(assignments, dict):
            return {"status": "retry", "reason": "assignments must map each of your paper ids to a category name"}
        filing = {str(k).strip(): str(v).strip() for k, v in assignments.items()}
        unknown = sorted(set(filing) - set(share))
        missing = sorted(set(share) - set(filing))
        empty = sorted(pid for pid, cat in filing.items() if not cat)
        if unknown or missing or empty:
            return {
                "status": "retry",
                "reason": "File exactly your own papers, each with a non-empty category.",
                "your_papers": share,
                "not_yours": unknown,
                "missing": missing,
                "empty_category": empty,
            }
        result = {"agent": agent_name, "filed": filing, "state_updates": {"filed": {agent_name: filing}}}
        execution_result = {"status": "success", "result": result}
        if self.blackboard_manager:
            self.blackboard_manager.log_action_to_blackboards(
                agent_name, {"action": "file_papers", "assignments": filing}, execution_result, phase, iteration
            )
        return execution_result


def register_tools() -> None:
    """Terrarium finds tools by scanning its own envs/ package; this environment lives outside it."""
    from terrarium import environment_tools

    environment_tools._discover_tools()
    environment_tools._TOOL_CLASS_BY_ENV_NAME["LibraryEnvironment"] = LibraryTools
    environment_tools._TOOL_CLASS_BY_NAME["LibraryTools"] = LibraryTools
