"""
Strict profile loading, complete-scan reporting and the scan_cli command.

Run with:
    uv run python -m pytest tests/test_scan_cli.py
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.security import config as security_config
from src.security import scan_cli, scanner
from src.security.config import ScanConfig, SecurityConfigError, load_security_config
from src.security.scanner import scan_text

REPO_ROOT = Path(__file__).parent.parent
SECRET = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef1234567890"
CLEAN = "please fix the login redirect loop on the settings page"


@pytest.fixture
def profile(tmp_path, monkeypatch):
    """Point the loader at a temporary security_profile.yaml; returns a writer."""
    path = tmp_path / "security_profile.yaml"
    monkeypatch.setattr(security_config, "USER_CONFIG_PATH", path)

    def write(text: str) -> Path:
        path.write_text(text, encoding="utf-8")
        return path

    return write


class _BrokenDetector:
    CATEGORY = "Broken"

    def scan(self, text, line_offsets, allowlist=frozenset()):
        raise RuntimeError("detector bug")


# ── 0a: strict profile loading ────────────────────────────────────────────────

class TestStrictLoading:
    def test_unreadable_yaml_raises_when_strict(self, profile):
        profile("enabled: [unclosed\n")
        with pytest.raises(SecurityConfigError) as exc:
            load_security_config(strict=True)
        assert exc.value.reason == "profile_unreadable"

    def test_unreadable_yaml_still_falls_back_when_not_strict(self, profile):
        profile("enabled: [unclosed\n")
        cfg = load_security_config()
        assert cfg.prompt_config is not None

    def test_missing_pyyaml_raises_when_strict(self, profile, monkeypatch):
        profile("enabled: true\n")
        monkeypatch.setitem(sys.modules, "yaml", None)  # makes `import yaml` fail
        with pytest.raises(SecurityConfigError) as exc:
            load_security_config(strict=True)
        assert exc.value.reason == "profile_unreadable"

    @pytest.mark.parametrize("text", ["", "- just\n- a list\n"])
    def test_non_mapping_raises_when_strict(self, profile, text):
        profile(text)
        with pytest.raises(SecurityConfigError) as exc:
            load_security_config(strict=True)
        assert exc.value.reason == "profile_not_mapping"

    @pytest.mark.parametrize("pattern_yaml", [
        "  - name: bad\n    pattern: '([unclosed'\n",       # regex does not compile
        "  - pattern: 'abc[0-9]+'\n",                          # no name
        "  - name: nothing\n",                                 # neither pattern nor examples
        "  - name: empty\n    examples: []\n",                 # empty examples
        "  - just a string\n",                                 # not a mapping
    ])
    def test_unusable_pattern_raises_when_strict(self, profile, pattern_yaml):
        profile("enabled: true\ncustom_patterns:\n" + pattern_yaml)
        with pytest.raises(SecurityConfigError) as exc:
            load_security_config(strict=True)
        assert exc.value.reason == "pattern_invalid"

    def test_unusable_pattern_is_skipped_when_not_strict(self, profile):
        profile(
            "enabled: true\ncustom_patterns:\n"
            "  - name: bad\n    pattern: '([unclosed'\n"
            "  - name: good\n    pattern: 'abc[0-9]+'\n"
        )
        cfg = load_security_config()
        assert [p["name"] for p in cfg.prompt_config.custom_patterns] == ["good"]

    def test_valid_profile_loads_the_same_either_way(self, profile):
        profile("enabled: true\nplan: strict\ncustom_patterns:\n  - name: good\n    pattern: 'abc[0-9]+'\n")
        assert load_security_config(strict=True) == load_security_config()

    def test_missing_profile_is_not_an_error(self, profile):
        cfg = load_security_config(strict=True)
        assert cfg.enabled is False


# ── 0b: complete-scan reporting ───────────────────────────────────────────────

class TestCompleteScan:
    def test_normal_scan_is_complete(self):
        result = scan_text(CLEAN, ScanConfig())
        assert result.complete is True
        assert result.incomplete_reasons == []

    @pytest.mark.parametrize("lines", [1, 600])  # sequential and parallel paths
    def test_detector_error_marks_incomplete(self, monkeypatch, lines):
        monkeypatch.setattr(scanner.DetectorRegistry, "get_enabled", lambda categories: [_BrokenDetector()])
        result = scan_text("\n".join([CLEAN] * lines), ScanConfig())
        assert result.complete is False
        assert result.incomplete_reasons == ["detector_error:Broken"]

    def test_pattern_timeout_marks_incomplete(self, monkeypatch):
        monkeypatch.setattr(scanner, "_run_timed", lambda pattern, text, **options: None)
        cfg = ScanConfig(custom_patterns=[{"name": "slow", "pattern": "a+"}])
        result = scan_text(CLEAN, cfg)
        assert result.complete is False
        assert result.incomplete_reasons == ["pattern_timeout:slow"]

    def test_invalid_pattern_at_scan_time_marks_incomplete(self):
        cfg = ScanConfig(custom_patterns=[{"name": "bad", "pattern": "([unclosed"}])
        result = scan_text(CLEAN, cfg)
        assert result.incomplete_reasons == ["pattern_error:bad"]

    def test_nameless_pattern_marks_incomplete(self):
        cfg = ScanConfig(custom_patterns=[{"pattern": "abc"}])
        result = scan_text(CLEAN, cfg)
        assert result.incomplete_reasons == ["pattern_skipped:?"]

    def test_filtered_strategy_marks_incomplete(self):
        result = scan_text("x\n" * (scanner.MAX_LINES_CHUNK + 1), ScanConfig())
        assert result.scan_strategy == "filtered"
        assert result.complete is False
        assert result.incomplete_reasons == ["filtered_strategy"]

    def test_reasons_are_not_repeated(self, monkeypatch):
        monkeypatch.setattr(
            scanner.DetectorRegistry, "get_enabled", lambda categories: [_BrokenDetector(), _BrokenDetector()]
        )
        result = scan_text(CLEAN, ScanConfig())
        assert result.incomplete_reasons == ["detector_error:Broken"]


# ── 0c: scan_cli verdicts ─────────────────────────────────────────────────────

class TestScanCli:
    def test_no_profile_uses_defaults(self, profile):
        verdict, code = scan_cli.scan(CLEAN, strict=True)
        assert code == 0
        assert verdict == {
            "status": "defaults", "complete": True, "incomplete_reasons": [],
            "blocked": False, "categories": [], "profile": "default",
        }

    def test_secret_is_blocked_and_never_printed(self, profile):
        profile("enabled: true\nplan: standard\n")
        verdict, code = scan_cli.scan(f"deploy with {SECRET}", strict=True)
        assert code == 0
        assert verdict["status"] == "ok"
        assert verdict["blocked"] is True
        assert verdict["categories"]
        assert verdict["profile"] == "standard"
        assert SECRET not in json.dumps(verdict)

    def test_disabled_guardrail_still_scans(self, profile):
        profile("enabled: false\n")
        verdict, _ = scan_cli.scan(f"deploy with {SECRET}", strict=True)
        assert verdict["status"] == "ok"
        assert verdict["blocked"] is True

    def test_allowlisted_value_is_not_blocked(self, profile):
        profile(f"enabled: true\nallowlist:\n  - {SECRET}\n")
        verdict, _ = scan_cli.scan(f"deploy with {SECRET}", strict=True)
        assert verdict["blocked"] is False

    def test_strict_refuses_a_broken_profile(self, profile):
        profile(f"allowlist:\n  - {SECRET}\nenabled: [unclosed\n")
        verdict, code = scan_cli.scan(CLEAN, strict=True)
        assert code == scan_cli.EXIT_STRICT_REFUSED
        assert verdict == {
            "status": "error", "complete": False, "incomplete_reasons": ["profile_unreadable"],
            "blocked": None, "categories": [], "profile": None,
        }
        assert SECRET not in json.dumps(verdict)

    def test_without_strict_a_broken_profile_falls_back_and_says_so(self, profile):
        profile("enabled: [unclosed\n")
        verdict, code = scan_cli.scan(CLEAN, strict=False)
        assert code == 0
        assert verdict["status"] == "fallback"
        assert verdict["complete"] is False
        assert verdict["incomplete_reasons"] == ["profile_fallback"]

    def test_incomplete_scan_is_reported(self, profile, monkeypatch):
        monkeypatch.setattr(scanner.DetectorRegistry, "get_enabled", lambda categories: [_BrokenDetector()])
        verdict, code = scan_cli.scan(CLEAN, strict=True)
        assert code == 0
        assert verdict["complete"] is False
        assert verdict["incomplete_reasons"] == ["detector_error:Broken"]

    def test_scan_failure_is_an_error_verdict(self, profile, monkeypatch):
        def boom(text, cfg):
            raise RuntimeError("scanner bug")
        monkeypatch.setattr(scanner, "scan_text", boom)
        verdict, code = scan_cli.scan(CLEAN, strict=True)
        assert code == scan_cli.EXIT_SCAN_FAILED
        assert verdict["status"] == "error"
        assert verdict["blocked"] is None


# ── End to end: the command as a caller runs it ───────────────────────────────

def _run_cli(home: Path, stdin: bytes, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    return subprocess.run(
        [sys.executable, "-m", "src.security.scan_cli", *args],
        input=stdin, capture_output=True, cwd=REPO_ROOT, env=env, timeout=60,
    )


class TestCommandLine:
    def test_prints_one_json_line_and_exits_zero(self, tmp_path):
        proc = _run_cli(tmp_path, f"deploy with {SECRET}".encode("utf-8"), "--strict")
        assert proc.returncode == 0, proc.stderr
        lines = proc.stdout.decode("utf-8").splitlines()
        assert len(lines) == 1
        verdict = json.loads(lines[0])
        assert verdict["status"] == "defaults"
        assert verdict["blocked"] is True
        assert SECRET.encode() not in proc.stdout

    def test_utf8_with_bom_is_read(self, tmp_path):
        proc = _run_cli(tmp_path, "﻿café — naïve résumé".encode("utf-8"), "--strict")
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout)["blocked"] is False

    def test_strict_refusal_exits_three(self, tmp_path):
        security_dir = tmp_path / ".cloudbyte" / "security"
        security_dir.mkdir(parents=True)
        (security_dir / "security_profile.yaml").write_text("enabled: [unclosed\n", encoding="utf-8")
        proc = _run_cli(tmp_path, CLEAN.encode("utf-8"), "--strict")
        assert proc.returncode == 3
        assert json.loads(proc.stdout)["status"] == "error"
        assert proc.stderr == b""
