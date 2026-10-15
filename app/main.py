from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import init_db
from app.webhooks import router as webhooks_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="AgentPay", lifespan=lifespan)
app.include_router(webhooks_router)


@app.get("/health")
def health():
    return {"status": "ok"}
