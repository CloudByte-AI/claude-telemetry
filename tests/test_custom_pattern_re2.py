"""
Custom security patterns run on RE2.

A pathological pattern cannot stall a scan, a pattern RE2 cannot compile is
refused when the profile loads, the soft deadline still reports a timeout, and
what a finding holds (value, character offsets, allowlist) is unchanged.

Run with:
    uv run python -m pytest tests/test_custom_pattern_re2.py
"""

import logging
import sys
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common import safe_regex
from src.guardrails import rules
from src.security import config as security_config
from src.security import pattern_builder, scan_cli, scanner
from src.security.config import (
    ScanConfig,
    SecurityConfigError,
    _validate_patterns,
    load_security_config,
)
from src.security.detectors.base import compute_line_offsets
from src.security.registry import DetectorRegistry
from src.security.scanner import _run_timed, _scan_custom_patterns, scan_text

CLEAN = "please fix the login redirect loop on the settings page"
EVIL = r"(a+)+$"  # catastrophic on a backtracking engine
BOUND = 1.0       # seconds; the pathological scans below take milliseconds on RE2

# Built-in detectors off, so a test sees only what its custom pattern does.
NO_DETECTORS = {category: False for category in DetectorRegistry.all_categories()}


def _config(*patterns, allowlist=()):
    return ScanConfig(
        categories=dict(NO_DETECTORS),
        custom_patterns=[{"name": f"p{i}", "pattern": p} for i, p in enumerate(patterns)],
        allowlist=list(allowlist),
    )


def _custom(result):
    return [f for f in result.findings if f.category == "Custom"]


@pytest.fixture
def profile(tmp_path, monkeypatch):
    """Point the loader at a temporary security_profile.yaml; returns a writer."""
    path = tmp_path / "security_profile.yaml"
    monkeypatch.setattr(security_config, "USER_CONFIG_PATH", path)

    def write(text: str) -> Path:
        path.write_text(text, encoding="utf-8")
        return path

    return write


# ── The bug: a catastrophic pattern must not stall the scan ───────────────────

class TestPathologicalPattern:
    @pytest.mark.parametrize("n", [27, 50_000])
    def test_run_timed_finishes_with_no_match(self, n):
        text = "a" * n + "!"
        start = time.perf_counter()
        result = _run_timed(EVIL, text)
        elapsed = time.perf_counter() - start
        assert result == []  # no match, and not None: it did not time out
        assert elapsed < BOUND

    def test_the_pattern_itself_still_matches_when_it_can(self):
        # Control for the test above: it returns [] because the text ends in "!".
        assert _run_timed(EVIL, "a" * 50) == [safe_regex.Hit("a" * 50, 0, 50)]

    @pytest.mark.parametrize("n", [27, 50_000])
    def test_scan_custom_patterns_finishes_with_no_finding(self, n):
        text = "a" * n + "!"
        issues: list[str] = []
        start = time.perf_counter()
        findings = _scan_custom_patterns(
            text, _config(EVIL), compute_line_offsets(text), frozenset(), issues
        )
        elapsed = time.perf_counter() - start
        assert findings == []
        assert issues == []  # in particular, no pattern_timeout
        assert elapsed < BOUND

    @pytest.mark.parametrize("n", [27, 50_000])
    def test_scan_text_finishes_complete(self, n):
        start = time.perf_counter()
        result = scan_text("a" * n + "!", _config(EVIL))
        elapsed = time.perf_counter() - start
        assert _custom(result) == []
        assert result.complete is True
        assert not any(r.startswith("pattern_timeout") for r in result.incomplete_reasons)
        assert elapsed < BOUND

    def test_other_nested_quantifier_shapes_are_linear_too(self):
        text = "x" * 30_000 + "!"
        start = time.perf_counter()
        for pattern in (r"(x+x+)+y", r"(x|x)+$", r"(.*)*y", r"^(x+)+$"):
            assert _run_timed(pattern, text) == []
        assert time.perf_counter() - start < BOUND

    def test_a_match_flood_under_the_deadline_is_not_a_timeout(self):
        hits = _run_timed("a", "a" * 5_000)
        assert hits is not None and len(hits) == 5_000


# ── Patterns RE2 cannot compile are refused when the profile loads ────────────

