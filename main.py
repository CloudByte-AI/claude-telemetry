"""
CloudByte Root Entry Point

Simple wrapper that delegates to src.main
"""

import sys
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

# Import and run the main entry point
from src.main import main

if __name__ == "__main__":
    main()
