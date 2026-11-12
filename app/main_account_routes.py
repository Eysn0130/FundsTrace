"""Legacy HTTP adaptation for case-bound main-account management."""
from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from . import db
from .case_packages import require_case
from .services import main_accounts as main_account_service
from .services.cases import ServiceError


router = APIRouter()


class CaseIdIn(BaseModel):
    case_id: int = 1


class MainAccountIn(BaseModel):
    case_id: int
    account: str
    name: str = ""


@contextmanager
def _case_transaction(case_id):
    with db.transaction() as connection:
        require_case(case_id)
        yield connection


@router.post("/api/main-accounts/auto")
def auto_detect_all(case_id_in: CaseIdIn):
    """扫描当前案件的全部单账户（模式B）批次，自动识别主账号（存量数据一键识别）"""
    with _case_transaction(case_id_in.case_id):
        return main_account_service.auto_detect_all(case_id_in.case_id)


@router.get("/api/main-accounts/candidates")
def main_account_candidates(case_id: int, q: str = "", exact: bool = False, page: int = Query(1, ge=1),
                            page_size: int = Query(30, ge=1, le=100)):
    """Search accounts, rather than a truncated page of transaction matches."""
    with _case_transaction(case_id):
        return main_account_service.main_account_candidates(case_id, q, exact, page, page_size)


@router.get("/api/main-accounts")
def list_main_accounts(case_id: int = 1):
    with _case_transaction(case_id):
        return main_account_service.list_main_accounts(case_id)


@router.post("/api/main-accounts/clear-auto")
def clear_auto_main_accounts(body: CaseIdIn):
    """清除本案件全部"自动识别"的主账号标记（手动标记保留）"""
    with _case_transaction(body.case_id):
        return main_account_service.clear_auto_main_accounts(body.case_id)


@router.get("/api/debug/batch-accounts")
def debug_batch_accounts(case_id: int = 1):
    """诊断：各批次的模式、行数、出现占比最高的账号（用于排查主账号识别）"""
    return main_account_service.debug_batch_accounts(case_id)


@router.post("/api/main-accounts")
def mark_main_account(body: MainAccountIn):
    try:
        with _case_transaction(body.case_id):
            return main_account_service.mark_main_account(body.case_id, body.account, body.name)
    except ServiceError as error:
        raise HTTPException(error.status, error.message) from None


@router.delete("/api/main-accounts")
def unmark_main_account(body: MainAccountIn):
    try:
        with _case_transaction(body.case_id):
            return main_account_service.unmark_main_account(body.case_id, body.account)
    except ServiceError as error:
        raise HTTPException(error.status, error.message) from None
