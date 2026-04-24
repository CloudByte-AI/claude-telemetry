"""
Worker launcher script - keeps the worker process running.
"""
import sys
import os
import subprocess
from pathlib import Path

# Change to project directory
os.chdir(Path(__file__).parent.parent.parent)

# Start worker as a detached process
print("Starting CloudByte LLM Worker...")

# Use CREATE_NEW_PROCESS_GROUP to detach from parent
process = subprocess.Popen(
    [sys.executable, "-m", "src.workers.llm_worker"],
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)

print(f"Worker started with PID: {process.pid}")
print("Worker is running in the background.")
print("\nTo check worker status, run:")
print("  uv run python check_worker.py")
print("\nTo stop the worker, run:")
print("  curl -X POST http://localhost:8765/shutdown")
