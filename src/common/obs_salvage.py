"""
Deterministic repair for deviant record_observation payloads.

Canonical implementation. `src/mcp/server.py` carries an inline copy because
`scripts/start_mcp.py` launches it by script path, so `import src...` is not
available there; `tests/test_obs_contract.py` asserts the two agree.

Models deviate from the advertised contract in four observed ways:

  omission    a required field is simply absent
  renaming    real content arrives under a name the schema does not use
  unparsed    the client could not parse the model's JSON and wrapped it whole
  mis-close   a prose parameter was closed with a name-matched tag such as
              </narrative> instead of the generic </parameter>, so the harness
              scanned on to the next real </parameter> and swallowed the block
              that followed into the prose value

Only the last one is both silent and destructive, and it is the only one where
the lost content is still sitting in the payload. Everything here is pure
repair - no model round-trip, no network, no I/O.
"""

import json
import re
from typing import Any, Dict, List, Tuple


OBS_FIELDS = (
    "type", "title", "subtitle", "narrative",
    "facts", "concepts", "files_read", "files_modified",
)

OBS_LIST_FIELDS = ("facts", "concepts", "files_read", "files_modified")

# Non-schema keys observed carrying real content, mapped to where they belong.
# Grow this from the `unknown=[...]` lists in the OBS_INCOMPLETE log lines:
#   grep -o "unknown=\[[^]]*\]" ~/.cloudbyte/logs/*/*.log | sort | uniq -c | sort -rn
OBS_ALIASES = {
    "summary": "subtitle",
    "description": "subtitle",
    "details": "narrative",
    "what_happened": "narrative",
    "body": "narrative",
    "key_facts": "facts",
    "observation_type": "type",
    "tags": "concepts",
    "files_touched": "files_read",
}

# Editorial inventions that map to nothing. Dropped, but named in the log so
# they are visible rather than silently discarded.
OBS_DROP = ("assumptions", "project", "autocancel")

# A prose value that swallowed the parameter block after it. The head is
# non-greedy so the FIRST mis-close wins, which is where the real prose ends.
XML_LEAK_RE = re.compile(
    r"^(?P<head>.*?)</(?P<tag>[A-Za-z_][A-Za-z0-9_]*)>\s*"
    r'<parameter name="(?P<field>[A-Za-z_][A-Za-z0-9_]*)">\s*(?P<value>.*)$',
    re.S,
)

_SCALAR_RE = r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"'
_ARRAY_RE = r'"%s"\s*:\s*(\[[^\]]*\])'


def coerce_obs_value(field: str, text: str) -> Any:
    """Parse a recovered parameter value into the type its schema declares."""
    text = (text or "").strip()
    want_list = field in OBS_LIST_FIELDS
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return [text] if want_list and text else text
    if want_list and not isinstance(parsed, list):
        return [parsed] if parsed else []
    return parsed


def extract_fields_from_raw(text: str) -> Dict[str, Any]:
    """Best-effort field recovery from tool input JSON the client could not parse.

    Deliberately regex-based rather than a JSON repairer: the payload is broken
    by definition, so the goal is to rescue the well-formed leading fields
    rather than to reconstruct a valid document.
    """
    out: Dict[str, Any] = {}
    if not isinstance(text, str):
        return out
    for field in OBS_FIELDS:
        pattern = _ARRAY_RE if field in OBS_LIST_FIELDS else _SCALAR_RE
        match = re.search(pattern % re.escape(field), text)
        if not match:
            continue
        raw = match.group(1)
        try:
            out[field] = json.loads(raw if raw.startswith("[") else f'"{raw}"')
        except (json.JSONDecodeError, ValueError):
            continue
    return out


def salvage_obs_args(args: Any) -> Tuple[Dict[str, Any], List[str]]:
    """Repair a deviant payload. Returns (payload, repairs_applied).

    Never raises and never invents content: every value in the result was
    present in the input. An unrepairable payload comes back unchanged with an
    empty repair list, for the caller to reject or store as it sees fit.
    """
    repairs: List[str] = []
    if not isinstance(args, dict):
        return {}, repairs
    args = dict(args)

    # 1. The client could not parse the model's JSON and wrapped it whole.
    if "__unparsedToolInput" in args:
        wrapper = args.pop("__unparsedToolInput")
        raw = wrapper.get("raw") if isinstance(wrapper, dict) else wrapper
        recovered: Dict[str, Any] = {}
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                recovered = parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, ValueError):
                recovered = extract_fields_from_raw(raw)
        if recovered:
            repairs.append(f"unwrapped __unparsedToolInput ({len(recovered)} fields recovered)")
            recovered.update({k: v for k, v in args.items() if v})
            args = recovered

    # 2. A prose parameter closed with a name-matched tag and swallowed the
    #    block after it. Loop, because a chain of mis-closes nests.
    for _ in range(len(OBS_FIELDS)):
        leaked = None
        for key, value in args.items():
            if isinstance(value, str) and "<parameter name=" in value:
                match = XML_LEAK_RE.match(value)
                if match:
                    leaked = (key, match)
                    break
        if not leaked:
            break
        key, match = leaked
        field, tail = match.group("field"), match.group("value")
        # The swallowed block ends at the real </parameter>, if one survived.
        tail = tail.split("</parameter>", 1)[0].strip()
        if args.get(field):
            break  # already populated - never clobber real content
        args[key] = match.group("head")
        args[field] = coerce_obs_value(field, tail)
        repairs.append(f"recovered {field} from a </{match.group('tag')}> mis-close in {key}")

    # 3. Content sent under a name we recognise but do not use.
    for wrong, right in OBS_ALIASES.items():
        if wrong in args and not args.get(right):
            args[right] = args.pop(wrong)
            repairs.append(f"mapped {wrong} to {right}")

    # 4. Inventions that map to nothing.
    for junk in [k for k in OBS_DROP if k in args]:
        args.pop(junk)
        repairs.append(f"dropped {junk}")

    return args, repairs
