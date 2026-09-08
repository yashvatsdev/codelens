from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.db.database import engine

app = FastAPI(
    title="CodeLens API",
    version="0.1.0",
    description="AI-powered code intelligence platform",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "codelens",
    }

@app.get("/db/health")
def db_health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            connection.commit()

        return {
            "status": "ok",
            "database": "connected",
        }

    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "database": "disconnected",
                "detail": str(e),
            },
        )