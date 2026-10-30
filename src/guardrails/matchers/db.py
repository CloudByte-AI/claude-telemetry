"""
Database matchers.

SQL arrives as an argument to a client (`psql -c "DROP TABLE users"`), so these
match on the SQL verb inside the command rather than on the command name alone.
Word-boundary anchored: a table called `dropbox_events` must not read as a DROP.
"""

from __future__ import annotations

import re

from src.guardrails.matchers.base import (BaseMatcher, MatchResult, option_of, remote_runs, summary_path,
                                          summary_value, targets_production)
from src.guardrails.matchers.inline_code import code_sql, code_targets_production
from src.guardrails.registry import register_matcher
from src.guardrails.toolcall import KIND_MCP, KIND_SHELL, ToolCall

_CLIENTS = frozenset({
    "psql", "mysql", "mysqladmin", "mariadb", "sqlite3", "mongo", "mongosh",
    "redis-cli", "clickhouse-client", "cockroach", "sqlcmd", "prisma", "flyway",
})
# Command-line tools that drop a whole database or role on their own.
_DROP_COMMANDS = {"dropdb": "database", "dropuser": "role"}
# Framework and ORM commands that drop or wipe a whole database: (command, subcommand words, flag needed).
_RESET_COMMANDS = (
    ("prisma", ("migrate", "reset"), None),
    ("prisma", ("db", "push"), "--force-reset"),
    ("rails", ("db:drop",), None), ("rails", ("db:reset",), None), ("rails", ("db:purge",), None),
    ("rake", ("db:drop",), None), ("rake", ("db:reset",), None), ("rake", ("db:purge",), None),
    ("sequelize", ("db:drop",), None),
    ("typeorm", ("schema:drop",), None),
)
# Django's `manage.py flush` / `reset_db` empty or recreate the database.
_MANAGE_RESETS = frozenset({"flush", "reset_db"})
# Mongo shell drops: `db.dropDatabase()`, `db.orders.drop()`.
_MONGO_DROP = re.compile(r"\.drop(Database)?\(\s*\)")

# MCP tools that send SQL: a tool named for SQL or queries, with the statement
# in one of these arguments.
_SQL_TOOL_WORDS = frozenset({"sql", "query", "execute", "statement", "statements"})
_SQL_ARGUMENTS = ("sql", "query", "statement", "statements", "sqlQuery", "sql_query")
# Servers whose `query` is a log or metrics query, not SQL.
_NOT_SQL_SERVERS = re.compile(r"datadog|logs|metrics|grafana|prometheus|sentry|splunk|elastic", re.IGNORECASE)
_WORD_SPLIT = re.compile(r"[a-z0-9]+")


def _mcp_sql(call: ToolCall) -> str | None:
    """The SQL an MCP tool call sends, or None."""
    if call.kind != KIND_MCP or not isinstance(call.raw_input, dict):
        return None
    if _NOT_SQL_SERVERS.search(call.mcp_server or "") or _NOT_SQL_SERVERS.search(call.mcp_tool or ""):
        return None
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", call.mcp_tool or "").lower()
    if not set(_WORD_SPLIT.findall(spaced)) & _SQL_TOOL_WORDS:
        return None
    parts = []
    for key in _SQL_ARGUMENTS:
        value = call.raw_input.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(v) for v in value if isinstance(v, str))
    return "\n;".join(parts) or None


def _sql_sources(call: ToolCall) -> list[tuple[str, str, str, object]]:
    """
    (sql, client, channel, command) for every place this call sends SQL: a
    database client's arguments and here-document (channel `client`), a client
    run inside a container or on another machine (`docker_exec`,
    `kubectl_exec`, `ssh`; the command is the wrapper), an MCP SQL tool
    (`mcp`, no command), and SQL that inline interpreter code sends through a
    database driver (`code`; the client is the driver).
    """
    sources: list[tuple[str, str, str, object]] = []
    for command in call.commands:
        if command.command_name in _CLIENTS:
            for text in _sql_texts(command):
                sources.append((text, command.command_name, "client", command))
    for run in remote_runs(call):
        for inner in run.commands:
            if inner.command_name in _CLIENTS:
                for text in _sql_texts(inner):
                    sources.append((text, inner.command_name, run.channel, run.wrapper))
    sql = _mcp_sql(call)
    if sql:
        server = (call.mcp_server or "").removeprefix("mcp__").removesuffix("__") or "mcp"
        sources.append((sql, server, "mcp", None))
    for text, driver, command in code_sql(call):
        sources.append((text, driver, "code", command))
    return sources


