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

from app.services import cases, imports, import_report
from app import evidence_resolution
from .models import (ApiError, Case, CaseCreate, CaseList, CaseStats, CommitRequest,
                     CommitStarted, FlowPage, ImportJob, ImportPreview, RepreviewRequest,
                     ImportSessionList, CleanupRequest, CleanupResult, FlowSourceList, ImportRecordPage, ImportReviewPage, ResolutionRegistry)
from .models import ImportReport, ImportReportIntegrity, ImportReportRecords, ImportReportAccountOptions


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
                response = error_response(error.status, error.code, error.message)
                response.headers["X-FundsTrace-Import-Failure-Kind"] = getattr(error, "failure_kind",
                    "file" if error.code in {"unsupported_file_type", "file_too_large", "directory_file_count",
                                             "unsafe_directory_path", "duplicate_directory_path"} else "unknown")
                return response
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
def sessions(case_id: CaseId, limit: int = Query(100, ge=1, le=200),
             import_id: str | None = Query(None, pattern=r"^[0-9a-f]{12,32}$")):
    return imports.list_sessions(case_id, limit, import_id)


@router.get("/cases/{case_id}/imports/{source_id}/records", response_model=ImportRecordPage,
            operation_id="v1_import_records")
def records(case_id: CaseId, source_id: ImportId, page: int = Query(1, ge=1, le=2_147_483_647),
            page_size: int = Query(100, ge=1, le=200),
            disposition: Literal["imported", "duplicate", "review", "unresolved", "non_monetary", "cancelled", "non_transaction", "auxiliary", "removed"] | None = None,
            member_path: str | None = Query(None, min_length=1, max_length=4096), include_preview: bool = False,
            source_row: int | None = Query(None, ge=1, le=2_147_483_647)):
    return imports.list_records(case_id, source_id, page, page_size, disposition, member_path, include_preview, source_row)


@router.get('/cases/{case_id}/imports/{source_id}/review', response_model=ImportReviewPage,
            operation_id='v1_import_review')
def review(case_id: CaseId, source_id: ImportId, member_path: str = Query(..., min_length=1, max_length=4096),
           view: Literal['all', 'review', 'accepted'] = 'all', page: int = Query(1, ge=1, le=2_147_483_647),
           page_size: int = Query(50, ge=1, le=50), issue_index: int | None = Query(None, ge=1, le=2_147_483_647)):
    return imports.review_records(case_id, source_id, member_path, view, page, page_size, issue_index)


@router.get('/cases/{case_id}/imports/{source_id}/report', response_model=ImportReport, operation_id='v1_import_funds_report')
def funds_report(case_id: CaseId, source_id: ImportId, member_path: str = Query(...,min_length=1,max_length=4096),
                 currency: str | None = Query(None,max_length=16), account: str | None = Query(None,max_length=4096),
                 page: int = Query(1,ge=1,le=2_147_483_647), page_size: int = Query(20,ge=1,le=50),
                 accounts_page: int | None = Query(None,ge=1,le=2_147_483_647),relations_page: int | None = Query(None,ge=1,le=2_147_483_647),
                 timeline_grain: Literal['auto','day','month','year'] = 'auto',timeline_page: int = Query(1,ge=1,le=2_147_483_647),
                 peer_account: str | None = Query(None,max_length=4096),
                 timeline_scope: Literal['all','selected','except'] = 'all',timeline_accounts: list[str] = Query(default=[],max_length=200),
                 timeline_search: str = Query('',max_length=200),
                 expected_revision: str | None = Query(None,pattern=r'^[0-9a-f]{32}$')):
    return import_report.report(case_id,source_id,member_path,currency,account,page,page_size,expected_revision,accounts_page,relations_page,timeline_grain,timeline_page,peer_account,timeline_scope,timeline_accounts,timeline_search)


