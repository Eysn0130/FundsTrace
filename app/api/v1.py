"""Stable v1 routes; the legacy /api endpoints remain independently mounted."""
from typing import Annotated, Literal

from fastapi import APIRouter, File, Path, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from urllib.parse import quote
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException

from app.services import cases, imports
from app import evidence_resolution
from .models import (ApiError, Case, CaseCreate, CaseList, CaseStats, CommitRequest,
                     CommitStarted, FlowPage, ImportJob, ImportPreview, RepreviewRequest,
                     ImportSessionList, CleanupRequest, CleanupResult, FlowSourceList, ImportRecordPage, ResolutionRegistry)


def error_response(status, code, message):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def http_error_response(error):
    messages = {400: "请求无效，请检查输入", 403: "系统未激活，请先完成激活", 404: "资源不存在，请重新选择",
                405: "请求方法不支持", 413: "文件过大，请拆分后重新上传"}
    response = error_response(error.status_code, f"http_{error.status_code}",
                              messages.get(error.status_code, "请求失败，请检查输入后重试"))
    if error.status_code == 405 and error.headers and "Allow" in error.headers:
        response.headers["Allow"] = error.headers["Allow"]
    return response


def install_error_handlers(app):
    """Cover routing-level 404/405 while preserving legacy exception handling."""
    fallback = app.exception_handlers.get(HTTPException, http_exception_handler)

    @app.exception_handler(HTTPException)
    async def versioned_http_exception(request, error):
        if request.url.path == "/api/v1" or request.url.path.startswith("/api/v1/"):
            return http_error_response(error)
        return await fallback(request, error)


class ContractRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request):
            try:
                return await handler(request)
            except cases.ServiceError as error:
                return error_response(error.status, error.code, error.message)
            except RequestValidationError:
                return error_response(422, "invalid_request", "请求参数无效，请检查案件、页码和必填字段")
            except HTTPException as error:
                return http_error_response(error)
            except Exception:
                return error_response(500, "internal_error", "操作失败，请重试；若持续失败，请联系维护人员")
        return wrapped


ERROR_RESPONSES = {status: {"model": ApiError, "description": description} for status, description in {
    400: "Invalid file or request", 403: "Application is not activated", 404: "Case, session or source not found",
    405: "HTTP method is not supported", 409: "State conflict or source unavailable", 422: "Invalid parameters or mapping",
    500: "Operation failed safely",
}.items()}
router = APIRouter(prefix="/api/v1", tags=["v1"], route_class=ContractRoute, responses=ERROR_RESPONSES)
CaseId = Annotated[str, Path(pattern=r"^[1-9][0-9]{0,18}$")]
ImportId = Annotated[str, Path(pattern=r"^[0-9a-f]{12,32}$")]


@router.get("/cases", response_model=CaseList, operation_id="v1_list_cases")
def list_cases():
    return cases.list_cases()


@router.post("/cases", response_model=Case, status_code=201, operation_id="v1_create_case")
def create_case(body: CaseCreate):
    return cases.create_case(body.name)


@router.get("/cases/{case_id}/stats", response_model=CaseStats, response_model_exclude_unset=True, operation_id="v1_case_stats")
def stats(case_id: CaseId):
    return cases.stats(case_id)


@router.get("/cases/{case_id}/flows", response_model=FlowPage, operation_id="v1_list_flows")
def flows(case_id: CaseId, account: str = "", kw: str = "", page: int = Query(1, ge=1, le=2_147_483_647),
          page_size: int = Query(50, ge=1, le=200), granularity: Literal["events", "observations"] = "events"):
    return cases.flows(case_id, account, kw, page, page_size, granularity)


@router.get("/cases/{case_id}/flows/{flow_id}/sources", response_model=FlowSourceList, operation_id="v1_flow_sources")
def flow_sources(case_id: CaseId, flow_id: CaseId, page: int = Query(1, ge=1, le=2_147_483_647),
                 page_size: int = Query(50, ge=1, le=200)):
    return cases.flow_sources(case_id, flow_id, page, page_size)


