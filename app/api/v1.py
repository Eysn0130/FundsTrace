"""Stable v1 routes; the legacy /api endpoints remain independently mounted."""
from typing import Annotated, Literal

from fastapi import APIRouter, File, Path, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from urllib.parse import quote
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException

from app.services import assistance, cases, imports, import_ready_evidence, import_report
from app import evidence_resolution, table_import
from .models import (ApiError, Case, CaseCreate, CaseList, CaseStats, CommitRequest,
                     CommitStarted, FlowPage, ImportJob, ImportPreview, RepreviewRequest,
                     ImportSessionList, CleanupRequest, CleanupResult, FlowSourceList, ImportRecordPage, ImportReviewPage, ResolutionRegistry)
from .models import ImportReport, ImportReportIntegrity, ImportReportRecords, ImportReportAccountOptions, ImportSourceMetadata, WithdrawnImport
from .models import ImportRowCompareRequest, ImportRowComparison, ImportSignedAmountReview
from .models import (ImportReadyEvidence, ImportReadyProposal, ImportReadyProposalRequest, ImportReadyReadRequest,
                     ImportReadyVerdict, ImportReadyVerifyRequest)
from .models import ImportLayoutApplyRequest, ImportLayoutReadRequest, ImportLayoutTrial, ImportLayoutTrialRequest, ImportLayoutWindow
from .models import (AssistancePacket, ImportAssistanceRequest, ImportBalanceRoleTrialRequest, ImportModelTrialRequest, ImportPostingRoleTrialRequest, ImportReferenceTrialRequest,
                     ModelTrialResult, ModelTrialStatus, ReviewAssistanceRequest)
from .models import (ImportColumnEvidence, ImportColumnEvidenceRequest, IntakeReceipt, TrialReceiptHistory,
                     TrialTraceLookupRequest, TrialTraceLookupResult)


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


@router.get("/cases/{case_id}/sources/{source_id}/metadata", response_model=ImportSourceMetadata, operation_id="v1_source_metadata")
def source_metadata(case_id: CaseId, source_id: ImportId):
    return imports.source_metadata(case_id, source_id)


@router.delete("/cases/{case_id}/sources/{source_id}/operation", response_model=WithdrawnImport, operation_id="v1_withdraw_source")
def withdraw_source(case_id: CaseId, source_id: ImportId, expected_batch_id: str = Query(..., pattern=r"^[1-9][0-9]{0,18}$")):
    return imports.withdraw_source(case_id, source_id, expected_batch_id)


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
            source_row: int | None = Query(None, ge=1, le=2_147_483_647),
            pdf_page: int | None = Query(None, ge=1, le=2_147_483_647)):
    return imports.list_records(case_id, source_id, page, page_size, disposition, member_path, include_preview, source_row,
                                pdf_page=pdf_page)


@router.get('/cases/{case_id}/imports/{source_id}/review', response_model=ImportReviewPage,
            operation_id='v1_import_review')
def review(case_id: CaseId, source_id: ImportId, member_path: str = Query(..., min_length=1, max_length=4096),
           view: Literal['all', 'review', 'accepted'] = 'all', page: int = Query(1, ge=1, le=2_147_483_647),
           page_size: int = Query(50, ge=1, le=50), issue_index: int | None = Query(None, ge=1, le=2_147_483_647)):
    return imports.review_records(case_id, source_id, member_path, view, page, page_size, issue_index)


@router.post("/cases/{case_id}/imports/{source_id}/review/compare", response_model=ImportRowComparison,
             operation_id="v1_import_review_compare")
def compare_import_rows(case_id: CaseId, source_id: ImportId, body: ImportRowCompareRequest, request: Request, response: Response):
    _require_desktop_trial(request)
    response.headers["Cache-Control"] = "no-store"
    from app.services import trial_pair_comparison

    return trial_pair_comparison.compare(case_id, source_id, body)


@router.get("/cases/{case_id}/imports/{source_id}/review/signed-amounts",
            response_model=ImportSignedAmountReview | None, operation_id="v1_import_review_signed_amounts")
def import_review_signed_amounts(case_id: CaseId, source_id: ImportId, request: Request, response: Response,
                                 member_path: str = Query(..., min_length=1, max_length=4096)):
    _require_desktop_trial(request)
    response.headers["Cache-Control"] = "no-store"
    from app.services import signed_amount_review

    return signed_amount_review.review(case_id, source_id, member_path)


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
async def directory_upload(case_id: CaseId, request: Request, files: list[UploadFile] = File(...)):
    if getattr(request.app.state, "desktop_model_trial", None) is not None:
        return _mark_trial_preview(await imports.upload_directory(case_id, files, intake_trial=True), request)
    return await imports.upload_directory(case_id, files)