def _production(call: ToolCall, channel: str, command) -> bool:
    """Whether a source's database is production: from the command's words, or the host in inline code."""
    if command is None:
        return False
    if channel == "code":
        return code_targets_production(command) or targets_production(command, call)
    return targets_production(command, call)


def _reset_command(command) -> str | None:
    """The tool name when this command drops or wipes a whole database through a framework or ORM."""
    name = command.command_name.lower()
    path = tuple(command.subcommand_path)
    for tool, words, flag in _RESET_COMMANDS:
        if name == tool and path[:len(words)] == words and (flag is None or flag in command.flags):
            return tool
    if name in ("python", "python3", "py") and len(path) >= 2 and path[0].replace("\\", "/").endswith("manage.py") \
            and path[1] in _MANAGE_RESETS:
        return "django"
    return None


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
# Flags that only ask a tool about itself; nothing is read from a database.
_TOOL_PROBE_FLAGS = frozenset({"--version", "-V", "--help", "-?"})


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


# The object a statement acts on: `UPDATE orders SET`, `DELETE FROM orders`,
# `INSERT INTO orders`, `DROP TABLE IF EXISTS orders`, `ALTER TABLE orders`.
_STATEMENT_OBJECT = (r"\b{verb}\s+(?:(?:into|from|table|or|replace|if|not|exists|unique|index|view|schema|"
                     r"database|collection|only)\s+)*"
                     r"([`\"\[]?[\w$]+[`\"\]]?(?:\.[`\"\[]?[\w$]+[`\"\]]?)*)")
# How each statement reads before its object: `INSERT into orders`, `DELETE from orders`.
_STATEMENT_WORDS = {"insert": "INSERT into", "delete": "DELETE from", "merge": "MERGE into", "replace": "REPLACE into"}
_NO_OBJECT_STATEMENTS = frozenset({"grant", "revoke", "copy", "upsert"})
_NOT_OBJECT_NAMES = frozenset({"set", "select", "values", "where", "on", "to"})
# Client options naming the database host (never a connection URL: it can hold a password).
_HOST_OPTIONS = ("-h", "--host", "--hostname")


def _statement_object(result: MatchResult, verb: str) -> str | None:
    """The table (or other object) the SQL in the command that hit acts on; None when unclear."""
    if not verb or verb in _NO_OBJECT_STATEMENTS or result.command is None:
        return None
    text = " ".join(result.command.argv)
    found = re.search(_STATEMENT_OBJECT.format(verb=re.escape(verb)), text, re.IGNORECASE)
    if not found:
        return None
    name = ".".join(part.strip("`\"[]") for part in found.group(1).split("."))
    return None if name.lower() in _NOT_OBJECT_NAMES else summary_value(name)


def _db_host(result: MatchResult) -> str | None:
    """The host a database client run here was pointed at with a host option, else None."""
    if result.facts.get("channel") != "client":
        return None
    # sqlcmd's -h is the header-rows count (`-h -1`); its host is -S.
    names = ("-s", "--server") if result.facts.get("client") == "sqlcmd" else _HOST_OPTIONS
    return summary_value(option_of(result.command, *names))


def _db_where(result: MatchResult, *extra: str) -> str:
    """` (psql)`, ` (psql, production)`, plus any extra notes."""
    parts = [str(result.facts.get("client") or ""), "production" if result.facts.get("production") else "", *extra]
    parts = [part for part in parts if part]
    return f" ({', '.join(parts)})" if parts else ""


