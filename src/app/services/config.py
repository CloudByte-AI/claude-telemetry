"""Business logic for the configuration page."""

from ..queries.config import load_config, save_config, config_exists

PLACEHOLDER_KEYS = {"enter_your_api_key_here", "YOUR_GEMINI_API_KEY", ""}


def _api_key_is_real(key: str) -> bool:
    return (key or "").strip() not in PLACEHOLDER_KEYS


def get_config_context() -> dict:
    """
    Always reads directly from the config file.
    No defaults injected — what's in the file is what the user sees.
    """
    cfg      = load_config()
    endpoint = cfg.get("llm", {}).get("endpoints", {}).get("default", {})
    settings = cfg.get("settings", {})
    worker   = cfg.get("worker", {})
    key      = endpoint.get("api_key", "")
    key_set  = _api_key_is_real(key)

    # Warn if features are enabled but key is not real
    feature_warning = (
        settings.get("enable_observations")
        and not key_set
    )

    return {
        "active":          "config",
        "config":          cfg,
        "endpoint":        endpoint,
        "config_exists":   config_exists(),
        "settings":        settings,
        "worker":          worker,
        "api_key_set":     key_set,
        "feature_warning": feature_warning,
    }


def update_config(form: dict) -> tuple[bool, str]:
    """
    Read the file, apply only the fields from the form, write back.
    Features can be set to any value — if key is missing we just warn, not block.
    """
    try:
        cfg = load_config()

        # Ensure nested structure exists
        cfg.setdefault("settings", {})
        cfg.setdefault("llm", {}).setdefault("endpoints", {}).setdefault("default", {})
        cfg.setdefault("worker", {})

        ep = cfg["llm"]["endpoints"]["default"]

        # ── LLM Provider ──────────────────────────────────────────────────────
        ep["provider"]    = form.get("provider", ep.get("provider", "")).strip()
        ep["model"]       = form.get("model",    ep.get("model",    "")).strip()
        ep["temperature"] = _float(form.get("temperature"), ep.get("temperature", 0.7))
        ep["max_tokens"]  = _int(form.get("max_tokens"),    ep.get("max_tokens",  4000))

        # Only update api_key if user typed something new
        new_key = form.get("api_key", "").strip()
        if new_key and new_key not in PLACEHOLDER_KEYS:
            ep["api_key"] = new_key

        # ── Features — save whatever user set, just warn if key missing ───────
        cfg["settings"]["enable_observations"] = form.get("enable_observations") == "1"

        # ── Worker port ───────────────────────────────────────────────────────
        new_port = form.get("worker_port", "").strip()
        if new_port:
            cfg["worker"]["port"] = _int(new_port, cfg["worker"].get("port", 8765))

        save_config(cfg)

        # Check if we should add a warning about features + missing key
        key_is_real = _api_key_is_real(ep.get("api_key", ""))
        features_on = cfg["settings"]["enable_observations"]
        if features_on and not key_is_real:
            return True, (
                "Configuration saved — but features are enabled without a valid API key. "
                "Observations will not work until you provide a real API key."
            )

        return True, "Configuration saved successfully."

    except Exception as e:
        return False, f"Failed to save configuration: {e}"


def _float(val, default: float) -> float:
    try:    return float(val)
    except: return default


def _int(val, default: int) -> int:
    try:    return int(val)
    except: return default