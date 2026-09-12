"""Tests for the MCP server (src/mcp_server.py)."""

import json
import unittest

from base import HostsTestCase
import loader


class McpTestCase(HostsTestCase):
    def setUp(self):
        super().setUp()
        self.mcp = loader.load_mcp()

    def call(self, name, arguments):
        """Invoke a tools/call and return the parsed reply dict."""
        return self.mcp.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )

    def text(self, reply):
        return reply["result"]["content"][0]["text"]


class TestProtocol(McpTestCase):
    def test_initialize(self):
        reply = self.mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(reply["result"]["serverInfo"]["name"], "htb-hosts")
        self.assertIn("protocolVersion", reply["result"])

    def test_tools_list(self):
        reply = self.mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in reply["result"]["tools"]}
        self.assertLessEqual(
            {"htb_hosts_add", "htb_hosts_remove", "htb_hosts_list", "htb_hosts_clean"},
            names,
        )

    def test_notification_gets_no_reply(self):
        self.assertIsNone(
            self.mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )

    def test_ping(self):
        reply = self.mcp.handle({"jsonrpc": "2.0", "id": 9, "method": "ping"})
        self.assertEqual(reply["result"], {})

    def test_unknown_method(self):
        reply = self.mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "bogus"})
        self.assertEqual(reply["error"]["code"], -32601)


class TestTools(McpTestCase):
    def test_add_updates_file(self):
        reply = self.call("htb_hosts_add", {"ip": "10.129.45.2", "hostnames": ["machine.htb"]})
        self.assertIn("machine.htb", self.text(reply))
        self.assertIn("machine.htb", self.read())

    def test_list(self):
        self.call("htb_hosts_add", {"ip": "10.0.0.1", "hostnames": ["a.htb"], "tag": "one"})
        reply = self.call("htb_hosts_list", {"tag": "one"})
        payload = json.loads(self.text(reply))
        self.assertEqual(payload["entries"][0]["hostnames"], ["a.htb"])

    def test_remove(self):
        self.call("htb_hosts_add", {"ip": "10.0.0.1", "hostnames": ["a.htb"]})
        self.call("htb_hosts_remove", {"selector": "a.htb"})
        self.assertEqual(self.core.list_entries(), [])

    def test_clean(self):
        self.call("htb_hosts_add", {"ip": "10.0.0.1", "hostnames": ["a.htb"]})
        self.call("htb_hosts_clean", {})
        self.assertEqual(self.core.list_entries(), [])

    def test_bad_input_is_error_not_crash(self):
        reply = self.call("htb_hosts_add", {"ip": "bad-ip", "hostnames": ["a.htb"]})
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("error:", self.text(reply))

    def test_unknown_tool_is_error(self):
        reply = self.call("nope", {})
        self.assertTrue(reply["result"]["isError"])

    def test_clean_exclude(self):
        self.call("htb_hosts_add", {"ip": "10.10.11.5", "hostnames": ["code.forest.htb"]})
        self.call("htb_hosts_add", {"ip": "10.10.11.7", "hostnames": ["sequel.htb"]})
        self.call("htb_hosts_clean", {"exclude": ["forest"]})
        self.assertEqual([e["ip"] for e in self.core.list_entries()], ["10.10.11.5"])

    def test_show(self):
        self.call("htb_hosts_add", {"ip": "10.10.11.5", "hostnames": ["forest.htb"]})
        reply = self.call("htb_hosts_show", {"full": True})
        data = json.loads(self.text(reply))
        self.assertEqual(data["box_count"], 1)
        self.assertIn("forest", data["groups"])
        self.assertIn("unmanaged", data)

    def test_retag(self):
        self.call("htb_hosts_add", {"ip": "10.0.0.1", "hostnames": ["a.htb"], "tag": "imported"})
        self.call("htb_hosts_retag", {})
        self.assertEqual(self.core.list_entries()[0]["tag"], "a")

    def test_backup_restore_roundtrip(self):
        self.call("htb_hosts_add", {"ip": "10.0.0.1", "hostnames": ["keep.htb"]})
        self.call("htb_hosts_backup", {"label": "cp"})
        self.call("htb_hosts_clean", {})
        self.call("htb_hosts_restore", {})
        self.assertIn("keep.htb", self.read())

    def test_list_backups(self):
        self.call("htb_hosts_backup", {"label": "snap"})
        reply = self.call("htb_hosts_list_backups", {})
        payload = json.loads(self.text(reply))
        self.assertTrue(any("snap" in b["name"] for b in payload["backups"]))

    def test_add_accepts_a_comma_separated_string(self):
        self.call(
            "htb_hosts_add",
            {"ip": "10.10.11.6", "hostnames": "example.local, jobs.example.local"},
        )
        self.assertEqual(len(self.core.list_entries()[0]["hostnames"]), 2)

    def test_tools_list_has_new_tools(self):
        reply = self.mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in reply["result"]["tools"]}
        self.assertTrue(
            {
                "htb_hosts_retag",
                "htb_hosts_backup",
                "htb_hosts_restore",
                "htb_hosts_list_backups",
            }
            <= names
        )


if __name__ == "__main__":
    unittest.main()
