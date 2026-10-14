"""
Workspace guardrails from the command line - for anyone, whether or not their
organisation manages guardrails centrally.

    python -m src.main guardrails workspace init   [--path DIR] [--preset NAME] [--force]
    python -m src.main guardrails workspace status [--path DIR]
    python -m src.main guardrails workspace path   [--path DIR]

`init` writes the workspace file for a folder (default: the current one) from
a shipped preset, keeping the preset's comments, and checks it with the same
validation an editor uses. It never replaces a centrally managed file: the
organisation owns that folder's policy, and the sync agent would put it back
anyway.

`status` shows which policies a session opened in the folder gets, and every
workspace file on the machine. `path` prints where the folder's file lives,
for opening it in an editor.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PRESETS = ("minimal", "standard", "strict")


def _root_or_exit(path: str | None) -> str:
    from src.guardrails.workspaces import normalise_root

    folder = os.path.abspath(path or os.getcwd())
    if not os.path.isdir(folder):
        raise SystemExit(f"Not a folder: {folder}")
    root = normalise_root(folder)
    if root is None:
        raise SystemExit(f"Cannot use {folder} as a workspace")
    return root


def render_workspace_file(root: str, preset: str) -> str:
    """The text `init` writes: a header, the preset as shipped, then ui_workspace."""
    from src.guardrails.config import PROFILES_DIR

    preset_text = (PROFILES_DIR / f"{preset}.yaml").read_text(encoding="utf-8").rstrip()
    header = (
        f"# Workspace guardrails for {root}\n"
        f"# Created from the {preset} preset by `python -m src.main guardrails workspace init`.\n"
        f"#\n"
        f"# Applies to Claude Code and Cursor sessions opened in this folder (and in\n"
        f"# subfolders without a file of their own), ON TOP of the global policy\n"
        f"# (~/.cloudbyte/guardrails/global_profile.yaml): every call is checked\n"
        f"# against both and the stricter decision wins. If your organisation\n"
        f"# manages this folder's policy, its file replaces this one.\n"
        f"#\n"
        f"# The preset's own notes follow.\n"
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


def _write_atomically(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def cmd_init(args) -> int:
    from src.guardrails.config import read_yaml_mapping, validate_profile_text
    from src.guardrails.workspaces import is_managed, path_for

    root = _root_or_exit(args.path)
    target = path_for(root)
    if target.exists():
        if is_managed(read_yaml_mapping(target, what="workspace guardrails profile")):
            print(f"{target} is managed by your organisation and cannot be replaced here.",
                  file=sys.stderr)
            return 1
        if not args.force:
            print(f"{target} already exists. Edit it, or pass --force to start over.", file=sys.stderr)
            return 1

    text = render_workspace_file(root, args.preset)
    problems = validate_profile_text(text)
    if problems:   # a shipped preset failing validation is a plugin bug
        print("The preset did not pass validation, nothing written:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    _write_atomically(target, text)
    print(f"Workspace guardrails for {root}:\n  {target}\n"
          f"Edit that file to change the rules; changes apply on the next tool call.")
    return 0


def cmd_status(args) -> int:
    from src.guardrails.config import load_profile, user_profile_path
    from src.guardrails.workspaces import list_workspace_files, load_workspace_profiles

    root = _root_or_exit(args.path)
    global_profile = load_profile()
    print(f"Session folder: {root}")
    print(f"Global policy:  {user_profile_path()} "
          f"({'on' if global_profile.enabled else 'off'}, {global_profile.source})")

    applied = load_workspace_profiles(root, None)
    if applied:
        print("Workspace policies for this folder (stricter decision wins):")
        for workspace in applied:
            owner = "organisation" if workspace.managed else "you"
            state = "on" if workspace.profile.enabled else "off"
            print(f"  {workspace.root}  [{owner}, {state}]  {workspace.path.name}")
    else:
        print("Workspace policies for this folder: none")

    every = list_workspace_files()
    if every:
        print(f"All workspace files ({len(every)}):")
        for entry in every:
            flags = ["managed" if entry["managed"] else "user"]
            if not entry["readable"]:
                flags.append("UNREADABLE")
            elif not entry["name_matches_root"]:
                flags.append("IGNORED: name does not match its root")
            print(f"  {entry['file']}  {entry['root'] or '(no root)'}  [{', '.join(flags)}]")
    return 0


def cmd_path(args) -> int:
    from src.guardrails.workspaces import path_for

    print(path_for(_root_or_exit(args.path)))
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.main guardrails",
                                     description="Guardrails policy files.")
    area = parser.add_subparsers(dest="area", required=True)
    workspace = area.add_parser("workspace", help="Guardrails for one folder")
    actions = workspace.add_subparsers(dest="action", required=True)

    init = actions.add_parser("init", help="Create this folder's workspace policy from a preset")
    init.add_argument("--path", help="The workspace folder (default: the current folder)")
    init.add_argument("--preset", choices=PRESETS, default="standard")
    init.add_argument("--force", action="store_true", help="Replace your existing file")
    init.set_defaults(handler=cmd_init)

    status = actions.add_parser("status", help="Which policies apply to a session in this folder")
    status.add_argument("--path")
    status.set_defaults(handler=cmd_status)

    path = actions.add_parser("path", help="Print where this folder's workspace policy lives")
    path.add_argument("--path")
    path.set_defaults(handler=cmd_path)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
