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

$ nmap -p- $(htb-hosts get forest)      # resolve a box name to its IP, inline
Starting Nmap 7.95 ( https://nmap.org ) ...

$ htb-hosts clean --exclude forest      # done with everything but forest
cleaned 3 entry/entries (all except ~forest)
```

## Features

- 🔓 **No sudo for daily use** — a one-time ACL grant lets you edit `/etc/hosts`
  without `sudo` ever again.
- 🧹 **One-shot cleanup** — everything lives in a managed block. `clean` wipes it;
  `clean --tag forest` drops one box; `clean --exclude forest` keeps one and clears
  the rest.
- 🔎 **Pipe-friendly lookups** — `htb-hosts get forest` prints just the IP, so
  `nmap $(htb-hosts get forest)` and `ffuf -u http://$(htb-hosts get forest --host)`
  just work.
- 🏷️ **Automatic per-box tagging** — hostnames are grouped by box name
  (`dc01.forest.htb` → `forest`), even across multiple IPs.
- 🧰 **Safe by construction** — strict input validation (no injection), `flock`
  locking for writers *and* readers, in-place writes that preserve the ACL, and
  automatic backups on every change (loud warning if a snapshot ever fails).
- ⏪ **Backup & restore** — named snapshots (private: `0700`/`0600`) and
  one-command rollback.
- ⌨️ **Tab completion** — zsh and bash completions for commands, flags, and your
  live hostnames, IPs, tags, and snapshots.
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
your user an ACL on `/etc/hosts`, installs the shell completions, and registers the
MCP server. It works whether
you run it directly or under `sudo` — the steps that need privilege call `sudo`
themselves, and the human user is auto-detected (`SUDO_USER` when under sudo, else
the current user) so the ACL always targets **you**, not root. Override with
`HOSTS_USER=<name>` if needed.

> The one-time install needs privilege (it writes to `/opt` and `/usr/local/bin`
> and sets the ACL). Everyday `htb-hosts` use afterward needs **no** sudo.

New shells pick the completions up automatically; in a shell that is already open,
`exec zsh` (or `rm -f ~/.zcompdump && compinit`) refreshes them.

Already have a pile of entries in `/etc/hosts`? Pull them into the managed block:

```bash
htb-hosts migrate      # imports existing host lines, tagged by box name
```

To uninstall: `make remove` (leaves your entries + ACL) or `make purge` (removes
everything, including the managed block and ACL).

## Usage

```console
htb-hosts add 10.10.11.161 forest.htb dc01.forest.htb   # IP and hosts in any order
htb-hosts add 10.10.11.161 "forest.htb, dc01.forest.htb"  # ...or one quoted string
htb-hosts add www.forest.htb 10.10.11.161 --tag forest  # extra vhost, same box
htb-hosts show                                          # box-grouped overview
htb-hosts show full                                     # also show unmanaged/system lines
htb-hosts get forest                                    # just the IP, for $(...)
htb-hosts get forest --host                             # just the hostnames
htb-hosts get forest --host --first                     # just the first hostname
htb-hosts get forest --host -n                          # numbered, to pick an index
htb-hosts list                                          # flat list of managed entries
htb-hosts rm forest.htb                                 # remove one hostname
htb-hosts clean --tag forest                            # remove a whole box
htb-hosts clean --exclude forest                        # remove everything EXCEPT forest
htb-hosts clean                                         # wipe all managed entries
htb-hosts doctor                                        # check access / show the fix
```

The IP can go anywhere in the arguments — `htb-hosts add forest.htb 10.10.11.161`
works too, and any single argument may carry several names separated by commas or
spaces. Add `--json` to most commands for machine-readable output.

### Command reference

| Command | What it does |
|---------|--------------|
| `add <ip> <host…>` | Add/update an entry (idempotent; merges by IP) |
| `rm <host\|ip>` | Remove a hostname or a whole IP from the block |
| `show [full]` | Pretty, box-grouped overview (`full` adds unmanaged lines) |
| `show <pattern>` / `get <pattern>` | Bare values for matching entries, for `$(…)` |
| `get … --index N` / `--first` | Keep only the Nth value when a box has several |
| `list [--tag T]` | Flat list of managed entries |
| `clean [--tag T \| --exclude P…]` | Remove all, one box, or all-but-matches |
| `retag` | Re-derive every tag from its box name |
| `backup [--label L]` | Save an explicit snapshot |
| `restore [--list] [name]` | Roll back to a snapshot |
| `migrate` | Import existing `/etc/hosts` entries into the block |
| `doctor` | Check access + managed-block integrity; print the exact fix |

### Selecting values for other tools

`get` (alias `select`) prints nothing but the values of the entries matching a
fuzzy pattern — one per line, deduplicated — so they drop straight into another
command. `show <pattern>` is the same thing, for when `show` is already in your
fingers:

```bash
nmap -p- $(htb-hosts get forest)                   # -> 10.10.11.161
ffuf -u "http://$(htb-hosts get forest --host)/FUZZ" -w words.txt
htb-hosts get forest --both                        # IP and every hostname
htb-hosts get                                      # every managed IP
```

The pattern is matched case-insensitively against each entry's IP, tag, and
hostnames — the same fuzzy rule `clean --exclude` uses. `--ip` (the default),
`--host`, and `--both` choose what gets printed; with `--host`, the output narrows
to the hostnames that actually matched (`get jobs --host` → `jobs.example.htb`),
falling back to all of the entry's names when the match came from its IP or tag.

Nothing matched is an error: stdout stays empty and the exit code is `1`, so a
typo can't silently hand an empty target list to the next command.

#### Picking one of several values

A box with a few vhosts has a few hostnames on one IP, and pasting all of them
into `$(...)` word-splits into several arguments — `curl http://a.htb b.htb c.htb/`.
`-n`/`--numbered` shows you what's there, and `-i`/`--index` keeps just the lines
you want (1-based):

```console
$ htb-hosts get forest --host -n
1  forest.htb
2  dc01.forest.htb
3  www.forest.htb

$ curl "http://$(htb-hosts get forest --host --first)/"    # -> forest.htb
$ htb-hosts get forest --host -i 2                         # -> dc01.forest.htb
$ htb-hosts get forest --host -i -1                        # last one
$ htb-hosts get forest --host -i 2-3                       # a range
$ htb-hosts get forest --host -i 1,3                       # a list, in that order
```

`--first` is shorthand for `--index 1`. An out-of-range index is an error (exit
`2`, empty stdout) rather than a silent drop, for the same reason an empty match
is: a pipeline should never quietly get fewer arguments than you asked for.

When you want every value but on **one** line, `--sep` joins them instead:

```bash
nmap $(htb-hosts get forest --sep ,)      # -> nmap 10.10.11.161,10.10.11.162
```

### Tab completion

`make install` drops a zsh completion in `$PREFIX/share/zsh/site-functions` and a
bash one in `$PREFIX/share/bash-completion/completions` — both on the default
search paths, so no rc-file edit is needed. `make completions` installs just those.

Completion is **live**: candidates come from the managed block as it stands right
now, not a static list.

| You type | You get |
|----------|---------|
| `htb-hosts <TAB>` | subcommands, with descriptions |
| `htb-hosts rm <TAB>` | your managed hostnames and IPs |
| `htb-hosts get <TAB>` | hostnames, IPs **and** tags (anything a pattern matches) |
| `htb-hosts clean --tag <TAB>` | your box tags |
| `htb-hosts restore <TAB>` | your snapshots, newest first |
| `htb-hosts add … --<TAB>` | that subcommand's flags |

The scripts get their data from `htb-hosts complete <kind>`, a hidden subcommand
that prints one candidate per line. It is deliberately silent on failure — an
unreadable or malformed `/etc/hosts` degrades completion rather than spraying
errors into your prompt.

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
`htb_hosts_select`, `htb_hosts_clean` (accepts `exclude`), `htb_hosts_retag`,
`htb_hosts_backup`, `htb_hosts_restore`, `htb_hosts_list_backups`.
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
restore it (`doctor` also flags a mangled managed block, e.g. an orphaned marker).

### Security notes

- **The ACL is broader than the tool.** `setfacl -m u:you:rw /etc/hosts` lets
  *any* process running as your user rewrite the whole file and redirect name
  resolution system-wide — the managed block is a convention the tool honors,
  not something the kernel enforces. That's the deliberate trade-off for
  sudo-free use; make it on a personal lab machine, not a shared or production
  one. `make purge` removes the ACL again.
- **Backups outlive `clean`.** Snapshots under `~/.local/state/htb-hosts` keep
  old box/engagement hostnames after you wipe the block. The directory is
  created `0700` (snapshots `0600`); delete snapshots by hand if that history
  matters.

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
migration + tagging, backup/restore (including snapshot permissions and the
warn-but-don't-block behavior on backup failure), `doctor`'s marker-integrity
check, the in-place-edit (inode-preserving) invariant that protects the ACL,
`flock` concurrency, and the full CLI + MCP surfaces.

### Layout

```
src/htb_hosts.py    core library (parse/validate/lock/backup)  → /opt/htb-hosts
src/mcp_server.py   zero-dependency stdio JSON-RPC MCP server   → /opt/htb-hosts
src/htb-hosts       CLI entrypoint                              → /usr/local/bin
completions/        zsh + bash tab completion scripts           → share/{zsh,bash-completion}
tests/              unittest suite (core, CLI, MCP)
Makefile            install / remove / lint / test targets
pyproject.toml      black + pylint config (line length 100)
```

## License

[MIT](LICENSE)
