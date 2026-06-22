"""
JDBC Connection String Detector.

Detects JDBC URLs that contain a password parameter:
  jdbc:mysql://host/db?user=u&password=secret
  jdbc:postgresql://host/db?user=u&password=secret
  jdbc:sqlserver://host;user=u;password=secret
  jdbc:oracle:thin:user/password@host:port/service

The secret captured is the value of the password keyword.
"""

import re
from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector


def _jdbc_kv_pattern(drivers: list[str]) -> re.Pattern:
    """JDBC URL with key=value password parameter (MySQL/PostgreSQL/MariaDB style)."""
    alt = "|".join(re.escape(d) for d in drivers)
    return re.compile(
        rf"jdbc:(?:{alt})://[^\s]+[?&;]password=([^\s&;\"']+)",
        re.IGNORECASE,
    )


@register_detector
class JDBCConnectionDetector(BaseDetector):
    CATEGORY           = "JDBC Connection"
    ENABLED_BY_DEFAULT = True
    DESCRIPTION        = "JDBC connection strings containing embedded passwords"
    DOMAIN             = "Database"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="JDBC MySQL / MariaDB",
            label="JDBC_MYSQL_PASSWORD",
            severity="HIGH",
            detection="pattern",
            capture_group=1,
            pattern=_jdbc_kv_pattern(["mysql", "mariadb"]),
            description="JDBC MySQL/MariaDB connection string with embedded password",
            example="jdbc:mysql://db.example.com/app?user=root&password=s3cr3tP4ss",
        ),
        TokenDefinition(
            type="JDBC PostgreSQL",
            label="JDBC_POSTGRES_PASSWORD",
            severity="HIGH",
            detection="pattern",
            capture_group=1,
            pattern=_jdbc_kv_pattern(["postgresql", "postgres"]),
            description="JDBC PostgreSQL connection string with embedded password",
            example="jdbc:postgresql://db.example.com/app?user=postgres&password=s3cr3tP4ss",
        ),
        TokenDefinition(
            type="JDBC SQL Server",
            label="JDBC_SQLSERVER_PASSWORD",
            severity="HIGH",
            detection="pattern",
            capture_group=1,
            # jdbc:sqlserver://host;user=u;password=secret;...
            pattern=re.compile(
                r"jdbc:sqlserver://[^\s]*[;,]password=([^\s;,\"']+)",
                re.IGNORECASE,
            ),
            description="JDBC SQL Server connection string with embedded password",
            example="jdbc:sqlserver://db.example.com;user=sa;password=s3cr3tP4ss",
        ),
        TokenDefinition(
            type="JDBC Oracle Thin",
            label="JDBC_ORACLE_PASSWORD",
            severity="HIGH",
            detection="pattern",
            capture_group=1,
            # jdbc:oracle:thin:user/password@//host:port/service
            pattern=re.compile(
                r"jdbc:oracle:thin:[^/\s]+/([^@\s]+)@",
                re.IGNORECASE,
            ),
            description="JDBC Oracle thin connection string with embedded password",
            example="jdbc:oracle:thin:sys/s3cr3tP4ss@//oracle.example.com:1521/XE",
        ),
        TokenDefinition(
            type="JDBC H2",
            label="JDBC_H2_PASSWORD",
            severity="MEDIUM",
            detection="pattern",
            capture_group=1,
            pattern=_jdbc_kv_pattern(["h2"]),
            description="JDBC H2 in-process database connection string with embedded password",
            example="jdbc:h2:mem:testdb?user=sa&password=s3cr3tP4ss",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return ["jdbc:mysql", "jdbc:postgresql", "jdbc:sqlserver", "jdbc:oracle", "jdbc:h2"]
