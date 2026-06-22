"""
Database Connection String Detector.

Detects connection strings that embed credentials:
  mongodb(+srv)://user:pass@host
  postgresql://user:pass@host
  mysql://user:pass@host
  redis://:password@host
  redis://user:password@host
  mssql://user:pass@host
  amqp(s)://user:pass@host  (RabbitMQ)
  smtp://user:pass@host     (mail relay)

Patterns are "structural" — the credential is captured from the URI itself.
"""

import re
from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector

# Reusable URI sub-patterns
_USERINFO     = r"[^:@/\s]{1,128}:[^@\s]{1,256}"   # user:pass
_HOST         = r"[^/\s@]+"                          # host[:port]
_OPTIONAL_DB  = r"(?:/[^\s]*)?"                       # /dbname?options — optional


def _conn_pattern(schemes: list[str]) -> re.Pattern:
    """Build a structural connection-string pattern for a list of URI schemes."""
    alt = "|".join(re.escape(s) for s in schemes)
    return re.compile(
        rf"(?:{alt})://({_USERINFO})@{_HOST}{_OPTIONAL_DB}",
        re.IGNORECASE,
    )


@register_detector
class DatabaseConnectionDetector(BaseDetector):
    CATEGORY           = "Database Connection"
    ENABLED_BY_DEFAULT = True
    DESCRIPTION        = "Database / message-broker connection strings with embedded credentials"
    DOMAIN             = "Database"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="MongoDB Connection String",
            label="MONGODB_CONNECTION_STRING",
            severity="HIGH",
            detection="pattern",
            capture_group=1,
            pattern=_conn_pattern(["mongodb", "mongodb+srv"]),
            description="MongoDB connection string with embedded credentials — grants full database access",
            example="mongodb://admin:s3cr3tP4ss@mongo.example.com:27017/mydb",
        ),
        TokenDefinition(
            type="PostgreSQL Connection String",
            label="POSTGRES_CONNECTION_STRING",
            severity="HIGH",
            detection="pattern",
            capture_group=1,
            pattern=_conn_pattern(["postgresql", "postgres"]),
            description="PostgreSQL connection string with embedded credentials",
            example="postgresql://postgres:s3cr3tP4ss@db.example.com:5432/appdb",
        ),
        TokenDefinition(
            type="MySQL Connection String",
            label="MYSQL_CONNECTION_STRING",
            severity="HIGH",
            detection="pattern",
            capture_group=1,
            pattern=_conn_pattern(["mysql", "mysql+mysqlconnector", "mysql+pymysql"]),
            description="MySQL/MariaDB connection string with embedded credentials",
            example="mysql://root:s3cr3tP4ss@mysql.example.com:3306/appdb",
        ),
        TokenDefinition(
            type="Redis Connection String",
            label="REDIS_CONNECTION_STRING",
            severity="HIGH",
            detection="pattern",
            capture_group=1,
            # redis://:pass@host  OR  redis://user:pass@host
            pattern=re.compile(
                r"rediss?://(?:[^:@/\s]{0,128}:[^@\s]{1,256}|:[^@\s]{1,256})@[^/\s@]+(?:/[^\s]*)?",
                re.IGNORECASE,
            ),
            description="Redis connection string with embedded password",
            example="redis://:s3cr3tP4ss@redis.example.com:6379/0",
        ),
        TokenDefinition(
            type="MSSQL Connection String",
            label="MSSQL_CONNECTION_STRING",
            severity="HIGH",
            detection="pattern",
            capture_group=1,
            pattern=_conn_pattern(["mssql", "sqlserver"]),
            description="Microsoft SQL Server connection string with embedded credentials",
            example="mssql://sa:s3cr3tP4ss@sqlserver.example.com:1433/appdb",
        ),
        TokenDefinition(
            type="AMQP Connection String",
            label="AMQP_CONNECTION_STRING",
            severity="HIGH",
            detection="pattern",
            capture_group=1,
            pattern=_conn_pattern(["amqp", "amqps"]),
            description="AMQP (RabbitMQ) connection string with embedded credentials",
            example="amqp://user:s3cr3tP4ss@rabbitmq.example.com:5672/vhost",
        ),
        TokenDefinition(
            type="SMTP Connection String",
            label="SMTP_CONNECTION_STRING",
            severity="MEDIUM",
            detection="pattern",
            capture_group=1,
            pattern=_conn_pattern(["smtp", "smtps", "smtp+tls"]),
            description="SMTP mail relay connection string with embedded credentials",
            example="smtp://user:s3cr3tP4ss@smtp.example.com:587",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return [
            "mongodb://", "mongodb+srv://",
            "postgresql://", "postgres://",
            "mysql://",
            "redis://", "rediss://",
            "mssql://", "sqlserver://",
            "amqp://", "amqps://",
            "smtp://", "smtps://",
        ]
