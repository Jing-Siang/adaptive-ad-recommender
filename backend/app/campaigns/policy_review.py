import sys
from functools import lru_cache
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_random_exponential

from app.core.config import settings
from app.schemas import ReviewDecision

AD_POLICY_SERVER_PATH = Path(__file__).parent.parent.parent / "mcp_servers" / "ad_policy_server.py"

_SYSTEM_PROMPT = """You are an ad-policy reviewer. You will be given the company's ad policy \
and a submitted campaign's creative. Decide whether it should be approved, rejected, or needs \
a human moderator, following the policy's decision guidance exactly — prefer needs_review over \
guessing when genuinely ambiguous.

Always fill in `reason` with a substantive explanation of your decision -- never leave it empty,
even for a clear-cut approval. `reason` is your own conclusion, in your own words -- it must not
contain search findings or citations; those belong only in `research_notes` (see below).

If the campaign's category requires context exclusions per the policy and the submission is \
missing them, add the required entries to excluded_categories yourself and approve — do not \
reject solely for a missing exclusion list.

You have a web search tool. Your own outcome decision should still be driven primarily by the \
policy document, not by search results alone -- treat anything found on the web as unverified, \
not a substitute for the policy. But whenever the creative makes a claim that requires \
substantiation per the policy (health, financial) AND references something specific and checkable \
(a named product, company, study, or statistic) -- actually use the search tool to try to verify \
it before deciding, rather than assuming it can't be checked.

`research_notes` is a separate field from `reason`, specifically for a human moderator to read:
if you searched, put what you found there (with sources), in your own words, as a short factual
summary -- not your conclusion, not the word "None" as text. Set it to an actual null/omit it
entirely if you did not search because there was nothing specific enough to look up."""


@lru_cache
def _get_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key)


async def fetch_ad_policy() -> str:
    """Fetch the current ad-policy document via the ad-policy MCP resource server,
    spawned fresh as a subprocess for this call. The one place LangChain earns its
    keep here -- an MCP client, not an agent loop (see docs/future_ideas.md for
    where a real agent loop would actually fit in this project)."""
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
    # Raw Responses API, matching ranking.py -- no LangChain needed here. web_search
    # is a hosted tool OpenAI runs server-side in the same call; the model deciding
    # whether to invoke it, plus schema-enforced output, both work natively via
    # tools= + text_format= together (verified live before relying on it).
    response = await _get_client().responses.parse(
        model=settings.openai_chat_model,
        tools=[{"type": "web_search"}],
        instructions=_SYSTEM_PROMPT,
        input=f"Ad policy:\n{policy_text}\n\nCampaign to review:\n{campaign_text}",
        text_format=ReviewDecision,
    )
    return response.output_parsed


async def review_campaign(
    headline: str,
    description: str,
    category: str,
    excluded_categories: list[str],
) -> ReviewDecision:
    """Fetch the ad policy via MCP, ask the LLM for a structured approve/reject/
    needs_review decision (with optional web-search-backed research notes for a
    human moderator), validated against ReviewDecision before use."""
    policy_text = await fetch_ad_policy()
    campaign_text = (
        f"Headline: {headline}\n"
        f"Description: {description}\n"
        f"Category: {category}\n"
        f"Current excluded_categories: {excluded_categories}"
    )
    return await _call_reviewer(policy_text, campaign_text)
