"""Entry point for the Gradescope MCP server.

Usage:
    uv run python -m gradescope_mcp
"""

import logging

from dotenv import load_dotenv

from gradescope_mcp.cache import configure_process_cache_env

# Load environment variables from .env file
load_dotenv()
cache_root = configure_process_cache_env()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


def main():
    logging.getLogger(__name__).info("Using runtime cache directory: %s", cache_root)
    from gradescope_mcp.server import mcp
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
