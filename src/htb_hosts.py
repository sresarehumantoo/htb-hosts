"""htb_hosts - sudo-free, cleanable /etc/hosts management for HTB.

Core library shared by the CLI (/usr/local/bin/htb-hosts) and the MCP server
(/opt/htb-hosts/mcp_server.py). Everything the tool owns lives inside a single
delimited "managed block"; lines outside the markers are never touched.

Key constraint: on the real /etc/hosts, unprivileged access is granted to owen
via a POSIX ACL (`setfacl -m u:owen:rw /etc/hosts`). owen is NOT the file owner
and therefore cannot re-apply an ACL, so we must edit the file IN PLACE
(open r+, rewrite, truncate) and never write-temp-then-rename -- a rename would
drop the ACL and flip ownership back to whoever ran the tool.
"""

from __future__ import annotations

import datetime
import fcntl
import ipaddress
import os
import re
import sys
from typing import Any, Callable, Optional

HOSTS_FILE: str = os.environ.get("HTB_HOSTS_FILE", "/etc/hosts")

BEGIN: str = "# >>> htb-hosts >>>"
END: str = "# <<< htb-hosts <<<"
BEGIN_LINE: str = f"{BEGIN}  (managed by htb-hosts; run `htb-hosts clean` to remove)"

BACKUP_DIR: str = os.environ.get("HTB_HOSTS_BACKUP_DIR") or os.path.expanduser(
    "~/.local/state/htb-hosts"
)
BACKUP_KEEP: int = 5
BACKUP_PREFIX: str = "hosts.bak."  # automatic snapshots, pruned to BACKUP_KEEP
SAVE_PREFIX: str = "hosts.save."  # explicit `backup` snapshots, never auto-pruned

# Strict hostname validation: one or more DNS labels joined by dots. Rejects
# anything with whitespace, '#', or other characters that could inject a new
# line/comment into the hosts file.
_LABEL = r"[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?"
_HOSTNAME_RE = re.compile(rf"^{_LABEL}(?:\.{_LABEL})*$")
_ENTRY_RE = re.compile(r"^\s*(?P<ip>\S+)\s+(?P<hosts>[^#]+?)\s*(?:#\s*(?P<comment>.*))?$")

# Separators accepted inside a single host argument, so one shell-quoted string
# can carry several names: "a.htb, b.htb" / "a.htb,b.htb" / "a.htb b.htb".
_TOKEN_SEP_RE = re.compile(r"[,;\s]+")

# Hostnames/patterns that always stay OUTSIDE the managed block on migrate.
_INFRA_TOKENS = ("localhost", "ip6-", "ff02::", "ff00::")

# Extra substrings (case-insensitive) to also keep outside the managed block on
# migrate — e.g. a homelab/corp domain you manage by hand. Comma-separated.
#   HTB_HOSTS_KEEP="corp.lan,homelab.internal"
_KEEP_EXTRA = [
    s.strip().lower() for s in os.environ.get("HTB_HOSTS_KEEP", "").split(",") if s.strip()
]

Dict = dict[str, Any]


class HostsError(Exception):
    """User-facing error (bad input, permission problem, etc.)."""


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def validate_ip(ip: str) -> str:
    """Return the normalized IP string, or raise HostsError."""
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError as exc:
        raise HostsError(f"invalid IP address: {ip!r}") from exc
    # ipaddress accepts any characters in an IPv6 scope id (fe80::1%<anything>),
    # newlines and '#' included, and str() echoes them back into the file.
    scope = getattr(addr, "scope_id", None)
    if scope is not None and not re.match(r"^[A-Za-z0-9_.-]+$", scope):
        raise HostsError(f"invalid IP address: {ip!r}")
    return str(addr)


def validate_hostname(name: str) -> str:
    """Return the trimmed hostname if it is a valid DNS name, else raise."""
    name = name.strip()
    if not name or len(name) > 253 or not _HOSTNAME_RE.match(name):
        raise HostsError(f"invalid hostname: {name!r}")
    return name


