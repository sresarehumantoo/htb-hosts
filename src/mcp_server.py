#!/usr/bin/env python3
"""Zero-dependency stdio MCP server for htb-hosts.

Speaks MCP over line-delimited JSON-RPC 2.0 on stdin/stdout (no SDK required).
Exposes tools that add/remove/list/select/clean HTB host entries in /etc/hosts
without sudo, backed by the shared core library.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional

# Appended, not inserted: sys.path[0] is the script's own directory, so running
# straight out of a source checkout picks up the sibling htb_hosts.py, while the
# installed CLI (in a bindir with no library next to it) falls through to /opt.
sys.path.append("/opt/htb-hosts")
import htb_hosts as hh  # noqa: E402  pylint: disable=wrong-import-position

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "htb-hosts", "version": "1.0.0"}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "htb_hosts_add",
        "description": (
            "Add or update an HTB host entry in /etc/hosts (no sudo needed). "
            "Use this whenever you need a hostname like machine.htb to resolve "
            "to a box IP. Entries go in a managed block and can be cleaned later."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ip": {"type": "string", "description": "Box IP, e.g. 10.129.45.2"},
                "hostnames": {
                    "type": ["array", "string"],
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": (
                        "One or more hostnames, e.g. ['machine.htb','www.machine.htb']. "
                        "A single comma/space separated string also works."
                    ),
                },
                "tag": {
                    "type": "string",
                    "description": "Optional group tag (defaults to the box name) for bulk cleanup",
                },
            },
            "required": ["ip", "hostnames"],
        },
    },
    {
        "name": "htb_hosts_remove",
        "description": "Remove an HTB host entry from the managed block by hostname or IP.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "Hostname or IP to remove"},
            },
            "required": ["selector"],
        },
    },
    {
        "name": "htb_hosts_list",
        "description": "List host entries currently in the managed block.",
        "inputSchema": {
            "type": "object",
            "properties": {"tag": {"type": "string", "description": "Optional tag filter"}},
        },
    },
    {
        "name": "htb_hosts_clean",
        "description": (
            "Remove managed host entries. No args wipes the whole block; `tag` "
            "removes one box; `exclude` removes everything EXCEPT fuzzy matches "
            "(e.g. exclude=['forest'] keeps every forest entry and clears the rest). "
            "`tag` and `exclude` are mutually exclusive."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "description": "Remove only entries with this exact tag"},
                "exclude": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Fuzzy patterns to KEEP; everything else is removed",
                },
            },
        },
    },
    {
        "name": "htb_hosts_show",
        "description": (
            "Structured overview of managed hosts grouped by box (tag). Set "
            "full=true to also include unmanaged/system entries (localhost, etc.)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "full": {"type": "boolean", "description": "Also include unmanaged entries"}
            },
        },
    },
    {
        "name": "htb_hosts_select",
        "description": (
            "Look up managed entries by fuzzy pattern and return just their bare "
            "values, ready to feed into another command. `patterns` matches against "
            "ip/tag/hostname; `field` picks what comes back: 'ip' (default), 'host', "
            "or 'both'. Use this to resolve a box name to its IP before scanning."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Fuzzy patterns, e.g. ['machine']; omit to match everything",
                },
                "field": {
                    "type": "string",
                    "enum": ["ip", "host", "both"],
                    "description": "Which values to return (default 'ip')",
                },
            },
        },
    },
    {
        "name": "htb_hosts_retag",
        "description": "Recompute every entry's tag from its box name (fixes bulk tag=imported).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "htb_hosts_backup",
        "description": "Save an explicit snapshot of /etc/hosts (never auto-pruned).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Optional label for the backup filename"}
            },
        },
    },
    {
        "name": "htb_hosts_restore",
        "description": "Restore /etc/hosts from a snapshot (most recent if no name is given).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Backup name or substring to restore"}
            },
        },
    },
    {
        "name": "htb_hosts_list_backups",
        "description": "List available /etc/hosts snapshots, oldest first.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _call_tool(name: str, args: dict[str, Any]) -> hh.Dict:
    """Dispatch an MCP tool call to the core library."""
    handlers = {
        "htb_hosts_add": lambda: hh.add(args["ip"], args["hostnames"], args.get("tag")),
        "htb_hosts_remove": lambda: hh.remove(args["selector"]),
        "htb_hosts_list": lambda: {"entries": hh.list_entries(args.get("tag"))},
        "htb_hosts_show": lambda: hh.show_data(bool(args.get("full"))),
        "htb_hosts_select": lambda: hh.select(args.get("patterns"), args.get("field") or "ip"),
        "htb_hosts_clean": lambda: hh.clean(args.get("tag"), args.get("exclude")),
        "htb_hosts_retag": hh.retag,
        "htb_hosts_backup": lambda: hh.backup(args.get("label")),
        "htb_hosts_restore": lambda: hh.restore(args.get("name")),
        "htb_hosts_list_backups": lambda: {"backups": hh.list_backups()},
    }
    if name not in handlers:
        raise hh.HostsError(f"unknown tool: {name}")
    return handlers[name]()


def _result(id_: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _error(id_: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def handle(msg: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Handle a single JSON-RPC message; return a reply, or None for notifications."""
    # pylint: disable=too-many-return-statements
    method = msg.get("method")
    id_ = msg.get("id")
    is_notification = "id" not in msg

    if method == "initialize":
        return _result(
            id_,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "tools/list":
        return _result(id_, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params", {})
        name = params.get("name")
        args = params.get("arguments", {}) or {}
        try:
            data = _call_tool(name, args)
            return _result(id_, {"content": [{"type": "text", "text": json.dumps(data, indent=2)}]})
        except hh.HostsError as exc:
            return _result(
                id_,
                {"content": [{"type": "text", "text": f"error: {exc}"}], "isError": True},
            )
        except Exception as exc:  # noqa: BLE001  pylint: disable=broad-exception-caught
            return _error(id_, -32603, f"internal error: {exc}")

    if is_notification:
        return None  # notifications (e.g. notifications/initialized) get no reply
    if method == "ping":
        return _result(id_, {})
    return _error(id_, -32601, f"method not found: {method}")


def main() -> None:
    """Read JSON-RPC messages line by line and write replies to stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        reply = handle(msg)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
