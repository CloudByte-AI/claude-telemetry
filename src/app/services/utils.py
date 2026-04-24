"""Shared utility functions used across services."""

from datetime import date, timedelta


def resolve_dates(dr: str, date_from: str, date_to: str) -> tuple[str, str]:
    """Convert a date range preset into concrete from/to date strings."""
    today = date.today()
    if dr == "all":
        return "2000-01-01", "2099-12-31"
    elif dr == "today":
        return today.isoformat(), today.isoformat()
    elif dr == "7days":
        return (today - timedelta(days=6)).isoformat(), today.isoformat()
    elif dr == "30days":
        return (today - timedelta(days=29)).isoformat(), today.isoformat()
    elif dr == "custom" and date_from and date_to:
        return date_from, date_to
    else:
        return today.isoformat(), today.isoformat()


def paginate(rows: list, page: int, per_page: int) -> tuple[list, dict]:
    """Slice a list of rows and return pagination metadata."""
    total    = len(rows)
    pages    = max(1, (total + per_page - 1) // per_page)
    page     = max(1, min(page, pages))
    sliced   = rows[(page - 1) * per_page: page * per_page]
    pg_start = max(1, page - 2)
    pg_end   = min(pages, page + 2)
    return sliced, {
        "page": page, "total": total, "pages": pages,
        "pg_start": pg_start, "pg_end": pg_end,
        "page_nums": list(range(pg_start, pg_end + 1)),
    }


def sum_token_row(row, keys=("inp", "out", "cr", "cc")) -> int:
    """Sum 4 token columns from a query row."""
    return sum(row[k] or 0 for k in keys)


def format_token_total(v: int) -> str:
    """Format a token count as M/K abbreviated string."""
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    elif v >= 1_000:
        return f"{v/1_000:.1f}K"
    return str(v)