def _mark_trial_preview(value, request):
    if getattr(request.app.state, "desktop_model_trial", None) is not None and value.get("layout_review"):
        summary = value.get("table_summary") or {}
        if summary.get("profile") == table_import.CSV_TAIL_SOURCE_PROFILE:
            return value
        unresolved = summary.get("unresolved", 0)
        documents = summary.get("documents", ())
        reason_counts = summary.get("reason_counts", {})
        session = None
        row_issues_only = False
        if unresolved and value.get("automatic") is True and summary.get("accepted", 0) > 0 and len(documents) == 1:
            profile = summary.get("profile")
            first_row = documents[0]["header_rows"] == [1]
            if profile == table_import.CODED_BANK_CSV_TRIAL_PROFILE and first_row:
                row_issues_only = sum(reason_counts.get(reason, 0) for reason in (
                    "conflicting_direction", "invalid_direction", "invalid_currency")) == unresolved
            elif profile == table_import.BANK_SOURCE_PROFILE and first_row and reason_counts.get("negative_amount") == unresolved:
                session = imports._session(value["case_id"], value["import_id"])
                row_issues_only = session["suffix"] == ".csv"
            elif profile == table_import.table_tiered_headers.GROUPED_PROFILE and len(documents[0]["header_rows"]) == 1:
                row_issues_only = sum(reason_counts.get(reason, 0) for reason in (
                    "conflicting_income_expense", "conflicting_amount", "missing_time", "invalid_time")) == unresolved
            elif profile == table_import.BANK_COMPONENT_TRIAL_PROFILE and len(documents[0]["header_rows"]) == 1:
                row_issues_only = sum(reason_counts.get(reason, 0) for reason in (
                    table_import.table_sign_declarations.LEG_OUTSIDE,
                    table_import.table_sign_declarations.LEG_UNPROVED)) == unresolved
        if unresolved and not row_issues_only and not value.get("layout_applied"):
            value["layout_model_trial"] = True
        from app.services import import_column_trial

        if (reason_counts.get("preamble")
                or any(member["sheet"] in assistance._SCOPED_NOTE_SHEETS or len(member["header_rows"]) > 1
                       for member in value["layout_review"]["members"])
                or any(import_column_trial.own_account_alias(column)
                       for document in documents for column in document["columns"])):
            if session is None:
                session = imports._session(value["case_id"], value["import_id"])
            if import_column_trial.mapping_conflict(session):
                value["layout_model_trial"] = value["layout_model_trial_conflict"] = True
    return value


@router.post("/cases/{case_id}/imports/preview", response_model=ImportPreview, operation_id="v1_upload_preview")
async def upload(case_id: CaseId, request: Request, file: UploadFile = File(...)):
    if getattr(request.app.state, "desktop_model_trial", None) is None:
        return await imports.upload(case_id, file)
    value = _mark_trial_preview(await imports.upload(case_id, file, intake_trial=True, receive=request.receive), request)
    if value.get("layout_review") and not value.get("layout_model_trial_conflict"):
        from app.services import import_reference_trial

        prepared = await run_in_threadpool(import_reference_trial.prepare_upload, value)
        if prepared is not value:
            return _mark_trial_preview(prepared, request)
    if value.get("layout_model_trial") and not value.get("layout_model_trial_conflict"):
        from app.services import import_trial_rules
        value = await run_in_threadpool(import_trial_rules.prepare_upload, value)
    if (scope := getattr(request.app.state, "desktop_reference_review_scope", None)) is not None:
        await run_in_threadpool(scope.bind_upload, value)
    return value


def _require_desktop_trial(request):
    if getattr(request.app.state, "desktop_model_trial", None) is None:
        raise cases.ServiceError(404, "model_trial_disabled", "此操作仅在桌面隔离试用中可用")


@router.get("/cases/{case_id}/imports/{import_id}/intake", response_model=IntakeReceipt, operation_id="v1_import_intake")
def intake(case_id: CaseId, import_id: ImportId, request: Request, response: Response):
    _require_desktop_trial(request)
    response.headers["Cache-Control"] = "no-store"
    return imports.intake(case_id, import_id)


