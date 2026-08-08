"""shift-agent-read — owner-facing read-only Hermes tools.

Registers tools under the dedicated `shift_agent_read` toolset. That name is
load-bearing: `agent.disabled_toolsets` suppresses by NAME and is applied last,
so a project toolset survives while the generic `skills` and `terminal` toolsets
stay disabled. These tools therefore need neither re-armed.

Discovery is Hermes's own progressive disclosure — the tool is deferred behind
tool_search/tool_describe/tool_call and found from its capability description.
That description is ordinary tool metadata, not a routing mechanism; there is no
router, no intent classifier and no keyword dispatch anywhere in this plugin.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import compliance_tool  # noqa: E402


def register(ctx) -> None:
    """Hermes plugin entry point, called once at gateway startup."""
    ctx.register_tool(
        name=compliance_tool.TOOL_NAME,
        toolset=compliance_tool.TOOLSET,
        schema=compliance_tool.SCHEMA,
        handler=compliance_tool.handler,
        description=compliance_tool.DESCRIPTION,
    )
