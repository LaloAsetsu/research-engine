"""The FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from ...bootstrap import Container, container_from_env
from .problems import install_problem_handlers, use_problem_media_in_openapi
from .routes import router

API_PREFIX = "/api/v1"


def build_app(container: Container) -> FastAPI:
    # No docs UI and no served schema: the UI pages load scripts from a CDN, and the contract
    # is exported with `research-api openapi`.
    app = FastAPI(
        title="Research engine API",
        version=container.version,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.container = container
    app.include_router(router, prefix=API_PREFIX)
    install_problem_handlers(app)

    build_schema = app.openapi

    def openapi() -> dict[str, object]:
        if app.openapi_schema is None:
            use_problem_media_in_openapi(build_schema())
        assert app.openapi_schema is not None
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
    return app


def create_app() -> FastAPI:
    """The no-argument factory: ``uvicorn research_api.entrypoints.http:create_app --factory``."""
    return build_app(container_from_env())
