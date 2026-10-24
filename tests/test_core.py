"""Tests for the core library (htb_hosts.py)."""

import contextlib
import io
import os
import threading
import unittest

from base import HostsTestCase


class TestValidation(HostsTestCase):
    def test_validate_ip_ok(self):
        self.assertEqual(self.core.validate_ip(" 10.129.45.2 "), "10.129.45.2")
        self.assertEqual(self.core.validate_ip("::1"), "::1")

    def test_validate_ip_bad(self):
        for bad in ("999.1.1.1", "10.0.0", "nope", ""):
            with self.assertRaises(self.core.HostsError):
                self.core.validate_ip(bad)

    def test_validate_ip_scope_id(self):
        self.assertEqual(self.core.validate_ip("fe80::1%eth0"), "fe80::1%eth0")
        for bad in ("fe80::1%a\nb", "fe80::1%x#y", "fe80::1%a b", "fe80::1%\t1.2.3.4 evil.htb"):
            with self.assertRaises(self.core.HostsError):
                self.core.validate_ip(bad)

    def test_add_rejects_scope_id_line_injection(self):
        with self.assertRaises(self.core.HostsError):
            self.core.add("fe80::1%x\n10.10.11.5 evil.htb", ["box.htb"])
        self.assertEqual(self.core.list_entries(), [])

    def test_validate_hostname_ok(self):
        for good in ("machine.htb", "a.b.c.htb", "host_1.vl", "x"):
            self.assertEqual(self.core.validate_hostname(good), good)

    def test_validate_hostname_bad(self):
        for bad in ("a b.htb", "a\nb", "a#b", "", "-lead.htb", "x" * 254):
            with self.assertRaises(self.core.HostsError):
                self.core.validate_hostname(bad)

    def test_validate_tag(self):
        self.assertEqual(self.core.validate_tag("box-1_2.x"), "box-1_2.x")
        with self.assertRaises(self.core.HostsError):
            self.core.validate_tag("bad tag")

    def test_default_tag(self):
        self.assertEqual(self.core.default_tag("app.blurry.htb"), "blurry")
        self.assertEqual(self.core.default_tag("sequel.htb"), "sequel")
        self.assertEqual(self.core.default_tag("solo"), "solo")


class TestSplit(HostsTestCase):
    def test_ip_any_position(self):
        for tokens in (
            ["10.129.45.2", "a.htb", "b.htb"],
            ["a.htb", "b.htb", "10.129.45.2"],
            ["a.htb", "10.129.45.2", "b.htb"],
        ):
            ip, hosts = self.core.split_ip_and_hosts(tokens)
            self.assertEqual(ip, "10.129.45.2")
            self.assertEqual(hosts, ["a.htb", "b.htb"])

    def test_no_ip(self):
        with self.assertRaises(self.core.HostsError):
            self.core.split_ip_and_hosts(["a.htb", "b.htb"])

    def test_two_ips(self):
        with self.assertRaises(self.core.HostsError):
            self.core.split_ip_and_hosts(["10.0.0.1", "a.htb", "10.0.0.2"])


