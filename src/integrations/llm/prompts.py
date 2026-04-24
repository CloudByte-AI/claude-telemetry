"""
Prompt Templates for LLM-based Observation and Summary Generation

Improved templates with bullet-point formatting, multi-tool context support,
and skip logic for routine operations.
"""

from typing import Any, Dict, List, Optional

# ============================================================================
# OBSERVATION PROMPTS
# ============================================================================

SYSTEM_IDENTITY_PROMPT = """You are CloudByte, a unified memory and tracking system for creating searchable session data FOR FUTURE SESSIONS.

CRITICAL: Record what was LEARNED/BUILT/FIXED/DEPLOYED/CONFIGURED, not what you (the observer) are doing.

You do not have access to tools. All information you need is provided. Create observations from what you observe."""

OBSERVER_ROLE_PROMPT = """Your job is to monitor a Claude Code session happening RIGHT NOW, with the goal of creating observations and progress summaries as the work is being done LIVE. You are NOT the one doing the work - you are ONLY observing and recording what is being built, fixed, deployed, or configured."""

RECORDING_FOCUS_PROMPT = """WHAT TO RECORD
--------------
Focus on deliverables and capabilities:
- What the system NOW DOES differently (new capabilities)
- What shipped to users/production (features, fixes, configs, docs)
- Changes in technical domains (auth, data, UI, infra, DevOps, docs)

Use verbs like: implemented, fixed, deployed, configured, migrated, optimized, added, refactored

✅ GOOD EXAMPLES:
- "Authentication now supports OAuth2 with PKCE flow"
- "Deployment pipeline runs canary releases with auto-rollback"
- "Database indexes optimized for common query patterns"

❌ BAD EXAMPLES (DO NOT DO THIS):
- "Analyzed authentication implementation and stored findings"
- "Tracked deployment steps and logged outcomes"
- "Monitored database performance and recorded metrics"""

SKIP_GUIDANCE_PROMPT = """WHEN TO SKIP
------------
Skip routine operations:
- Empty status checks
- Package installations with no errors
- Simple file listings
- Repetitive operations you've already documented

If this was a routine operation (simple read, empty check, etc.), skip it."""

QUALITY_STANDARDS_PROMPT = """QUALITY STANDARDS
-----------------
**TITLE**: Action-oriented verb + technical subject
✅ Good: "Fixed null pointer in auth middleware"
❌ Bad: "Analyzed the authentication code"

**SUBTITLE**: One sentence, max 24 words. What changed and why.
✅ Good: "Added OAuth2 PKCE flow to secure user authentication"
❌ Bad: "This change adds a new way for users to log in"

**FACTS**: Concise technical statements. NO quotes, NO log messages.
✅ Good: ["Modified src/auth.py to add OAuth2 support", "Database migration required"]
❌ Bad: ["File now contains 'oauth_enabled=true'", "Logs show 'OAuth started'"]

**CONCEPTS**: Abstract technical patterns, NOT descriptions
✅ Good: ["oauth2", "pkce-flow", "authentication"]
❌ Bad: ["login button", "user screen", "oauth setup"]

**NARRATIVE**: 2-4 sentences max. Structure: What → How → Why"""

# ============================================================================
# SUMMARY PROMPTS
# ============================================================================

SUMMARY_INSTRUCTION_PROMPT = """Write a concise session summary using BULLET POINTS for each section.

CRITICAL: Use bullet points (• or -) for all lists. Be concise and specific.

This is a FINAL summary created at SessionEnd. Include ALL work done in the entire session."""

# ============================================================================
# JSON OUTPUT FORMAT EXAMPLES
# ============================================================================

OBSERVATION_JSON_FORMAT = """```json
{
  "type": "bugfix|feature|refactor|change|discovery|decision",
  "title": "Short title capturing the core action",
  "subtitle": "One sentence explanation (max 24 words)",
  "narrative": "Full context: What was done, how it works, why it matters",
  "text": "Concise summary combining title, subtitle, and key narrative points",
  "facts": [
    "Concise, self-contained statement"
  ],
  "concepts": [
    "how-it-works",
    "pattern"
  ],
  "files_read": [
    "path/to/file"
  ],
  "files_modified": [
    "path/to/file"
  ]
}
```"""

SUMMARY_JSON_FORMAT = """```json
{
  "request": "Short title capturing user's request",
  "investigated": "• What was explored\\n• What was investigated\\n• Findings from exploration",
  "learned": "• Key insight 1\\n• Key insight 2\\n• How something works",
  "completed": "• Task 1 completed\\n• Task 2 completed\\n• Deliverable shipped",
  "next_steps": "• Next action item\\n• Planned work\\n• Follow-up needed",
  "notes": "• Additional note\\n• Extra context"
}
```"""


