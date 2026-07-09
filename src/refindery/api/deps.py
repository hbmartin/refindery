"""Dependency accessors: the wired Container lives on app.state."""

from fastapi import Request

from refindery.application.container import Container


def get_container(request: Request) -> Container:
    """Return the app's composition root."""
    return request.app.state.container
