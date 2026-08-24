"""Small, stateless Streamable HTTP client for an MCP server."""

from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyUrl

import httpx

class MCPClient:
    """Initializes an MCP connection for each operation."""

    def __init__(self, server_url: str, auth_token: str = "") -> None:
        self.server_url = str(AnyUrl(server_url))
        self.auth_token = auth_token.strip()

    async def list_tools(self) -> list[dict[str, Any]]:
      context = _MCPSessionContext(self.server_url, self.auth_token)
      async with context as session:
            result = await session.list_tools()
            return [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.input_schema,
                }
                for tool in result.tools
            ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
      context = _MCPSessionContext(self.server_url, self.auth_token)
      async with context as session:
            result = await session.call_tool(name, arguments)
            response = {
                "http_response": context.last_http_response,
                "mcp_result": result.model_dump(mode="json"),
            }
            return response


class _MCPSessionContext:
    def __init__(self, server_url: str, auth_token: str) -> None:
        self.server_url = server_url
        self.auth_token = auth_token
        self._stack = AsyncExitStack()
        self.last_http_response: dict[str, Any] | None = None

    async def _capture_response(self, response: httpx.Response) -> None:
        self.last_http_response = {
            "headers": dict(response.headers),
        }

    async def __aenter__(self) -> "_MCPSessionContext":
        http_client = httpx.AsyncClient(
            headers={"Authorization": self.auth_token},
            event_hooks={"response": [self._capture_response]},
        )
        http_client = await self._stack.enter_async_context(http_client)

        read_stream, write_stream = await self._stack.enter_async_context(
            streamable_http_client(self.server_url, http_client=http_client)
        )
        self._session = await self._stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await self._session.initialize()
        return self._session

    async def __aexit__(self, *exc_info: Any) -> None:
        await self._stack.aclose()