class TestAdd(HostsTestCase):
    def test_add_creates_entry(self):
        res = self.core.add("10.129.45.2", ["machine.htb", "www.machine.htb"])
        self.assertEqual(res["entry"]["ip"], "10.129.45.2")
        self.assertEqual(res["entry"]["tag"], "machine")
        self.assertEqual(len(self.block_lines()), 1)
        self.assertIn("machine.htb", self.read())

    def test_add_explicit_tag_and_date(self):
        res = self.core.add("10.0.0.1", ["a.htb"], tag="box")
        self.assertEqual(res["entry"]["tag"], "box")
        self.assertRegex(res["entry"]["added"], r"^\d{4}-\d{2}-\d{2}$")

    def test_add_is_idempotent(self):
        self.core.add("10.0.0.1", ["a.htb"])
        self.core.add("10.0.0.1", ["a.htb"])
        line = self.block_lines()[0]
        self.assertEqual(line.count("a.htb"), 1)
        self.assertEqual(len(self.block_lines()), 1)

    def test_add_merges_same_ip(self):
        self.core.add("10.0.0.1", ["a.htb"])
        self.core.add("10.0.0.1", ["b.htb"])
        self.assertEqual(len(self.block_lines()), 1)
        entries = self.core.list_entries()
        self.assertEqual(sorted(entries[0]["hostnames"]), ["a.htb", "b.htb"])

    def test_add_rehomes_hostname(self):
        self.core.add("10.0.0.1", ["a.htb"])
        self.core.add("10.0.0.2", ["a.htb"])  # move a.htb to a new IP
        entries = self.core.list_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["ip"], "10.0.0.2")

    def test_add_warns_when_outside_block(self):
        # nas.homelab.lan exists in the infra section (outside the block)
        res = self.core.add("10.0.0.9", ["nas.homelab.lan"])
        self.assertTrue(any("outside" in w for w in res["warnings"]))

    def test_add_preserves_infra(self):
        self.core.add("10.0.0.1", ["a.htb"])
        text = self.read()
        self.assertIn("127.0.0.1\tlocalhost", text)
        self.assertIn("ip6-loopback", text)
        self.assertIn("nas.homelab.lan", text)

    def test_add_rejects_bad_input(self):
        with self.assertRaises(self.core.HostsError):
            self.core.add("bad-ip", ["a.htb"])
        with self.assertRaises(self.core.HostsError):
            # splitting on whitespace/commas never lets an invalid token through:
            # '#' would start a comment, so it must still be rejected.
            self.core.add("10.0.0.1", ["bad#host"])


class TestRemoveCleanList(HostsTestCase):
    def setUp(self):
        super().setUp()
        self.core.add("10.0.0.1", ["a.htb", "www.a.htb"], tag="one")
        self.core.add("10.0.0.2", ["b.htb"], tag="two")

    def test_remove_one_hostname(self):
        res = self.core.remove("www.a.htb")
        self.assertEqual(len(res["removed"]), 1)
        entries = self.core.list_entries()
        first = next(e for e in entries if e["ip"] == "10.0.0.1")
        self.assertEqual(first["hostnames"], ["a.htb"])

    def test_remove_by_ip(self):
        self.core.remove("10.0.0.1")
        self.assertEqual([e["ip"] for e in self.core.list_entries()], ["10.0.0.2"])

    def test_remove_missing(self):
        res = self.core.remove("nope.htb")
        self.assertEqual(res["removed"], [])
        self.assertEqual(len(self.core.list_entries()), 2)

    def test_clean_by_tag(self):
        self.core.clean(tag="one")
        self.assertEqual([e["tag"] for e in self.core.list_entries()], ["two"])

    def test_clean_all_removes_block(self):
        self.core.clean()
        self.assertEqual(self.core.list_entries(), [])
        self.assertNotIn(self.core.BEGIN, self.read())

    def test_list_filter(self):
        self.assertEqual(len(self.core.list_entries()), 2)
        self.assertEqual(len(self.core.list_entries(tag="two")), 1)


class TestMigrate(HostsTestCase):
    initial = (
        "127.0.0.1\tlocalhost\n"
        "::1 localhost ip6-localhost\n"
        "10.10.11.3 app.blurry.htb\n"
        "10.10.11.7 sequel.htb dc01.sequel.htb\n"
        "10.10.11.9 target.vl\n"
    )

    def test_migrate_partitions(self):
        res = self.core.migrate()
        self.assertEqual(len(res["imported"]), 3)  # blurry, sequel, target
        inside = "\n".join(self.block_lines())
        self.assertIn("app.blurry.htb", inside)
        self.assertIn("target.vl", inside)
        # universal infra (localhost/IPv6) stays outside the block
        outside = self.read().split(self.core.BEGIN)[0]
        self.assertIn("localhost", outside)
        self.assertNotIn("app.blurry.htb", outside)

    def test_migrate_tags_by_box(self):
        self.core.migrate()
        tags = {e["tag"] for e in self.core.list_entries()}
        self.assertEqual(tags, {"blurry", "sequel", "target"})

    def test_migrate_dedupes(self):
        self.core.add("10.10.11.3", ["app.blurry.htb"], tag="blurry")
        self.core.migrate()  # blurry already managed -> must not duplicate
        blurry = [e for e in self.core.list_entries() if "app.blurry.htb" in e["hostnames"]]
        self.assertEqual(len(blurry), 1)

    def test_migrate_lossless(self):
        before = set(self.read().split()) - {self.core.BEGIN}
        self.core.migrate()
        after = set(w for w in self.read().split() if not w.startswith("#"))
        self.assertTrue(before.issubset(after))


