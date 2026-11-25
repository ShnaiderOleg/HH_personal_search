import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import init_db
from .poller import Poller
from .notifications.telegram import TelegramReactionPoller
from .routers import api, pages
from .state import state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# В URL запросов Bot API содержится токен; httpx на INFO выводит полный URL.
logging.getLogger("httpx").setLevel(logging.WARNING)

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    settings = get_settings()
    poller = Poller(settings)
    state.poller = poller
    poller.start()
    reaction_poller = TelegramReactionPoller(settings)
    state.telegram_reaction_poller = reaction_poller
    reaction_poller.start()
    yield
    await reaction_poller.stop()
    state.telegram_reaction_poller = None
    poller.shutdown()
    state.poller = None


app = FastAPI(title="HH Search Monitor", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(pages.router)
app.include_router(api.router, prefix="/api")
