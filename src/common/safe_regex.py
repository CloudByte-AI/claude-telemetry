"""
Safe regex - the one way user-written patterns are compiled and matched.

User patterns (policies, scan profiles) run against agent-produced text. Python's
`re` backtracks with no timeout, so one bad pattern can stall a hook until it
times out and its check is skipped. User patterns therefore run on RE2, whose
match time grows only with input length, whatever the pattern. RE2 rejects
lookaround, backreferences and atomic groups; the errors below say so in words a
profile author can act on.

Rules for callers:
  * Import nothing but this module, so replacing the engine touches this file alone.
  * Ask only "does it match?" (`Pattern.search`, a bool) unless the caller must
    know where. Then use `Finder.finditer`, which yields plain `Hit` values with
    character offsets. Neither returns a match object, so no caller depends on
    engine-specific capture behaviour.
  * Bound the input yourself: RE2's cost is linear in the text, so a cap on the
    text is a hard cap on the time. A caller that cannot cap its input checks a
    clock between `Hit`s instead.
  * Never fall back to `re`: if the engine cannot be loaded, patterns fail to
    compile with an error saying why, so a profile is never safe on one machine
    and able to hang on another.

The engine is imported on first compile, so a profile with no regex conditions
never pays for it.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Iterator
from typing import Any, NamedTuple

ENGINE = "RE2"
PACKAGE = "google-re2"

# Compiled patterns kept per process. Profiles hold tens of patterns, not
# thousands; the bound only stops a pathological caller growing it forever.
_CACHE_SIZE = 512


class RegexError(ValueError):
    """A pattern that cannot be used. `str(exc)` is written for the pattern's author."""


class Pattern:
    """A compiled user pattern. It answers one question: does it occur in the text?"""

    __slots__ = ("source", "_compiled")

    def __init__(self, source: str, compiled: Any) -> None:
        self.source = source
        self._compiled = compiled

    def search(self, text: str) -> bool:
        """True when the pattern occurs anywhere in `text`."""
        return self._compiled.search(text) is not None

    def __repr__(self) -> str:
        return f"Pattern({self.source!r})"


class Hit(NamedTuple):
    """One occurrence of a pattern. `start` and `end` are character offsets into the text."""

    value: str
    start: int
    end: int


class Finder:
    """A compiled user pattern that reports where it occurs, not just whether."""

    __slots__ = ("source", "_compiled")

    def __init__(self, source: str, compiled: Any) -> None:
        self.source = source
        self._compiled = compiled

    def finditer(self, text: str) -> Iterator[Hit]:
        """
        Yield every occurrence of the pattern in `text`, lazily and in order.

        A hit is group 1 when the pattern has one that matched some text, else
        the whole match. Whatever the match holds around group 1 is context, so
        the search resumes at the end of group 1: a boundary written as text,
        `(tok)(?:$|[^A-Za-z0-9])`, then never hides the next token's own boundary
        the way it would if the search resumed after the whole match, which is
        where a lookahead would have left it.
        """
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            # A lone surrogate cannot be encoded for the engine. Replacing it
            # with "?" keeps one character per character, so offsets still hold.
            text = text.encode("utf-8", "replace").decode("utf-8")

        compiled = self._compiled
        grouped = compiled.groups > 0
        pos = 0
        while True:
            resume = None
            # The engine's own iteration is one pass over the text. Only a
            # match that leaves context after group 1 restarts it, which
            # costs one more pass.
            for m in compiled.finditer(text, pos):
                start, end = m.span()
                if grouped:
                    group_start, group_end = m.span(1)
                    if group_end > group_start >= 0:
                        if group_end < end:
                            resume = group_end
                        start, end = group_start, group_end
                yield Hit(text[start:end], start, end)
                if resume is not None:
                    break
            if resume is None:
                return
            pos = resume

    def __repr__(self) -> str:
        return f"Finder({self.source!r})"


@functools.cache
def _engine() -> tuple[Any, Any]:
    """
    (module, options), loaded on first use.

    A failed load is not cached, so installing the package fixes a long-running
    process on its next compile rather than on its next restart.
    """
    try:
        import re2

        options = re2.Options()
        # Errors come back to the caller as RegexError. Left on, RE2 also logs
        # every rejected pattern to stderr, which a hook would show as noise.
        options.log_errors = False
        # Only match/no-match is ever asked for; capturing costs time for nothing.
        options.never_capture = True
    except Exception as exc:  # ImportError, or a native build that fails to load
        from src.common.package_repair import report_if_missing

        report_if_missing(exc, PACKAGE)
        raise RegexError(
            f"regex conditions need the {PACKAGE} package, which could not be loaded "
            f"({type(exc).__name__}: {exc}). They are disabled until it is installed - "
            f"run `uv sync` in the plugin directory."
        ) from exc
    return re2, options


@functools.lru_cache(maxsize=_CACHE_SIZE)
def compile(pattern: str) -> Pattern:  # noqa: A001 - mirrors re.compile on purpose
    """Compile a user pattern, or raise RegexError explaining why it cannot be used."""
    engine, options = _engine()
    try:
        return Pattern(pattern, engine.compile(pattern, options))
    except engine.error as exc:
        raise RegexError(explain(_engine_message(exc), pattern)) from None


