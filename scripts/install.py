#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CloudByte Installation Script (Cross-Platform)

This script creates the .cloudbyte folder structure and initializes the database.

Checks for and installs prerequisites:
- Python 3.10+ (required)
- uv (optional, will install if missing)

Compatible with:
- Windows 10/11
- macOS 10.15+
- Linux (Ubuntu, Debian, Fedora, Arch, etc.)

NOTE: For Claude Code Marketplace installation:
- No path configuration needed!
- hooks.json uses relative paths that work automatically
- Plugin structure is ready for marketplace distribution

Run this script after installing from marketplace or cloning.
"""

import json
import os
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
    """
    Get platform information for better error messages.

    Returns:
        dict: Platform info with keys: system, release, installation_hint
    """
    system = platform.system()
    release = platform.release()

    if system == "Windows":
        return {
            "system": "Windows",
            "release": release,
            "install_hint": "winget install Python.Python.3.12",
            "package_manager": "winget"
        }
    elif system == "Darwin":
        return {
            "system": "macOS",
            "release": release,
            "install_hint": "brew install python@3.12",
            "package_manager": "brew"
        }
    elif system == "Linux":
        # Try to detect the distribution
        try:
            with open("/etc/os-release", "r") as f:
                os_release = f.read()
                if "ubuntu" in os_release.lower() or "debian" in os_release.lower():
                    return {
                        "system": "Linux",
                        "distro": "Ubuntu/Debian",
                        "install_hint": "sudo apt update && sudo apt install python3.12",
                        "package_manager": "apt"
                    }
                elif "fedora" in os_release.lower():
                    return {
                        "system": "Linux",
                        "distro": "Fedora",
                        "install_hint": "sudo dnf install python3.12",
                        "package_manager": "dnf"
                    }
                elif "arch" in os_release.lower():
                    return {
                        "system": "Linux",
                        "distro": "Arch",
                        "install_hint": "sudo pacman -S python",
                        "package_manager": "pacman"
                    }
        except Exception:
            pass

        # Generic Linux fallback
        return {
            "system": "Linux",
            "install_hint": "sudo apt install python3.12  # Ubuntu/Debian",
            "package_manager": "unknown"
        }
    else:
        return {
            "system": "Unknown",
            "install_hint": "Visit https://www.python.org/downloads/",
            "package_manager": "unknown"
        }


def check_python_version() -> tuple[bool, str, str]:
    """
    Check if Python version is 3.10 or higher.

    Returns:
        tuple: (is_valid, version_string, error_message)
    """
    version = sys.version_info
    version_str = f"{version.major}.{version.minor}.{version.micro}"

    if version.major < 3 or (version.major == 3 and version.minor < 10):
        return False, version_str, f"Python 3.10+ required, but found Python {version_str}"
    return True, version_str, ""


def install_uv_with_pip() -> bool:
    """
    Install uv using pip.

    Returns:
        bool: True if successful
    """
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
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to install uv: {e}")
        return False


def check_uv_installed() -> bool:
    """
    Check if uv is installed and accessible.

    Returns:
        bool: True if uv is installed
    """
    try:
        result = subprocess.run(
            ["uv", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            print(f"✅ uv found: {result.stdout.strip()}")
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return False


def ensure_prerequisites() -> bool:
    """
    Ensure all prerequisites are installed (Python and uv).

    Returns:
        bool: True if all prerequisites are available
    """
    print("="*50)
    print("🔍 Checking Prerequisites")
    print("="*50)

    # Get platform info for better error messages
    platform_info = get_platform_info()
    print(f"🖥️  Platform: {platform_info['system']}", end="")
    if platform_info.get('distro'):
        print(f" ({platform_info['distro']})", end="")
    print()

    # Check Python version
    print("\n🐍 Checking Python version...")
    is_valid, version_str, error_msg = check_python_version()

    if not is_valid:
        print(f"❌ {error_msg}")
        print("\n⚠️  Python 3.10 or higher is required!")
        print("\n📥 Please install Python 3.10+:")
        print(f"   • {platform_info['install_hint']}")
        print("   • Or download from: https://www.python.org/downloads/")
        print("\nAfter installing Python, run this script again.")
        return False

    print(f"✅ Python {version_str} detected")

    # Check for uv
    print("\n⚡ Checking for uv...")
    if check_uv_installed():
        return True

    print("⚠️  uv not found. It's recommended for faster package management.")
    print("     The plugin will work with pip, but uv is much faster.")

    # Ask user if they want to install uv
    if sys.stdin.isatty():  # Only prompt if running interactively
        try:
            response = input("\n❓ Would you like to install uv now? [Y/n]: ").strip().lower()
            if response in ("", "y", "yes"):
                if install_uv_with_pip():
                    # On Windows, add user bin to PATH for current session
                    if sys.platform == "win32":
                        user_bin = Path.home() / "AppData" / "Roaming" / "Python" / "Scripts"
                        if str(user_bin) not in os.environ.get("PATH", ""):
                            os.environ["PATH"] = str(user_bin) + os.pathsep + os.environ.get("PATH", "")
                    print("✅ Prerequisites check passed!")
                    return True
                else:
                    print("⚠️  Continuing with pip (slower but will work)...")
                    return True
        except (EOFError, KeyboardInterrupt):
            print("\n⚠️  Continuing with pip (slower but will work)...")

    # Non-interactive mode or user declined
    print("ℹ️  Continuing without uv (will use pip instead)...")
    return True


def get_plugin_dir() -> Path:
    """Get the directory where this plugin is located."""
    return Path(__file__).parent.resolve()


def verify_hooks_structure(plugin_dir: Path) -> bool:
    """
    Verify that hooks.json exists and is properly configured.

    Args:
        plugin_dir: The plugin directory path

    Returns:
        bool: True if hooks are properly configured
    """
    hooks_file = plugin_dir / "hooks" / "hooks.json"

    if not hooks_file.exists():
        print(f"❌ Error: hooks.json not found at {hooks_file}")
        return False

    try:
        with open(hooks_file, "r", encoding="utf-8") as f:
            hooks_data = json.load(f)

        # Check that commands use relative paths
        for hook_category in hooks_data.get("hooks", {}).values():
            if isinstance(hook_category, list):
                for hook_group in hook_category:
                    if isinstance(hook_group, dict) and "hooks" in hook_group:
                        for hook in hook_group["hooks"]:
                            if "command" in hook:
                                cmd = hook["command"]
                                if "--directory" in cmd and ".." in cmd:
                                    print(f"✅ Hooks use relative paths (marketplace ready)")
                                    return True

        print(f"⚠️  Warning: hooks.json may not be configured correctly")
        return False

    except Exception as e:
        print(f"❌ Error reading hooks.json: {e}")
        return False


def run_setup(plugin_dir: Path) -> bool:
    """
    Run the setup command to initialize the database.

    Args:
        plugin_dir: The plugin directory path

    Returns:
        bool: True if successful
    """
    try:
        print("\n🚀 Running initial setup...")

        # Change to plugin directory
        os.chdir(plugin_dir)

        # Import and run setup
        sys.path.insert(0, str(plugin_dir / "src"))
        from src.main import setup

        setup()

        print("✅ Setup completed successfully!")
        return True

    except Exception as e:
        print(f"❌ Error running setup: {e}")
        return False


def print_success_message(plugin_dir: Path):
    """Print success message with next steps."""
    print("\n" + "="*50)
    print("🎉 CloudByte Plugin Installation Complete!")
    print("="*50)
    print()
    print(f"📁 Plugin directory: {plugin_dir}")
    print(f"📊 Data directory: {Path.home() / '.cloudbyte'}")
    print()
    print("📋 What's been created:")
    print("   • .cloudbyte/ folder in your user directory")
    print("   • .cloudbyte/data/cloudbyte.db (SQLite database)")
    print("   • .cloudbyte/logs/ (log files)")
    print("   • .cloudbyte/config.json (settings)")
    print()
    print("✨ Marketplace Installation:")
    print("   • Plugin is ready to use with Claude Code")
    print("   • Hooks will run automatically on:")
    print("     - Setup (when Claude starts)")
    print("     - SessionStart (new session)")
    print("     - UserPromptSubmit (your prompts)")
    print("     - Stop (processing ends)")
    print("     - SessionEnd (session ends)")
    print()
    print("🔧 To verify installation:")
    print(f"   cd {plugin_dir}")
    print("   python -m src.main setup")
    print()


def main():
    """Main installation function."""
    print("="*50)
    print("☁️  CloudByte Plugin Installation")
    print("="*50)
    print()

    # Check prerequisites first
    if not ensure_prerequisites():
        print("\n❌ Installation failed: Missing prerequisites")
        sys.exit(1)

    # Get plugin directory
    plugin_dir = get_plugin_dir()
    print(f"\n📁 Plugin directory: {plugin_dir}")
    print()

    # Verify hooks structure
    if not verify_hooks_structure(plugin_dir):
        print("\n⚠️  Warning: Hooks may not be properly configured")
        print("   For marketplace installation, this should work automatically")
        print()

    # Run setup
    if not run_setup(plugin_dir):
        print("\n⚠️  Installation completed but setup had issues.")
        print("   You can run setup manually with: python -m src.main setup")
        sys.exit(1)

    # Print success message
    print_success_message(plugin_dir)


if __name__ == "__main__":
    main()