@register_matcher
class DatabaseDropMatcher(BaseMatcher):
    OPERATION = "db.drop"
    DOMAIN = "db"
    DESCRIPTION = "Drops a database, schema, table or collection"
    APPLIES_TO = (KIND_SHELL, KIND_MCP)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "critical"
    # channel: client (a database client run here), docker_exec,
    # kubectl_exec, ssh (a client run in a container or on another machine),
    # mcp (an MCP SQL tool) or command (dropdb, dropuser).
    FACTS = ("object_type", "client", "channel", "production")

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            object_type = _DROP_COMMANDS.get(command.command_name)
            if object_type and command.subcommand_path:
                return self.hit(target=command.subcommand_path[-1], command=command, object_type=object_type,
                                client=command.command_name, channel="command",
                                production=targets_production(command, call))
            reset = _reset_command(command)
            if reset:
                return self.hit(target=reset, command=command, object_type="database", client=reset,
                                channel="command", production=targets_production(command, call))
        for text, client, channel, command in _sql_sources(call):
            mongo = _MONGO_DROP.search(text) if client in ("mongo", "mongosh") else None
            if mongo:
                object_type = "database" if mongo.group(1) else "collection"
                return self.hit(target=object_type, evidence=mongo.group(0), command=command,
                                object_type=object_type, client=client, channel=channel,
                                production=_production(call, channel, command))
            found = _DROP.search(text)
            if found:
                return self.hit(
                    target=found.group(1).lower(),
                    evidence=found.group(0),
                    command=command,
                    object_type=found.group(1).lower(),
                    client=client,
                    channel=channel,
                    production=_production(call, channel, command),
                )
        return None

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        facts = result.facts
        name = summary_value(result.target) if facts.get("channel") == "command" \
            else _statement_object(result, "drop")
        what = f"{facts.get('object_type')} {name}" if name else f"a {facts.get('object_type')}"
        return f"drop {what}{_db_where(result)}"


@register_matcher
class DatabaseTruncateMatcher(BaseMatcher):
    OPERATION = "db.truncate"
    DOMAIN = "db"
    DESCRIPTION = "Empties a table (SQL TRUNCATE sent to a database client)"
    APPLIES_TO = (KIND_SHELL, KIND_MCP)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "critical"
    FACTS = ("table", "client", "channel", "production")

    def match(self, call: ToolCall) -> MatchResult | None:
        for text, client, channel, command in _sql_sources(call):
            found = _TRUNCATE.search(text)
            if found:
                table = found.group(1).strip("`\"[]")
                return self.hit(
                    target=table,
                    evidence=found.group(0).strip().lstrip(";").strip(),
                    command=command,
                    table=table,
                    client=client,
                    channel=channel,
                    production=_production(call, channel, command),
                )
        return None

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        return f"empty table {result.facts.get('table')}" \
               f"{' (production)' if result.facts.get('production') else ''}"


# One SQL statement at a time: split on `;` (good enough for statements sent
# from a command line; a `;` inside a string literal only splits a statement
# further, which can hide a WHERE but never invent one).
_STATEMENTS = re.compile(r";")
_DELETE_ALL = re.compile(r"^\s*delete\s+from\s+([`\"\[]?[\w.$]+[`\"\]]?)\s*(?:as\s+\w+\s*)?$", re.IGNORECASE | re.DOTALL)
_UPDATE_ALL = re.compile(r"^\s*update\s+([`\"\[]?[\w.$]+[`\"\]]?)\s+set\s+(?:(?!\bwhere\b).)*$", re.IGNORECASE | re.DOTALL)
_REDIS_FLUSH = frozenset({"flushall", "flushdb"})
_MONGO_DELETE_ALL = re.compile(r"\.(?:deleteMany|remove)\s*\(\s*\{\s*\}\s*\)")


