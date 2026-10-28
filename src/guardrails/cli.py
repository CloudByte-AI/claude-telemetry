"""
Workspace guardrails from the command line - for anyone, whether or not their
organisation manages guardrails centrally.

    python -m src.main guardrails workspace init   [--path DIR] [--preset NAME] [--force]
    python -m src.main guardrails workspace status [--path DIR]
    python -m src.main guardrails workspace path   [--path DIR]
    python -m src.main guardrails notify-test
    python -m src.main guardrails notify-test

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
    from src.common.atomic_files import write_atomically

    write_atomically(path, text)


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


def remembered_approvals_label(profile) -> str:
    """How a policy treats an approved ask (approvals.py), in plain words."""
    mode, minutes = profile.ask_memory_mode, profile.ask_memory_minutes
    if mode == "same_target":
        return f"remembered for {minutes} minutes per session (same rule, same target)"
    if mode == "any_target":
        return f"remembered for {minutes} minutes per session (same rule, any target)"
    return "asked every time"


def cmd_status(args) -> int:
    from src.guardrails.config import load_profile, user_profile_path
    from src.guardrails.workspaces import list_workspace_files, load_workspace_profiles

    root = _root_or_exit(args.path)
    global_profile = load_profile()
    print(f"Session folder: {root}")
    print(f"Global policy:  {user_profile_path()} "
          f"({'on' if global_profile.enabled else 'off'}, {global_profile.source})")
    print(f"  Approvals:    {remembered_approvals_label(global_profile)}")

    applied = load_workspace_profiles(root, None)
    if applied:
        print("Workspace policies for this folder (stricter decision wins):")
        for workspace in applied:
            owner = "organisation" if workspace.managed else "you"
            state = "on" if workspace.profile.enabled else "off"
            print(f"  {workspace.root}  [{owner}, {state}]  {workspace.path.name}")
            print(f"    Approvals: {remembered_approvals_label(workspace.profile)}")
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


# What each backend shows, and what to check when nothing appeared.
_NOTIFY_HELP = {
    "windows_toast": (
        "A CloudByte-AI notification should appear at the bottom right now.",
        ["Settings > System > Notifications: notifications on, and CloudByte-AI on.",
         "Focus (Do not disturb) off."],
    ),
    "macos_osascript_dialog": (
        "A CloudByte dialog should appear now. It closes by itself after 20 seconds.",
        ["Send ~/.cloudbyte/logs/notifications-macos.log to your administrator."],
    ),
    "macos_osascript_notification": (
        "A notification from Script Editor should appear at the top right now.",
        ["Open Script Editor, run: display notification \"hello\" and click Allow when macOS asks.",
         "System Settings > Notifications > Script Editor: Allow notifications on, style Banners or Alerts.",
         "Focus (Do Not Disturb) off.",
         "Or set mac_style: dialog in ~/.cloudbyte/guardrails/guardrails_notifications.yaml."],
    ),
    "wsl_windows_toast": (
        "A CloudByte-AI notification should appear on the Windows desktop now (sent from WSL).",
        ["Windows Settings > System > Notifications: notifications on, and CloudByte-AI on.",
         "See ~/.cloudbyte/logs/notifications-wsl.log for what Windows reported."],
    ),
    "linux_freedesktop": (
        "A CloudByte-AI notification should appear now.",
        ["Your desktop's notifications on, and Do Not Disturb off."],
    ),
}


def cmd_notify_test(args) -> int:
    """Send one test notification and say how it was sent and what to check."""
    from src.common import notifications
    from src.guardrails.notify import ICONS_DIR, load_mac_style

    backend = notifications.backend_name()
    if backend is None or not notifications.supported():
        print("CloudByte: this machine has no way to show desktop notifications "
              "(no desktop session, or no notification service is running).")
        print("Guardrail decisions are still enforced and recorded; only the pop-up is missing.")
        return 1
    style = load_mac_style()
    key = f"{backend}_{style}" if backend == "macos_osascript" else backend
    icon = ICONS_DIR / "claude_code-warn.png"
    shown = notifications.show(notifications.Notice(
        level=notifications.WARN,
        title="CloudByte Guardrails  " + chr(0xB7) + "  Notification test",
        text="If you can read this, guardrail notifications work on this machine.",
        detail="Sent by: python -m src.main guardrails notify-test",
        icon=icon if icon.is_file() else None,
        mac_style=style,
    ))
    expect, checks = _NOTIFY_HELP.get(key, ("A notification should appear now.", []))
    if not shown:
        print("CloudByte: the test notification could not be sent. See the plugin log for the reason.")
        return 1
    print(f"CloudByte: test sent ({key}). {expect}")
    if checks:
        print("If nothing appeared:")
        for number, check in enumerate(checks, 1):
            print(f"  {number}. {check}")
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

    test = area.add_parser("notify-test", help="Send one test notification and say what to check")
    test.set_defaults(handler=cmd_notify_test)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
