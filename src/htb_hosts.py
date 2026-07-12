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
        return str(ipaddress.ip_address(ip.strip()))
    except ValueError as exc:
        raise HostsError(f"invalid IP address: {ip!r}") from exc


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


def split_ip_and_hosts(tokens: list[str]) -> tuple[str, list[str]]:
    """Split mixed positional tokens (in any order) into (ip, hostnames).

    Lets callers pass `machine.htb 10.129.45.2` or `10.129.45.2 machine.htb`
    interchangeably. Raises HostsError unless exactly one token is an IP.
    """
    ips: list[str] = []
    hosts: list[str] = []
    for token in tokens:
        try:
            ipaddress.ip_address(token.strip())
            ips.append(token.strip())
        except ValueError:
            hosts.append(token)
    if not ips:
        raise HostsError(f"no IP address found in: {' '.join(tokens)}")
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
    """Return the full contents of HOSTS_FILE, or '' if it does not exist."""
    if not os.path.exists(HOSTS_FILE):
        return ""
    with open(HOSTS_FILE, encoding="utf-8") as handle:
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


def _backup(text: str) -> None:
    """Write a timestamped backup and prune to the last BACKUP_KEEP; best-effort."""
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(BACKUP_DIR, f"{BACKUP_PREFIX}{stamp}")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        backups = sorted(b for b in os.listdir(BACKUP_DIR) if b.startswith(BACKUP_PREFIX))
        for old in backups[:-BACKUP_KEEP]:
            os.remove(os.path.join(BACKUP_DIR, old))
    except OSError:
        # Backups are best-effort; never block the actual edit on them.
        pass


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
def add(ip: str, hostnames: list[str], tag: Optional[str] = None) -> Dict:
    """Add or update a managed entry; idempotent upsert keyed on IP.

    Returns {"entry": <dict>, "warnings": [<str>, ...]}.
    """
    ip = validate_ip(ip)
    hostnames = [validate_hostname(h) for h in hostnames]
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


def _entry_matches(entry: Entry, patterns: list[str]) -> bool:
    """Fuzzy match: True if any pattern is a case-insensitive substring of the
    entry's IP, tag, or any hostname (e.g. 'forest' matches WEB.FOREST.HTB)."""
    haystack = " ".join([entry.ip, entry.tag or "", *entry.hostnames]).lower()
    return any(p in haystack for p in patterns)


def clean(tag: Optional[str] = None, exclude: Optional[list[str]] = None) -> Dict:
    """Remove managed entries.

    - no args: wipe the whole managed block
    - `tag`: remove only entries with that exact tag
    - `exclude`: remove everything EXCEPT entries fuzzily matching any pattern
      (case-insensitive substring over ip/tag/hostnames)

    `tag` and `exclude` are mutually exclusive.
    """
    if tag and exclude:
        raise HostsError("use either tag or exclude, not both")
    if tag:
        tag = validate_tag(tag)
    patterns = [p.strip().lower() for p in (exclude or []) if p.strip()]
    removed: list[Dict] = []

    def mutate(
        pre: list[str], entries: list[Entry], post: list[str]
    ) -> tuple[list[str], list[Entry], list[str], list[Dict]]:
        kept: list[Entry] = []
        for entry in entries:
            if patterns:
                keep = _entry_matches(entry, patterns)  # keep the excluded ones
            elif tag is not None:
                keep = entry.tag != tag
            else:
                keep = False  # no filter -> remove everything
            if keep:
                kept.append(entry)
            else:
                removed.append(entry.to_dict())
        entries[:] = kept
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


def show_data(full: bool = False) -> Dict:
    """Structured overview of managed hosts grouped by box (tag).

    With `full=True`, also includes the unmanaged/system entries.
    """
    managed = list_entries()
    groups: dict[str, list[Dict]] = {}
    for entry in managed:
        groups.setdefault(entry["tag"] or "(untagged)", []).append(entry)
    data: Dict = {
        "managed_count": len(managed),
        "box_count": len(groups),
        "groups": groups,
    }
    if full:
        data["unmanaged"] = outside_entries()
    return data


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


def doctor() -> Dict:
    """Report writability/ACL status and, if broken, the exact setfacl fix."""
    info: Dict = {"hosts_file": HOSTS_FILE, "exists": os.path.exists(HOSTS_FILE)}
    info["writable"] = os.access(HOSTS_FILE, os.W_OK) if info["exists"] else None
    _, entries, _ = _split(_read())
    info["managed_entries"] = len(entries)
    info["ok"] = bool(info["exists"] and info["writable"])
    if not info["ok"]:
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
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f".{validate_tag(label)}" if label else ""
    name = f"{SAVE_PREFIX}{stamp}{suffix}"
    path = os.path.join(BACKUP_DIR, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
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
