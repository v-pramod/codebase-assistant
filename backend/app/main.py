from fastapi import Depends, FastAPI

from app.api.routes import public_router, router
from app.auth.dependencies import get_current_user
from app.core.errors import AppError, app_error_handler


def create_app() -> FastAPI:
    app = FastAPI(title="Codebase Chat Assistant")
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(public_router, prefix="/api")
    app.include_router(router, prefix="/api", dependencies=[Depends(get_current_user)])
    return app


app = create_app()