UNSUPPORTED = [
    ("backref", r"(a+)\1", "backreference"),
    ("lookbehind", r"(?<=key=)\w+", "lookbehind"),
    ("neg_lookbehind", r"(?<!x)abc", "negative lookbehind"),
    ("lookahead", r"abc(?=d)", "lookahead"),
    ("neg_lookahead", r"abc(?!d)", "negative lookahead"),
    ("named_backref", r"(?P<n>a)(?P=n)", "named backreference"),
    ("atomic", r"(?>a+)b", "atomic group"),
    ("huge_repeat", r"a{2000}", "too large"),
]
GOOD = {"name": "good", "pattern": "abc[0-9]+"}


class TestUnsupportedPatternsAreRefused:
    @pytest.mark.parametrize("name,pattern,feature", UNSUPPORTED)
    def test_dropped_with_a_warning_that_names_it(self, caplog, name, pattern, feature):
        with caplog.at_level(logging.WARNING, logger=security_config.logger.name):
            valid = _validate_patterns([{"name": name, "pattern": pattern}, GOOD], "top-level")
        assert valid == [GOOD]
        warning = caplog.text
        assert f"'{name}'" in warning
        assert "RE2 cannot compile it" in warning
        assert feature in warning
        assert "unbounded time in a hook" in warning

    def test_lookbehind_warning_suggests_the_rewrite(self, caplog):
        with caplog.at_level(logging.WARNING, logger=security_config.logger.name):
            _validate_patterns([{"name": "k", "pattern": r"(?<=key=)\w+"}], "top-level")
        assert "(?:^|[^A-Za-z0-9])(...)" in caplog.text
        assert "group 1" in caplog.text

    @pytest.mark.parametrize("name,pattern,feature", UNSUPPORTED)
    def test_strict_load_refuses(self, name, pattern, feature):
        with pytest.raises(SecurityConfigError) as exc:
            _validate_patterns([{"name": name, "pattern": pattern}], "top-level", strict=True)
        assert exc.value.reason == "pattern_invalid"
        assert f"'{name}'" in str(exc.value)

    @pytest.mark.parametrize("pattern", [123, ["a"], {"a": 1}, None])
    def test_a_pattern_that_is_not_text_is_refused_not_a_crash(self, pattern):
        assert _validate_patterns([{"name": "odd", "pattern": pattern}, GOOD], "top-level") == [GOOD]

    def test_unusable_patterns_never_reach_the_scanner(self, profile):
        profile(
            "enabled: true\ncustom_patterns:\n"
            "  - name: backref\n    pattern: '(a+)\\1'\n"
            "  - name: lookbehind\n    pattern: '(?<=key=)\\w+'\n"
            "  - name: good\n    pattern: 'key=(\\w+)'\n"
        )
        cfg = load_security_config().prompt_config
        assert [p["name"] for p in cfg.custom_patterns] == ["good"]
        cfg.categories = dict(NO_DETECTORS)
        result = scan_text("key=abc123", cfg)
        assert [f.secret_value for f in _custom(result)] == ["abc123"]
        assert result.complete is True  # nothing was skipped at scan time

    def test_strict_profile_load_refuses(self, profile):
        profile("enabled: true\ncustom_patterns:\n  - name: lb\n    pattern: '(?<=key=)\\w+'\n")
        with pytest.raises(SecurityConfigError) as exc:
            load_security_config(strict=True)
        assert exc.value.reason == "pattern_invalid"

    def test_scan_cli_strict_refuses_with_the_strict_exit_code(self, profile):
        profile("enabled: true\ncustom_patterns:\n  - name: lb\n    pattern: '(?<=key=)\\w+'\n")
        verdict, code = scan_cli.scan(CLEAN, strict=True)
        assert code == scan_cli.EXIT_STRICT_REFUSED
        assert verdict["status"] == "error"

    def test_a_pattern_the_scanner_is_handed_anyway_is_an_error_not_a_crash(self):
        result = scan_text(CLEAN, _config(r"(?<=key=)\w+"))
        assert result.incomplete_reasons == ["pattern_error:p0"]

    def test_generated_patterns_are_checked_too(self, monkeypatch, caplog):
        bad = pattern_builder.GeneratedPattern(
            name="gen", pattern=r"(?<![a-z])tok_[0-9]{8}", confidence="HIGH",
            confidence_reason="", severity="HIGH", false_positive_risk="LOW", fp_risk_reason="",
        )
        monkeypatch.setattr(pattern_builder, "analyze_examples", lambda **kwargs: bad)
        entry = {"name": "gen", "examples": ["tok_12345678"]}
        with caplog.at_level(logging.WARNING, logger=security_config.logger.name):
            assert _validate_patterns([entry], "top-level") == []
        assert "'gen'" in caplog.text and "RE2 cannot compile it" in caplog.text
        with pytest.raises(SecurityConfigError):
            _validate_patterns([entry], "top-level", strict=True)


