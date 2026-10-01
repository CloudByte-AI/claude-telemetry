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
_DUMP_COMMANDS = frozenset({
    "pg_dump", "pg_dumpall", "mysqldump", "mongodump", "sqlite3",
    "redis-dump", "clickhouse-backup",
})


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
            # The SQL sits in an operand; searching the whole raw command would
            # also match a `--comment "drop table"`.
            for operand in command.subcommand_path:
                found = _DROP.search(operand)
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
