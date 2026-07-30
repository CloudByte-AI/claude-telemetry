"""
CloudByte Workers Module

Manages the shared background dashboard process (src.app.app on
localhost:4723) that both the Claude Code and Cursor plugins depend on:

    llm_client.ensure_worker_running()          - start it and wait for the port
    worker_checker.ensure_worker_quick_sync()   - start it, don't wait
    kill_worker.shutdown_worker_if_no_active_sessions()
                                                - stop it, but only when no
                                                  other session still needs it

Nothing here re-exports at package level on purpose: both plugins import from
the submodules directly (`from src.workers.kill_worker import ...`), and a
package-level import list is one more place to keep in sync. Keeping this file
empty of imports also means a broken submodule can't take the whole package
down with it.
"""

__all__: list[str] = []