# ── What the pattern builder generates runs on RE2 ────────────────────────────

class TestGeneratedPatterns:
    EXAMPLES = ["INT_TKN_aBcD1234eFgH5678", "INT_TKN_xYz9876wVuT5432", "INT_TKN_mNoP2345qRsT6789"]

    def test_output_has_no_lookaround_and_compiles_on_re2(self):
        for examples in (
            self.EXAMPLES,
            ["550e8400-e29b-41d4-a716-446655440000", "123e4567-e89b-12d3-a456-426614174000"],
            ["aBcD1234eFgH5678iJkL9012"],
            ["rzp_live_ABC123XYZ789"],
        ):
            generated = pattern_builder.analyze_examples("t", examples)
            assert generated.examples_matched == generated.examples_total
            patterns = [generated.pattern] + [a.split(":", 1)[1].strip() for a in generated.alternatives]
            for pattern in patterns:
                assert not any(token in pattern for token in ("(?<", "(?=", "(?!")), pattern
                assert safe_regex.check(pattern, finder=True) is None, pattern

    def test_generated_pattern_loads_and_finds_tokens_one_space_apart(self, profile):
        profile(
            "enabled: true\ncustom_patterns:\n  - name: Internal Token\n    examples:\n"
            + "".join(f"      - {e}\n" for e in self.EXAMPLES)
        )
        cfg = load_security_config().prompt_config
        assert [p["name"] for p in cfg.custom_patterns] == ["Internal Token"]
        cfg.categories = dict(NO_DETECTORS)
        text = "a INT_TKN_aaaaaaaaaaaaaaaa INT_TKN_bbbbbbbbbbbbbbbb z"
        found = _custom(scan_text(text, cfg))
        assert [f.secret_value for f in found] == ["INT_TKN_aaaaaaaaaaaaaaaa", "INT_TKN_bbbbbbbbbbbbbbbb"]
        # The leading boundary is context, not part of the finding.
        assert all(text[f.char_start:f.char_end] == f.secret_value for f in found)

    def test_boundary_still_rejects_a_token_inside_a_longer_one(self):
        pattern = pattern_builder.analyze_examples("t", self.EXAMPLES).pattern
        hits = list(safe_regex.compile_finder(pattern).finditer("xINT_TKN_aBcD1234eFgH5678 INT_TKN_aBcD1234eFgH5678"))
        assert [h.start for h in hits] == [26]

    def test_builder_self_checks_use_re2(self):
        assert pattern_builder._safe_compile(r"(?<=a)b") is None
        assert pattern_builder._check_clean_text_fps(r"(?<=a)b") == 0
        assert pattern_builder._check_clean_text_fps(r"os\.getenv") > 0


# ── The soft deadline still reports a timeout ─────────────────────────────────

class TestSoftDeadline:
    def test_run_timed_returns_none_when_the_deadline_passed(self, monkeypatch):
        monkeypatch.setattr(scanner, "_PATTERN_TIMEOUT_SECS", 0)
        assert _run_timed("a", "aaa") is None

    def test_scan_custom_patterns_records_pattern_timeout(self, monkeypatch):
        monkeypatch.setattr(scanner, "_PATTERN_TIMEOUT_SECS", 0)
        issues: list[str] = []
        findings = _scan_custom_patterns(
            "aaa", ScanConfig(custom_patterns=[{"name": "flood", "pattern": "a"}]),
            compute_line_offsets("aaa"), frozenset(), issues,
        )
        assert findings == []
        assert issues == ["pattern_timeout:flood"]

    def test_scan_text_is_incomplete_with_the_reason(self, monkeypatch):
        monkeypatch.setattr(scanner, "_PATTERN_TIMEOUT_SECS", 0)
        cfg = ScanConfig(categories=dict(NO_DETECTORS), custom_patterns=[{"name": "flood", "pattern": "a"}])
        result = scan_text("aaa", cfg)
        assert result.complete is False
        assert "pattern_timeout:flood" in result.incomplete_reasons
        assert _custom(result) == []

    def test_clock_is_checked_between_hits_and_stops_early(self, monkeypatch):
        ticks: list[float] = []

        def fake_monotonic():
            ticks.append(float(len(ticks)))  # one second per call
            return ticks[-1]

        monkeypatch.setattr(scanner, "time", types.SimpleNamespace(monotonic=fake_monotonic))
        monkeypatch.setattr(scanner, "_PATTERN_TIMEOUT_SECS", 2.5)
        # Call 0 sets the deadline (2.5); call 3 is the first one past it.
        assert _run_timed("a", "a" * 100) is None
        assert len(ticks) == 4  # three hits were taken, not a hundred

    def test_a_pattern_with_no_hit_is_not_a_timeout(self, monkeypatch):
        monkeypatch.setattr(scanner, "_PATTERN_TIMEOUT_SECS", 0)
        assert _run_timed("z", "aaa") == []


