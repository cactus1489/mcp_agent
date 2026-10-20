"""Start the API server using the shared Settings object."""

import uvicorn

from .config import settings


def main() -> None:
    print(
        f"Starting server: profile={settings.profile}, host={settings.host}, port={settings.port}",
        flush=True,
    )
    uvicorn.run(
        "agent.src.app:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
