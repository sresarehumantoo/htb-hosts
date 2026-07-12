"""Load the htb-hosts sources from ../src as importable modules.

The CLI (`htb-hosts`) has no .py extension and the CLI/MCP modules hardcode
`sys.path.insert(0, "/opt/htb-hosts")`; loading here by explicit file path and
caching in sys.modules makes the test suite exercise the in-repo `src/` copy
regardless of whether anything is installed under /opt.
"""

import importlib.util
import pathlib
import sys
from importlib.machinery import SourceFileLoader

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"

_CACHE: dict[str, object] = {}


def _load(name: str, filename: str):
    if name in _CACHE:
        return _CACHE[name]
    # SourceFileLoader works for the extensionless CLI as well as the .py files.
    src_loader = SourceFileLoader(name, str(SRC / filename))
    spec = importlib.util.spec_from_loader(name, src_loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # so `import htb_hosts` inside cli/mcp finds this one
    src_loader.exec_module(module)
    _CACHE[name] = module
    return module


def load_core():
    """Return the core library module (htb_hosts)."""
    return _load("htb_hosts", "htb_hosts.py")


def load_cli():
    """Return the CLI module (its main() takes an argv list)."""
    load_core()
    return _load("htb_hosts_cli", "htb-hosts")


def load_mcp():
    """Return the MCP server module (its handle() takes a JSON-RPC dict)."""
    load_core()
    return _load("mcp_server", "mcp_server.py")