def validate_tag(tag: str) -> str:
    """Return the trimmed tag if it contains only safe characters, else raise."""
    tag = tag.strip()
    if not tag or not re.match(r"^[A-Za-z0-9._-]+$", tag):
        raise HostsError(f"invalid tag: {tag!r}")
    return tag


def default_tag(hostname: str) -> str:
    """Derive a tag from a hostname: app.blurry.htb -> blurry, sequel.htb -> sequel."""
    labels = hostname.lower().split(".")
    if len(labels) >= 2:
        return labels[-2]
    return labels[0]


def parse_tokens(value: Any) -> list[str]:
    """Flatten a string or list of strings into individual tokens.

    Each item is split on commas, semicolons and whitespace, so a single quoted
    argument can carry several hostnames:
        "example.local, jobs.example.local"  ->  two tokens
    Order is preserved; empty fragments are dropped.
    """
    items = [value] if isinstance(value, str) else list(value or [])
    out: list[str] = []
    for item in items:
        for part in _TOKEN_SEP_RE.split(str(item).strip()):
            if part:
                out.append(part)
    return out


def split_ip_and_hosts(tokens: list[str]) -> tuple[str, list[str]]:
    """Split mixed positional tokens (in any order) into (ip, hostnames).

    Lets callers pass `machine.htb 10.129.45.2` or `10.129.45.2 machine.htb`
    interchangeably, and accepts comma/space separated groups inside a single
    argument. Raises HostsError unless exactly one token is an IP.
    """
    ips: list[str] = []
    hosts: list[str] = []
    for token in parse_tokens(tokens):
        try:
            ipaddress.ip_address(token.strip())
            ips.append(token.strip())
        except ValueError:
            hosts.append(token)
    if not ips:
        raise HostsError(f"no IP address found in: {' '.join(parse_tokens(tokens))}")
    if len(ips) > 1:
        raise HostsError(f"multiple IPs given ({', '.join(ips)}); expected exactly one")
    return ips[0], hosts


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #
class Entry:
    """A single managed hosts line: one IP, one or more hostnames, metadata."""

    __slots__ = ("ip", "hostnames", "tag", "added")

    def __init__(
        self,
        ip: str,
        hostnames: list[str],
        tag: Optional[str],
        added: Optional[str],
    ) -> None:
        self.ip = ip
        self.hostnames = list(hostnames)
        self.tag = tag
        self.added = added

    def render(self) -> str:
        """Render this entry as a single /etc/hosts line."""
        hosts = " ".join(self.hostnames)
        meta: list[str] = []
        if self.tag:
            meta.append(f"tag={self.tag}")
        if self.added:
            meta.append(f"added={self.added}")
        comment = f"  # {' '.join(meta)}" if meta else ""
        return f"{self.ip}\t{hosts}{comment}"

    def to_dict(self) -> Dict:
        """Return a JSON-serializable view of this entry."""
        return {
            "ip": self.ip,
            "hostnames": list(self.hostnames),
            "tag": self.tag,
            "added": self.added,
        }


def _parse_entry(line: str) -> Optional[Entry]:
    """Parse one raw hosts line into an Entry, or None if it is not an entry."""
    if not line.strip() or line.lstrip().startswith("#"):
        return None  # blank or comment line, never an entry
    match = _ENTRY_RE.match(line)
    if not match:
        return None
    ip = match.group("ip")
    hosts = match.group("hosts").split()
    if not hosts:
        return None
    comment = match.group("comment") or ""
    tag_match = re.search(r"\btag=(\S+)", comment)
    added_match = re.search(r"\badded=(\S+)", comment)
    tag = tag_match.group(1) if tag_match else None
    added = added_match.group(1) if added_match else None
    return Entry(ip, hosts, tag, added)


# --------------------------------------------------------------------------- #
# File read / split / write
# --------------------------------------------------------------------------- #
Mutator = Callable[
    [list[str], list[Entry], list[str]],
    tuple[list[str], list[Entry], list[str], Any],
]


