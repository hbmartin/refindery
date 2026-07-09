"""Entry point: ``python -m refindery`` serves the API on loopback."""

import uvicorn

from refindery.api.app import create_app
from refindery.config import load_settings


def main() -> None:
    """Build settings from the environment and serve."""
    settings = load_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.bind_host,
        port=settings.bind_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