@router.get("/cases/{case_id}/sources/{import_id}/member-file", operation_id="v1_intake_member_file",
            response_class=StreamingResponse,
            responses={200: {"content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}})
def intake_member_file(case_id: CaseId, import_id: ImportId, request: Request,
                       member_path: Annotated[str, Query(min_length=1, max_length=1024)]):
    _require_desktop_trial(request)
    stream, filename = imports.get_source_member_file(case_id, import_id, member_path)
    return StreamingResponse(iter(lambda: stream.read(1024 * 1024), b""), media_type="application/octet-stream",
                             background=BackgroundTask(stream.close),
                             headers={"Cache-Control": "no-store", "Content-Disposition": f"attachment; filename*=utf-8''{quote(filename, safe='')}"})


@router.get("/cases/{case_id}/sources/{import_id}/pdf-page/{page}", operation_id="v1_intake_pdf_page",
            response_class=Response, responses={200: {"content": {"image/png": {"schema": {"type": "string", "format": "binary"}}}}})
def intake_pdf_page(case_id: CaseId, import_id: ImportId, request: Request,
                    page: Annotated[int, Path(ge=1, le=100)],
                    member_path: Annotated[str, Query(min_length=1, max_length=1024)]):
    _require_desktop_trial(request)
    from app.services import intake_pages

    return Response(intake_pages.pdf_page(case_id, import_id, member_path, page), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@router.post("/cases/{case_id}/imports/{import_id}/preview", response_model=ImportPreview, operation_id="v1_repreview")
def repreview(case_id: CaseId, import_id: ImportId, body: RepreviewRequest, request: Request):
    value = _mark_trial_preview(imports.repreview(case_id, import_id, body), request)
    if value.get("layout_model_trial") and not value.get("layout_model_trial_conflict"):
        from app.services import import_trial_rules

        return import_trial_rules.prepare_upload(value, describe_only=True)
    return value


@router.post("/cases/{case_id}/imports/{import_id}/ready-evidence", response_model=ImportReadyEvidence,
             operation_id="v1_read_ready_import_evidence")
def read_ready_import_evidence(case_id: CaseId, import_id: ImportId, body: ImportReadyReadRequest, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return import_ready_evidence.read(case_id, import_id, body)


@router.post("/cases/{case_id}/imports/{import_id}/ready-evidence/proposal", response_model=ImportReadyProposal,
             operation_id="v1_validate_ready_import_proposal")
def validate_ready_import_proposal(case_id: CaseId, import_id: ImportId, body: ImportReadyProposalRequest,
                                   response: Response):
    response.headers["Cache-Control"] = "no-store"
    return import_ready_evidence.propose(case_id, import_id, body)


@router.post("/cases/{case_id}/imports/{import_id}/ready-evidence/verify", response_model=ImportReadyVerdict,
             operation_id="v1_verify_ready_import_role")
def verify_ready_import_role(case_id: CaseId, import_id: ImportId, body: ImportReadyVerifyRequest, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return import_ready_evidence.verify(case_id, import_id, body)


@router.post("/cases/{case_id}/imports/{import_id}/layout-review", response_model=ImportLayoutWindow,
             operation_id="v1_read_import_layout")
def read_import_layout(case_id: CaseId, import_id: ImportId, body: ImportLayoutReadRequest, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return import_ready_evidence.layout(case_id, import_id, body)


@router.post("/cases/{case_id}/imports/{import_id}/layout-review/column-evidence",
             response_model=ImportColumnEvidence, operation_id="v1_import_column_evidence")
def import_column_evidence(case_id: CaseId, import_id: ImportId, body: ImportColumnEvidenceRequest,
                           request: Request, response: Response):
    _require_desktop_trial(request)
    response.headers["Cache-Control"] = "no-store"
    return import_ready_evidence.column_evidence(case_id, import_id, body)


@router.post("/cases/{case_id}/imports/{import_id}/layout-review/dry-run", response_model=ImportLayoutTrial,
             operation_id="v1_trial_import_layout")
def trial_import_layout(case_id: CaseId, import_id: ImportId, body: ImportLayoutTrialRequest, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return import_ready_evidence.trial_layout(case_id, import_id, body)


@router.post("/cases/{case_id}/imports/{import_id}/layout-review/apply", response_model=ImportPreview,
             operation_id="v1_apply_import_layout")
def apply_import_layout(case_id: CaseId, import_id: ImportId, body: ImportLayoutApplyRequest, response: Response, request: Request):
    response.headers["Cache-Control"] = "no-store"
    if getattr(request.app.state, "desktop_model_trial", None) is not None:
        return _mark_trial_preview(imports.apply_layout(case_id, import_id, body, trial_roles=True), request)
    return imports.apply_layout(case_id, import_id, body)


@router.post("/cases/{case_id}/imports/{import_id}/layout-review/assistance", response_model=AssistancePacket,
             operation_id="v1_import_assistance")
def import_assistance(case_id: CaseId, import_id: ImportId, body: ImportAssistanceRequest, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return assistance.import_assistance(case_id, import_id, body)


@router.post("/cases/{case_id}/review-assistance", response_model=AssistancePacket,
             operation_id="v1_review_assistance")
def review_assistance(case_id: CaseId, body: ReviewAssistanceRequest, response: Response):
    response.headers["Cache-Control"] = "no-store"
    if body.explain:
        raise cases.ServiceError(422, "trace_explanation_trial_only", "追踪说明仅供桌面隔离试用")
    if body.ledger_binding is not None:
        raise cases.ServiceError(422, "trial_trace_binding_only", "账簿查询证明仅供当前隔离追踪核证")
    return assistance.review_assistance(case_id, body)


@router.get("/model-trial/status", response_model=ModelTrialStatus, operation_id="v1_model_trial_status")
def model_trial_status(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    trial = getattr(request.app.state, "desktop_model_trial", None)
    return {"enabled": trial is not None, "configured": trial is not None and trial[0] is not None}


def _model_trial_endpoints(request, body):
    trial = getattr(request.app.state, "desktop_model_trial", None)
    if trial is None:
        raise cases.ServiceError(404, "model_trial_disabled", "此操作仅在桌面隔离试用中可用")
    if getattr(body, "reply", None) is not None:
        raise cases.ServiceError(422, "model_trial_request_only", "试用只接收当前核对请求")
    return trial[0]


def _run_model_trial(request, body, prepare, **options):
    from app.services import desktop_model_trial

    return desktop_model_trial.run(lambda reply: prepare(body.model_copy(update={"reply": reply})),
                                   _model_trial_endpoints(request, body), **options)


@router.post("/cases/{case_id}/imports/{import_id}/layout-review/model-trial", response_model=ModelTrialResult,
             operation_id="v1_import_model_trial")
def import_model_trial(case_id: CaseId, import_id: ImportId, body: ImportModelTrialRequest, request: Request,
                       response: Response, experimental_review_read: bool = False):
    response.headers["Cache-Control"] = "no-store"
    from app.services import import_user_decision

    endpoints = _model_trial_endpoints(request, body)
    if isinstance(body, ImportBalanceRoleTrialRequest):
        if body.candidate is None and (grant := getattr(request.app.state, "desktop_verbatim_balance_review", None)) is not None:
            from app.services import import_verbatim_balance

            return import_verbatim_balance.run(case_id, import_id, body, endpoints, grant,
                lambda: getattr(request.app.state, "desktop_verbatim_balance_review", None) is grant)
        from app.services import import_balance_trial

        return import_balance_trial.run(case_id, import_id, body)
    if imports._table_profile(imports._session(case_id, import_id)) == table_import.CSV_TAIL_SOURCE_PROFILE:
        raise cases.ServiceError(422, table_import.CSV_TAIL_SOURCE_REASON, "末行缺格的CSV仅供原件核对")
    if isinstance(body, ImportPostingRoleTrialRequest):
        from app.services import import_posting_role_trial

        return import_posting_role_trial.run(case_id, import_id, body)
    if decision := import_user_decision.run(case_id, import_id, body):
        return decision
    from app.services import desktop_model_trial

    result = desktop_model_trial.run(lambda reply: assistance.import_assistance(
        case_id, import_id, body.model_copy(update={"reply": reply}), column_trial=True), endpoints,
        experimental_review_read=experimental_review_read)
    if body.prepare_layout and result["state"] == "proposal":
        from app.services import import_trial_rules

        prepared = import_trial_rules.prepare_assisted(result)
        if prepared is None:
            result.update(state="pending", reason="layout_not_admitted")
        else:
            result["prepared_preview"] = prepared
    return result


@router.post("/cases/{case_id}/imports/{import_id}/layout-review/reference-policy-trial", response_model=ModelTrialResult,
             operation_id="v1_import_reference_policy_trial")
def import_reference_policy_trial(case_id: CaseId, import_id: ImportId, body: ImportReferenceTrialRequest,
                                  request: Request, response: Response):
    _require_desktop_trial(request)
    response.headers["Cache-Control"] = "no-store"
    from app.services import import_reference_trial

    state = request.app.state
    scope = getattr(state, "desktop_reference_review_scope", None)
    if scope is not None:
        grant = scope.grant_for(case_id, import_id, body)

        def active():
            return (getattr(state, "desktop_reference_review_scope", None) is scope
                    and scope.grant_for(case_id, import_id, body) is grant)
    else:
        grant = getattr(state, "desktop_verbatim_reference_review", None)

        def active():
            return getattr(state, "desktop_verbatim_reference_review", None) is grant

    return import_reference_trial.run(case_id, import_id, body, state.desktop_model_trial[0], grant=grant, active=active)


@router.post("/cases/{case_id}/review-model-trial", response_model=ModelTrialResult, operation_id="v1_review_model_trial")
def review_model_trial(case_id: CaseId, body: ReviewAssistanceRequest, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    from app.services import desktop_model_trial, trial_review_receipts, trial_trace_lookup

    if body.explain and body.kind == "pair":
        raise cases.ServiceError(422, "trace_explanation_only", "仅追踪卡可请求说明")
    endpoints = _model_trial_endpoints(request, body)

    def bound_ledger():
        if body.ledger_binding is None:
            return None
        _require_desktop_trial(request)
        if body.kind == "pair" or not body.anchor_id:
            raise cases.ServiceError(422, "invalid_trace_binding", "请选择所查账簿内的追踪流水")
        return trial_trace_lookup.require(case_id, body.ledger_binding, body.anchor_id)

    ledger_scope = bound_ledger()
    if body.kind == "pair":
        if (grant := getattr(request.app.state, "desktop_verbatim_pair_review", None)) is not None:
            from app.services import pair_verbatim_trial

            return pair_verbatim_trial.run(case_id, body, endpoints, grant,
                lambda: getattr(request.app.state, "desktop_verbatim_pair_review", None) is grant)
        if (decision := desktop_model_trial.pair_business(case_id, body, endpoints)) is not None:
            return decision
    range_tickets = {}
    result = _run_model_trial(request, body, lambda value: assistance.review_assistance(
        case_id, value, product=True, range_tickets=range_tickets, ledger_scope=ledger_scope),
        range_tickets=range_tickets, ledger_scope=ledger_scope)
    trial_review_receipts.record(case_id, body, result, range_tickets)
    if body.explain:
        desktop_model_trial.explain_trace(lambda _: assistance.review_assistance(
            case_id, body, product=True, ledger_scope=bound_ledger()), request.app.state.desktop_model_trial[0], result)
    return result


@router.post("/cases/{case_id}/trace-ledgers", response_model=TrialTraceLookupResult, operation_id="v1_trace_ledgers")
def trace_ledgers(case_id: CaseId, body: TrialTraceLookupRequest, request: Request, response: Response):
    _require_desktop_trial(request)
    response.headers["Cache-Control"] = "no-store"
    from app.services import trial_trace_lookup

    return trial_trace_lookup.lookup(case_id, body)


@router.get("/cases/{case_id}/model-trial/receipts/{receipt_id}", response_model=TrialReceiptHistory,
            operation_id="v1_model_trial_receipt")
def trial_receipt(case_id: CaseId, receipt_id: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
                  request: Request, response: Response):
    _require_desktop_trial(request)
    response.headers["Cache-Control"] = "no-store"
    from app.services import trial_review_receipts

    return trial_review_receipts.read(case_id, receipt_id)


@router.post("/cases/{case_id}/imports/{import_id}/commit", response_model=CommitStarted, operation_id="v1_commit_import")
def commit(case_id: CaseId, import_id: ImportId, body: CommitRequest, request: Request):
    if getattr(request.app.state, "desktop_model_trial", None) is not None:
        return imports.commit(case_id, import_id, body, trial_roles=True)
    return imports.commit(case_id, import_id, body)


@router.get("/cases/{case_id}/imports/{import_id}", response_model=ImportJob, operation_id="v1_import_progress")
def progress(case_id: CaseId, import_id: ImportId):
    return imports.progress(case_id, import_id)