def _bulk_statement(text: str) -> tuple[str, str] | None:
    """(statement, table) for a DELETE or UPDATE with no WHERE, or a delete-everything call."""
    if _MONGO_DELETE_ALL.search(text):
        return "delete", "collection"
    for statement in _STATEMENTS.split(text):
        cleaned = re.sub(r"--[^\n]*", "", statement).strip()
        found = _DELETE_ALL.match(cleaned)
        if found:
            return "delete", found.group(1).strip("`\"[]")
        found = _UPDATE_ALL.match(cleaned)
        if found:
            return "update", found.group(1).strip("`\"[]")
    return None


@register_matcher
class BulkDeleteMatcher(BaseMatcher):
    OPERATION = "db.bulk_delete"
    DOMAIN = "db"
    DESCRIPTION = "Deletes or overwrites every row in one statement (no WHERE)"
    APPLIES_TO = (KIND_SHELL, KIND_MCP)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "warn"
    # statement: delete (DELETE FROM t, deleteMany({})), update (UPDATE t SET
    # ... with no WHERE) or flush (redis FLUSHALL / FLUSHDB). table: the table.
    FACTS = ("statement", "table", "client", "channel", "production")

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in self.commands_named(call, ("redis-cli",)):
            flush = next((a for a in command.subcommand_path if a.lower() in _REDIS_FLUSH), None)
            if flush:
                return self.hit(target=flush.upper(), command=command, statement="flush", table=None,
                                client="redis-cli", channel="client", production=targets_production(command, call))
        for text, client, channel, command in _sql_sources(call):
            if client == "redis-cli" and any(w.lower() in _REDIS_FLUSH for w in text.split()):
                return self.hit(target="FLUSH", command=command, statement="flush", table=None, client=client,
                                channel=channel, production=_production(call, channel, command))
            found = _bulk_statement(text)
            if found:
                statement, table = found
                return self.hit(target=table, evidence=text[:200], command=command, statement=statement, table=table,
                                client=client, channel=channel,
                                production=_production(call, channel, command))
        return None

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        facts = result.facts
        if facts.get("statement") == "flush":
            return "delete every key (redis)"
        verb = "delete every row of" if facts.get("statement") == "delete" else "overwrite every row of"
        return f"{verb} {facts.get('table')}{' (production)' if facts.get('production') else ''}"


# A statement that changes data or schema, at the start of a statement.
_WRITE = re.compile(
    r"(?:^|;|\()\s*(insert\s+into|update\s+[`\"\[]?[\w.$]+[`\"\]]?\s+set|delete\s+from|merge\s+into|upsert|"
    r"replace\s+into|alter\s+(?:table|database|schema|index|view|user|role|type|sequence)|"
    r"create\s+(?:or\s+replace\s+)?(?:unique\s+)?(?:table|index|schema|view|database|function|procedure|trigger|"
    r"extension|policy|role|user|type|sequence|materialized\s+view)|drop\s+\w+|truncate\s+|grant\s+|revoke\s+|"
    r"rename\s+table|comment\s+on|copy\s+[\w.\"]+\s+from|"
    # SQL Server system procedures that change users, roles, permissions or settings.
    r"exec(?:ute)?\s+(?:[\w\[\]]+\.)*\[?sp_(?:add|drop|grant|revoke|change|rename|set)\w*)",
    re.IGNORECASE | re.MULTILINE,
)
_ROLLED_BACK = re.compile(r"(?:^|;)\s*rollback\s*;?\s*$", re.IGNORECASE)


