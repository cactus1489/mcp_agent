"""Small, stateless Streamable HTTP client for an MCP server."""

from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from pydantic import AnyUrl

class MCPClient:
    """Initializes an MCP connection for each operation.

    Per-request sessions avoid sharing a stateful JSON-RPC connection between
    concurrent FastAPI workers.
    """

    # .env에서 전달받은 MCP Streamable HTTP 주소를 저장한다.
    def __init__(self, server_url: str, auth_token: str = "") -> None:
        self.server_url = str(AnyUrl(server_url))
        self.auth_token = auth_token.strip()

    # MCP 서버에 tools/list를 요청해 LLM 선택에 쓸 도구 목록을 만든다.
    async def list_tools(self) -> list[dict[str, Any]]:
        async with _MCPSessionContext(
            self.server_url,
            self.auth_token,
        ) as session:
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

    # LLM이 고른 도구 이름과 인자를 MCP 서버에 tools/call로 전달한다.
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async with _MCPSessionContext(
            self.server_url,
            self.auth_token,
        ) as session:
            result = await session.call_tool(name, arguments)
            return result.model_dump(mode="json")

class _MCPSessionContext:
    def __init__(self, server_url: str, auth_token: str) -> None:
        self.server_url = server_url
        self.auth_token = auth_token
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None

    # HTTP 연결 후 MCP initialize HandShake 먼저 완료한다.
    async def __aenter__(self) -> ClientSession:
        http_client = await self._stack.enter_async_context(
            create_mcp_http_client(headers = {"Authorization": self.auth_token})
        )
        streams = await self._stack.enter_async_context(
            streamable_http_client(self.server_url, http_client=http_client)
        )
        read_stream, write_stream = streams
        self._session = await self._stack.enter_async_context(ClientSession(read_stream, write_stream))
        await self._session.initialize()
        return self._session

# async with 블록이 끝날 때 자동 실행되는 종료 처리 함수
    async def __aexit__(self, *exc_info: Any) -> None:
        await self._stack.aclose()
