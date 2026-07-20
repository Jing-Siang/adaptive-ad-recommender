"""MCP server exposing the company ad-policy document as a resource.

Spawned as a short-lived subprocess (stdio transport) by the policy review
agent (app/policy_review.py) — not a persistent networked service, and not
meant for a human to connect to directly. See docs/spec.md for why.
"""

from pathlib import Path

from mcp.server.fastmcp import FastMCP

POLICY_PATH = Path(__file__).parent.parent / "app" / "policy" / "ad_policy.md"

mcp = FastMCP("ad-policy")


@mcp.resource("ad-policy://document")
def get_ad_policy() -> str:
    """The current company ad policy, used to review submitted campaigns."""
    return POLICY_PATH.read_text()


if __name__ == "__main__":
    mcp.run(transport="stdio")