# ── Findings keep their shape ─────────────────────────────────────────────────

class TestResultShape:
    def test_group_one_is_the_finding_when_the_pattern_has_one(self):
        text = "set key=abc123 now"
        (finding,) = _custom(scan_text(text, _config(r"key=(\w+)")))
        assert finding.secret_value == "abc123"
        assert (finding.char_start, finding.char_end) == (8, 14)
        assert finding.masked_value == "[REDACTED:P0]"

    def test_whole_match_is_the_finding_when_there_is_no_group(self):
        text = "set key=abc123 now"
        (finding,) = _custom(scan_text(text, _config(r"key=\w+")))
        assert finding.secret_value == "key=abc123"
        assert (finding.char_start, finding.char_end) == (4, 14)

    def test_group_one_wins_over_later_groups(self):
        (finding,) = _custom(scan_text("id=7 tok=zzz", _config(r"tok=(\w+)(?:\s|$)(x)?")))
        assert finding.secret_value == "zzz"

    def test_offsets_are_characters_not_bytes(self):
        prefix = "é€😀 "  # 2, 3 and 4 bytes in UTF-8
        text = prefix + "key=abc123 tail"
        (finding,) = _custom(scan_text(text, _config(r"key=(\w+)")))
        assert finding.char_start == len(prefix) + 4
        assert finding.char_end == finding.char_start + len("abc123")
        assert text[finding.char_start:finding.char_end] == "abc123"

    def test_whole_match_offsets_are_characters_too(self):
        text = "é€😀 key=abc123"
        (finding,) = _custom(scan_text(text, _config(r"key=\w+")))
        assert text[finding.char_start:finding.char_end] == "key=abc123"

    def test_line_number_with_a_non_ascii_first_line(self):
        text = "é€😀\nkey=abc123"
        (finding,) = _custom(scan_text(text, _config(r"key=(\w+)")))
        assert finding.line_number == 2

    def test_lone_surrogate_in_the_text_does_not_break_the_scan(self):
        text = "pre \ud800 key=abc9 post"
        (finding,) = _custom(scan_text(text, _config(r"key=(\w+)")))
        assert (finding.secret_value, finding.char_start) == ("abc9", text.index("abc9"))

    def test_group_that_did_not_match_falls_back_to_the_whole_match(self):
        text = "x ac y"
        (finding,) = _custom(scan_text(text, _config(r"a(b)?c")))
        assert finding.secret_value == "ac"
        assert text[finding.char_start:finding.char_end] == "ac"

    def test_allowlisted_group_value_is_suppressed(self):
        result = scan_text("key=abc key=zzz", _config(r"key=(\w+)", allowlist=["abc"]))
        assert [f.secret_value for f in _custom(result)] == ["zzz"]

    def test_allowlisted_whole_match_is_suppressed(self):
        result = scan_text("key=abc key=zzz", _config(r"key=\w+", allowlist=["key=abc"]))
        assert [f.secret_value for f in _custom(result)] == ["key=zzz"]

    def test_already_redacted_values_are_skipped(self):
        assert _custom(scan_text("key=[REDACTED:X]", _config(r"key=(\S+)"))) == []

    @pytest.mark.parametrize("pattern", [
        r"(?:^|[^a-zA-Z0-9])(TKN_[a-z]{8})",                                  # leading boundary
        r"(TKN_[a-z]{8})(?:$|[^a-zA-Z0-9])",                                  # trailing boundary
        r"(?:^|[^a-zA-Z0-9])(TKN_[a-z]{8})(?:$|[^a-zA-Z0-9])",                # both
    ])
    def test_a_consumed_boundary_does_not_hide_the_next_token(self, pattern):
        text = "TKN_aaaaaaaa TKN_bbbbbbbb TKN_cccccccc"
        found = _custom(scan_text(text, _config(pattern)))
        assert [f.secret_value for f in found] == ["TKN_aaaaaaaa", "TKN_bbbbbbbb", "TKN_cccccccc"]
        assert [f.char_start for f in found] == [0, 13, 26]

    def test_two_tokens_one_space_apart_after_a_non_ascii_prefix(self):
        text = "é€😀 TKN_aaaaaaaa TKN_bbbbbbbb"
        found = _custom(scan_text(text, _config(r"(TKN_[a-z]{8})(?:$|[^a-zA-Z0-9])")))
        assert [text[f.char_start:f.char_end] for f in found] == ["TKN_aaaaaaaa", "TKN_bbbbbbbb"]

    def test_a_boundary_pattern_without_a_neighbour_matches_like_the_lookaround_it_replaces(self):
        found = _custom(scan_text("xTKN_aaaaaaaa", _config(r"(?:^|[^a-zA-Z0-9])(TKN_[a-z]{8})")))
        assert found == []


