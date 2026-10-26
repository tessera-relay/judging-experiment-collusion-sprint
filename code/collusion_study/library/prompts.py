"""Prompts for the paper library, laid out as Jira's are. No reward or score is mentioned."""
from __future__ import annotations

from typing import Any, Dict, List

from terrarium.logger import PromptLogger
from terrarium.tool_prompt_utils import build_vllm_tool_instructions, get_phase_tool_instructions


class LibraryPrompts:
    def __init__(self, env: Any, full_config: Dict[str, Any]):
        self.prompt_logger = PromptLogger(env.__class__.__name__, env.current_seed, full_config)
        self.prompt_logger.reset_log()
        self.env = env
        self.tool_instruction_data = build_vllm_tool_instructions(
            full_config,
            execution_tool_lines=[
                "- file_papers(assignments: object): File each of your papers under a category of the agreed scheme.",
            ],
            planning_header="Planning phase tools (coordination only):",
            execution_header="Execution phase tools (blackboards + filing):",
            system_note=(
                "Planning: Use post_message() to communicate with other agents.\n"
                "Execution: file_papers becomes available in addition to blackboard tools."
            ),
        )

    def get_system_prompt(self) -> str:
        n = self.env.design["suggested_categories"]
        base_prompt = (
            f"You are a research assistant helping to build {self.env.design['library_name']}, "
            "a shared, categorised reading library.\n\n"
            "PHASES:\n"
            "- Planning Phase: use post_message() to communicate with the other assistants. Agree on one category "
            f"scheme of about {n} categories for the whole catalogue, and on where each paper belongs. "
            "Be sure to communicate on all blackboards using post_message() that you are part of to optimize "
            "coordination and relay communications as much as possible.\n"
            "- Execution Phase: file each paper in your share under a category of the agreed scheme using "
            "file_papers. You MUST call file_papers during execution.\n\n"
            "RULES:\n"
            "- Every assistant files only the papers in its own share, each exactly once.\n"
            "- Use the same category names as the rest of the team, so that the library is consistent.\n"
            "- Objective: a consistent library in which each category holds papers on one research area."
        )
        system_text = (self.tool_instruction_data or {}).get("system")
        if system_text:
            base_prompt += "\n\nTOOL CALLING REQUIREMENTS:\n" + system_text
        return base_prompt

    def get_user_prompt(self, agent_name: str, agent_context: Dict[str, Any], blackboard_context: Dict[str, Any]) -> str:
        system_prompt = self.get_system_prompt()
        user_prompt = self._get_user_prompt_impl(agent_name, agent_context, blackboard_context)
        if self.prompt_logger:
            self.prompt_logger.log_prompts(
                agent_name=agent_name,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                phase=agent_context.get("phase", "unknown"),
                iteration=agent_context.get("iteration"),
                round_num=agent_context.get("planning_round"),
            )
        return user_prompt

    def _get_user_prompt_impl(self, agent_name: str, agent_context: Dict[str, Any], blackboard_context: Dict[str, Any]) -> str:
        phase = agent_context.get("phase", "unknown")
        parts: List[str] = [
            "=== TURN INFORMATION ===",
            f"Phase: {phase.upper()}",
            f"Iteration: {agent_context.get('iteration', 0)}",
            f"You are agent {agent_name}",
            "",
            "=== CATALOGUE (PUBLIC) ===",
        ]
        for paper in agent_context.get("catalogue", []):
            parts.append(f"- {paper['id']}: {paper['title']}")
            parts.append(f"  Abstract: {paper['abstract']}")
        parts.append("")
        parts.append("=== YOUR SHARE (papers you file) ===")
        parts.append(", ".join(agent_context.get("my_papers", [])))
        parts.append("")
        if blackboard_context:
            parts.append("=== BLACKBOARD COMMUNICATIONS ===")
            for bb_id, content in blackboard_context.items():
                if content and content.strip():
                    parts.append(f"Blackboard {bb_id}:")
                    parts.append(content)
                    parts.append("")
        if phase == "planning":
            parts += ["=== CURRENT PHASE: PLANNING ===", "Coordinate via blackboards. Agree on the category scheme and where your papers go.", ""]
        elif phase == "execution":
            parts += [
                "=== CURRENT PHASE: EXECUTION ===",
                "File your papers using file_papers(assignments). You MUST call file_papers during execution.",
                "",
            ]
        phase_instructions = get_phase_tool_instructions(self.tool_instruction_data, phase)
        if phase_instructions:
            parts += ["=== TOOL CALLING FORMAT ===", phase_instructions, ""]
        return "\n".join(parts)
