"""FastAPI application entry point.

Run: .venv/bin/uvicorn app.main:app --reload
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers.api import router as api_router
from app.routers.ui import router as ui_router

app = FastAPI(title="Daske — optimizacija rezanja", version="0.1.0")
app.include_router(api_router)
app.include_router(ui_router)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/health")
def health():
    return {"status": "ok"}
