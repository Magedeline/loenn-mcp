"""Entry point of the loenn-mcp MCPB bundle (uv runtime).

The host runs `uv run --directory <bundle> src/server.py`. uv installs the
dependencies listed in the bundle's pyproject.toml; this file puts the bundle
root on sys.path so the loenn_mcp package next to it imports, then starts the
MCP server on stdio. LOENN_MCP_WORKSPACE comes from the bundle's user_config.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loenn_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