@register_matcher
class DatabaseWriteMatcher(BaseMatcher):
    OPERATION = "db.write"
    DOMAIN = "db"
    DESCRIPTION = "Changes data or schema in a database (INSERT, UPDATE, DELETE, DDL)"
    APPLIES_TO = (KIND_SHELL, KIND_MCP)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "warn"
    # statement: the first changing statement's verb (insert, update, delete,
    # create, alter, drop, grant...). channel: as for db.drop. production: the
    # client, container, pod or host named is a production one. rolled_back:
    # the SQL ends in ROLLBACK, a rehearsal that keeps nothing.
    FACTS = ("statement", "client", "channel", "production", "rolled_back")

    def match(self, call: ToolCall) -> MatchResult | None:
        for text, client, channel, command in _sql_sources(call):
            found = _WRITE.search(text)
            if found:
                statement = found.group(1).split()[0].lower()
                return self.hit(target=client, evidence=found.group(0).strip(" ;(")[:120], command=command,
                                statement=statement, client=client, channel=channel,
                                production=_production(call, channel, command),
                                rolled_back=bool(_ROLLED_BACK.search(text.strip())))
        return None

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        statement = str(result.facts.get("statement") or "")
        table = _statement_object(result, statement)
        text = f"{_STATEMENT_WORDS.get(statement, statement.upper())} {table}" if table \
            else f"run SQL {statement.upper()}"
        host = _db_host(result)
        extra = ("rolled back",) if result.facts.get("rolled_back") else ()
        return f"{text}{f' on {host}' if host else ''}{_db_where(result, *extra)}"


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
                # `pg_dump --version` / `mysqldump --help` checks the tool is there.
                if command.flags & _TOOL_PROBE_FLAGS:
                    continue
                destination = command.redirects[0] if command.redirects else None
                return self.hit(
                    target=destination,
                    command=command,
                    tool=command.command_name,
                    destination=destination,
                )
        return None

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        command = result.command
        operands = command.subcommand_path if command is not None else ()
        # sqlite3 takes the database file first (`sqlite3 app.db .dump`); the dump tools take it last.
        positional = (operands[0] if result.facts.get("tool") == "sqlite3" else operands[-1]) if operands else None
        database = summary_value(option_of(command, "-d", "--dbname", "--db", "--database")) \
            or summary_value(positional)
        destination = result.facts.get("destination")
        if str(destination).lstrip("&") in ("1", "2") or str(destination).lower() in ("/dev/null", "nul"):
            destination = None
        destination = summary_path(destination) or summary_path(
            option_of(command, "-f", "--file", "--out", "-o", "--result-file"))
        text = f"export database {database}" if database else "export a database"
        return f"{text}{f' to {destination}' if destination else ''} ({result.facts.get('tool')})"


# Migration tools: name -> (whether the run may sit anywhere among the
# arguments - after connection options such as `goose postgres "$DSN" up` -
# and the runs that change the schema, with their direction). Runs that only
# report (status, diff, info, history) or write a new migration FILE (create,
# generate, revision, makemigrations) are not here: they change no database.
_UP, _DOWN, _RESET, _PUSH = "up", "down", "reset", "push"
_RAILS_TASKS = {("db:migrate",): _UP, ("db:prepare",): _UP, ("db:rollback",): _DOWN,
                ("db:migrate:down",): _DOWN, ("db:migrate:redo",): _DOWN, ("db:schema:load",): _RESET,
                ("db:reset",): _RESET, ("db:drop",): _RESET, ("db:setup",): _RESET}
