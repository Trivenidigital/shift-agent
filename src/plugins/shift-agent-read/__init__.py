"""shift-agent-read — read-only Hermes tools over deterministic project data.

Each tool owns its OWN authorization policy; the plugin does not impose one.
`get_compliance_deadlines`, `get_equipment_maintenance_due` and
`get_pending_catering_approvals` are owner-only and enforce that in their
handlers. `find_nearest_location` and `get_catering_menu_items` are deliberately
public and expose only facts a business publishes. Describing the plugin as
owner-only stopped being true when the second tool landed, so it no longer says
that — and there is no shared authorization framework here to keep them
consistent, because tools with genuinely different policies do not need one.

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

from . import (catering_approvals_tool, catering_menu_tool, compliance_tool,
               equipment_tool, location_tool)


def register(ctx) -> None:
    """Hermes plugin entry point, called once at gateway startup."""
    for tool in (catering_approvals_tool, catering_menu_tool, compliance_tool,
                 equipment_tool, location_tool):
        ctx.register_tool(
            name=tool.TOOL_NAME,
            toolset=tool.TOOLSET,
            schema=tool.SCHEMA,
            handler=tool.handler,
            description=tool.DESCRIPTION,
        )
