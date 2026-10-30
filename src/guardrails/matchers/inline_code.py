"""
Inline code an interpreter runs, and the actions in it that shell rules already protect.

`python -c "..."`, `node -e "..."`, `ruby -e`, `php -r` and a here-document fed
to a bare `python` / `node` carry their program on the command line. The shell
parser sees only "an interpreter ran", so an agent can reach a database, a
credential file or a remote host there without any database, file or upload
rule seeing it. This module finds that code and recognises three things in it:

- SQL sent through a database driver (psycopg, pg, mysql2, sqlite3...): the
  string literals that are SQL statements, and whether the connection names a
  production host.
- A credential file opened by a literal path (`open('.env')`, `readFileSync`).
- Data sent to a remote host (`requests.post`, `fetch(url, {method: 'POST'})`).

Pattern based on the code text: the plugin never runs or fully parses the
language, so code that builds a path or a statement at run time is not seen.
"""

from __future__ import annotations

import re
from functools import lru_cache

from src.guardrails.matchers.base import BaseMatcher, names_production
from src.guardrails.matchers.exec import _inline_code_flag
from src.guardrails.parser import Command
from src.guardrails.toolcall import ToolCall

# Interpreters that may read their program from standard input (a here-document).
_STDIN_CODE = frozenset({"python", "python3", "py", "node", "ruby", "perl", "php", "bun"})

# Database drivers and clients a program imports, by language; the name is reported as `client`.
_DRIVERS = (
    ("psycopg", re.compile(r"\b(import\s+psycopg2?|from\s+psycopg2?\b|psycopg2?\.connect)")),
    ("asyncpg", re.compile(r"\b(import\s+asyncpg|asyncpg\.connect)")),
    ("pymysql", re.compile(r"\b(import\s+(pymysql|MySQLdb|mysql\.connector)|from\s+mysql\s+import\s+connector)")),
    ("pyodbc", re.compile(r"\bimport\s+(pyodbc|pymssql)\b")),
    ("sqlite3", re.compile(r"\bimport\s+sqlite3\b|require\(\s*['\"](better-)?sqlite3['\"]\s*\)")),
    ("sqlalchemy", re.compile(r"\b(from\s+sqlalchemy|import\s+sqlalchemy|create_engine\()")),
    ("pymongo", re.compile(r"\b(import\s+pymongo|from\s+pymongo|MongoClient\()")),
    ("pg", re.compile(r"require\(\s*['\"]pg(-promise)?['\"]\s*\)|from\s+['\"]pg['\"]|new\s+(Pool|Client)\(\s*\{[^}]*connectionString")),
    ("mysql2", re.compile(r"require\(\s*['\"]mysql2?(/promise)?['\"]\s*\)|from\s+['\"]mysql2?(/promise)?['\"]")),
    ("mssql", re.compile(r"require\(\s*['\"](mssql|tedious)['\"]\s*\)")),
    ("mongodb", re.compile(r"require\(\s*['\"]mongodb['\"]\s*\)|from\s+['\"]mongodb['\"]")),
    ("knex", re.compile(r"require\(\s*['\"]knex['\"]\s*\)|from\s+['\"]knex['\"]")),
    ("prisma", re.compile(r"\$(queryRaw|executeRaw)(Unsafe)?\b")),
    ("pdo", re.compile(r"new\s+PDO\(|mysqli_connect\(")),
)
# All driver patterns as one alternation, so a long program is scanned once; the
# group that matched names the driver.
_ANY_DRIVER = re.compile("|".join(f"(?P<d{i}>{rx.pattern})" for i, (_, rx) in enumerate(_DRIVERS)))
# Words one of the driver patterns needs, checked first because a plain `in` is far cheaper.
_DRIVER_HINTS = ("psycopg", "asyncpg", "mysql", "MySQLdb", "pyodbc", "pymssql", "sqlite", "sqlalchemy",
                 "create_engine", "pymongo", "MongoClient", "'pg", '"pg', "connectionString", "mssql", "tedious",
                 "mongodb", "knex", "queryRaw", "executeRaw", "PDO", "mysqli")
# A string literal: Python/JS single, double, triple and template quotes. Escapes are kept as written.
_LITERAL = re.compile(r"(\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|`[^`]*`|\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*')")
# A literal that is an SQL statement (or a Mongo drop).
_SQL_LITERAL = re.compile(
    r"^\s*(select|insert\s+into|update\s+\S+\s+set|delete\s+from|merge\s+into|upsert|replace\s+into|"
    r"alter\s+\w+|create\s+(or\s+replace\s+)?\w+|drop\s+\w+|truncate\b|grant\b|revoke\b|with\s+\w+\s+as\b|"
    r"copy\s+\S+\s+from|exec(ute)?\s+\w+)", re.IGNORECASE)
_MONGO_DROP = re.compile(r"\.drop(Database)?\(\s*\)|\.drop_database\(|\.drop_collection\(|\.delete_many\(\s*\{\s*\}\s*\)")
# Where a connection names its host: a URL, a host/server keyword, or an environment variable name.
_HOST_IN_CODE = (
    re.compile(r"[a-z][a-z0-9+.-]*://(?:[^@/\s'\"`]*@)?([^:/\s'\"`?]+)", re.IGNORECASE),
    re.compile(r"\b(?:host|hostname|server|data\s+source)\s*[:=]\s*['\"`]?([\w.-]+)", re.IGNORECASE),
    re.compile(r"(?:environ(?:\.get)?\s*[\[(]\s*|getenv\(\s*|process\.env\.)['\"]?([A-Z][A-Z0-9_]*(?:DATABASE|DB|PG|MYSQL|MONGO|SQL)[A-Z0-9_]*)"),
)

