from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from httpx import ASGITransport, AsyncClient

from .models import ChatRequest


UI_DIRECTORY = Path(__file__).parent / "static" / "ui"


def register_chat_ui(app: FastAPI, *, authorization: str) -> None:
    app.mount("/ui/assets", StaticFiles(directory=UI_DIRECTORY), name="chat-ui-assets")

    @app.get("/", include_in_schema=False)
    @app.get("/ui", include_in_schema=False)
    async def chat_ui() -> FileResponse:
        return FileResponse(
            UI_DIRECTORY / "index.html", headers={"Cache-Control": "no-store"}
        )

    @app.post("/ui/chat", include_in_schema=False)
    async def ui_chat(body: ChatRequest, request: Request) -> JSONResponse:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://localhost", timeout=190
        ) as client:
            response = await client.post(
                "/chat", json=body.model_dump(), headers={"Authorization": authorization}
            )
        return JSONResponse(
            response.json(), status_code=response.status_code,
            headers={"Cache-Control": "no-store"},
        )
