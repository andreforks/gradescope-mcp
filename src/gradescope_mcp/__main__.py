"""Entry point for the Gradescope MCP server.

Usage:
    uv run python -m gradescope_mcp
"""

import logging

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


def main():
    from gradescope_mcp.server import mcp
    mcp.run()


if __name__ == "__main__":
    main()