_TYPEORM = {("migration:run",): _UP, ("migration:revert",): _DOWN, ("schema:sync",): _PUSH, ("schema:drop",): _RESET}
_SEQUELIZE = {("db:migrate",): _UP, ("db:migrate:undo",): _DOWN, ("db:migrate:undo:all",): _DOWN}
_MIGRATORS: dict[str, tuple[bool, dict[tuple[str, ...], str]]] = {
    "prisma": (False, {("migrate", "deploy"): _UP, ("migrate", "dev"): _UP, ("migrate", "reset"): _RESET,
                       ("db", "push"): _PUSH}),
    "flyway": (True, {("migrate",): _UP, ("undo",): _DOWN, ("clean",): _RESET}),
    "liquibase": (True, {("update",): _UP, ("updatecount",): _UP, ("updatetotag",): _UP,
                         ("rollback",): _DOWN, ("rollbackcount",): _DOWN, ("rollbacktodate",): _DOWN,
                         ("dropall",): _RESET}),
    "alembic": (True, {("upgrade",): _UP, ("downgrade",): _DOWN}),
    "django-admin": (True, {("migrate",): _UP}),
    "rails": (True, _RAILS_TASKS),
    "rake": (True, _RAILS_TASKS),
    "knex": (True, {("migrate:latest",): _UP, ("migrate:up",): _UP, ("migrate:rollback",): _DOWN,
                    ("migrate:down",): _DOWN}),
    "sequelize": (True, _SEQUELIZE),
    "sequelize-cli": (True, _SEQUELIZE),
    "typeorm": (True, _TYPEORM),
    "typeorm-ts-node-commonjs": (True, _TYPEORM),
    "typeorm-ts-node-esm": (True, _TYPEORM),
    "drizzle-kit": (False, {("migrate",): _UP, ("push",): _PUSH}),
    "goose": (True, {("up",): _UP, ("upbyone",): _UP, ("upto",): _UP, ("down",): _DOWN,
                     ("downto",): _DOWN, ("redo",): _DOWN, ("reset",): _RESET}),
    "migrate": (True, {("up",): _UP, ("down",): _DOWN, ("goto",): _UP, ("drop",): _RESET}),
    "dbmate": (True, {("up",): _UP, ("migrate",): _UP, ("down",): _DOWN, ("rollback",): _DOWN,
                      ("drop",): _RESET}),
    "atlas": (True, {("migrate", "apply"): _UP, ("schema", "apply"): _PUSH}),
    "diesel": (False, {("migration", "run"): _UP, ("migration", "revert"): _DOWN,
                       ("migration", "redo"): _DOWN, ("database", "reset"): _RESET}),
    "sqlx": (False, {("migrate", "run"): _UP, ("migrate", "revert"): _DOWN, ("database", "reset"): _RESET}),
    "dotnet": (False, {("ef", "database", "update"): _UP, ("ef", "database", "drop"): _RESET}),
    "dotnet-ef": (False, {("database", "update"): _UP, ("database", "drop"): _RESET}),
    "mix": (True, {("ecto.migrate",): _UP, ("ecto.rollback",): _DOWN, ("ecto.reset",): _RESET,
                   ("ecto.drop",): _RESET}),
    "supabase": (False, {("db", "push"): _PUSH, ("db", "reset"): _RESET, ("migration", "up"): _UP}),
    "hasura": (False, {("migrate", "apply"): _UP}),
}
# Laravel: `php artisan migrate[:fresh|:refresh|:reset|:rollback]`.
_ARTISAN = {"migrate": _UP, "migrate:install": _UP, "migrate:rollback": _DOWN, "migrate:reset": _RESET,
            "migrate:refresh": _RESET, "migrate:fresh": _RESET}
# Options that only show what WOULD run, write a migration file without
# applying it (`prisma migrate dev --create-only`), or print help.
_MIGRATION_PREVIEWS = frozenset({
    "--dry-run", "--plan", "--check", "--sql", "--pretend", "--dry", "--create-only", "--help", "-h",
})


def _token(word: str) -> str:
    """`update-count`, `updateCount`, `up_by_one` -> one comparable spelling."""
    return word.lower().replace("-", "").replace("_", "")


def _migration(command) -> tuple[str, str] | None:
    """(tool, direction) when this command applies, reverts or resets a schema."""
    name = command.command_name.lower()
    path = command.subcommand_path
    # Read as written: many migration tools take single-dash words (`-path`),
    # which the parser also splits into letters, and one of them would be `-h`.
    if {a.lower().partition("=")[0] for a in command.argv} & _MIGRATION_PREVIEWS:
        return None
    # Django: `python manage.py migrate`.
    if name in ("python", "python3", "py") and len(path) >= 2 \
            and path[0].replace("\\", "/").rsplit("/", 1)[-1] == "manage.py" and path[1] == "migrate":
        return "django", _UP
    if name == "php" and len(path) >= 2 and path[0].replace("\\", "/").rsplit("/", 1)[-1] == "artisan" \
            and path[1].lower() in _ARTISAN:
        return "artisan", _ARTISAN[path[1].lower()]
    spec = _MIGRATORS.get(name)
    if spec is None:
        return None
    anywhere, runs = spec
    tokens = [_token(w) for w in path]
    for run, direction in runs.items():
        wanted = [_token(w) for w in run]
        if anywhere:
            found = any(tokens[i:i + len(wanted)] == wanted for i in range(len(tokens)))
        else:
            found = tokens[:len(wanted)] == wanted
        if found:
            return name, direction
    return None


