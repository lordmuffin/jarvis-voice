from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Jarvis Live")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness: the process is up."""
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        """Readiness: always ok until dependencies (DB, STT) are wired in."""
        return {"status": "ok"}

    return app


app = create_app()
