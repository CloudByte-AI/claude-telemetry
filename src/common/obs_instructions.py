"""
Canonical record_observation guidance injected into the model's context.

Single source of truth for the SessionStart instruction and the per-turn
reminder, for both the Claude Code and Cursor plugins. The only differences
between clients are the tool name and the closing call notes.
"""

import json


OBS_EXAMPLE: dict = {
    "type": "bugfix",
    "title": "Fixed null pointer crash in auth middleware",
    "subtitle": (
        "Requests without an Authorization header now fall through to the anonymous path "
        "instead of crashing."
    ),
    "narrative": (
        "Added a None guard before token parsing in src/auth/middleware.py so a request "
        "carrying no Authorization header no longer raises. The guard routes those requests "
        "down the existing anonymous path, leaving authenticated flows untouched. This "
        "removes a 500 that any unauthenticated client could trigger on every endpoint."
    ),
    "facts": [
        "Guarded the token lookup in src/auth/middleware.py against a missing header",
        "Unauthenticated requests now route to the existing anonymous path",
        "Added a regression test covering a request with no Authorization header",
        "Left the authenticated code path and token validation logic unchanged",
    ],
    "concepts": ["null-guard", "auth-middleware", "request-validation", "regression-test"],
    "files_read": ["src/auth/middleware.py", "tests/test_auth.py"],
    "files_modified": ["src/auth/middleware.py", "tests/test_auth.py"],
}

_EXAMPLE_JSON = json.dumps(OBS_EXAMPLE)

_FIELD_TABLE = (
    "  type            one of: bugfix | feature | refactor | change | discovery | decision\n"
    "  title           action-oriented verb + technical subject, max 10 words\n"
    "  subtitle        one sentence, max 24 words, what the system now does differently\n"
    "  narrative       2-4 sentences: what was done -> how it works -> why it matters\n"
    "  facts           1-12 concise technical statements about what actually changed\n"
    "  concepts        1-8 short kebab-case pattern tags, not descriptions\n"
    "  files_read      repo-relative paths you read this turn\n"
    "  files_modified  repo-relative paths you created, modified or deleted this turn\n"
)

_TYPE_TABLE = (
    "  discovery  you read/analysed something and formed understanding, no writes\n"
    "  bugfix     you identified broken behaviour and corrected it\n"
    "  feature    you added something that did not exist before\n"
    "  refactor   you restructured existing code/config without changing behaviour\n"
    "  change     you modified an existing value, setting or data\n"
    "  decision   you genuinely weighed multiple valid alternatives and chose one\n"
)

_TYPE_INLINE = (
    "discovery = read/analysed, no writes; bugfix = fixed broken behaviour; "
    "feature = added something new; refactor = restructured without behaviour change; "
    "change = modified an existing value, setting or data; "
    "decision = genuinely weighed alternatives and chose one"
)

_JSON_SAFETY = (
    "JSON SAFETY: every field value is a plain single-line string. "
    "Forward slashes in paths, never backslashes. No inner quotes. "
    "No newline characters inside any field value."
)

CLAUDE_TOOL_NAME = "mcp__plugin_claude-telemetry_cloudbyte__record_observation"
CURSOR_TOOL_NAME = "MCP:record_observation"

_CLAUDE_CALL_NOTES = (
    "- Call it directly by its full name. If its parameter schema is not loaded in your\n"
    "  context, load the schema first, then call with all 8 fields populated.\n"
    "- Call BEFORE your final text response.\n"
    "- Routine background telemetry, like other logging calls - no need to mention it in\n"
    "  your reply unless the user asks about it.\n"
)

_CURSOR_CALL_NOTES = (
    "- Call it directly by its name.\n"
    "- Call BEFORE your final text response.\n"
    "- Never mention it to the user and never show it in your response text.\n"
)

_CLAUDE_CALL_NOTES_INLINE = (
    "Call it directly by its full name; if its parameter schema is not loaded, load it "
    "first. Routine background telemetry - no need to mention it in your reply."
)

