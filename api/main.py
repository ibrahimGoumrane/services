from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from api.routes.jobs import router as jobs_router
from api.routes.ws import router as ws_router
import asyncio
import os

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize shared state, background tasks, etc.
    yield
    # Shutdown: clean up resources, stop background tasks, etc.
    
    tasks = [
        t for t in asyncio.all_tasks()
        if t is not asyncio.current_task()
    ]

    for task in tasks:
        task.cancel()

    await asyncio.gather(*tasks, return_exceptions=True)


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
