"""
Script matchers.

An agent often writes a shell script and runs it in a later call (`bash
deploy.sh`). The run command says nothing about what the script does, so the
only moment its content can be checked is when it is written. This matcher
parses a written script with the same shell parser and matchers as a Bash
command and reports the operations it found; rules decide which ones matter.
"""

from __future__ import annotations

import posixpath
import re

from src.guardrails.matchers.base import BaseMatcher, MatchResult, summary_value
from src.guardrails.parser import POSIX, POWERSHELL
from src.guardrails.registry import MatcherRegistry, register_matcher
from src.guardrails.toolcall import KIND_EDIT, KIND_SHELL, KIND_WRITE, ToolCall

# Script files by extension, and the dialect their content is parsed in.
_SCRIPT_DIALECTS = {".sh": POSIX, ".bash": POSIX, ".zsh": POSIX, ".ksh": POSIX, ".ps1": POWERSHELL,
                    ".psm1": POWERSHELL}
# A POSIX shell shebang on the first line makes any file a shell script.
_SHEBANG = re.compile(r"^#!\s*\S*/(?:env\s+)?(?:ba|z|k|da)?sh\b")
# Longer content is parsed up to this length: a script is short, and the check
# runs in front of the write.
_MAX_SCRIPT_CHARS = 200_000
# Operations that say nothing about risk inside a script: running an
# interpreter, deleting one file, the platform's own web tools.
_NOT_REPORTED = frozenset({"exec.interpreter", "fs.delete", "net.web.fetch", "net.web.search"})
# Operations that destroy work, reach other people or production, or change who
# can access what: the ones a script is most often written to hide from review.
_RISKY_OPERATIONS = frozenset({
    "fs.delete.recursive", "vcs.push", "vcs.force_push", "vcs.history.rewrite", "devflow.deploy",
    "infra.cloud.delete", "infra.iam.change", "infra.dns.change", "sec.firewall.change", "db.bulk_delete",
})
_STEPS_SHOWN = 2
_ALERT_ORDER = {"critical": 0, "warn": 1, "info": 2}


def _written_text(call: ToolCall) -> str:
    """What this Write or Edit puts into the file: the content, or the new text of each edit."""
    if call.content:
        return call.content
    raw = call.raw_input or {}
    parts = [raw.get("new_string")]
    parts += [edit.get("new_string") for edit in raw.get("edits") or [] if isinstance(edit, dict)]
    return "\n".join(part for part in parts if isinstance(part, str) and part)


def _dialect(path: str, text: str):
    """The dialect to parse this file in, or None when it is not a shell script."""
    _, extension = posixpath.splitext(path.replace("\\", "/").lower())
    if extension in _SCRIPT_DIALECTS:
        return _SCRIPT_DIALECTS[extension]
    return POSIX if _SHEBANG.match(text) else None


@register_matcher
class ScriptWriteMatcher(BaseMatcher):
    OPERATION = "fs.script.write"
    DOMAIN = "fs"
    DESCRIPTION = "Writes a shell script that runs risky operations"
    APPLIES_TO = (KIND_WRITE, KIND_EDIT)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "warn"
    # operations: the operations found in the written text, comma-separated and
    # sorted (`fs.delete.recursive,vcs.push`), so a rule can match the ones it
    # cares about with `contains` or `regex`. Clean-ups of build output and
    # single-file deletes are not reported. risky: one of them destroys work,
    # reaches other people or production, or changes access (a recursive
    # delete, a push or history rewrite, a deploy, a cloud delete, an IAM,
    # firewall or DNS change, an every-row delete), so a rule can ask on that
    # without a regex. language: shell or powershell. production: an operation
    # in it names a production target.
    FACTS = ("operations", "risky", "language", "production")

    def match(self, call: ToolCall) -> MatchResult | None:
        path = call.file_paths[0] if call.file_paths else ""
        text = _written_text(call)
        if not path or not text.strip():
            return None
        dialect = _dialect(path, text)
        if dialect is None:
            return None
        inner = ToolCall.build(
            platform=call.platform, tool_name="Bash", kind=KIND_SHELL, hook_event=call.hook_event,
            cwd=call.cwd, workspace_root=call.workspace_root, session_id=call.session_id,
            command=text[:_MAX_SCRIPT_CHARS], dialect=dialect,
        )
        from src.guardrails.engine import run_matchers   # the engine imports this package

        # A forced delete of single files and a clean-up of build output are
        # not reported, as the shipped presets do not ask about them either.
        found = [m for m in run_matchers(inner) if m.operation not in _NOT_REPORTED
                 and not (m.operation == "fs.delete.recursive"
                          and (m.facts.get("disposable") or not m.facts.get("recursive")))]
        if not found:
            return None
        steps = []
        # The most serious first: the alert level each operation's matcher ships with.
        for m in sorted(found, key=lambda m: _ALERT_ORDER.get(getattr(MatcherRegistry.get(m.operation),
                                                                     "DEFAULT_ALERT", ""), 3)):
            matcher = MatcherRegistry.get(m.operation)
            try:
                text = matcher.summary(m) if matcher else None
            except Exception:
                text = None
            if not text and matcher is not None and matcher.DESCRIPTION:
                text = matcher.DESCRIPTION[0].lower() + matcher.DESCRIPTION[1:]
            steps.append(text or m.operation)
        return self.hit(
            target=path, evidence=path,
            operations=",".join(sorted({m.operation for m in found})),
            risky=any(m.operation in _RISKY_OPERATIONS for m in found),
            language="powershell" if dialect is POWERSHELL else "shell",
            production=any(bool(m.facts.get("production")) for m in found),
            steps=" | ".join(steps),
        )

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        name = summary_value(posixpath.basename(str(result.target or "").replace("\\", "/"))) or "a script"
        steps = [s for s in str(result.facts.get("steps") or "").split(" | ") if s]
        if not steps:
            return f"write script {name}"
        shown = ", ".join(summary_value(s) or s for s in steps[:_STEPS_SHOWN])
        more = f" and {len(steps) - _STEPS_SHOWN} more" if len(steps) > _STEPS_SHOWN else ""
        return f"write script {name} that will {shown}{more}"
