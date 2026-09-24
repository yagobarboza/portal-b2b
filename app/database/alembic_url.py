"""Helpers for safely passing database URLs through Alembic's ConfigParser."""


def escape_alembic_url(database_url: str) -> str:
    """Escape percent signs without changing the URL received by asyncpg.

    Alembic stores ``sqlalchemy.url`` in a ``ConfigParser``. Percent-encoded
    credentials such as ``%40`` must therefore be written as ``%%40`` at this
    boundary; reading the option returns the original URL again.
    """

    return database_url.replace("%", "%%")