@register_matcher
class MigrationMatcher(BaseMatcher):
    OPERATION = "db.migration"
    DOMAIN = "db"
    DESCRIPTION = "Applies, reverts or resets a database schema migration"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "warn"
    # tool: prisma, flyway, alembic, django, rails, artisan, knex, ... direction:
    # up (apply), down (revert), reset (drop and rebuild - destroys data) or
    # push (sync the schema directly, without migration files).
    FACTS = ("tool", "direction")

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        words = {"up": "apply", "down": "revert", "reset": "reset", "push": "push"}
        return f"{words.get(result.facts.get('direction'), 'run')} database migrations ({result.facts.get('tool')})"

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            found = _migration(command)
            if found:
                tool, direction = found
                return self.hit(target=tool, command=command, tool=tool, direction=direction)
        return None



# What a statement looks like, so option values (a host, a user, a database
# name) are not one: SQL keywords at a statement start, psql meta-commands
# (`\dt`), a mongo shell call (`db.users.find(...)`, never a host such as
# `db.internal.example.com`), a redis command word.
_SQL_STATEMENT = re.compile(
    r"(?:^|;|\()\s*(?:select|with|show|describe|desc|explain|insert|update|delete|merge|upsert|replace|alter|"
    r"create|drop|truncate|grant|revoke|copy|call|exec|execute|begin|values|table|pragma|use|set)\b"
    r"|^\s*\\[a-z]+|\bdb\.[\w$]+(?:\.[\w$]+)*\s*\(",
    re.IGNORECASE | re.MULTILINE,
)
_REDIS_COMMANDS = frozenset({
    "get", "set", "mget", "mset", "del", "keys", "scan", "exists", "type", "ttl", "expire", "incr", "decr",
    "hget", "hgetall", "hset", "hdel", "lrange", "lpush", "rpush", "smembers", "sadd", "zrange", "zadd",
    "flushall", "flushdb", "info", "dbsize", "config", "client", "monitor", "eval", "publish", "xadd", "xrange",
})


def _is_statement(text: str, client: str) -> bool:
    """Whether `text` given to `client` is a statement rather than an option value."""
    if client == "redis-cli":
        return text.strip().split(" ", 1)[0].lower() in _REDIS_COMMANDS
    return bool(_SQL_STATEMENT.search(text))

@register_matcher
class DatabaseQueryMatcher(BaseMatcher):
    OPERATION = "db.query"
    DOMAIN = "db"
    DESCRIPTION = "Sends a statement to a database, read or write"
    APPLIES_TO = (KIND_SHELL, KIND_MCP)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "info"
    # Every statement a database client is given, reads included, by every
    # channel db.write reads (a client here, in a container or pod, over ssh, an
    # MCP SQL tool), so a rule can ask before anything touches a production
    # database. statement_kind: write (what db.write detects) or read.
    FACTS = ("statement_kind", "client", "channel", "production")

    def match(self, call: ToolCall) -> MatchResult | None:
        sources = [s for s in _sql_sources(call) if _is_statement(s[0], s[1])]
        if not sources:
            return None
        writes = [s for s in sources if _WRITE.search(s[0])]
        text, client, channel, command = (writes or sources)[0]
        return self.hit(target=client, evidence=text.strip()[:120], command=command,
                        statement_kind="write" if writes else "read", client=client, channel=channel,
                        production=_production(call, channel, command))

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        kind = "change data" if result.facts.get("statement_kind") == "write" else "read data"
        host = _db_host(result)
        where = f" on {host}" if host else ""
        return f"{kind}{where}{_db_where(result)}"