def _today() -> str:
    return datetime.date.today().isoformat()


def _read() -> str:
    """Return the full contents of HOSTS_FILE, or '' if it does not exist.

    Takes a shared flock so reads never observe a write in progress (writers
    hold an exclusive lock in _locked_write and edit in place).
    """
    if not os.path.exists(HOSTS_FILE):
        return ""
    with open(HOSTS_FILE, encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        return handle.read()


def _split(text: str) -> tuple[list[str], list[Entry], list[str]]:
    """Return (pre_lines, block_entries, post_lines).

    pre/post are the raw lines outside the managed block (markers excluded).
    If no block exists, block_entries is [] and post_lines is [].
    """
    lines = text.splitlines()
    begin_idx: Optional[int] = None
    end_idx: Optional[int] = None
    for i, line in enumerate(lines):
        if begin_idx is None and line.startswith(BEGIN):
            begin_idx = i
        elif begin_idx is not None and line.startswith(END):
            end_idx = i
            break
    if begin_idx is None or end_idx is None:
        return lines, [], []
    pre = lines[:begin_idx]
    post = lines[end_idx + 1 :]
    entries: list[Entry] = []
    for line in lines[begin_idx + 1 : end_idx]:
        if not line.strip():
            continue
        entry = _parse_entry(line)
        if entry:
            entries.append(entry)
    return pre, entries, post


def _compose(pre: list[str], entries: list[Entry], post: list[str]) -> str:
    """Reassemble the file text from its parts, emitting the managed block."""
    out: list[str] = list(pre)
    # Trim trailing blank lines from pre so the block sits cleanly.
    while out and out[-1].strip() == "":
        out.pop()
    if entries:
        if out:
            out.append("")
        out.append(BEGIN_LINE)
        out.extend(entry.render() for entry in entries)
        out.append(END)
    # Preserve post content (rare) below the block.
    post_trimmed = list(post)
    while post_trimmed and post_trimmed[0].strip() == "":
        post_trimmed.pop(0)
    if post_trimmed:
        out.append("")
        out.extend(post_trimmed)
    return "\n".join(out) + "\n"


def _write_snapshot(path: str, text: str) -> None:
    """Write a snapshot file readable only by its owner (it may name lab targets)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def _backup(text: str) -> None:
    """Write a timestamped backup and prune to the last BACKUP_KEEP; best-effort."""
    try:
        os.makedirs(BACKUP_DIR, mode=0o700, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        _write_snapshot(os.path.join(BACKUP_DIR, f"{BACKUP_PREFIX}{stamp}"), text)
        backups = sorted(b for b in os.listdir(BACKUP_DIR) if b.startswith(BACKUP_PREFIX))
        for old in backups[:-BACKUP_KEEP]:
            os.remove(os.path.join(BACKUP_DIR, old))
    except OSError as exc:
        # Never block the edit on a failed snapshot, but never fail silently
        # either: a quiet miss removes the safety net exactly when it matters.
        print(f"warning: automatic backup failed ({exc}); continuing without it", file=sys.stderr)


def _permission_hint() -> str:
    """Return a human-readable hint for restoring write access via setfacl."""
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "owen"
    return (
        f"cannot write {HOSTS_FILE} without privileges. Grant access once as root:\n"
        f"    sudo setfacl -m u:{user}:rw {HOSTS_FILE}\n"
        f"(then re-run; no sudo needed afterwards)"
    )


def _locked_write(builder: Callable[[str], tuple[str, Any]]) -> Any:
    """Lock HOSTS_FILE, hand its current text to `builder`, write the result back.

    `builder(original_text)` returns `(new_text, result)`. The file is rewritten
    only when the text differs, and always via an in-place truncate+write (never a
    rename) so the POSIX ACL granting owen access survives. The prior content is
    backed up before any change.
    """
    try:
        fd = os.open(HOSTS_FILE, os.O_RDWR | os.O_CREAT, 0o644)
    except PermissionError as exc:
        raise HostsError(_permission_hint()) from exc
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with os.fdopen(fd, "r+", closefd=False, encoding="utf-8") as handle:
            original = handle.read()
            new_text, result = builder(original)
            if new_text != original:
                _backup(original)
                handle.seek(0)
                handle.write(new_text)
                handle.truncate()
                handle.flush()
                os.fsync(handle.fileno())
            return result
    finally:
        os.close(fd)


def _transaction(mutate: Mutator) -> Any:
    """Apply a (pre, entries, post) mutation and rewrite the managed block."""

    def builder(original: str) -> tuple[str, Any]:
        pre, entries, post = _split(original)
        pre, entries, post, result = mutate(pre, entries, post)
        return _compose(pre, entries, post), result

    return _locked_write(builder)


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #
def add(ip: str, hostnames: Any, tag: Optional[str] = None) -> Dict:
    """Add or update a managed entry; idempotent upsert keyed on IP.

    `hostnames` may be a list or a single comma/space separated string.
    Returns {"entry": <dict>, "warnings": [<str>, ...]}.
    """
    ip = validate_ip(ip)
    hostnames = [validate_hostname(h) for h in parse_tokens(hostnames)]
    if not hostnames:
        raise HostsError("at least one hostname is required")
    tag = validate_tag(tag) if tag else default_tag(hostnames[0])
    warnings: list[str] = []

    def mutate(
        pre: list[str], entries: list[Entry], post: list[str]
    ) -> tuple[list[str], list[Entry], list[str], Dict]:
        # Warn if any hostname is already defined OUTSIDE the managed block.
        outside: set[str] = set()
        for line in pre + post:
            entry = _parse_entry(line)
            if entry:
                outside.update(h.lower() for h in entry.hostnames)
        for host in hostnames:
            if host.lower() in outside:
                warnings.append(f"{host} also exists outside the managed block")

        wanted = {h.lower() for h in hostnames}
        # Drop these hostnames from any existing managed entry (re-home them).
        for entry in entries:
            entry.hostnames = [h for h in entry.hostnames if h.lower() not in wanted]
        entries[:] = [e for e in entries if e.hostnames]

        # Merge into an existing entry for this IP, else create one.
        target = next((e for e in entries if e.ip == ip), None)
        if target is None:
            target = Entry(ip, [], tag, _today())
            entries.append(target)
        have = {h.lower() for h in target.hostnames}
        for host in hostnames:
            if host.lower() not in have:
                target.hostnames.append(host)
        target.tag = tag
        return pre, entries, post, target.to_dict()

    entry = _transaction(mutate)
    return {"entry": entry, "warnings": warnings}


def remove(selector: str) -> Dict:
    """Remove managed entries matching a hostname or IP. Returns {"removed": [...]}."""
    selector = selector.strip()
    removed: list[Dict] = []

    def mutate(
        pre: list[str], entries: list[Entry], post: list[str]
    ) -> tuple[list[str], list[Entry], list[str], list[Dict]]:
        sel = selector.lower()
        for entry in list(entries):
            if entry.ip == selector:
                removed.append(entry.to_dict())
                entries.remove(entry)
                continue
            kept = [h for h in entry.hostnames if h.lower() != sel]
            if len(kept) != len(entry.hostnames):
                removed.append({"ip": entry.ip, "hostname": selector})
                entry.hostnames = kept
        entries[:] = [e for e in entries if e.hostnames]
        return pre, entries, post, removed

    return {"removed": _transaction(mutate)}


def _entry_matches(entry: Entry, patterns: list[str], require_all: bool = False) -> bool:
    """Fuzzy match against the entry's IP, tag and hostnames, case-insensitively
    (e.g. 'forest' matches WEB.FOREST.HTB).

    `require_all` picks the quantifier, and the two callers genuinely differ:
    `clean --exclude` keeps an entry matching *any* pattern (the flag is
    repeatable and additive), while `select` treats each extra pattern as a
    refinement, so every one of them has to match.
    """
    haystack = " ".join([entry.ip, entry.tag or "", *entry.hostnames]).lower()
    quantifier = all if require_all else any
    return quantifier(p in haystack for p in patterns)


def clean(
    tag: Optional[str] = None,
    exclude: Optional[list[str]] = None,
    match: Any = None,
    dry_run: bool = False,
) -> Dict:
    """Remove managed entries.

    - no args: wipe the whole managed block
    - `tag`: remove only entries with that exact tag
    - `match`: remove entries fuzzily matching *every* pattern — the same
      selector language as `select`, so `clean --match example web` removes
      exactly what `get example web` shows
    - `exclude`: remove everything EXCEPT entries fuzzily matching *any* pattern
      (additive keep-list; note the opposite quantifier to `match`)

    The three are mutually exclusive. `dry_run` computes the same result without
    touching the file, so a caller can show the user what is about to go.
    """
    modes = [bool(tag), bool(exclude), bool(match)]
    if sum(modes) > 1:
        raise HostsError("use only one of tag, exclude, or match")
    if tag:
        tag = validate_tag(tag)
    excludes = [p.strip().lower() for p in (exclude or []) if p.strip()]
    matches = [p.lower() for p in parse_tokens(match)]

    def keep(entry: Entry) -> bool:
        if excludes:
            return _entry_matches(entry, excludes)  # keep the excluded ones
        if matches:
            return not _entry_matches(entry, matches, require_all=True)
        if tag is not None:
            return entry.tag != tag
        return False  # no filter -> remove everything

    if dry_run:
        _, entries, _ = _split(_read())
        return {"removed": [e.to_dict() for e in entries if not keep(e)]}

    def mutate(
        pre: list[str], entries: list[Entry], post: list[str]
    ) -> tuple[list[str], list[Entry], list[str], list[Dict]]:
        removed = [e.to_dict() for e in entries if not keep(e)]
        entries[:] = [e for e in entries if keep(e)]
        return pre, entries, post, removed

    return {"removed": _transaction(mutate)}


def list_entries(tag: Optional[str] = None) -> list[Dict]:
    """Return the managed entries as dicts, optionally filtered by `tag`."""
    if tag:
        tag = validate_tag(tag)
    _, entries, _ = _split(_read())
    if tag:
        entries = [e for e in entries if e.tag == tag]
    return [e.to_dict() for e in entries]


def outside_entries() -> list[Dict]:
    """Return host entries that live OUTSIDE the managed block (localhost, IPv6,
    anything hand-added) — i.e. everything the tool does not manage."""
    pre, _, post = _split(_read())
    out: list[Dict] = []
    for line in pre + post:
        entry = _parse_entry(line)
        if entry:
            out.append(entry.to_dict())
    return out


def show_data(full: bool = False, patterns: Any = None) -> Dict:
    """Structured overview of managed hosts, both flat ("entries") and grouped
    by box ("groups").

    `patterns` narrows to the entries they select, using the same rule as
    `select`, so a pattern means one thing everywhere in the tool. With
    `full=True`, the unmanaged/system entries are included too.
    """
    pats = [p.lower() for p in parse_tokens(patterns)]
    _, entries, _ = _split(_read())
    if pats:
        entries = [e for e in entries if _entry_matches(e, pats, require_all=True)]
    managed = [e.to_dict() for e in entries]
    groups: dict[str, list[Dict]] = {}
    for entry in managed:
        groups.setdefault(entry["tag"] or "(untagged)", []).append(entry)
    data: Dict = {
        "patterns": pats,
        "managed_count": len(managed),
        "box_count": len(groups),
        "entries": managed,
        "groups": groups,
    }
    if full:
        data["unmanaged"] = outside_entries()
    return data


SELECT_FIELDS = {
    "ip": "ip",
    "ips": "ip",
    "host": "host",
    "hosts": "host",
    "hostname": "host",
    "hostnames": "host",
}


def _selected_hostnames(entry: Entry, patterns: list[str]) -> list[str]:
    """Return the entry hostnames worth emitting for `patterns`.

    Narrow to the hostnames matching every pattern (so `select example jobs`
    yields only jobs.example.htb), but fall back to all of them when none match
    — the entry got here on its IP or tag, and those names are the answer.
    """
    if not patterns:
        return list(entry.hostnames)
    hit = [h for h in entry.hostnames if all(p in h.lower() for p in patterns)]
    return hit or list(entry.hostnames)


# One index spec item: "2", "-1" (from the end) or the inclusive range "2-4".
_INDEX_RE = re.compile(r"^(?P<from>-?\d+)(?:-(?P<to>\d+))?$")


def resolve_index(spec: Any, count: int) -> list[int]:
    """Turn a 1-based index spec into 0-based positions into a list of `count`.

    Accepts an int, or a string/list of comma or space separated items: a single
    index ("2"), an index from the end ("-1") or an inclusive range ("2-4").
    Positions are returned in the order asked for, deduplicated. Out-of-range is
    an error rather than a silent drop: a pipeline must not quietly receive
    fewer arguments than the command line asked for.
    """
    positions: list[int] = []
    for token in parse_tokens([str(spec)] if isinstance(spec, int) else spec):
        match = _INDEX_RE.match(token)
        if not match:
            raise HostsError(f"invalid index: {token!r} (use e.g. 2, -1, or 2-4)")
        start, stop = match.group("from"), match.group("to")
        if stop is None:
            wanted = [int(start)]
        elif int(start) < 1:
            raise HostsError(f"invalid range: {token!r} (ranges are 1-based and positive)")
        elif int(start) > int(stop):
            raise HostsError(f"invalid range: {token!r} (start is past the end)")
        else:
            wanted = list(range(int(start), int(stop) + 1))
        for num in wanted:
            if num == 0:
                raise HostsError("invalid index: 0 (indexes are 1-based)")
            pos = num - 1 if num > 0 else count + num
            if not 0 <= pos < count:
                raise HostsError(f"index {num} is out of range ({count} value(s) available)")
            if pos not in positions:
                positions.append(pos)
    return positions


def select(patterns: Any = None, field: str = "ip", index: Any = None) -> Dict:
    """Pick managed entries by fuzzy pattern and return bare values for piping.

    `patterns` is a string or list (comma/space separated); each is matched
    case-insensitively against an entry's IP, tag and hostnames. Several patterns
    **narrow**: every one has to match, so `get example jobs` drills into the
    example box rather than adding a second one. `field` chooses what lands in
    "values": "ip" (default) or "host". Values are deduplicated in
    first-seen order, so `nmap $(htb-hosts get example)` gets a clean target
    list.

    `index` narrows "values" to the given 1-based line(s) — the answer to a box
    whose several hostnames would otherwise word-split into several arguments
    (see `resolve_index` for the accepted forms). No match is not an error here:
    "values" is just empty, and `index` is not applied to an empty list.
    """
    key = SELECT_FIELDS.get((field or "ip").strip().lower())
    if key is None:
        raise HostsError(f"unknown field: {field!r} (use ip or host)")
    pats = [p.lower() for p in parse_tokens(patterns)]
    _, entries, _ = _split(_read())
    matched = [e for e in entries if not pats or _entry_matches(e, pats, require_all=True)]
    values: list[str] = []
    for entry in matched:
        if key == "ip":
            values.append(entry.ip)
        else:
            values.extend(_selected_hostnames(entry, pats))
    seen: set[str] = set()
    unique = [v for v in values if not (v in seen or seen.add(v))]
    if index is not None and unique:
        unique = [unique[pos] for pos in resolve_index(index, len(unique))]
    return {
        "patterns": pats,
        "field": key,
        "index": index,
        "entries": [e.to_dict() for e in matched],
        "values": unique,
    }


COMPLETE_KINDS = ("hosts", "ips", "tags", "selectors", "targets", "backups")


def complete(kind: str, patterns: Any = None) -> list[str]:
    """Return candidate words for shell tab completion.

    "hosts"/"ips"/"tags" are the managed hostnames, IPs and tags. "selectors" is
    hostnames + IPs — what `add` and `rm` name an entry by. "targets" adds tags
    on top, which is what a fuzzy pattern (`get`, `clean --exclude`) may match.
    "backups" is the snapshot names, newest first.

    `patterns` scopes the result to the entries those patterns already select,
    using exactly `select`'s narrowing rule — so completing the next word of
    `get example --host` offers the hostnames inside the example box, and
    accepting one does what the menu implied. Patterns already typed are dropped
    from the candidates. Everything is deduplicated; only "backups" keeps a
    meaningful order, the rest are sorted so the completion menu is stable.
    """
    if kind == "backups":
        return [b["name"] for b in reversed(list_backups())]
    if kind not in COMPLETE_KINDS:
        raise HostsError(f"unknown completion kind: {kind!r} (use {', '.join(COMPLETE_KINDS)})")
    pats = [p.lower() for p in parse_tokens(patterns)]
    _, entries, _ = _split(_read())
    words: set[str] = set()
    for entry in entries:
        if pats and not _entry_matches(entry, pats, require_all=True):
            continue
        if kind in ("hosts", "selectors", "targets"):
            words.update(_selected_hostnames(entry, pats))
        if kind in ("ips", "selectors", "targets"):
            words.add(entry.ip)
        if kind in ("tags", "targets") and entry.tag:
            words.add(entry.tag)
    return sorted(w for w in words if w.lower() not in pats)


def _is_infra(line: str) -> bool:
    """True for lines that must stay outside the managed block on migrate."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return True  # blank/comment lines are structural, keep them in place
    low = stripped.lower()
    if any(tok in low for tok in _INFRA_TOKENS):
        return True
    if low.startswith(("::1", "127.0.0.1", "127.0.1.1")):
        return True
    if any(keep in low for keep in _KEEP_EXTRA):  # user-configured domains to keep out
        return True
    return False


def migrate() -> Dict:
    """One-time import of free-form host entries into the managed block.

    Infra lines (localhost/IPv6, plus any HTB_HOSTS_KEEP domains) and
    comments/blanks stay in place; every other host entry is moved into the block
    and tagged by box name
    (derived from its first hostname, e.g. dc01.forest.htb -> tag=forest).
    """
    imported: list[Dict] = []

    def mutate(
        pre: list[str], entries: list[Entry], post: list[str]
    ) -> tuple[list[str], list[Entry], list[str], list[Dict]]:
        new_pre: list[str] = []
        existing = {h.lower() for e in entries for h in e.hostnames}
        for line in pre:
            if _is_infra(line):
                new_pre.append(line)
                continue
            entry = _parse_entry(line)
            if entry is None:
                new_pre.append(line)
                continue
            fresh = [h for h in entry.hostnames if h.lower() not in existing]
            if not fresh:
                continue  # fully duplicate, drop the free-form line
            existing.update(h.lower() for h in fresh)
            tag = entry.tag or default_tag(fresh[0])
            entries.append(Entry(entry.ip, fresh, tag, entry.added or _today()))
            imported.append({"ip": entry.ip, "hostnames": fresh, "tag": tag})
        return new_pre, entries, post, imported

    return {"imported": _transaction(mutate)}


def retag() -> Dict:
    """Recompute every managed entry's tag from its first hostname (box name).

    Fixes entries that were bulk-tagged (e.g. the old tag=imported) so that
    tag-based operations group by box. Returns {"retagged": [<changes>]}.
    """
    changed: list[Dict] = []

    def mutate(
        pre: list[str], entries: list[Entry], post: list[str]
    ) -> tuple[list[str], list[Entry], list[str], list[Dict]]:
        for entry in entries:
            if not entry.hostnames:
                continue
            new_tag = default_tag(entry.hostnames[0])
            if new_tag != entry.tag:
                changed.append({"ip": entry.ip, "old": entry.tag, "new": new_tag})
                entry.tag = new_tag
        return pre, entries, post, changed

    return {"retagged": _transaction(mutate)}


def _marker_problem(text: str) -> Optional[str]:
    """Describe a malformed managed block (orphaned/duplicate markers), or None.

    A truncated or hand-mangled block makes _split treat its entries as
    unmanaged, so the next write would append a second block. Surface that in
    `doctor` instead of letting it bite silently.
    """
    markers = [
        "BEGIN" if line.startswith(BEGIN) else "END"
        for line in text.splitlines()
        if line.startswith((BEGIN, END))
    ]
    if markers in ([], ["BEGIN", "END"]):
        return None
    return (
        f"malformed managed block (marker sequence: {' '.join(markers)}); "
        "fix the markers by hand or roll back with `htb-hosts restore`"
    )


def doctor() -> Dict:
    """Report writability/ACL status and, if broken, the exact setfacl fix."""
    info: Dict = {"hosts_file": HOSTS_FILE, "exists": os.path.exists(HOSTS_FILE)}
    info["writable"] = os.access(HOSTS_FILE, os.W_OK) if info["exists"] else None
    text = _read()
    _, entries, _ = _split(text)
    info["managed_entries"] = len(entries)
    problem = _marker_problem(text)
    info["block_ok"] = problem is None
    if problem:
        info["block_problem"] = problem
    info["ok"] = bool(info["exists"] and info["writable"] and info["block_ok"])
    if not (info["exists"] and info["writable"]):
        info["fix"] = _permission_hint()
    return info


# --------------------------------------------------------------------------- #
# Backup / restore
# --------------------------------------------------------------------------- #
def list_backups() -> list[Dict]:
    """Return available snapshots (auto + explicit), oldest first."""
    if not os.path.isdir(BACKUP_DIR):
        return []
    out: list[Dict] = []
    for name in os.listdir(BACKUP_DIR):
        if not (name.startswith(BACKUP_PREFIX) or name.startswith(SAVE_PREFIX)):
            continue
        path = os.path.join(BACKUP_DIR, name)
        stat = os.stat(path)
        out.append(
            {
                "name": name,
                "path": path,
                "bytes": stat.st_size,
                "mtime": stat.st_mtime,
                "kind": "save" if name.startswith(SAVE_PREFIX) else "auto",
            }
        )
    out.sort(key=lambda b: b["mtime"])
    return out


def backup(label: Optional[str] = None) -> Dict:
    """Save an explicit snapshot of the current hosts file (never auto-pruned).

    An optional `label` is appended to the filename for easy identification.
    """
    text = _read()
    os.makedirs(BACKUP_DIR, mode=0o700, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f".{validate_tag(label)}" if label else ""
    name = f"{SAVE_PREFIX}{stamp}{suffix}"
    path = os.path.join(BACKUP_DIR, name)
    _write_snapshot(path, text)
    return {"name": name, "path": path, "bytes": len(text)}


def restore(name: Optional[str] = None) -> Dict:
    """Restore the hosts file from a snapshot (most recent if `name` is omitted).

    `name` may be an exact backup filename or a substring (e.g. a label). The
    current content is snapshotted before the restore, which is written in place
    so the ACL is preserved.
    """
    backups = list_backups()
    if not backups:
        raise HostsError(f"no backups found in {BACKUP_DIR}")
    if name:
        matches = [b for b in backups if b["name"] == name or name in b["name"]]
        if not matches:
            raise HostsError(f"no backup matching {name!r}")
        chosen = matches[-1]
    else:
        chosen = backups[-1]
    with open(chosen["path"], encoding="utf-8") as handle:
        content = handle.read()
    _locked_write(lambda original: (content, None))
    return {"restored_from": chosen["name"], "bytes": len(content)}
