"""
Edit evidence capture: a local, exact record of the changes an AI assistant makes to files.

    config.py   the capture settings (on by default; an off switch; retention; excluded paths)
    edits.py    the evidence records of one tool call: one per file it changed
    snapshot.py shell commands: git status before and after, exact lines of what changed
    spool.py    the evidence files: one file per record, kept for the retention period
    hook.py     the hook-side entry points (never raise, never block)

Everything stays on this machine. Nothing here uploads anything.
"""
