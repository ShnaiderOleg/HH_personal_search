from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import init_db
from .poller import Poller
from .routers import api, pages
from .state import state

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    settings = get_settings()
    poller = Poller(settings)
    state.poller = poller
    poller.start()
    yield
    poller.shutdown()
    state.poller = None


app = FastAPI(title="HH Search Monitor", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(pages.router)
app.include_router(api.router, prefix="/api")
