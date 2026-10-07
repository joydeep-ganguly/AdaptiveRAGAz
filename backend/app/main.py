from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agent.graph import create_adaptive_rag
from app.api.routers import admin, auth, chat, conversations, health, media
from app.config import get_settings
from app.logging_config import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    settings.ensure_storage_dirs()

    # Built once per process — Chroma/embeddings/SQL connections are expensive
    # to set up, and every request shares this one compiled graph via
    # app.state (see app/deps.py: get_agent_graph).
    app.state.graph = create_adaptive_rag()

    yield

    # No explicit teardown yet — the E2B sandbox singleton in
    # agent/tools/code_tools.py isn't lifecycle-managed here (section 4 of the plan).


app = FastAPI(title="Adaptive RAG API", lifespan=lifespan)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(conversations.router)
app.include_router(chat.router)
app.include_router(media.router)