@router.get("/cases/{case_id}/sources/{source_id}/file", operation_id="v1_source_file", response_class=StreamingResponse,
            responses={200: {"description": "Verified original source bytes", "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}})
def source_file(case_id: CaseId, source_id: ImportId):
    source, filename = imports.get_source_file(case_id, source_id)

    def chunks():
        try:
            while block := source.read(1024 * 1024):
                yield block
        finally:
            source.close()

    return StreamingResponse(chunks(), media_type="application/octet-stream", background=BackgroundTask(source.close),
                             headers={"Cache-Control": "no-store", "Content-Disposition": f"attachment; filename*=utf-8''{quote(filename, safe='')}"})


@router.get("/cases/{case_id}/imports", response_model=ImportSessionList, operation_id="v1_import_sessions")
def sessions(case_id: CaseId, limit: int = Query(100, ge=1, le=200)):
    return imports.list_sessions(case_id, limit)


@router.get("/cases/{case_id}/imports/{source_id}/records", response_model=ImportRecordPage,
            operation_id="v1_import_records")
def records(case_id: CaseId, source_id: ImportId, page: int = Query(1, ge=1, le=2_147_483_647),
            page_size: int = Query(100, ge=1, le=200),
            disposition: Literal["imported", "duplicate", "review", "unresolved", "non_monetary", "cancelled", "non_transaction", "auxiliary", "removed"] | None = None):
    return imports.list_records(case_id, source_id, page, page_size, disposition)


@router.get("/cases/{case_id}/sources/{source_id}/report", operation_id="v1_import_report",
            response_class=StreamingResponse, responses={200: {"description": "Complete source disposition report",
                "content": {"application/json": {"schema": {"type": "string", "format": "binary"}}}}})
def report(case_id: CaseId, source_id: ImportId):
    stream, filename = imports.get_disposition_report(case_id, source_id)

    def chunks():
        try:
            while block := stream.read(1024 * 1024):
                yield block
        finally:
            stream.close()

    return StreamingResponse(chunks(), media_type="application/json", background=BackgroundTask(stream.close),
                             headers={"Cache-Control": "no-store", "Content-Disposition": f"attachment; filename*=utf-8''{quote(filename, safe='')}"})


@router.post("/cases/{case_id}/imports/cleanup", response_model=CleanupResult, operation_id="v1_cleanup_uploads")
def cleanup(case_id: CaseId, body: CleanupRequest):
    return imports.cleanup_uploads(case_id=case_id, dry_run=body.dry_run)


@router.get("/cases/{case_id}/resolution", response_model=ResolutionRegistry, operation_id="v1_resolution")
def resolution(case_id: CaseId):
    return evidence_resolution.registry(case_id)


@router.post("/cases/{case_id}/imports/directory/preview", response_model=ImportPreview, operation_id="v1_directory_preview")
async def directory_upload(case_id: CaseId, files: list[UploadFile] = File(...)):
    return await imports.upload_directory(case_id, files)


@router.post("/cases/{case_id}/imports/preview", response_model=ImportPreview, operation_id="v1_upload_preview")
async def upload(case_id: CaseId, file: UploadFile = File(...)):
    return await imports.upload(case_id, file)


@router.post("/cases/{case_id}/imports/{import_id}/preview", response_model=ImportPreview, operation_id="v1_repreview")
def repreview(case_id: CaseId, import_id: ImportId, body: RepreviewRequest):
    return imports.repreview(case_id, import_id, body)


@router.post("/cases/{case_id}/imports/{import_id}/commit", response_model=CommitStarted, operation_id="v1_commit_import")
def commit(case_id: CaseId, import_id: ImportId, body: CommitRequest):
    return imports.commit(case_id, import_id, body)


@router.get("/cases/{case_id}/imports/{import_id}", response_model=ImportJob, operation_id="v1_import_progress")
def progress(case_id: CaseId, import_id: ImportId):
    return imports.progress(case_id, import_id)
