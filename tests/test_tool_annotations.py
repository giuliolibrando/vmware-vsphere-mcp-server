"""Every MCP tool must declare accurate ToolAnnotations so clients can
distinguish safe read-only calls from mutating/destructive ones."""

from vsphere_mcp_server import server

# name → (destructiveHint, idempotentHint) for every tool that mutates
# vSphere state. Everything not listed here must be read-only.
MUTATING = {
    "power_on_vm": (False, True),
    "power_off_vm": (False, True),
    "bulk_power_operations": (True, False),   # includes 'restart'
    "create_vm_snapshot": (False, False),     # each call adds a snapshot
    "delete_vm_snapshot": (True, True),
    "delete_vm": (True, True),
    "modify_vm_resources": (True, True),
    "bulk_delete_vms": (True, True),
    "force_power_off_vm": (True, True),       # hard off, data-loss risk
    "clone_vm": (False, False),               # each call adds a clone
}


def _tools():
    tools = server.mcp._tool_manager.list_tools()
    assert tools, "no tools registered"
    return tools


def test_mutating_map_matches_registered_tools():
    names = {tool.name for tool in _tools()}
    assert MUTATING.keys() <= names, MUTATING.keys() - names


def test_every_tool_declares_annotations_with_closed_world():
    for tool in _tools():
        assert tool.annotations is not None, tool.name
        assert tool.annotations.readOnlyHint is not None, tool.name
        # All tools talk to one configured vCenter — a closed domain.
        assert tool.annotations.openWorldHint is False, tool.name


def test_read_only_tools_are_marked_read_only():
    for tool in _tools():
        if tool.name not in MUTATING:
            assert tool.annotations is not None, tool.name
            assert tool.annotations.readOnlyHint is True, tool.name


def test_mutating_tools_declare_destructive_and_idempotent_hints():
    by_name = {tool.name: tool for tool in _tools()}
    for name, (destructive, idempotent) in MUTATING.items():
        annotations = by_name[name].annotations
        assert annotations is not None, name
        assert annotations.readOnlyHint is False, name
        assert annotations.destructiveHint is destructive, name
        assert annotations.idempotentHint is idempotent, name
