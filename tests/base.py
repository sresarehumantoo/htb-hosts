"""Shared TestCase base: points the core lib at a throwaway hosts file."""

import os
import shutil
import tempfile
import unittest

import loader

INFRA = (
    "127.0.0.1\tlocalhost\n"
    "127.0.1.1\tmyhost\n"
    "::1 localhost ip6-localhost ip6-loopback\n"
    "ff02::1 ip6-allnodes\n"
    "10.20.0.9 nas.homelab.lan\n"  # a hand-managed host outside the block
)


class HostsTestCase(unittest.TestCase):
    """Base case that isolates HOSTS_FILE and BACKUP_DIR to a temp dir."""

    initial = INFRA

    def setUp(self):
        self.core = loader.load_core()
        self.tmp = tempfile.mkdtemp(prefix="htbtest-")
        self.hosts = os.path.join(self.tmp, "hosts")
        with open(self.hosts, "w", encoding="utf-8") as handle:
            handle.write(self.initial)
        # Redirect the module globals the code reads at call time.
        self.core.HOSTS_FILE = self.hosts
        self.core.BACKUP_DIR = os.path.join(self.tmp, "backups")
        self.core._KEEP_EXTRA = []  # isolate the HTB_HOSTS_KEEP setting per test

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def read(self) -> str:
        with open(self.hosts, encoding="utf-8") as handle:
            return handle.read()

    def block_lines(self) -> list[str]:
        """Return the entry lines currently inside the managed block."""
        out, inside = [], False
        for line in self.read().splitlines():
            if line.startswith(self.core.BEGIN):
                inside = True
                continue
            if line.startswith(self.core.END):
                inside = False
                continue
            if inside and line.strip():
                out.append(line)
        return out
