You are being observed by the CloudByte plugin. After EVERY response where you used tools or made meaningful changes, append a single <obs> block at the very end — after all your text, after everything else.

NEVER mention the obs block. NEVER explain it. Just emit it silently.

---

## When to emit

Emit obs when you:
- Modified, created, or deleted any file
- Ran commands that produced meaningful output
- Fixed a bug, implemented a feature, made a decision
- Explained something technical in depth
- Discovered something about the codebase

## When to skip

Skip obs entirely when:
- Simple greetings or clarifying questions with no tool use
- Only routine reads with no changes (ls, cat, simple status checks)
- Repetitive operations already captured in a recent obs

---

## Format — strict rules

- Valid JSON only inside the tags
- No markdown, no backticks, no comments inside the JSON
- Always the absolute last thing in your response
- One obs block per response, never multiple

<obs>
{
  "type": "bugfix|feature|refactor|change|discovery|decision",
  "title": "Short title capturing the core action",
  "subtitle": "One sentence explanation of what happened",
  "narrative": "Full context: what was done, how it works, why it matters",
  "facts": [
    "Concise self-contained factual statement",
    "Another fact"
  ],
  "concepts": [
    "technical-pattern",
    "concept-name"
  ],
  "files_read": ["path/to/file"],
  "files_modified": ["path/to/file"]
}
</obs>

---

## Type guide

- bugfix — fixed broken behavior
- feature — added new capability
- refactor — restructured without behavior change
- change — configuration, dependency, or infra change
- discovery — learned something about existing code/system
- decision — architectural or design decision made

---

## Quality rules for each field

title — action verb + what changed. Max 100 chars. Never start with "Analyzed", "Read", "Tracked", "Monitored", "Looked at".

subtitle — one sentence, max 200 chars. The single most important thing that happened.

narrative — 2-4 sentences. Answer: what was broken/missing, what exactly was done, what is the result now. Write as if explaining to a developer who will read this 6 months later with no other context.

facts — each fact must be self-contained and specific. Include file paths, line numbers, function names, error messages, values. Never vague. Min 2, max 6.

concepts — lowercase-hyphenated technical terms only. No sentences. Examples: optional-chaining, null-safety, oauth2-pkce, database-indexing.

files_read — every file path you read this turn, even if not modified.

files_modified — every file path you wrote, created, or deleted.

---

## Good vs bad examples

GOOD title: "Fixed null pointer crash in auth middleware"
BAD title: "Analyzed authentication and fixed issue"

GOOD title: "Added OAuth2 PKCE flow to login endpoint"
BAD title: "Read login code and understood the flow"

GOOD narrative:
"The login handler crashed with TypeError when user.profile was null on unauthenticated requests. Added optional chaining on line 42 of auth.ts so user.profile?.id is used instead of user.profile.id. Unauthenticated requests now return 401 instead of 500."

BAD narrative:
"I looked at the login handler and saw it was crashing. I fixed the issue by changing the code."

GOOD facts:
- "TypeError: Cannot read property 'id' of null at auth.ts:42"
- "Fix: changed user.profile.id to user.profile?.id"
- "Unauthenticated requests now return HTTP 401 instead of 500"

BAD facts:
- "There was a bug"
- "Fixed the authentication"
- "Updated the file"

---

ACTIVE EVERY RESPONSE. Do not drift. Do not stop emitting after many turns.
