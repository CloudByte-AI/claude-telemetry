#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CloudByte Prerequisites Validation Script

Checks Python and uv availability before running setup.
Works on Windows, macOS, and Linux.
"""

import platform
import subprocess
import sys
from pathlib import Path

# Fix encoding for Windows console
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def get_platform_info() -> dict:
    """Get platform information for better error messages."""
    system = platform.system()
    release = platform.release()

    if system == "Windows":
        return {
            "system": "Windows",
            "install_hint": "winget install Python.Python.3.12",
        }
    elif system == "Darwin":
        return {
            "system": "macOS",
            "install_hint": "brew install python@3.12",
        }
    elif system == "Linux":
        return {
            "system": "Linux",
            "install_hint": "sudo apt install python3.12  # Ubuntu/Debian",
        }
    else:
        return {
            "system": "Unknown",
            "install_hint": "Visit https://www.python.org/downloads/",
        }


def check_python_version() -> tuple[bool, str]:
    """Check if Python version is 3.10 or higher."""
    version = sys.version_info
    version_str = f"{version.major}.{version.minor}.{version.micro}"

    if version.major < 3 or (version.major == 3 and version.minor < 10):
        return False, version_str
    return True, version_str


def check_uv_installed() -> bool:
    """Check if uv is installed and accessible."""
    try:
        result = subprocess.run(
            ["uv", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def install_uv_with_pip() -> bool:
    """Install uv using pip."""
    print("\n📦 Installing uv using pip...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--user", "uv"],
            check=True,
            capture_output=True,
            text=True
        )
        print("✅ uv installed successfully!")
        return True
    except subprocess.CalledProcessError:
        print("⚠️  Failed to install uv")
        return False


def main():
    """Main validation function."""
    print("🔍 CloudByte Prerequisites Check")
    print("==================================")
    print()

    # Get platform info
    platform_info = get_platform_info()
    print(f"🖥️  Platform: {platform_info['system']}")
    print()

    # Check Python version
    print("🐍 Checking Python...")
    is_valid, version_str = check_python_version()

    if not is_valid:
        print(f"❌ Python {version_str} is too old!")
        print()
        print("Python 3.10 or higher is required.")
        print(f"Please install: {platform_info['install_hint']}")
        sys.exit(1)

    print(f"✅ Python {version_str} detected")

    # Check for uv
    print()
    print("⚡ Checking for uv...")
    if check_uv_installed():
        print("✅ uv found")
    else:
        print("⚠️  uv not found (optional but recommended)")
        print()
        print("uv is a fast Python package manager.")

        # Ask if user wants to install uv (only if interactive)
        if sys.stdin.isatty():
            try:
                response = input("Would you like to install uv now? [Y/n]: ").strip().lower()
                if response in ("", "y", "yes"):
                    install_uv_with_pip()
            except (EOFError, KeyboardInterrupt):
                pass

    print()
    print("==================================")
    print("✅ Prerequisites check passed!")
    print("==================================")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
