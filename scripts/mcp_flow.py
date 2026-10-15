"""Run PayPal's MCP server (sandbox), list its tools, and make a top-up through create_order + pay_order."""

import asyncio
import json
import sys

sys.path.insert(0, ".")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.paypal import PayPalSandbox

SERVER = StdioServerParameters(
    command="npx",
    args=["-y", "@paypal/mcp@1.8.1", "--tools=all"],
    env={"PAYPAL_ACCESS_TOKEN": PayPalSandbox().access_token(), "PAYPAL_ENVIRONMENT": "SANDBOX"},
)


def text(result) -> str:
    return "\n".join(c.text for c in result.content if getattr(c, "text", None))


async def main():
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name: t for t in (await session.list_tools()).tools}
            print("Tools:", ", ".join(sorted(tools)))

            schema = json.dumps(tools["create_order"].input_schema, default=str)
            print("\ncreate_order accepts a vault reference:", "vault" in schema.lower())
            print("Any payout tool:", any("payout" in n for n in tools))

            created = text(await session.call_tool("create_order", {
                "currencyCode": "USD",
                "items": [{"name": "AgentPay credit", "quantity": 1, "itemCost": 1.00, "itemTotal": 1.00}],
            }))
            try:
                order = json.loads(created)
            except json.JSONDecodeError:
                sys.exit(f"create_order did not return JSON:\n{created}")
            links = {l["rel"]: l["href"] for l in order.get("links", [])}
            print("\nOrder:", order.get("id"), order.get("status"))
            print("Open this link, log in with the SANDBOX personal account, and approve:\n",
                  links.get("payer-action") or links.get("approve") or links)
            input("\nPress Enter after approving...")

            paid = text(await session.call_tool("pay_order", {"id": order["id"]}))
            print("pay_order result:", paid)


asyncio.run(main())
