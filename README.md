# htb-hosts

[![CI](https://github.com/sresarehumantoo/htb-hosts/actions/workflows/ci.yml/badge.svg)](https://github.com/sresarehumantoo/htb-hosts/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

**Sudo-free, cleanable `/etc/hosts` management for Hack The Box (and any lab work).**

Doing HTB, pentests, or CTFs means constantly adding `IP → hostname` lines to
`/etc/hosts` — then hunting them down to clean up later. `htb-hosts` keeps every
entry it manages inside one delimited block, grants you passwordless write access
via a POSIX ACL, and gives you one command to wipe a box (or everything). It ships
as both a **CLI** and a zero-dependency **MCP server**, so you *and* your AI coding
agents can manage hosts the same safe way.

```console
$ htb-hosts add 10.10.11.161 forest.htb dc01.forest.htb
added: 10.10.11.161 forest.htb dc01.forest.htb [tag=forest]

$ htb-hosts show
HTB hosts — 5 managed entries in 3 boxes

  forest (2)
    10.10.11.161    forest.htb
    10.10.11.161    dc01.forest.htb

  machine (1)
    10.129.45.2     machine.htb www.machine.htb

  sequel (2)
    10.10.11.202    sequel.htb dc01.sequel.htb

$ htb-hosts clean --exclude forest      # done with everything but forest
cleaned 3 entry/entries (all except ~forest)
```

## Features

- 🔓 **No sudo for daily use** — a one-time ACL grant lets you edit `/etc/hosts`
  without `sudo` ever again.
- 🧹 **One-shot cleanup** — everything lives in a managed block. `clean` wipes it;
  `clean --tag forest` drops one box; `clean --exclude forest` keeps one and clears
  the rest.
- 🏷️ **Automatic per-box tagging** — hostnames are grouped by box name
  (`dc01.forest.htb` → `forest`), even across multiple IPs.
- 🧰 **Safe by construction** — strict input validation (no injection), `flock`
  locking, in-place writes that preserve the ACL, and automatic backups on every
  change.
- ⏪ **Backup & restore** — named snapshots and one-command rollback.
- 🤖 **MCP server included** — first-class tools for Claude Code / MCP agents.
- 🪶 **Zero runtime dependencies** — pure Python standard library.

## Install

Requirements: Linux, Python 3.11+, and a filesystem with POSIX ACL support
(ext4/xfs/btrfs — the default almost everywhere) plus `setfacl`.

```bash
git clone https://github.com/sresarehumantoo/htb-hosts.git
cd htb-hosts
make install
```

`make install` copies the tool into `/opt/htb-hosts` and `/usr/local/bin`, grants
your user an ACL on `/etc/hosts`, and registers the MCP server. It works whether
you run it directly or under `sudo` — the steps that need privilege call `sudo`
themselves, and the human user is auto-detected (`SUDO_USER` when under sudo, else
the current user) so the ACL always targets **you**, not root. Override with
`HOSTS_USER=<name>` if needed.

> The one-time install needs privilege (it writes to `/opt` and `/usr/local/bin`
> and sets the ACL). Everyday `htb-hosts` use afterward needs **no** sudo.

Already have a pile of entries in `/etc/hosts`? Pull them into the managed block:

```bash
htb-hosts migrate      # imports existing host lines, tagged by box name
```

To uninstall: `make remove` (leaves your entries + ACL) or `make purge` (removes
everything, including the managed block and ACL).

## Usage

```console
htb-hosts add 10.10.11.161 forest.htb dc01.forest.htb   # IP and hosts in any order
htb-hosts add www.forest.htb 10.10.11.161 --tag forest  # extra vhost, same box
htb-hosts show                                          # box-grouped overview
htb-hosts show full                                     # also show unmanaged/system lines
htb-hosts list                                          # flat list of managed entries
htb-hosts rm forest.htb                                 # remove one hostname
htb-hosts clean --tag forest                            # remove a whole box
htb-hosts clean --exclude forest                        # remove everything EXCEPT forest
htb-hosts clean                                         # wipe all managed entries
htb-hosts doctor                                        # check access / show the fix
```

The IP can go anywhere in the arguments — `htb-hosts add forest.htb 10.10.11.161`
works too. Add `--json` to most commands for machine-readable output.

### Command reference

| Command | What it does |
|---------|--------------|
| `add <ip> <host…>` | Add/update an entry (idempotent; merges by IP) |
| `rm <host\|ip>` | Remove a hostname or a whole IP from the block |
| `show [full]` | Pretty, box-grouped overview (`full` adds unmanaged lines) |
| `list [--tag T]` | Flat list of managed entries |
| `clean [--tag T \| --exclude P…]` | Remove all, one box, or all-but-matches |
| `retag` | Re-derive every tag from its box name |
| `backup [--label L]` | Save an explicit snapshot |
| `restore [--list] [name]` | Roll back to a snapshot |
| `migrate` | Import existing `/etc/hosts` entries into the block |
| `doctor` | Report access status and the exact fix |

### Tagging

Every entry carries a `tag`, defaulting to the **box name** derived from its first
hostname (`dc01.forest.htb` → `forest`; `fileserver.corp.local` → `corp`). That
groups a whole box — even across several IPs — under one tag, so `clean --tag
forest` clears it in one go. Re-derive tags for existing entries any time:

```bash
htb-hosts retag
```

### Keep-all-but: fuzzy `--exclude`

The inverse of `--tag`: remove everything **except** fuzzy matches. Patterns are
case-insensitive substrings tested against each entry's IP, tag, and hostnames,
and `--exclude` is repeatable:

```bash
htb-hosts clean --exclude forest             # keep the forest box, clear the rest
htb-hosts clean --exclude forest --exclude vpn
```

### Backup & restore

Every write drops an automatic snapshot (last 5 kept). You can also take named
snapshots that are never auto-pruned, and roll back:

```bash
htb-hosts backup --label before-pivot   # explicit snapshot
htb-hosts restore --list                # show snapshots (auto + saved)
htb-hosts restore                       # roll back to the most recent
htb-hosts restore before-pivot          # roll back to a named/substring match
```

## MCP integration

`htb-hosts` includes a stdio MCP server so MCP-aware agents (e.g. Claude Code) can
manage hosts natively. `make install` registers it; to register by hand:

```bash
claude mcp add -s user htb-hosts -- python3 /opt/htb-hosts/mcp_server.py
```

<details>
<summary>Tools exposed (server name <code>htb-hosts</code>)</summary>

`htb_hosts_add`, `htb_hosts_remove`, `htb_hosts_list`, `htb_hosts_show`,
`htb_hosts_clean` (accepts `exclude`), `htb_hosts_retag`, `htb_hosts_backup`,
`htb_hosts_restore`, `htb_hosts_list_backups`.
</details>

## How it works

Everything the tool manages lives between two markers:

```
# >>> htb-hosts >>>  (managed by htb-hosts; run `htb-hosts clean` to remove)
10.10.11.161	forest.htb dc01.forest.htb  # tag=forest added=2026-07-11
# <<< htb-hosts <<<
```

Lines outside the markers (localhost, IPv6, anything you added by hand) are never
touched.

Write access is granted once via a POSIX ACL:

```bash
setfacl -m u:youruser:rw /etc/hosts
```

Because a non-owner can't re-apply an ACL, the tool edits `/etc/hosts` **in place**
(open `r+`, rewrite, truncate) under an `flock` — it never renames a temp file over
it, which would drop the ACL and flip ownership. All input is strictly validated so
nothing can inject extra lines, and the prior contents are snapshotted before every
change. If the ACL is ever lost, `htb-hosts doctor` prints the exact command to
restore it.

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `HTB_HOSTS_FILE` | `/etc/hosts` | Target hosts file (handy for testing) |
| `HTB_HOSTS_BACKUP_DIR` | `~/.local/state/htb-hosts` | Where snapshots are stored |
| `HTB_HOSTS_KEEP` | *(empty)* | Comma-separated domain substrings to keep **outside** the block on `migrate` (e.g. a homelab domain: `HTB_HOSTS_KEEP=corp.lan,homelab.internal`) |

## Development

```bash
make test     # stdlib unittest suite (no root, /etc/hosts untouched)
make smoke    # quick end-to-end CLI check against a throwaway file
make lint     # black --check + pylint (gated at 10.00/10)
make help     # list all targets
```

Tests load the modules directly from `src/`, so they exercise the in-repo copy
regardless of what's installed under `/opt`, and each test runs against a throwaway
temp file — your real `/etc/hosts` is never touched. Coverage includes input
validation/injection rejection, add/merge/rehome/idempotency, clean/exclude,
migration + tagging, backup/restore, the in-place-edit (inode-preserving) invariant
that protects the ACL, `flock` concurrency, and the full CLI + MCP surfaces.

### Layout

```
src/htb_hosts.py    core library (parse/validate/lock/backup)  → /opt/htb-hosts
src/mcp_server.py   zero-dependency stdio JSON-RPC MCP server   → /opt/htb-hosts
src/htb-hosts       CLI entrypoint                              → /usr/local/bin
tests/              unittest suite (core, CLI, MCP)
Makefile            install / remove / lint / test targets
pyproject.toml      black + pylint config (line length 100)
```

## License

[MIT](LICENSE)
