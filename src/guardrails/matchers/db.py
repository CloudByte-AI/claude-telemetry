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

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            found = _migration(command)
            if found:
                tool, direction = found
                return self.hit(target=tool, command=command, tool=tool, direction=direction)
        return None