@router.get('/cases/{case_id}/imports/{source_id}/report/accounts',response_model=ImportReportAccountOptions,operation_id='v1_import_funds_accounts')
def funds_account_options(case_id: CaseId,source_id: ImportId,member_path: str = Query(...,min_length=1,max_length=4096),
                          expected_revision: str = Query(...,pattern=r'^[0-9a-f]{32}$'),currency: str = Query(...,max_length=16),
                          account: str | None = Query(None,max_length=4096),search: str = Query('',max_length=200),
                          page: int = Query(1,ge=1,le=2_147_483_647),page_size: int = Query(20,ge=1,le=50)):
    return import_report.account_options(case_id,source_id,member_path,expected_revision,currency,account,search,page,page_size)


@router.post('/cases/{case_id}/imports/{source_id}/report/verify',response_model=ImportReportIntegrity,operation_id='v1_import_funds_verify')
def funds_verify(case_id: CaseId,source_id: ImportId,member_path: str = Query(...,min_length=1,max_length=4096),
                 expected_revision: str = Query(...,pattern=r'^[0-9a-f]{32}$')):
    return import_report.verify(case_id,source_id,member_path,expected_revision)


@router.get('/cases/{case_id}/imports/{source_id}/report/records',response_model=ImportReportRecords,operation_id='v1_import_funds_records')
def funds_records(case_id: CaseId,source_id: ImportId,member_path: str = Query(...,min_length=1,max_length=4096),
                  expected_revision: str = Query(...,pattern=r'^[0-9a-f]{32}$'),currency: str | None = Query(None,max_length=16),
                  account: str | None = Query(None,max_length=4096),counterparty: str | None = Query(None,max_length=4096),
                  direction: Literal['in','out'] | None = None,page: int = Query(1,ge=1,le=2_147_483_647),page_size: int = Query(50,ge=1,le=50),
                  timeline_scope: Literal['all','selected','except'] = 'all',timeline_accounts: list[str] = Query(default=[],max_length=200),
                  time_start: str | None = Query(None,max_length=48),time_end: str | None = Query(None,max_length=48),time_basis: Literal['utc','unqualified'] | None = None,timeline_search: str = Query('',max_length=200)):
    return import_report.records(case_id,source_id,member_path,expected_revision,currency,account,counterparty,direction,page,page_size,timeline_scope,timeline_accounts,time_start,time_end,time_basis,timeline_search)


@router.get('/cases/{case_id}/imports/{source_id}/report/document',operation_id='v1_import_funds_document',
            response_class=StreamingResponse,responses={200:{'description':'Current-view member funds brief, bounded pages explicitly labeled',
                'content':{'text/html':{'schema':{'type':'string','format':'binary'}}}}})
def funds_document(case_id: CaseId,source_id: ImportId,member_path: str = Query(...,min_length=1,max_length=4096),
                   expected_revision: str = Query(...,pattern=r'^[0-9a-f]{32}$'),currency: str | None = Query(None,max_length=16),
                   account: str | None = Query(None,max_length=4096),page: int = Query(1,ge=1,le=2_147_483_647),page_size: int = Query(20,ge=1,le=50),
                   timeline_grain: Literal['auto','day','month','year'] = 'auto',timeline_page: int = Query(1,ge=1,le=2_147_483_647),
                   peer_account: str | None = Query(None,max_length=4096),
                   timeline_scope: Literal['all','selected','except'] = 'all',timeline_accounts: list[str] = Query(default=[],max_length=200),timeline_search: str = Query('',max_length=200),
                   accounts_page: int | None = Query(None,ge=1,le=2_147_483_647),relations_page: int | None = Query(None,ge=1,le=2_147_483_647)):
    content=import_report.document(case_id,source_id,member_path,expected_revision,currency=currency,account=account,page=page,page_size=page_size,accounts_page=accounts_page,relations_page=relations_page,
        timeline_grain=timeline_grain,timeline_page=timeline_page,peer_account=peer_account,timeline_scope=timeline_scope,timeline_accounts=timeline_accounts,timeline_search=timeline_search)
    return StreamingResponse(iter([content]),media_type='text/html; charset=utf-8',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote('资金统计分析简报.html')})


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