@functools.cache
def _capture_options() -> Any:
    """Options for a `Finder`: the same as `compile`'s, but groups are captured."""
    engine, _ = _engine()
    options = engine.Options()
    options.log_errors = False
    return options


def compile_finder(pattern: str) -> Finder:
    """
    Compile a user pattern that must report where it matched, or raise RegexError.

    For patterns whose author reads their own matches (a profile's secret
    patterns); a pattern that only has to match or not goes through `compile`.
    The error wording names the rewrite for a pattern's lookaround boundary.
    """
    # A profile is YAML, so a pattern can arrive as a number or a list.
    if not isinstance(pattern, str):
        raise RegexError(f"a pattern must be text, not {type(pattern).__name__}.")
    return _compile_finder(pattern)


@functools.lru_cache(maxsize=_CACHE_SIZE)
def _compile_finder(pattern: str) -> Finder:
    engine, _ = _engine()
    try:
        return Finder(pattern, engine.compile(pattern, _capture_options()))
    except engine.error as exc:
        raise RegexError(
            explain(_engine_message(exc), pattern, lookaround_hint=_FINDER_LOOKAROUND_HINT)
        ) from None


def check(pattern: str, finder: bool = False) -> str | None:
    """
    Validate a pattern wherever one can enter (an editor, the profile loader).
    None means usable; otherwise the reason it is not. It is a compile, so a
    pattern that passes is guaranteed to compile at load.

    `finder` checks it as `compile_finder` would compile it, for patterns whose
    matches are read back.
    """
    try:
        (compile_finder if finder else compile)(pattern)
    except RegexError as exc:
        return str(exc)
    return None


# ── Error messages ────────────────────────────────────────────────────────────
#
# RE2's own messages name the offending token ("invalid perl operator: (?=")
# but not the feature, and not what to do instead. The features below are the
# ones people reach for out of habit from other regex dialects.

_UNSUPPORTED_GROUPS = (
    ("(?<=", "lookbehind `(?<=...)`"),
    ("(?<!", "negative lookbehind `(?<!...)`"),
    ("(?=", "lookahead `(?=...)`"),
    ("(?!", "negative lookahead `(?!...)`"),
)
_LOOKAROUND_HINT = "put that requirement in a second condition on the rule instead."
# For a pattern that reports its own matches, where the context can be matched
# as text and the value to report put in group 1.
_FINDER_LOOKAROUND_HINT = (
    "match that context as text instead and put the part to report in group 1 - "
    "e.g. replace a lookbehind boundary with `(?:^|[^A-Za-z0-9])(...)`, "
    "a lookahead with `(...)(?:$|[^A-Za-z0-9])`."
)

_BACKREFERENCE = re.compile(r"invalid escape sequence: \\[1-9]")
# A quantifier followed by `+`: `a++`, `a*+`, `a?+`, `a{2}+`. Not `+*`, which is
# simply two quantifiers in a row and gets the engine's own message.
_POSSESSIVE = re.compile(r"^(?:[*+?]|\{[\d,]+\})\+$")
_MAX_REPEAT = 1000


def _engine_message(exc: BaseException) -> str:
    raw = exc.args[0] if exc.args else ""
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)


def explain(message: str, pattern: str = "", lookaround_hint: str = _LOOKAROUND_HINT) -> str:
    """
    Turn an engine error into a sentence naming the feature and the fix.

    The pattern is consulted where the engine's message is too terse to tell
    features apart: RE2 reports a named backreference `(?P=name)` only as `(?P`.
    `lookaround_hint` is the advice for lookaround, which depends on what the
    caller's patterns are part of.
    """
    unsupported = f"is not supported by {ENGINE}"

    if message.startswith("invalid perl operator: "):
        token = message.split(": ", 1)[1]
        for prefix, feature in _UNSUPPORTED_GROUPS:
            if token.startswith(prefix):
                return f"{feature} {unsupported} - {lookaround_hint}"
        if token.startswith("(?>"):
            return (f"atomic group `(?>...)` {unsupported}, and is not needed: "
                    f"{ENGINE} never backtracks. Use a plain group `(?:...)`.")
        if token.startswith("(?P=") or (token == "(?P" and "(?P=" in pattern):
            return f"named backreference `(?P=name)` {unsupported} - match the literal text instead."
        if token.startswith("(?x"):
            return f"verbose mode `(?x)` {unsupported} - remove the whitespace and comments."
        return f"`{token}` {unsupported} ({message})."

    if _BACKREFERENCE.match(message):
        return f"backreferences like `\\1` are not supported by {ENGINE} - match the literal text instead."

    if message == "invalid escape sequence: \\Z":
        return f"`\\Z` {unsupported} - use `\\z` or `$` for the end of the text."

    if message.startswith("bad repetition operator: "):
        token = message.split(": ", 1)[1]
        if _POSSESSIVE.match(token):
            return (f"possessive quantifier `{token}` {unsupported}, and is not needed: "
                    f"{ENGINE} never backtracks. Drop the trailing `+`.")

    if message.startswith("invalid repetition size: "):
        token = message.split(": ", 1)[1]
        bounds = re.fullmatch(r"\{(\d+),(\d+)\}", token)
        if bounds and int(bounds.group(1)) > int(bounds.group(2)):
            return f"repetition `{token}` has its minimum above its maximum."
        return f"repetition count `{token}` is too large - {ENGINE} allows at most {_MAX_REPEAT}."

    return message