# ── The finder itself ─────────────────────────────────────────────────────────

class TestFinder:
    def test_hits_are_values_with_character_offsets(self):
        text = "é€😀 key=abc123 and key=zzz"
        hits = list(safe_regex.compile_finder(r"key=(\w+)").finditer(text))
        assert hits == [safe_regex.Hit("abc123", 8, 14), safe_regex.Hit("zzz", 23, 26)]
        assert all(text[h.start:h.end] == h.value for h in hits)

    def test_finditer_is_lazy(self):
        iterator = safe_regex.compile_finder("a").finditer("a" * 1_000_000)
        assert next(iterator) == safe_regex.Hit("a", 0, 1)

    def test_a_pattern_that_is_not_text_raises_regex_error(self):
        with pytest.raises(safe_regex.RegexError):
            safe_regex.compile_finder(123)

    def test_empty_matches_make_progress(self):
        assert len(list(safe_regex.compile_finder("").finditer("abc"))) == 4
        assert len(list(safe_regex.compile_finder("x*").finditer("abc"))) == 4

    def test_check_with_finder_uses_the_scan_wording(self):
        assert "(?:^|[^A-Za-z0-9])(...)" in safe_regex.check(r"(?<![a-z])abc", finder=True)


# ── Guardrails are unaffected ─────────────────────────────────────────────────

class TestGuardrailsUnchanged:
    def test_guardrail_wording_for_lookaround_is_the_same(self):
        assert safe_regex.check(r"(?<=key=)\w+") == (
            "lookbehind `(?<=...)` is not supported by RE2 - "
            "put that requirement in a second condition on the rule instead."
        )

    def test_pattern_search_is_still_a_bool(self):
        compiled = safe_regex.compile(r"foo\d")
        assert compiled.search("xfoo1") is True
        assert compiled.search("foo") is False

    def test_guardrail_regex_operator(self):
        assert rules.apply_operator("regex", r"rm\s+-rf", "rm -rf /tmp/x", "command") is True
        assert rules.apply_operator("regex", r"(?<=a)b", "ab", "command") is False

    def test_other_engine_messages_are_the_same(self):
        assert safe_regex.check(r"(a+)\1") == (
            "backreferences like `\\1` are not supported by RE2 - match the literal text instead."
        )
        assert safe_regex.check(r"a{2000}") == "repetition count `{2000}` is too large - RE2 allows at most 1000."


# ── Where RE2 differs from Python's re (pinned so an engine change is noticed) ─

class TestRe2Semantics:
    @staticmethod
    def _values(pattern, text):
        return [h.value for h in safe_regex.compile_finder(pattern).finditer(text)]

    def test_digit_word_space_and_boundary_classes_are_ascii_only(self):
        assert self._values(r"\d+", "x٣٤ 12") == ["12"]
        assert self._values(r"\w+", "café ok") == ["caf", "ok"]
        assert self._values(r"\s+", "a" + chr(0xA0) + "b c") == [" "]
        assert self._values(r"\bok\b", "éok ok") == ["ok", "ok"]

    def test_dollar_matches_only_at_the_very_end(self):
        assert self._values(r"a$", "a\n") == []
        assert self._values(r"a$", "a") == ["a"]
        assert self._values(r"(?m)a$", "a\nb") == ["a"]

    def test_inline_case_insensitive_flag_works(self):
        assert self._values(r"(?i)key=(\w+)", "KEY=abc") == ["abc"]

    def test_unsupported_python_only_syntax_is_refused(self):
        for pattern in (r"abc\Z", r"(?a)\w+", r"a(?#c)b", r"(?(1)a|b)", r"(?x) a b"):
            assert safe_regex.check(pattern, finder=True) is not None, pattern

    def test_brace_quantifier_with_no_minimum_is_literal(self):
        # Python reads a{,3} as 0 to 3 repeats; RE2 reads it as the text "a{,3}".
        assert self._values(r"a{,3}", "aaa a{,3}") == ["a{,3}"]
