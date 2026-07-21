import sys
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from tenacity import retry, stop_after_attempt, wait_random_exponential

from app.core.config import settings
from app.schemas import ReviewDecision

AD_POLICY_SERVER_PATH = Path(__file__).parent.parent.parent / "mcp_servers" / "ad_policy_server.py"

_SYSTEM_PROMPT = """You are an ad-policy reviewer. You will be given the company's ad policy \
and a submitted campaign's creative. Decide whether it should be approved, rejected, or needs \
a human moderator, following the policy's decision guidance exactly — prefer needs_review over \
guessing when genuinely ambiguous.

If the campaign's category requires context exclusions per the policy and the submission is \
missing them, add the required entries to excluded_categories yourself and approve — do not \
reject solely for a missing exclusion list."""


async def fetch_ad_policy() -> str:
    """Fetch the current ad-policy document via the ad-policy MCP resource server,
    spawned fresh as a subprocess for this call."""
    client = MultiServerMCPClient(
        {
            "ad_policy": {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(AD_POLICY_SERVER_PATH)],
            }
        }
    )
    blobs = await client.get_resources("ad_policy")
    return blobs[0].as_string()


@retry(stop=stop_after_attempt(4), wait=wait_random_exponential(multiplier=1, max=20), reraise=True)
async def _call_reviewer(policy_text: str, campaign_text: str) -> ReviewDecision:
    llm = ChatOpenAI(model=settings.openai_chat_model, api_key=settings.openai_api_key)
    structured_llm = llm.with_structured_output(ReviewDecision)
    return await structured_llm.ainvoke(
        [
            ("system", _SYSTEM_PROMPT),
            ("user", f"Ad policy:\n{policy_text}\n\nCampaign to review:\n{campaign_text}"),
        ]
    )


async def review_campaign(
    headline: str,
    description: str,
    category: str,
    excluded_categories: list[str],
) -> ReviewDecision:
    """Fetch the ad policy via MCP, ask the LLM for a structured approve/reject/
    needs_review decision, validated against ReviewDecision before use."""
    policy_text = await fetch_ad_policy()
    campaign_text = (
        f"Headline: {headline}\n"
        f"Description: {description}\n"
        f"Category: {category}\n"
        f"Current excluded_categories: {excluded_categories}"
    )
    return await _call_reviewer(policy_text, campaign_text)
