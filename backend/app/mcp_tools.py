import httpx

from app.config import settings


async def deliver_slack_report(message: str) -> bool:
    """Optional agentic-delivery tool: post a recommendation/decision report to Slack
    via an incoming webhook, instead of only returning JSON from the API."""
    if not settings.slack_webhook_url:
        return False
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(settings.slack_webhook_url, json={"text": message})
        return response.status_code == 200
