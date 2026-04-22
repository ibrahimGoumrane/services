from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from api.routes.jobs import router as jobs_router
from api.routes.ws import router as ws_router
import asyncio
import os
import logging


logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize shared state, background tasks, etc.
    yield
    # Shutdown: clean up resources, stop background tasks, etc.

    current_task = asyncio.current_task()
    seed_tasks = [
        t
        for t in asyncio.all_tasks()
        if t is not current_task and t.get_name().startswith("seed-job:")
    ]

    for task in seed_tasks:
        task.cancel()

    if seed_tasks:
        try:
            await asyncio.gather(*seed_tasks, return_exceptions=True)
        except asyncio.CancelledError:
            # Uvicorn may cancel lifespan during reload; keep shutdown clean.
            logger.debug("Lifespan shutdown gather cancelled during reload")


app = FastAPI(title="Database Seeding API", version="0.1.0", lifespan=lifespan)

cors_origins_env = os.getenv("CORS_ALLOW_ORIGINS", "")
allow_origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]
if not allow_origins:
    allow_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(jobs_router)
app.include_router(ws_router)
