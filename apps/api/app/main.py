from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.routes import repositories
from app.core.ai_errors import AIQuotaExceededError
from app.db.database import engine

app = FastAPI(
    title="CodeLens API",
    version="0.1.0",
    description="AI-powered code intelligence platform",
)


@app.exception_handler(AIQuotaExceededError)
def ai_quota_exception_handler(request: Request, exc: AIQuotaExceededError):
    return JSONResponse(
        status_code=429,
        content={
            "code": exc.code,
            "message": exc.message,
            "detail": {
                "code": exc.code,
                "message": exc.message,
            },
        },
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(repositories.router)


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