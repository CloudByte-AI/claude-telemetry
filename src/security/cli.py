"""
Workspace security profiles from the command line.

    python -m src.main security workspace status [--path DIR]
    python -m src.main security workspace path   [--path DIR]
    python -m src.main security workspace init   [--path DIR] [--preset NAME] [--force]

`status` shows which security profiles a session opened in the folder gets
(the global one plus the folder's workspace profiles, strictest wins) and every
workspace profile on the machine. `path` prints where the folder's profile
lives. `init` writes one from a shipped preset.

The file naming and folder walk are shared with guardrails
(src/common/workspace_profiles.py).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

PRESETS = ("minimal", "standard", "strict")
_WHAT = "workspace security profile"


def _root_or_exit(path: str | None) -> str:
    from src.common.workspace_profiles import normalise_root

    folder = os.path.abspath(path or os.getcwd())
    if not os.path.isdir(folder):
        raise SystemExit(f"Not a folder: {folder}")
    root = normalise_root(folder)
    if root is None:
        raise SystemExit(f"Cannot use {folder} as a workspace")
    return root


def render_workspace_file(root: str, preset: str) -> str:
    """The text `init` writes: a header, the preset as shipped, then ui_workspace."""
    from src.security.config import PROFILES_DIR

    preset_text = (PROFILES_DIR / f"{preset}.yaml").read_text(encoding="utf-8").rstrip()
    header = (
        f"# Workspace security scanning for {root}\n"
        f"# Created from the {preset} preset by `python -m src.main security workspace init`.\n"
        f"#\n"
        f"# Applies to Claude Code and Cursor sessions opened in this folder (and in\n"
        f"# subfolders without a profile of their own), ON TOP of the global profile\n"
        f"# (~/.cloudbyte/security/security_profile.yaml). It can only add scanning:\n"
        f"# a detector either profile turns on is on.\n"
        f"#\n"
    )
    footer = (
        f"\n\n# Which folder this file is for. The file name is derived from it, so a\n"
        f"# copied or renamed file is ignored rather than applied to the wrong folder.\n"
        f"ui_workspace:\n"
        f"  root: {json.dumps(root)}\n"
        f"  created_by: user\n"
    )
    return header + preset_text + footer


def cmd_init(args) -> int:
    from src.common.atomic_files import write_atomically
    from src.common.paths import get_security_dir
    from src.common.workspace_profiles import path_for

    root = _root_or_exit(args.path)
    target = path_for(root, get_security_dir())
    if target.exists():
        if not args.force:
            print(f"{target} already exists. Edit it, or pass --force to start over.", file=sys.stderr)
            return 1
    write_atomically(target, render_workspace_file(root, args.preset))
    print(f"Created {target}")
    return 0


def cmd_status(args) -> int:
    from src.common import workspace_profiles as ws
    from src.common.paths import get_security_dir
    from src.common.yaml_cache import read_yaml_mapping
    from src.security.config import USER_CONFIG_PATH, load_security_config

    root = _root_or_exit(args.path)
    cfg = load_security_config(workspace_root=root)
    print(f"Session folder:  {root}")
    print(f"Scanning:        {'on' if cfg.enabled else 'off'} "
          f"(plan {cfg.plan}, scope {cfg.scope})")
    print(f"Global profile:  {USER_CONFIG_PATH}{'' if USER_CONFIG_PATH.exists() else ' (none)'}")
    workspaces = [s for s in cfg.sources if s.scope == "workspace"]
    if workspaces:
        print("Workspace profiles for this folder (strictest wins):")
        for source in workspaces:
            print(f"  {source.root}  {os.path.basename(source.path)}")
    else:
        print("Workspace profiles for this folder: none")

    every = ws.list_workspace_files(ws.workspaces_dir(get_security_dir()), read_yaml_mapping, _WHAT)
    if every:
        print(f"All workspace profiles ({len(every)}):")
        for entry in every:
            flags = ["on" if entry["enabled"] else "off"]
            if not entry["readable"]:
                flags.append("UNREADABLE")
            if entry["root"] and not entry["name_matches_root"]:
                flags.append("NAME DOES NOT MATCH ROOT - ignored")
            print(f"  {entry['root'] or '(no root declared)'}  [{', '.join(flags)}]  {entry['file']}")
    return 0


def cmd_path(args) -> int:
    from src.common.paths import get_security_dir
    from src.common.workspace_profiles import path_for

    print(path_for(_root_or_exit(args.path), get_security_dir()))
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.main security",
                                     description="Security scanning profiles.")
    area = parser.add_subparsers(dest="area", required=True)
    workspace = area.add_parser("workspace", help="Security scanning for one folder")
    actions = workspace.add_subparsers(dest="action", required=True)

    init = actions.add_parser("init", help="Create this folder's workspace profile from a preset")
    init.add_argument("--path", help="The workspace folder (default: the current folder)")
    init.add_argument("--preset", choices=PRESETS, default="standard")
    init.add_argument("--force", action="store_true", help="Replace your existing file")
    init.set_defaults(handler=cmd_init)

    status = actions.add_parser("status", help="Which profiles apply to a session in this folder")
    status.add_argument("--path")
    status.set_defaults(handler=cmd_status)

    path = actions.add_parser("path", help="Print where this folder's workspace profile lives")
    path.add_argument("--path")
    path.set_defaults(handler=cmd_path)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