# Opening a file by a literal path, by language.
_FILE_READ = re.compile(
    r"(?:\bopen|read_text|read_bytes|readFileSync|readFile|createReadStream|File\.read|IO\.read|"
    r"file_get_contents|fopen|Path)\s*\(\s*(?:f|r|b)?(['\"`])([^'\"`\n]{1,300})\1")
# Sending data to a remote host.
_POST = (
    re.compile(r"\b(?:requests|httpx|session|client)\.(post|put|patch)\(\s*(?:f|r)?['\"]([^'\"\s]+)"),
    re.compile(r"\bfetch\(\s*['\"`]([^'\"`\s]+)['\"`]\s*,\s*\{[^}]*method\s*:\s*['\"`](POST|PUT|PATCH)['\"`]",
               re.IGNORECASE),
    re.compile(r"\burlopen\(\s*(?:urllib\.request\.)?Request\(\s*['\"]([^'\"\s]+)['\"][^)]*data\s*="),
    re.compile(r"\baxios\.(post|put|patch)\(\s*['\"`]([^'\"`\s]+)"),
)
_HOST_OF_URL = re.compile(r"^[a-z]+://(?:[^@/]*@)?([^:/?#]+)", re.IGNORECASE)


def inline_code(command: Command) -> str | None:
    """The program an interpreter command runs from its command line or a here-document, else None."""
    flag = _inline_code_flag(command)
    if flag is not None:
        argv = list(command.argv)
        for i, token in enumerate(argv):
            name, glued, value = token.partition("=")
            if name == flag and glued:
                return value
            if token == flag and i + 1 < len(argv):
                return argv[i + 1]
            if token.startswith("-") and not token.startswith("--") and token.endswith(flag[1:]) and i + 1 < len(argv):
                return argv[i + 1]     # a bundle ending in the code flag: `python -Ic "..."`
        return None
    if command.command_name in _STDIN_CODE and command.stdin and BaseMatcher.reads_code_from_stdin(command):
        return command.stdin
    return None


def inline_programs(call: ToolCall) -> list[tuple[str, Command]]:
    """(code, command) for every interpreter in this call that runs inline code."""
    found = []
    for command in call.commands:
        code = inline_code(command)
        if code:
            found.append((code, command))
    return found


def _literals(code: str) -> list[str]:
    """The text of every string literal in `code`, without its quotes."""
    out = []
    for match in _LITERAL.finditer(code):
        text = match.group(0)
        quote = 3 if text[:3] in ('"""', "'''") else 1
        out.append(text[quote:-quote] if len(text) >= 2 * quote else "")
    return out


# Several matchers ask about the same program in one call (every database
# matcher reads its SQL), so each analysis runs once per program text.
@lru_cache(maxsize=16)
def _sql_in(code: str) -> tuple[tuple[str, str], ...]:
    """(statement, driver) for each SQL statement `code` sends through a database driver."""
    if not any(hint in code for hint in _DRIVER_HINTS):
        return ()
    match = _ANY_DRIVER.search(code)
    if match is None:
        return ()
    driver = _DRIVERS[int(next(name for name, value in match.groupdict().items() if value is not None)[1:])][0]
    found = [(text, driver) for text in _literals(code) if _SQL_LITERAL.search(text)]
    if driver in ("pymongo", "mongodb") and _MONGO_DROP.search(code):
        found.append(("drop collection", driver))
    return tuple(found)


@lru_cache(maxsize=16)
def _production_in(code: str) -> bool:
    """Whether `code` names a production host or production connection variable."""
    url, keyword, variable = _HOST_IN_CODE
    candidates = [keyword]
    if "://" in code:
        candidates.append(url)
    if "environ" in code or "getenv" in code or "process.env" in code:
        candidates.append(variable)
    for pattern in candidates:
        for match in pattern.finditer(code):
            if names_production(match.group(1)):
                return True
    return False


@lru_cache(maxsize=16)
def _file_reads_in(code: str) -> tuple[str, ...]:
    """Every path `code` opens by a literal."""
    return tuple(match.group(2) for match in _FILE_READ.finditer(code))


_REQUEST_WORDS = re.compile(r"post|put|patch|urlopen", re.IGNORECASE)


@lru_cache(maxsize=16)
def _posts_in(code: str) -> tuple[str, ...]:
    """Every URL `code` sends a request body to."""
    if not _REQUEST_WORDS.search(code):
        return ()
    found = []
    for pattern in _POST:
        for match in pattern.finditer(code):
            url = next((g for g in match.groups() if g and "://" in g), None)
            if url:
                found.append(url)
    return tuple(found)


def code_sql(call: ToolCall) -> list[tuple[str, str, Command]]:
    """(statement, driver, command) for SQL that inline code sends through a database driver."""
    return [(text, driver, command) for code, command in inline_programs(call) for text, driver in _sql_in(code)]


def code_targets_production(command: Command) -> bool:
    """Whether the inline code names a production host or production connection variable."""
    return _production_in(inline_code(command) or "")


def code_file_reads(call: ToolCall) -> list[tuple[str, Command]]:
    """(path, command) for every file inline code opens by a literal path."""
    return [(path, command) for code, command in inline_programs(call) for path in _file_reads_in(code)]


def code_posts(call: ToolCall) -> list[tuple[str, Command]]:
    """(url, command) for every request inline code sends to a remote host with a body."""
    return [(url, command) for code, command in inline_programs(call) for url in _posts_in(code)]


def host_of(url: str) -> str | None:
    """The host of a URL, or None."""
    match = _HOST_OF_URL.match(url or "")
    return match.group(1).lower() if match else None
