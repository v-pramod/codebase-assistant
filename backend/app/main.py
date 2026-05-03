from fastapi import FastAPI

from app.api.routes import router
from app.core.errors import AppError, app_error_handler


def create_app() -> FastAPI:
    app = FastAPI(title="Codebase Chat Assistant")
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(router, prefix="/api")
    return app


app = create_app()
