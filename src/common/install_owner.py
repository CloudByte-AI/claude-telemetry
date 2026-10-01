"""
Which client an installed copy of this plugin belongs to.

One package ships every client's manifest (`.claude-plugin/`, `.cursor-plugin/`),
and some clients load other clients' installs: Cursor ("Third-Party Imports", on
by default) runs our Cursor hooks from the Claude Code install as well as from
its own, so every Cursor hook and its data would be doubled.

The rule: **a copy acts only for the client whose plugin folder it is installed
in.** A copy inside another known client's plugin folder stands down. Anything not
recognised (the repo, a test folder, an unknown layout) acts normally, and so does
any error: a missed folder costs a duplicate, while wrongly standing down would
switch a client's own install off without anyone noticing.

Stdlib only (`os`): src/hook_entry.py calls this before importing anything else.
"""

from __future__ import annotations

import os

CLAUDE_CODE = "claude_code"
CURSOR = "cursor"

# Each client: display name, and where it installs plugins under the home
# directory. Supporting a new client is one entry here.
CLIENTS = {
    CLAUDE_CODE: ("Claude Code", (".claude", "plugins")),
    CURSOR: ("Cursor", (".cursor", "plugins")),
}


def _package_root() -> str:
    """This copy's root: src/common/install_owner.py is three levels below it."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _normalise(path: str) -> str:
    """Links resolved, and on Windows case and separators folded."""
    return os.path.normcase(os.path.realpath(path))


def foreign_owner(client: str, root: str | None = None, home: str | None = None) -> str | None:
    """
    The other client whose plugin folder this copy is installed in, or None.

    None means "act normally", and is also the answer on any error. `root` and
    `home` default to this copy and the user's home; tests pass their own.
    """
    try:
        here = _normalise(root or _package_root())
        base = home or os.path.expanduser("~")
        for owner, (_, plugin_home) in CLIENTS.items():
            if owner == client:
                continue
            folder = _normalise(os.path.join(base, *plugin_home))
            if here == folder or here.startswith(folder + os.sep):
                return owner
    except Exception:
        return None
    return None


def stand_down_notice(owner: str, client: str) -> str:
    """One line for the client's hook log, so a copy that steps aside is visible."""
    owner_name = CLIENTS.get(owner, (owner,))[0]
    client_name = CLIENTS.get(client, (client,))[0]
    return (f"CloudByte: this copy is the {owner_name} install; {client_name}'s own "
            f"CloudByte install handles {client_name} hooks")
