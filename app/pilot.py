"""Serve the optional React pilot without replacing the existing G6 workspace."""
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .paths import BUNDLE_DIR


def mount_pilot(app: FastAPI):
    directory = BUNDLE_DIR / "frontend" / "dist"

    @app.get("/workspace", include_in_schema=False)
    def workspace_redirect():
        return RedirectResponse("/workspace/")

    @app.get("/workspace/", include_in_schema=False)
    def workspace_index():
        index = directory / "index.html"
        if not index.is_file():
            return JSONResponse(status_code=503, content={"detail": "React 工作台尚未构建，请按 README 构建前端。"})
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    app.mount("/workspace/assets", StaticFiles(directory=str(directory / "assets"), check_dir=False), name="pilot-assets")