class TestMigrateKeep(HostsTestCase):
    initial = "127.0.0.1\tlocalhost\n10.20.0.9 nas.homelab.lan\n10.10.11.3 box.htb\n"

    def setUp(self):
        super().setUp()
        self.core._KEEP_EXTRA = ["homelab.lan"]  # emulate HTB_HOSTS_KEEP=homelab.lan

    def test_keep_extra_stays_outside(self):
        self.core.migrate()
        inside = "\n".join(self.block_lines())
        outside = self.read().split(self.core.BEGIN)[0]
        self.assertIn("box.htb", inside)  # normal host is imported
        self.assertIn("nas.homelab.lan", outside)  # kept-domain host stays out
        self.assertNotIn("homelab.lan", inside)


class TestFileHandling(HostsTestCase):
    def test_edit_is_in_place(self):
        """The ACL-preservation invariant: writes must not change the inode."""
        ino_before = os.stat(self.hosts).st_ino
        self.core.add("10.0.0.1", ["a.htb"])
        self.assertEqual(os.stat(self.hosts).st_ino, ino_before)

    def test_backup_written(self):
        self.core.add("10.0.0.1", ["a.htb"])
        backups = os.listdir(self.core.BACKUP_DIR)
        self.assertTrue(any(b.startswith("hosts.bak.") for b in backups))

    def test_no_write_when_unchanged(self):
        self.core.add("10.0.0.1", ["a.htb"])
        mtime = os.stat(self.hosts).st_mtime_ns
        self.core.remove("does-not-exist.htb")  # no-op -> file must be untouched
        self.assertEqual(os.stat(self.hosts).st_mtime_ns, mtime)

    def test_doctor_reports_ok(self):
        info = self.core.doctor()
        self.assertTrue(info["ok"])
        self.assertTrue(info["writable"])

    def test_concurrent_adds(self):
        """flock must serialize writers so no entry is lost or corrupted."""

        def worker(i):
            self.core.add(f"10.50.0.{i}", [f"c{i}.htb"], tag=f"c{i}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(1, 13)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(self.core.list_entries()), 12)
        self.assertEqual(self.read().count(self.core.BEGIN), 1)


class TestExclude(HostsTestCase):
    def setUp(self):
        super().setUp()
        self.core.add("10.10.11.5", ["forest.htb", "dc01.forest.htb"], tag="forest")
        self.core.add("10.20.0.6", ["WEB.FOREST.HTB", "WEB"], tag="forest")  # uppercase
        self.core.add("10.10.11.7", ["sequel.htb"], tag="sequel")
        self.core.add("10.10.11.8", ["app.blurry.htb"], tag="blurry")

    def test_exclude_keeps_fuzzy_matches(self):
        res = self.core.clean(exclude=["forest"])
        # both forest entries survive (incl. the upper-case WEB.FOREST.HTB)
        surviving = {e["ip"] for e in self.core.list_entries()}
        self.assertEqual(surviving, {"10.10.11.5", "10.20.0.6"})
        self.assertEqual(len(res["removed"]), 2)

    def test_exclude_is_case_insensitive(self):
        self.core.clean(exclude=["FOREST"])
        self.assertEqual(len(self.core.list_entries()), 2)

    def test_exclude_multiple_patterns(self):
        self.core.clean(exclude=["forest", "sequel"])
        tags = sorted(e["tag"] for e in self.core.list_entries())
        self.assertEqual(tags, ["forest", "forest", "sequel"])

    def test_exclude_matches_hostname_substring(self):
        self.core.clean(exclude=["blurry"])
        self.assertEqual([e["tag"] for e in self.core.list_entries()], ["blurry"])

    def test_tag_and_exclude_mutually_exclusive(self):
        with self.assertRaises(self.core.HostsError):
            self.core.clean(tag="forest", exclude=["sequel"])


class TestShow(HostsTestCase):
    def setUp(self):
        super().setUp()
        self.core.add("10.10.11.5", ["forest.htb"], tag="forest")
        self.core.add("10.20.0.5", ["dc01.forest.htb"], tag="forest")
        self.core.add("10.10.11.7", ["sequel.htb"], tag="sequel")

    def test_groups_by_box(self):
        data = self.core.show_data()
        self.assertEqual(data["managed_count"], 3)
        self.assertEqual(data["box_count"], 2)
        self.assertEqual(len(data["groups"]["forest"]), 2)
        self.assertNotIn("unmanaged", data)

    def test_full_includes_unmanaged(self):
        data = self.core.show_data(full=True)
        unmanaged = " ".join(h for e in data["unmanaged"] for h in e["hostnames"])
        self.assertIn("localhost", unmanaged)
        self.assertIn("nas.homelab.lan", unmanaged)

    def test_outside_entries_excludes_managed(self):
        outside = self.core.outside_entries()
        hosts = {h for e in outside for h in e["hostnames"]}
        self.assertNotIn("forest.htb", hosts)  # managed, so not "outside"
        self.assertIn("localhost", hosts)

    def test_comment_lines_not_parsed_as_entries(self):
        outside = self.core.outside_entries()
        # a '# comment' line must never show up as an entry with ip '#'
        self.assertFalse(any(e["ip"].startswith("#") for e in outside))


class TestCommentLines(HostsTestCase):
    initial = "# a comment header\n127.0.0.1 localhost\n10.0.0.5 real.htb\n"

    def test_comment_ignored(self):
        outside = self.core.outside_entries()
        ips = {e["ip"] for e in outside}
        self.assertEqual(ips, {"127.0.0.1", "10.0.0.5"})


class TestRetag(HostsTestCase):
    def test_retag_regroups_by_box(self):
        # simulate old-style bulk tag
        self.core.add("10.10.11.5", ["control.fleet.forest.htb"], tag="imported")
        self.core.add("10.20.0.5", ["dc01.forest.htb"], tag="imported")
        self.core.add("10.20.0.9", ["fileserver.corp.local"], tag="imported")
        res = self.core.retag()
        self.assertEqual(len(res["retagged"]), 3)
        tags = {e["ip"]: e["tag"] for e in self.core.list_entries()}
        self.assertEqual(tags["10.10.11.5"], "forest")
        self.assertEqual(tags["10.20.0.5"], "forest")
        self.assertEqual(tags["10.20.0.9"], "corp")

    def test_retag_is_noop_when_correct(self):
        self.core.add("10.0.0.1", ["a.htb"])  # already tag=a
        res = self.core.retag()
        self.assertEqual(res["retagged"], [])


class TestBackupRestore(HostsTestCase):
    def test_backup_creates_named_snapshot(self):
        self.core.add("10.0.0.1", ["a.htb"])
        res = self.core.backup(label="checkpoint")
        self.assertIn("checkpoint", res["name"])
        self.assertTrue(res["name"].startswith(self.core.SAVE_PREFIX))
        names = [b["name"] for b in self.core.list_backups()]
        self.assertIn(res["name"], names)

    def test_restore_latest(self):
        self.core.add("10.0.0.1", ["keep.htb"])
        self.core.backup(label="good")
        self.core.clean()  # blow away the block
        self.assertEqual(self.core.list_entries(), [])
        self.core.restore()  # most recent snapshot is the "good" one
        self.assertIn("keep.htb", self.read())

    def test_restore_by_label(self):
        self.core.add("10.0.0.1", ["first.htb"])
        self.core.backup(label="first")
        self.core.add("10.0.0.2", ["second.htb"])
        self.core.backup(label="second")
        self.core.restore("first")
        hosts = {h for e in self.core.list_entries() for h in e["hostnames"]}
        self.assertEqual(hosts, {"first.htb"})

    def test_restore_preserves_inode(self):
        self.core.add("10.0.0.1", ["a.htb"])
        self.core.backup(label="x")
        ino = os.stat(self.hosts).st_ino
        self.core.restore()
        self.assertEqual(os.stat(self.hosts).st_ino, ino)

    def test_restore_without_backups_errors(self):
        with self.assertRaises(self.core.HostsError):
            self.core.restore()


class TestBackupHardening(HostsTestCase):
    def test_backup_dir_and_files_are_private(self):
        """Snapshots may name lab targets: dir must be 0700, files 0600."""
        self.core.add("10.0.0.1", ["a.htb"])  # auto snapshot
        self.core.backup(label="perm-check")  # explicit snapshot
        self.assertEqual(os.stat(self.core.BACKUP_DIR).st_mode & 0o777, 0o700)
        for name in os.listdir(self.core.BACKUP_DIR):
            path = os.path.join(self.core.BACKUP_DIR, name)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_failed_auto_backup_warns_but_does_not_block(self):
        """A snapshot failure must not stop the edit, and must not be silent."""
        blocker = os.path.join(self.tmp, "blocker")
        with open(blocker, "w", encoding="utf-8"):
            pass
        self.core.BACKUP_DIR = os.path.join(blocker, "nope")  # makedirs will fail
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.core.add("10.0.0.1", ["a.htb"])
        self.assertIn("backup failed", stderr.getvalue())
        self.assertIn("a.htb", self.read())  # the edit itself still landed

    def test_explicit_backup_failure_raises(self):
        """Unlike auto snapshots, an explicit `backup` must fail loudly."""
        blocker = os.path.join(self.tmp, "blocker")
        with open(blocker, "w", encoding="utf-8"):
            pass
        self.core.BACKUP_DIR = os.path.join(blocker, "nope")
        with self.assertRaises(OSError):
            self.core.backup(label="x")


class TestParseTokens(HostsTestCase):
    def test_splits_on_comma_and_space(self):
        self.assertEqual(
            self.core.parse_tokens("a.htb, b.htb c.htb"),
            ["a.htb", "b.htb", "c.htb"],
        )

    def test_flattens_a_list_of_mixed_tokens(self):
        self.assertEqual(
            self.core.parse_tokens(["10.0.0.1", "a.htb,b.htb"]),
            ["10.0.0.1", "a.htb", "b.htb"],
        )

    def test_empty_and_none(self):
        self.assertEqual(self.core.parse_tokens(None), [])
        self.assertEqual(self.core.parse_tokens("  ,, "), [])

    def test_add_accepts_a_comma_separated_string(self):
        res = self.core.add("10.10.11.6", "example.local, jobs.example.local")
        self.assertEqual(res["entry"]["hostnames"], ["example.local", "jobs.example.local"])
        self.assertEqual(res["entry"]["tag"], "example")

    def test_split_ip_and_hosts_expands_one_argument(self):
        ip, hosts = self.core.split_ip_and_hosts(["10.10.11.6", "a.htb, b.htb"])
        self.assertEqual(ip, "10.10.11.6")
        self.assertEqual(hosts, ["a.htb", "b.htb"])


class TestSelect(HostsTestCase):
    def setUp(self):
        super().setUp()
        self.core.add("10.10.11.6", ["example.local", "jobs.example.local"])
        self.core.add("10.10.11.5", ["dc01.forest.htb"])

    def test_ip_is_the_default_field(self):
        self.assertEqual(self.core.select("example")["values"], ["10.10.11.6"])

    def test_ip_is_deduplicated_per_entry(self):
        self.assertEqual(self.core.select(["example", "jobs"])["values"], ["10.10.11.6"])

    def test_extra_patterns_narrow_rather_than_widen(self):
        """Each pattern is a refinement, so completion can offer a drill-down."""
        self.assertEqual(self.core.select(["example", "forest"])["values"], [])
        self.assertEqual(
            self.core.select(["example", "jobs"], "host")["values"], ["jobs.example.local"]
        )

    def test_narrowing_applies_to_hostnames_too(self):
        self.core.add("10.10.11.9", ["www.shop.htb", "api.shop.htb", "api.dev.shop.htb"])
        values = self.core.select(["shop", "api"], "host")["values"]
        self.assertEqual(values, ["api.shop.htb", "api.dev.shop.htb"])

    def test_host_field_narrows_to_matching_hostnames(self):
        self.assertEqual(self.core.select("jobs", "host")["values"], ["jobs.example.local"])

    def test_host_field_falls_back_to_all_names_when_matched_by_ip(self):
        values = self.core.select("10.10.11.6", "host")["values"]
        self.assertEqual(values, ["example.local", "jobs.example.local"])

    def test_unknown_field_mentions_the_two_that_exist(self):
        with self.assertRaises(self.core.HostsError) as caught:
            self.core.select("forest", "both")
        self.assertIn("use ip or host", str(caught.exception))

    def test_matches_by_tag_case_insensitively(self):
        self.assertEqual(self.core.select("FOREST")["values"], ["10.10.11.5"])

    def test_no_pattern_returns_everything(self):
        self.assertEqual(self.core.select()["values"], ["10.10.11.6", "10.10.11.5"])

    def test_no_match_is_empty_not_an_error(self):
        data = self.core.select("nothing-here")
        self.assertEqual(data["values"], [])
        self.assertEqual(data["entries"], [])

    def test_unknown_field_raises(self):
        with self.assertRaises(self.core.HostsError):
            self.core.select("example", "port")


class TestSelectIndex(HostsTestCase):
    """A box with several vhosts must not word-split into several arguments."""

    def setUp(self):
        super().setUp()
        self.core.add("10.10.11.6", ["a.example.local", "b.example.local", "c.example.local"])

    def values(self, index):
        return self.core.select("example", "host", index)["values"]

    def test_without_an_index_every_hostname_comes_back(self):
        self.assertEqual(len(self.values(None)), 3)

    def test_single_index_is_one_based(self):
        self.assertEqual(self.values("1"), ["a.example.local"])
        self.assertEqual(self.values(2), ["b.example.local"])

    def test_negative_index_counts_from_the_end(self):
        self.assertEqual(self.values("-1"), ["c.example.local"])
        self.assertEqual(self.values("-3"), ["a.example.local"])

    def test_range(self):
        self.assertEqual(self.values("2-3"), ["b.example.local", "c.example.local"])

    def test_list_keeps_the_order_asked_for_and_dedupes(self):
        self.assertEqual(self.values("3,1,3"), ["c.example.local", "a.example.local"])

    def test_out_of_range_raises_rather_than_silently_dropping(self):
        for spec in ("4", "-4", "0", "1-4"):
            with self.subTest(spec=spec), self.assertRaises(self.core.HostsError):
                self.values(spec)

    def test_malformed_spec_raises(self):
        for spec in ("abc", "1-", "2-1", "-1-2", "1.5"):
            with self.subTest(spec=spec), self.assertRaises(self.core.HostsError):
                self.values(spec)

    def test_index_is_not_applied_to_an_empty_match(self):
        data = self.core.select("nothing-here", "host", "1")
        self.assertEqual(data["values"], [])

    def test_resolve_index_returns_zero_based_positions(self):
        self.assertEqual(self.core.resolve_index("1,3", 3), [0, 2])
        self.assertEqual(self.core.resolve_index("-1", 3), [2])


class TestComplete(HostsTestCase):
    def setUp(self):
        super().setUp()
        self.core.add("10.10.11.6", ["example.local", "jobs.example.local"])
        self.core.add("10.10.11.5", ["dc01.forest.htb"])

    def test_hosts(self):
        self.assertEqual(
            self.core.complete("hosts"),
            ["dc01.forest.htb", "example.local", "jobs.example.local"],
        )

    def test_ips(self):
        self.assertEqual(self.core.complete("ips"), ["10.10.11.5", "10.10.11.6"])

    def test_tags(self):
        self.assertEqual(self.core.complete("tags"), ["example", "forest"])

    def test_selectors_are_hostnames_and_ips_but_never_tags(self):
        selectors = self.core.complete("selectors")
        self.assertIn("dc01.forest.htb", selectors)
        self.assertIn("10.10.11.5", selectors)
        self.assertNotIn("forest", selectors)  # a tag is not a valid `rm` selector

    def test_targets_is_the_union(self):
        targets = self.core.complete("targets")
        self.assertEqual(len(targets), 7)
        self.assertEqual(targets, sorted(targets))
        self.assertIn("forest", targets)
        self.assertIn("10.10.11.5", targets)
        self.assertIn("dc01.forest.htb", targets)

    def test_backups_reverse_the_oldest_first_listing(self):
        self.core.backup(label="one")
        self.core.backup(label="two")
        oldest_first = [b["name"] for b in self.core.list_backups()]
        self.assertGreater(len(oldest_first), 1)
        self.assertEqual(self.core.complete("backups"), oldest_first[::-1])

    def test_backups_without_a_backup_dir(self):
        self.core.BACKUP_DIR = os.path.join(self.tmp, "no-such-dir")
        self.assertEqual(self.core.complete("backups"), [])

    def test_unknown_kind_raises(self):
        with self.assertRaises(self.core.HostsError):
            self.core.complete("nope")

    def test_patterns_scope_the_candidates(self):
        self.assertEqual(
            self.core.complete("hosts", ["example"]),
            ["example.local", "jobs.example.local"],
        )
        self.assertEqual(self.core.complete("ips", ["example"]), ["10.10.11.6"])

    def test_scoping_narrows_with_each_pattern(self):
        self.assertEqual(self.core.complete("hosts", ["example", "jobs"]), ["jobs.example.local"])

    def test_hand_edited_shell_syntax_is_never_offered(self):
        with open(self.hosts, "w", encoding="utf-8") as handle:
            handle.write(
                "# >>> htb-hosts >>>\n"
                "10.10.11.7\tbox.htb $(id).htb  # tag=`id`\n"
                "# <<< htb-hosts <<<\n"
            )
        self.assertEqual(self.core.complete("targets"), ["10.10.11.7", "box.htb"])

    def test_an_already_typed_pattern_is_not_offered_again(self):
        self.assertNotIn("forest", self.core.complete("targets", ["forest"]))

    def test_scope_matching_nothing_offers_nothing(self):
        self.assertEqual(self.core.complete("targets", ["nope"]), [])

    def test_clean_exclude_still_matches_any_pattern(self):
        """`--exclude` is additive, so it must keep using the `any` quantifier."""
        removed = self.core.clean(exclude=["example", "forest"])["removed"]
        self.assertEqual(removed, [])
        self.assertEqual(len(self.core.list_entries()), 2)


class TestDoctorMarkers(HostsTestCase):
    def test_doctor_ok_with_intact_block(self):
        self.core.add("10.0.0.1", ["a.htb"])
        info = self.core.doctor()
        self.assertTrue(info["block_ok"])
        self.assertTrue(info["ok"])

    def test_doctor_flags_orphaned_begin(self):
        with open(self.hosts, "a", encoding="utf-8") as handle:
            handle.write(f"{self.core.BEGIN_LINE}\n10.0.0.1\ta.htb\n")  # no END
        info = self.core.doctor()
        self.assertFalse(info["block_ok"])
        self.assertFalse(info["ok"])
        self.assertIn("marker", info["block_problem"])

    def test_doctor_flags_duplicate_blocks(self):
        self.core.add("10.0.0.1", ["a.htb"])
        with open(self.hosts, "a", encoding="utf-8") as handle:
            handle.write(f"{self.core.BEGIN_LINE}\n{self.core.END}\n")
        info = self.core.doctor()
        self.assertFalse(info["block_ok"])


if __name__ == "__main__":
    unittest.main()
