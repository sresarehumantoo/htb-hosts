"""Tests for the CLI entrypoint (src/htb-hosts)."""

import contextlib
import io
import json
import os
import unittest

from base import HostsTestCase
import loader


class CliTestCase(HostsTestCase):
    def setUp(self):
        super().setUp()
        self.cli = loader.load_cli()

    def run_cli(self, *argv):
        """Run main(argv), returning (exit_code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class TestCliAdd(CliTestCase):
    def test_add(self):
        code, out, _ = self.run_cli("add", "10.129.45.2", "machine.htb")
        self.assertEqual(code, 0)
        self.assertIn("added:", out)
        self.assertIn("machine.htb", self.read())

    def test_add_order_independent(self):
        code, _, _ = self.run_cli("add", "machine.htb", "10.129.45.2", "www.machine.htb")
        self.assertEqual(code, 0)
        entries = self.core.list_entries()
        self.assertEqual(entries[0]["ip"], "10.129.45.2")

    def test_add_json(self):
        code, out, _ = self.run_cli("add", "10.0.0.1", "a.htb", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["entry"]["ip"], "10.0.0.1")

    def test_add_bad_ip_exit_2(self):
        code, _, err = self.run_cli("add", "10.0.0", "a.htb")
        self.assertEqual(code, 2)
        self.assertIn("error:", err)

    def test_add_no_ip_exit_2(self):
        code, _, err = self.run_cli("add", "a.htb", "b.htb")
        self.assertEqual(code, 2)
        self.assertIn("no IP", err)

    def test_add_comma_separated_hostnames(self):
        code, _, _ = self.run_cli("add", "10.10.11.6", "example.local, jobs.example.local")
        self.assertEqual(code, 0)
        entries = self.core.list_entries()
        self.assertEqual(entries[0]["hostnames"], ["example.local", "jobs.example.local"])

    def test_add_all_in_one_argument(self):
        code, _, _ = self.run_cli("add", "10.10.11.6 example.local,jobs.example.local")
        self.assertEqual(code, 0)
        entries = self.core.list_entries()
        self.assertEqual(entries[0]["ip"], "10.10.11.6")
        self.assertEqual(len(entries[0]["hostnames"]), 2)

    def test_add_warning_to_stderr(self):
        code, _, err = self.run_cli("add", "10.0.0.9", "nas.homelab.lan")
        self.assertEqual(code, 0)
        self.assertIn("warning:", err)


class TestCliOther(CliTestCase):
    def setUp(self):
        super().setUp()
        self.run_cli("add", "10.0.0.1", "a.htb", "www.a.htb", "--tag", "one")
        self.run_cli("add", "10.0.0.2", "b.htb", "--tag", "two")

    def test_list(self):
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("a.htb", out)
        self.assertIn("b.htb", out)

    def test_list_json(self):
        _, out, _ = self.run_cli("list", "--json")
        self.assertEqual(len(json.loads(out)), 2)

    def test_rm(self):
        code, out, _ = self.run_cli("rm", "www.a.htb")
        self.assertEqual(code, 0)
        self.assertIn("removed", out)
        self.assertNotIn("www.a.htb", self.read())

    def test_clean_tag(self):
        self.run_cli("clean", "--tag", "one")
        self.assertEqual([e["tag"] for e in self.core.list_entries()], ["two"])

    def test_clean_all(self):
        self.run_cli("clean")
        self.assertEqual(self.core.list_entries(), [])

    def test_clean_exclude(self):
        code, out, _ = self.run_cli("clean", "--exclude", "a.htb")
        self.assertEqual(code, 0)
        self.assertIn("except", out)
        self.assertEqual([e["ip"] for e in self.core.list_entries()], ["10.0.0.1"])

    def test_retag(self):
        code, out, _ = self.run_cli("retag")
        self.assertEqual(code, 0)
        self.assertIn("retagged", out)

    def test_show(self):
        code, out, _ = self.run_cli("show")
        self.assertEqual(code, 0)
        self.assertIn("HTB hosts", out)
        self.assertIn("one (", out)  # box-grouped header

    def test_show_full_positional(self):
        code, out, _ = self.run_cli("show", "full")
        self.assertEqual(code, 0)
        self.assertIn("unmanaged / system", out)
        self.assertIn("localhost", out)

    def test_show_json(self):
        code, out, _ = self.run_cli("show", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["box_count"], 2)

    def test_doctor_exit_code(self):
        code, out, _ = self.run_cli("doctor")
        self.assertEqual(code, 0)
        self.assertIn('"ok": true', out)


class TestCliSelect(CliTestCase):
    def setUp(self):
        super().setUp()
        self.run_cli("add", "10.10.11.6", "example.local, jobs.example.local")
        self.run_cli("add", "10.10.11.5", "dc01.forest.htb")

    def test_get_prints_only_the_ip(self):
        code, out, _ = self.run_cli("get", "example")
        self.assertEqual(code, 0)
        self.assertEqual(out, "10.10.11.6\n")

    def test_select_is_an_alias_of_get(self):
        _, out, _ = self.run_cli("select", "forest")
        self.assertEqual(out, "10.10.11.5\n")

    def test_get_host(self):
        _, out, _ = self.run_cli("get", "jobs", "--host")
        self.assertEqual(out, "jobs.example.local\n")

    def test_get_both(self):
        _, out, _ = self.run_cli("get", "forest", "--both")
        self.assertEqual(out.split(), ["10.10.11.5", "dc01.forest.htb"])

    def test_show_with_a_pattern_selects(self):
        code, out, _ = self.run_cli("show", "example")
        self.assertEqual(code, 0)
        self.assertEqual(out, "10.10.11.6\n")

    def test_show_without_a_pattern_still_prints_the_overview(self):
        _, out, _ = self.run_cli("show")
        self.assertIn("HTB hosts", out)

    def test_no_match_exits_1_with_empty_stdout(self):
        code, out, err = self.run_cli("get", "nope")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("no managed entry", err)

    def test_get_json(self):
        _, out, _ = self.run_cli("get", "example", "--json")
        payload = json.loads(out)
        self.assertEqual(payload["field"], "ip")
        self.assertEqual(payload["values"], ["10.10.11.6"])


class TestCliSelectIndex(CliTestCase):
    def setUp(self):
        super().setUp()
        self.run_cli("add", "10.10.11.6", "a.example.local, b.example.local, c.example.local")

    def test_first(self):
        code, out, _ = self.run_cli("get", "example", "--host", "--first")
        self.assertEqual((code, out), (0, "a.example.local\n"))

    def test_index(self):
        _, out, _ = self.run_cli("get", "example", "--host", "-i", "2")
        self.assertEqual(out, "b.example.local\n")

    def test_negative_index_is_not_mistaken_for_a_flag(self):
        _, out, _ = self.run_cli("get", "example", "--host", "-i", "-1")
        self.assertEqual(out, "c.example.local\n")

    def test_range(self):
        _, out, _ = self.run_cli("get", "example", "--host", "-i", "1-2")
        self.assertEqual(out, "a.example.local\nb.example.local\n")

    def test_sep_joins_onto_one_line(self):
        _, out, _ = self.run_cli("get", "example", "--host", "--sep", ",")
        self.assertEqual(out, "a.example.local,b.example.local,c.example.local\n")

    def test_numbered(self):
        _, out, _ = self.run_cli("get", "example", "--host", "-n")
        self.assertEqual(out.splitlines()[0], "1  a.example.local")

    def test_out_of_range_exits_2_with_empty_stdout(self):
        code, out, err = self.run_cli("get", "example", "--host", "-i", "9")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("out of range", err)

    def test_index_and_first_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            self.run_cli("get", "example", "-i", "2", "--first")

    def test_numbered_and_sep_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            self.run_cli("get", "example", "-n", "--sep", ",")

    def test_show_takes_the_same_flags(self):
        _, out, _ = self.run_cli("show", "example", "--host", "--first")
        self.assertEqual(out, "a.example.local\n")


class TestCliComplete(CliTestCase):
    def setUp(self):
        super().setUp()
        self.run_cli("add", "10.10.11.6", "example.local, jobs.example.local")

    def test_commands_includes_aliases_but_not_complete_itself(self):
        code, out, _ = self.run_cli("complete", "commands")
        self.assertEqual(code, 0)
        words = out.split()
        self.assertIn("get", words)
        self.assertIn("select", words)
        self.assertNotIn("complete", words)

    def test_hosts_one_per_line(self):
        _, out, _ = self.run_cli("complete", "hosts")
        self.assertEqual(out.split("\n")[:2], ["example.local", "jobs.example.local"])

    def test_tags(self):
        _, out, _ = self.run_cli("complete", "tags")
        self.assertEqual(out.strip(), "example")

    def test_trailing_patterns_scope_the_candidates(self):
        self.run_cli("add", "10.10.11.5", "dc01.forest.htb")
        _, out, _ = self.run_cli("complete", "hosts", "example")
        self.assertEqual(out.split(), ["example.local", "jobs.example.local"])
        _, out, _ = self.run_cli("complete", "hosts", "example", "jobs")
        self.assertEqual(out.split(), ["jobs.example.local"])

    def test_stays_silent_when_the_hosts_file_is_unreadable(self):
        self.core.HOSTS_FILE = os.path.join(self.tmp, "definitely", "missing")
        code, out, err = self.run_cli("complete", "targets")
        self.assertEqual((code, out, err), (0, "", ""))

    def test_complete_is_hidden_from_help(self):
        with self.assertRaises(SystemExit):
            self.run_cli("--help")

    def test_unknown_kind_is_a_usage_error(self):
        with self.assertRaises(SystemExit):
            self.run_cli("complete", "nope")


class TestCliBackup(CliTestCase):
    def test_backup_and_restore(self):
        self.run_cli("add", "10.0.0.1", "keep.htb")
        code, out, _ = self.run_cli("backup", "--label", "cp")
        self.assertEqual(code, 0)
        self.assertIn("saved backup", out)
        self.run_cli("clean")
        code, out, _ = self.run_cli("restore")
        self.assertEqual(code, 0)
        self.assertIn("restored from", out)
        self.assertIn("keep.htb", self.read())

    def test_restore_list(self):
        self.run_cli("backup", "--label", "one")
        code, out, _ = self.run_cli("restore", "--list", "--json")
        self.assertEqual(code, 0)
        self.assertTrue(any("one" in b["name"] for b in json.loads(out)))


class TestCliMigrate(CliTestCase):
    initial = "127.0.0.1\tlocalhost\n10.129.132.3 app.blurry.htb\n"

    def test_migrate(self):
        code, out, _ = self.run_cli("migrate")
        self.assertEqual(code, 0)
        self.assertIn("imported 1", out)
        self.assertEqual(len(self.core.list_entries()), 1)


if __name__ == "__main__":
    unittest.main()
