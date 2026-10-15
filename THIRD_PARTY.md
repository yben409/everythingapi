# Third-party software

Direct dependencies (see `requirements.txt` for pinned versions). Transitive dependencies are pulled in by these.

| Project | Use | License |
|---|---|---|
| FastAPI | HTTP API | MIT |
| Starlette | ASGI toolkit (via FastAPI) | BSD-3-Clause |
| Uvicorn | ASGI server | BSD-3-Clause |
| pydantic / pydantic-settings | Data validation, settings | MIT |
| HTTPX | HTTP client (PayPal REST) | BSD-3-Clause |
| python-dotenv | Load `.env` in dev | BSD-3-Clause |
| SQLAlchemy | Database / ledger | MIT |
| pytest | Tests | MIT |
| MCP Python SDK (`mcp`) | MCP client (and later our MCP server) | MIT |

Run with `npx`, not bundled or vendored:

| Project | Use | License |
|---|---|---|
| @paypal/mcp | PayPal MCP server (sandbox) | Apache-2.0 |
| @paypal/agent-toolkit | Tools behind @paypal/mcp | ISC |
