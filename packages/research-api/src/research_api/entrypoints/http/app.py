"""The FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from ...bootstrap import Container, container_from_env
from .circuits import router as circuits_router
from .guard import BindGuard, loopback_only, loopback_or_address
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
    # Outermost, so it also covers a start that skips `research-api serve` and its bind check.
    app.add_middleware(
        BindGuard,
        allowed=loopback_only
        if container.mode == "fake"
        else loopback_or_address(container.settings.bind_host),
    )
    app.include_router(router, prefix=API_PREFIX)
    app.include_router(circuits_router, prefix=API_PREFIX)
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
