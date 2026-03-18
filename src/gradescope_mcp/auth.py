"""Authentication module for Gradescope.

Manages a singleton GSConnection instance with automatic re-login support.
"""

import os
import logging

from gradescopeapi.classes.connection import GSConnection

logger = logging.getLogger(__name__)

# Singleton connection instance
_connection: GSConnection | None = None


class AuthError(Exception):
    """Raised when authentication fails."""
    pass


def get_connection() -> GSConnection:
    """Get the authenticated GSConnection instance.

    Creates and caches a connection on first call. Subsequent calls
    return the cached instance.

    Returns:
        GSConnection: An authenticated Gradescope connection.

    Raises:
        AuthError: If credentials are missing or login fails.
    """
    global _connection

    if _connection is not None and _connection.logged_in:
        return _connection

    email = os.environ.get("GRADESCOPE_EMAIL")
    password = os.environ.get("GRADESCOPE_PASSWORD")

    if not email or not password:
        raise AuthError(
            "Missing Gradescope credentials. "
            "Set GRADESCOPE_EMAIL and GRADESCOPE_PASSWORD environment variables."
        )

    try:
        conn = GSConnection()
        conn.login(email, password)
        _connection = conn
        logger.info("Successfully logged in to Gradescope as %s", email)
        return _connection
    except ValueError as e:
        raise AuthError(f"Gradescope login failed: {e}") from e
    except Exception as e:
        raise AuthError(f"Unexpected error during login: {e}") from e


def reset_connection() -> None:
    """Reset the cached connection, forcing re-login on next access."""
    global _connection
    _connection = None