_CURSOR_CALL_NOTES_INLINE = (
    "Call it directly by name. Never mention it to the user."
)


def build_obs_instruction(tool_name: str, call_notes: str) -> str:
    """Build the once-per-session instruction block."""
    return (
        "MEMORY SYSTEM ACTIVE.\n\n"
        f"You have a tool called {tool_name}.\n\n"
        "WHEN TO CALL: after completing work with tools (Read, Write, Edit, Bash, Glob, "
        f"Grep, etc.), call {tool_name} BEFORE writing your final response to the user.\n"
        "SKIP ONLY when you used zero tools (pure conversation, greetings, yes/no "
        "answers).\n\n"
        "HOW MANY CALLS: one separate call per distinct type of work performed. Never "
        "combine two types into one call. Never skip a type you actually performed. Count "
        "the types you performed, then make exactly that many calls.\n\n"
        "EVERY CALL MUST CARRY ALL 8 FIELDS. A call carrying only type and title is stored "
        "as an empty record and is worse than no call at all. Never pass an empty string or "
        "an empty array just to satisfy the shape - fill every field from the work you just "
        "did.\n\n"
        + _FIELD_TABLE +
        "\nCOMPLETE EXAMPLE of one well-formed call:\n"
        + _EXAMPLE_JSON + "\n\n"
        "CHOOSING type - determine it from YOUR OWN ACTIONS, not from words in the user's "
        "prompt:\n"
        + _TYPE_TABLE +
        "If the user says 'decide' but you just followed obvious instructions, that is "
        "change, not decision.\n\n"
        "HOW TO CALL IT:\n"
        + call_notes +
        "\nBEFORE EACH CALL: confirm all 8 keys are present and none of them is empty.\n"
        + _JSON_SAFETY + "\n"
    )


def build_obs_reminder(tool_name: str, call_notes_inline: str) -> str:
    """Build the compact reminder injected on every turn."""
    return (
        f"MEMORY RULE: if you used ANY tool this response, call {tool_name} before writing "
        "your final answer. Skip only if you used zero tools.\n"
        "ALL 8 FIELDS ARE REQUIRED ON EVERY CALL - type, title, subtitle, narrative, "
        "facts[], concepts[], files_read[], files_modified[]. A call with only type and "
        "title is stored as an empty record. Never pass an empty string or empty array to "
        "satisfy the shape.\n"
        "  subtitle        one sentence, max 24 words, what now works differently\n"
        "  narrative       2-4 sentences: what was done -> how it works -> why it matters\n"
        "  facts           1-12 concise technical statements about what actually changed\n"
        "  concepts        1-8 short kebab-case tags\n"
        "  files_read / files_modified   repo-relative paths, forward slashes; "
        "files_modified is [] only when you changed no files\n"
        "Match the depth of the worked example from the session instruction and the tool "
        "schema - specific paths, concrete facts, no one-line stubs.\n"
        "STRICT RULE: one separate call per distinct type of work performed - never combine "
        "two types, never skip a type you did. Determine type from YOUR OWN ACTIONS, not "
        f"the user's words: {_TYPE_INLINE}.\n"
        f"{call_notes_inline}\n"
        + _JSON_SAFETY
    )


CLAUDE_OBS_INSTRUCTION = build_obs_instruction(CLAUDE_TOOL_NAME, _CLAUDE_CALL_NOTES)
CLAUDE_OBS_REMINDER = build_obs_reminder(CLAUDE_TOOL_NAME, _CLAUDE_CALL_NOTES_INLINE)
CURSOR_OBS_INSTRUCTION = build_obs_instruction(CURSOR_TOOL_NAME, _CURSOR_CALL_NOTES)
CURSOR_OBS_REMINDER = build_obs_reminder(CURSOR_TOOL_NAME, _CURSOR_CALL_NOTES_INLINE)