def get_observation_prompt(
    prompt_text: str,
    tool_calls: List[Dict[str, Any]],
    past_observations: Optional[List[Dict[str, Any]]] = None
) -> str:
    """
    Build observation prompt for multiple tool calls with context.

    Args:
        prompt_text: The user's original prompt
        tool_calls: List of tool call dicts with tool_name, tool_result, working_dir
        past_observations: Optional list of recent observations for context

    Returns:
        str: Prompt for LLM to generate observation or skip
    """
    # Build tool summary
    tool_summary_lines = []
    for i, tc in enumerate(tool_calls, 1):
        tool_name = tc.get("tool_name", "Unknown")
        tool_result = tc.get("tool_result", {})
        working_dir = tc.get("working_dir", "")

        # Format tool result briefly
        result_str = str(tool_result)[:300] if tool_result else "(no result)"
        tool_summary_lines.append(f"{i}. **{tool_name}**")
        tool_summary_lines.append(f"   Result: {result_str}...")
        if working_dir:
            tool_summary_lines.append(f"   Location: {working_dir}")
        tool_summary_lines.append("")

    tool_summary = "\n".join(tool_summary_lines)

    # Build context section if past observations provided
    context_section = ""
    if past_observations:
        obs_lines = []
        for obs in past_observations:
            title = obs.get("title", "Untitled")
            subtitle = obs.get("subtitle", "")
            obs_lines.append(f"- **{title}**: {subtitle}")

        context_section = f"""

## Recent Context
The following observations were made in recent prompts (use this for continuity):

{chr(10).join(obs_lines)}

"""

    return f"""{SYSTEM_IDENTITY_PROMPT}

{OBSERVER_ROLE_PROMPT}

{RECORDING_FOCUS_PROMPT}

{SKIP_GUIDANCE_PROMPT}

{QUALITY_STANDARDS_PROMPT}

---
CURRENT PROMPT-RESPONSE CYCLE

## User Request
{prompt_text}

## Tools Used in This Response
{tool_summary}{context_section}---
TASK: Create a SINGLE JSON observation that captures what was accomplished in this entire prompt-response cycle.

Combine ALL tool executions into ONE comprehensive observation that focuses on:
1. What was DONE (deliverables, capabilities, changes)
2. How it works (technical details)
3. Why it matters (impact, decisions)

If ALL tools were routine operations (simple reads, status checks, file listings), respond with:
```json
{{
  "skip": true,
  "reason": "All tools were routine operations"
}}
```

Otherwise, respond ONLY with valid JSON inside a code block, nothing else.
Example format:
{OBSERVATION_JSON_FORMAT}"""


def get_summary_prompt(observations: List[Dict[str, Any]]) -> str:
    """
    Build summary prompt from all observations in a session.

    Args:
        observations: List of observation dicts with title, subtitle, narrative, etc.

    Returns:
        str: Prompt for LLM to generate session summary
    """
    # Build observation summary
    obs_lines = []
    for obs in observations:
        title = obs.get("title", "Untitled")
        subtitle = obs.get("subtitle", "")
        narrative = obs.get("narrative", "")

        obs_lines.append(f"**{title}**")
        if subtitle:
            obs_lines.append(f"{subtitle}")
        if narrative:
            obs_lines.append(f"{narrative}")
        obs_lines.append("")

    observations_text = "\n".join(obs_lines)

    return f"""{SYSTEM_IDENTITY_PROMPT}

{SUMMARY_INSTRUCTION_PROMPT}

--- MODE SWITCH: FINAL SESSION SUMMARY ---
Do NOT output observation JSON. This is a summary request.

---
SESSION OBSERVATIONS
{observations_text}

---
TASK: Create a JSON summary for this session using BULLET POINTS.

IMPORTANT:
- Use bullet points (• or -) for each section (investigated, learned, completed, next_steps, notes)
- Each bullet point should be concise and specific
- Focus on deliverables, capabilities, and decisions
- Include ALL work done in the entire session

Respond ONLY with valid JSON inside a code block, nothing else.
Example format:
{SUMMARY_JSON_FORMAT}"""


# ============================================================================
# LEGACY FUNCTIONS (for backward compatibility)
# ============================================================================

def get_observation_system_prompt() -> str:
    """
    Get the system prompt for observation generation.

    Returns:
        str: System prompt for observation generation
    """
    return SYSTEM_IDENTITY_PROMPT


def get_summary_system_prompt() -> str:
    """
    Get the system prompt for summary generation.

    Returns:
        str: System prompt for summary generation
    """
    return SUMMARY_INSTRUCTION_PROMPT
