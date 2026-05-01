"""Read and write the CloudByte config.json file."""

import json
from pathlib import Path

CONFIG_PATH = Path.home() / ".cloudbyte" / "config.json"

# Used only when file doesn't exist — minimal structure so UI has something to show
_EMPTY_CONFIG = {
    "version":  "0.1.1",
    "settings": {
        "log_level":            "INFO",
        "enable_observations":  False,
    },
    "llm": {
        "default": "default",
        "endpoints": {
            "default": {
                "provider":    "",
                "model":       "",
                "api_key":     "",
                "temperature": 0.7,
                "max_tokens":  4000,
            }
        },
    },
    "worker": {
        "enabled": True,
        "port":    8765,
    },
    "central": {
        "enabled": False,
        "url":     "",
        "api_key": "",
        "sync_on_stop":         True,
        "sync_on_session_end":  True,
    },
}


def load_config() -> dict:
    """
    Load config directly from disk.
    If file exists, returns exactly what is in the file — no merging, no defaults.
    If file does not exist, returns a minimal empty structure.
    """
    if not CONFIG_PATH.exists():
        return json.loads(json.dumps(_EMPTY_CONFIG))
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return json.loads(json.dumps(_EMPTY_CONFIG))


def save_config(config: dict) -> None:
    """Write config dict to disk, creating the directory if needed."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def config_exists() -> bool:
    return CONFIG_PATH.exists()