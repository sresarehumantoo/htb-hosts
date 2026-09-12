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

    def test_show_flat(self):
        code, out, _ = self.run_cli("show", "--flat")
        self.assertEqual(code, 0)
        self.assertIn("a.htb", out)
        self.assertIn("b.htb", out)

    def test_show_flat_json(self):
        _, out, _ = self.run_cli("show", "--flat", "--json")
        self.assertEqual(len(json.loads(out)["entries"]), 2)

    def test_show_filters_by_pattern(self):
        _, out, _ = self.run_cli("show", "b.htb")
        self.assertIn("b.htb", out)
        self.assertNotIn("a.htb", out)

    def test_a_box_named_full_is_reachable(self):
        """`show full` used to mean the wide overview, hiding a box of that name."""
        self.run_cli("add", "10.0.0.3", "full.htb")
        _, out, _ = self.run_cli("show", "full")
        self.assertIn("full.htb", out)
        self.assertNotIn("unmanaged / system", out)

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

    def test_show_full_flag(self):
        code, out, _ = self.run_cli("show", "--full")
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


class TestCliCleanMatch(CliTestCase):
    def setUp(self):
        super().setUp()
        self.run_cli("add", "10.0.0.1", "example.htb")
        self.run_cli("add", "10.0.0.2", "web01.example.htb")
        self.run_cli("add", "10.0.0.3", "web02.example.htb")
        self.run_cli("add", "10.0.0.4", "forest.htb")

    def test_single_match_needs_no_confirmation(self):
        code, out, _ = self.run_cli("clean", "--match", "forest")
        self.assertEqual(code, 0)
        self.assertIn("cleaned 1", out)
        self.assertEqual(len(self.core.list_entries()), 3)

    def test_match_narrows_like_get(self):
        self.run_cli("clean", "--match", "example", "web", "--yes")
        self.assertEqual([e["ip"] for e in self.core.list_entries()], ["10.0.0.1", "10.0.0.4"])

    def test_multiple_matches_refuse_without_a_terminal(self):
        code, _, err = self.run_cli("clean", "--match", "example")
        self.assertEqual(code, 2)
        self.assertIn("pass --yes", err)
        self.assertIn("will remove 3 entries", err)
        self.assertEqual(len(self.core.list_entries()), 4)  # nothing touched

    def test_yes_skips_the_prompt(self):
        code, out, _ = self.run_cli("clean", "--match", "example", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("cleaned 3", out)

    def test_json_skips_the_prompt(self):
        """--json is a machine caller; it must not block on a prompt."""
        code, out, _ = self.run_cli("clean", "--match", "example", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["removed"]), 3)

    def test_scopes_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            self.run_cli("clean", "--tag", "example", "--match", "example")


class TestCliSelect(CliTestCase):
    def setUp(self):
        super().setUp()
        self.run_cli("add", "10.10.11.6", "example.local, jobs.example.local")
        self.run_cli("add", "10.10.11.5", "dc01.forest.htb")

    def test_get_prints_only_the_ip(self):
        code, out, _ = self.run_cli("get", "example")
        self.assertEqual(code, 0)
        self.assertEqual(out, "10.10.11.6\n")

    def test_select_alias_is_gone(self):
        with self.assertRaises(SystemExit):
            self.run_cli("select", "forest")

    def test_get_host(self):
        _, out, _ = self.run_cli("get", "jobs", "--host")
        self.assertEqual(out, "jobs.example.local\n")

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

    def test_numbered_and_sep_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            self.run_cli("get", "example", "-n", "--sep", ",")

    def test_show_does_not_take_gets_piping_flags(self):
        """show is for reading; get is for piping. No overlap."""
        with self.assertRaises(SystemExit):
            self.run_cli("show", "example", "--host")


class TestCliComplete(CliTestCase):
    def setUp(self):
        super().setUp()
        self.run_cli("add", "10.10.11.6", "example.local, jobs.example.local")

    def test_commands_lists_the_public_surface_only(self):
        code, out, _ = self.run_cli("complete", "commands")
        self.assertEqual(code, 0)
        words = out.split()
        self.assertIn("get", words)
        self.assertIn("show", words)
        self.assertNotIn("complete", words)  # hidden
        self.assertNotIn("select", words)  # alias removed
        self.assertNotIn("list", words)  # folded into `show --flat`

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
