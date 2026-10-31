from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import get_engine
from .routers import agent, chat, export, kb, review, rfps, system


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_engine()  # create tables on first start
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="RFP AI", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(system.router, prefix="/api")
    app.include_router(rfps.router, prefix="/api")
    app.include_router(kb.router, prefix="/api")
    app.include_router(agent.router, prefix="/api")
    app.include_router(review.router, prefix="/api")
    app.include_router(export.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    return app


app = create_app()
