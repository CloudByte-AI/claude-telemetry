"""
Edit evidence capture: a local, exact record of the changes an AI assistant makes to files.

    config.py   the capture settings (on by default; an off switch; retention; excluded paths)
    edits.py    the evidence records of one tool call: one per file it changed
    spool.py    the evidence files: one file per record, kept for the retention period
    hook.py     the hook-side entry point (never raises, never blocks)

Everything stays on this machine. Nothing here uploads anything.
"""
