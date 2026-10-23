"""
Database matchers.

SQL arrives as an argument to a client (`psql -c "DROP TABLE users"`), so these
match on the SQL verb inside the command rather than on the command name alone.
Word-boundary anchored: a table called `dropbox_events` must not read as a DROP.
"""

from __future__ import annotations

import re

from src.guardrails.matchers.base import BaseMatcher, MatchResult
from src.guardrails.registry import register_matcher
from src.guardrails.toolcall import KIND_SHELL, ToolCall

_CLIENTS = frozenset({
    "psql", "mysql", "mysqladmin", "mariadb", "sqlite3", "mongo", "mongosh",
    "redis-cli", "clickhouse-client", "cockroach", "sqlcmd", "prisma", "flyway",
})

_DROP = re.compile(r"\bdrop\s+(database|schema|table|index|view|collection)\b", re.IGNORECASE)
# TRUNCATE as a statement: at the start of the SQL or after `;`, so a
# `WHERE note = 'truncate later'` value is not one.
_TRUNCATE = re.compile(
    r"(?:^|;)\s*truncate\s+(?:table\s+)?(?:only\s+)?(?:if\s+exists\s+)?([`\"\[]?[\w.$]+[`\"\]]?)",
    re.IGNORECASE | re.MULTILINE,
)
_DUMP_COMMANDS = frozenset({
    "pg_dump", "pg_dumpall", "mysqldump", "mongodump", "sqlite3",
    "redis-dump", "clickhouse-backup",
})


def _sql_texts(command) -> list[str]:
    """
    Where a database client's SQL is: its operands (`psql -c "DROP ..."`) and a
    here-document on its input (`psql <<'SQL' ... SQL`). Never the whole raw
    command, which would also match a `--comment "drop table"`.
    """
    texts = list(command.subcommand_path)
    if command.stdin:
        texts.append(command.stdin)
    return texts


@register_matcher
class DatabaseDropMatcher(BaseMatcher):
    OPERATION = "db.drop"
    DOMAIN = "db"
    DESCRIPTION = "Drops a database, schema, table or collection"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "critical"
    FACTS = ("object_type", "client")

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            if command.command_name not in _CLIENTS:
                continue
            for text in _sql_texts(command):
                found = _DROP.search(text)
                if found:
                    return self.hit(
                        target=found.group(1).lower(),
                        evidence=found.group(0),
                        command=command,
                        object_type=found.group(1).lower(),
                        client=command.command_name,
                    )
        return None


@register_matcher
class DatabaseTruncateMatcher(BaseMatcher):
    OPERATION = "db.truncate"
    DOMAIN = "db"
    DESCRIPTION = "Empties a table (SQL TRUNCATE sent to a database client)"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "critical"
    FACTS = ("table", "client")

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            if command.command_name not in _CLIENTS:
                continue
            for text in _sql_texts(command):
                found = _TRUNCATE.search(text)
                if found:
                    table = found.group(1).strip("`\"[]")
                    return self.hit(
                        target=table,
                        evidence=found.group(0).strip().lstrip(";").strip(),
                        command=command,
                        table=table,
                        client=command.command_name,
                    )
        return None


@register_matcher
class DatabaseDumpMatcher(BaseMatcher):
    OPERATION = "db.dump"
    DOMAIN = "db"
    DESCRIPTION = "Exports database contents to a file or stream"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "warn"
    FACTS = ("tool", "destination")

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            if command.command_name in _DUMP_COMMANDS:
                if command.command_name == "sqlite3" and not self.subcommand_is(command, ".dump"):
                    continue
                destination = command.redirects[0] if command.redirects else None
                return self.hit(
                    target=destination,
                    command=command,
                    tool=command.command_name,
                    destination=destination,
                )
        return None
