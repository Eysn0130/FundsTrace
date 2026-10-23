# -*- coding: utf-8 -*-
"""资金追踪分析系统 — FastAPI 后端（DuckDB 存储）

各案件数据完全隔离（flows.case_id）：查询、统计、画像、导入、批次均按案件过滤。
"""
import base64
import datetime
from contextlib import asynccontextmanager, contextmanager
from html import escape
import shutil
import tempfile
import io
import json
import os
import re
import sys
import urllib.parse
import unicodedata
import uuid
from datetime import datetime as _dt
from pathlib import Path
from typing import List, Literal, Optional

import pandas as pd
import duckdb
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from . import db, importer, sample_data
from . import canvas_investigation
from .case_packages import CASE_PKG_TABLES, CASE_PKG_COLS, delete_package_case, export_package, import_package, require_case, photo_name
from . import license as lic
from .services import imports as import_service
from .services import accounts as account_service
from .services import account_suggestions as account_suggestion_service
from .services.cases import ServiceError
from .services.main_accounts import mark_automatic
from .money import money_decimal, money_string, money_display
from .import_coverage import import_coverage

@asynccontextmanager
async def application_lifespan(application):
    import_service.recover_interrupted()
    yield


app = FastAPI(title="FundsTrace", version="1.0.0", lifespan=application_lifespan)
from .api.v1 import router as api_v1_router, install_error_handlers
from .pilot import mount_pilot

app.include_router(api_v1_router)
install_error_handlers(app)
mount_pilot(app)
# 智能分析模块（独立新增）：导入失败不影响主系统
try:
    from . import analysis_routes
    app.include_router(analysis_routes.router)
    ANALYSIS_OK = True
except Exception as e:  # pragma: no cover
    ANALYSIS_OK = False
    ANALYSIS_ERR = str(e)
    print("[警告] 智能分析模块加载失败：", ANALYSIS_ERR)


# ---------------- 可选的历史授权兼容接口 ----------------

class ActivateIn(BaseModel):
    key: str


@app.get("/license")
def license_page():
    return FileResponse(STATIC_DIR / "license.html")


@app.get("/api/license/info")
def license_info():
    return {**lic.status(), "activation_required": False}


@app.post("/api/license/activate")
def license_activate(body: ActivateIn):
    exp = lic.verify(body.key, lic.machine_code())
    if exp is None:
        if body.key.strip().startswith("FT2.") and not lic.status().get("public_key_fingerprint"):
            raise HTTPException(400, "管理员尚未配置有效的验签公钥，请完成公钥配置后再输入授权码")
        raise HTTPException(400, "授权码无效：请核对机器码与授权码是否匹配、是否已过期")
    lic.save_key(body.key)
    return {"ok": True, "expiry": "永久" if exp == "0" else exp}


if getattr(sys, "frozen", False):
    STATIC_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "static"
else:
    STATIC_DIR = db.ROOT / "static"


@app.get("/analysis")
def analysis_page():
    """智能分析页面（独立程序模块）"""
    return FileResponse(STATIC_DIR / "analysis.html")


class TrackIn(BaseModel):
    case_id: int
    flow_ids: List[int]
    canvas_id: int = 0   # 目标画布（0 = 默认）


class TrackStateIn(BaseModel):
    case_id: int
    expected_ids: List[int]
    desired_ids: List[int]
    canvas_id: int = 0


class CaseIn(BaseModel):
    name: str


class CaseIdIn(BaseModel):
    case_id: int = 1


class MainAccountIn(BaseModel):
    case_id: int
    account: str
    name: str = ""


class RePreviewIn(BaseModel):
    import_id: str
    mode: str
    mapping: dict
    case_id: int = 1
    self_accounts: str = ""    # 手动填写的本方账号（可选，逗号分隔）
    mark_accounts: Optional[str] = None    # 预览中勾选的本方账号（逗号分隔），显式标记为主账号


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


# ---------------- 统计 / 案件 ----------------

@app.get("/api/stats")
def stats(case_id: int = 1):
    with db._lock:
        result = db.q("""
            select
              (select count(*) from effective_flows where case_id = ?) as flows,
              (select count(distinct a) from (
                  select payer_account as a from effective_flows where case_id = ?
                  union all select payee_account as a from effective_flows where case_id = ?
               ) where a is not null and a <> '') as accounts,
              (select count(*) from batches where case_id = ?) as batches
        """, [case_id, case_id, case_id, case_id])[0]
        result["import_coverage"] = import_coverage(case_id)
        return result


@app.get("/api/cases")
def list_cases():
    return db.q("select id, name, created_at from cases order by id")


@contextmanager
def _case_transaction(case_id):
    with db.transaction() as connection:
        require_case(case_id)
        yield connection


def _require_canvas(case_id, canvas_id):
    if canvas_id != 0 and not db.q("select id from canvases where id=? and case_id=?", [canvas_id, case_id]):
        raise HTTPException(404, "画布不存在")


def _require_flows(case_id, flow_ids, *, effective=False):
    ids = list(dict.fromkeys(int(i) for i in flow_ids))
    if ids:
        marks = ",".join("?" for _ in ids)
        table = "effective_flows" if effective else "flows"
        owned = db.q(f"select id from {table} where case_id=? and id in ({marks})", [case_id, *ids])
        if len(owned) != len(ids):
            raise HTTPException(404, "流水不存在、已匹配冲正撤销或不属于当前案件" if effective
                                else "流水不存在或不属于当前案件")
    return ids


def _require_graphable(case_id, ids):
    if ids:
        marks = ",".join("?" for _ in ids)
        if db.q(f"select id from flows where case_id=? and id in ({marks}) "
                "and (coalesce(payer_account,'')='' or coalesce(payee_account,'')='') limit 1", [case_id, *ids]):
            raise HTTPException(422, "所选流水含缺失账号的交易，事实已保留在明细中；补足身份依据后才能上图")


@app.post("/api/cases")
def create_case(body: CaseIn):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "案件名不能为空")
    try:
        with db.transaction():
            return db.q("insert into cases(name) values (?) returning id,name,created_at", [name])[0]
    except duckdb.ConstraintException as exc:
        raise HTTPException(400, "案件名已存在") from exc


@app.delete("/api/cases/{case_id}")
def delete_case(case_id: int):
    return delete_package_case(case_id)


# ---------------- 演示数据 ----------------

@app.post("/api/sample-data")
def gen_sample(body: CaseIdIn):
    with _case_transaction(body.case_id):
        return sample_data.generate(case_id=body.case_id)

# ---------------- 导入 ----------------

@app.get("/api/batches")
def batches(case_id: int = 1):
    return db.q("select id, case_id, filename, mode, rows_total, rows_imported, rows_skipped, rows_dup, imported_at "
                "from batches where case_id = ? order by id desc", [case_id])


@app.delete("/api/batches/{batch_id}")
def del_batch(batch_id: int, case_id: int = Query(...)):
    with _case_transaction(case_id):
        if not db.q("select id from batches where id=? and case_id=?", [batch_id, case_id]):
            raise HTTPException(404, "批次不存在")
        db.x("delete from tracked_flows where case_id=? and flow_id in "
             "(select id from flows where batch_id=? and case_id=?)", [case_id, batch_id, case_id])
        db.x("""update import_records set disposition='removed',reason_code='flow_deleted'
                 where case_id=? and (source_id,record_no) in
                 (select s.source_id,s.source_row from flow_sources s join flows f on f.id=s.flow_id and f.case_id=s.case_id
                  where s.case_id=? and f.batch_id=?)""", [case_id, case_id, batch_id])
        db.x("delete from flow_sources where case_id=? and flow_id in "
             "(select id from flows where batch_id=? and case_id=?)", [case_id, batch_id, case_id])
        db.x("delete from flows where batch_id=? and case_id=?", [batch_id, case_id])
        db.x("delete from batches where id=? and case_id=?", [batch_id, case_id])
    return {"ok": True}


def _legacy_import_result(operation, *args):
    try:
        return operation(*args)
    except ServiceError as error:
        raise HTTPException(error.status, error.message) from None


@app.post("/api/import/preview")
async def import_preview(file: UploadFile = File(...), case_id: Optional[int] = None):
    try:
        return await import_service.legacy_upload(file, case_id)
    except ServiceError as error:
        raise HTTPException(error.status, error.message) from None


@app.post("/api/import/repreview")
def repreview(body: RePreviewIn):
    return _legacy_import_result(import_service.legacy_repreview, body)


@app.post("/api/import/commit")
def commit(body: RePreviewIn):
    return _legacy_import_result(import_service.legacy_commit, body)


@app.get("/api/import/progress")
def import_progress(import_id: str, case_id: Optional[int] = None):
    return _legacy_import_result(import_service.legacy_progress, import_id, case_id)


@app.get("/api/account-registry")
def account_registry(case_id: int = Query(1, ge=1, le=2147483647),
                     source: Literal["flows", "identity"] = "flows", q: str = "",
                     page: int = Query(1, ge=1, le=2147483647),
                     page_size: int = Query(30, ge=1, le=100), account_exact: bool = False):
    try:
        return account_service.list_registry(case_id, source, q, page, page_size, account_exact)
    except ServiceError as error:
        raise HTTPException(error.status, error.message) from None


@app.get("/api/account-suggestions")
def account_suggestions(case_id: int = Query(..., ge=1, le=2147483647),
                        q: str = Query("", max_length=200),
                        source: Literal["flows", "identity", "all"] = "flows",
                        page_size: int = Query(30, ge=1, le=50),
                        offset: int = Query(0, ge=0, le=2147483647)):
    try:
        return account_suggestion_service.suggest_accounts(case_id, q, source, page_size, offset)
    except ServiceError as error:
        raise HTTPException(error.status, error.message) from None


@app.get("/api/account-registry/profile")
def account_registry_profile(case_id: int = Query(1, ge=1, le=2147483647),
                             account: str = Query(..., min_length=1)):
    try:
        return account_service.account_profile(case_id, account)
    except ServiceError as error:
        raise HTTPException(error.status, error.message) from None


# ---------------- 流水查询 ----------------

@app.get("/api/flows")
def flows(account: str = "", name: str = "", counter: str = "", kw: str = "",
          min_amount: Optional[str] = None, max_amount: Optional[str] = None,
          tfrom: str = "", tto: str = "", ids: str = "", case_id: int = 1,
          tracked: int = 0, canvas_id: int = 0,
          page: int = 1, size: int = Query(50, le=500), account_exact: bool = False,
          counter_exact: bool = False, entity: str = ""):
    try:
        lower = money_decimal(min_amount) if min_amount not in (None, "") else None
        upper = money_decimal(max_amount) if max_amount not in (None, "") else None
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if lower is not None and upper is not None and lower > upper:
        raise HTTPException(422, "金额下限不能超过上限")
    conds, params = ["case_id = ?"], [case_id]
    if ids:
        id_list = [str(int(x)) for x in ids.split(",") if x.strip().isdigit()]
        if id_list:
            conds.append(f"id in ({','.join(id_list)})")
    if tracked:
        # 仅已上图：限定当前画布的追踪集合
        conds.append("id in (select flow_id from tracked_flows where case_id = ? and canvas_id = ?)")
        params += [case_id, canvas_id]

    def like(v):
        return f"%{v.strip()}%"

    if account.strip():
        conds.append("(payer_account = ? or payee_account = ?)" if account_exact else "(payer_account ilike ? or payee_account ilike ?)")
        params += [account.strip() if account_exact else like(account)] * 2
    entity_query = unicodedata.normalize("NFKC", entity).strip().lower()
    if entity_query:
        # A free query searches complete row facts, independently of the bounded
        # suggestion pages. Identity is attached only to that case/account key.
        conds.append("""(
            contains(lower(coalesce(payer_account,'')), ?) or contains(lower(coalesce(payee_account,'')), ?)
            or contains(lower(coalesce(payer_name,'')), ?) or contains(lower(coalesce(payee_name,'')), ?)
            or exists (select 1 from account_ids identity
                where identity.case_id=flows.case_id
                  and (identity.account=flows.payer_account or identity.account=flows.payee_account)
                  and contains(lower(coalesce(identity.id_no,'')), ?))
        )""")
        params += [entity_query] * 5
    if name.strip():
        conds.append("(payer_name ilike ? or payee_name ilike ?)")
        params += [like(name)] * 2
    if counter.strip():
        if counter_exact:
            conds.append("(payer_account = ? or payee_account = ?)")
            params += [counter.strip()] * 2
        else:
            conds.append("""(payer_name ilike ? or payer_account ilike ? or payee_name ilike ? or payee_account ilike ?
                or exists (select 1 from account_ids identity
                    where identity.case_id=flows.case_id
                      and (identity.account=flows.payer_account or identity.account=flows.payee_account)
                      and contains(lower(coalesce(identity.id_no,'')), ?)))""")
            params += [like(counter)] * 4 + [unicodedata.normalize("NFKC", counter).strip().lower()]
    if kw.strip():
        conds.append("(summary ilike ? or trade_no ilike ?)")
        params += [like(kw)] * 2
    if lower is not None:
        conds.append("amount >= ?")
        params.append(lower)
    if upper is not None:
        conds.append("amount <= ?")
        params.append(upper)
    if tfrom:
        conds.append("trade_time >= ?")
        params.append(tfrom)
    if tto:
        conds.append("trade_time <= ?")
        params.append(tto + (" 23:59:59" if len(tto) == 10 else ""))
    where = "where " + " and ".join(conds)
    # Keep the identity subqueries bound to the case-filtered effective rows.
    total = db.q(f"select count(*) c from effective_flows as flows {where}", params)[0]["c"]
    off = (page - 1) * size
    rows = db.q(f"select * from effective_flows as flows {where} order by trade_time desc, id desc limit ? offset ?",
                params + [size, off])
    return {"total": total, "page": page, "size": size, "rows": rows}


@app.get("/api/accounts/{account}/profile")
def profile(account: str, case_id: int = 1):
    with db._lock:
        return _profile(account, case_id)


def _profile(account: str, case_id: int):
    if not account:
        raise HTTPException(422, "账号缺失，无法定位账户画像")
    r = db.q("""
        select count(*) as cnt,
          count(case when payer_account = ? then 1 end) as out_cnt,
          count(case when payee_account = ? then 1 end) as in_cnt,
          coalesce(sum(case when payer_account = ? then amount end), 0) as out_amt,
          coalesce(sum(case when payee_account = ? then amount end), 0) as in_amt,
          min(trade_time) as tmin, max(trade_time) as tmax
        from effective_flows where case_id = ? and (payer_account = ? or payee_account = ?)
    """, [account, account, account, account, case_id, account, account])[0]
    nm = db.q("""
        select name from (
          select payer_name as name from effective_flows where case_id = ? and payer_account = ?
          union all select payee_name from effective_flows where case_id = ? and payee_account = ?
        ) where name is not null and name <> '' group by name order by count(*) desc limit 1
    """, [case_id, account, case_id, account])
    r["account"] = account
    r["name"] = nm[0]["name"] if nm else ""
    r["top_counterparties"] = db.q("""
        select acct, max(nm) as name,
          sum(case when dir = 'out' then 1 else 0 end) as out_cnt,
          coalesce(sum(case when dir = 'out' then amt else 0 end), 0) as out_amt,
          sum(case when dir = 'in' then 1 else 0 end) as in_cnt,
          coalesce(sum(case when dir = 'in' then amt else 0 end), 0) as in_amt
        from (
          select payee_account as acct, payee_name as nm, 'out' as dir, amount as amt
            from effective_flows where case_id = ? and payer_account = ?
          union all
          select payer_account, payer_name, 'in', amount
            from effective_flows where case_id = ? and payee_account = ?
        ) where coalesce(acct,'')<>'' group by acct order by out_cnt desc, in_cnt desc, acct limit 20
    """, [case_id, account, case_id, account])
    money_rows = db.q_raw("""
        select acct, coalesce(currency, '') currency,
          count(*) filter (where dir='out') out_cnt,
          coalesce(sum(amount) filter (where dir='out'), 0) out_amt,
          count(*) filter (where dir='in') in_cnt,
          coalesce(sum(amount) filter (where dir='in'), 0) in_amt
        from (
          select payee_account acct, currency, amount, 'out' dir
            from effective_flows where case_id=? and payer_account=?
          union all
          select payer_account, currency, amount, 'in'
            from effective_flows where case_id=? and payee_account=?
        ) where coalesce(acct,'')<>'' group by acct, coalesce(currency, '') order by currency
    """, [case_id, account, case_id, account])
    for counterparty in r["top_counterparties"]:
        counterparty.update(_directional_money([row for row in money_rows if row["acct"] == counterparty["acct"]]))
    own_totals = db.q_raw("""
        select coalesce(currency, '') currency,
          count(*) filter (where payer_account=?) out_cnt,
          coalesce(sum(amount) filter (where payer_account=?), 0) out_amt,
          count(*) filter (where payee_account=?) in_cnt,
          coalesce(sum(amount) filter (where payee_account=?), 0) in_amt
        from effective_flows where case_id=? and (payer_account=? or payee_account=?)
        group by coalesce(currency, '') order by currency
    """, [account, account, account, account, case_id, account, account])
    r.update(_directional_money(own_totals))
    return r


# ---------------- 主账号标记 / 自动识别 ----------------

def _directional_money(rows):
    """Currency-separated totals; a mixed-currency scalar has no meaning."""
    result = {}
    for direction in ("out", "in"):
        totals = [{"currency": row["currency"], "amount": money_string(row[direction + "_amt"])}
                  for row in rows if row[direction + "_cnt"]]
        result[direction + "_totals"] = totals
        result[direction + "_amt"] = totals[0]["amount"] if len(totals) == 1 else (None if totals else "0.00")
    return result

def _auto_detect_main(batch_id: int, case_id: int):
    """从批次流水中自动识别主账号。两级策略：
    1) 声明优先：批次流水中的各个 self_account（导入时映射/手填的声明）；
    2) 占比后备：无声明时，统计各账号出现占比 ≥ 90% 即户主（单账户流水特征）。"""
    total = db.q("select count(*) c from flows where batch_id = ? and case_id = ?", [batch_id, case_id])[0]["c"]
    if not total:
        return None
    # 1) 声明优先（含被去重的行无法追溯，此处按已入库行统计声明）
    declared = db.q("""
        select trim(self_account) acct, max(self_name) nm, count(*) c
        from flows where batch_id = ? and case_id = ?
          and self_account is not null and trim(self_account) <> ''
        group by trim(self_account) order by c desc, acct
    """, [batch_id, case_id])
    if declared:
        marked = []
        for d in declared:
            marked.append({**mark_automatic(case_id, d["acct"], d["nm"]), "flows": d["c"], "total": total})
        return {"accounts": marked, "account": marked[0]["account"], "name": marked[0]["name"],
                "flows": total, "total": total}
    # 2) 占比后备
    candidates = db.q("""
        select acct, count(distinct id) c, max(nm) nm from (
            select id, trim(payer_account) acct, payer_name nm from flows
              where batch_id = ? and case_id = ? and payer_account is not null and trim(payer_account) <> ''
            union all
            select id, trim(payee_account), payee_name from flows
              where batch_id = ? and case_id = ? and payee_account is not null and trim(payee_account) <> ''
        ) group by acct order by c desc, acct limit 2
    """, [batch_id, case_id, batch_id, case_id])
    if not candidates or candidates[0]["c"] < total * 0.9:
        return None   # 双边流水/多户混合批次，无法可靠识别
    cand = candidates[0]
    if len(candidates) > 1 and candidates[1]["c"] == cand["c"]:
        return None  # 同等占比没有可辨别的本方，不按字母顺序任意选一个。
    return {**mark_automatic(case_id, cand["acct"], cand["nm"]), "flows": cand["c"], "total": total}


@app.post("/api/main-accounts/auto")
def auto_detect_all(case_id_in: CaseIdIn):
    """扫描当前案件的全部单账户（模式B）批次，自动识别主账号（存量数据一键识别）"""
    with _case_transaction(case_id_in.case_id):
        batches = db.q("select id, filename from batches where case_id = ? and mode = 'B' order by id", [case_id_in.case_id])
        detected, seen = [], set()
        for b in batches:
            d = _auto_detect_main(b["id"], case_id_in.case_id)
            for account in (d.get("accounts", [d]) if d else []):
                if account["account"] not in seen:
                    seen.add(account["account"])
                    detected.append({**account, "batch": b["filename"]})
        return {"detected": detected, "batches_scanned": len(batches),
                "added_count": sum(item["added"] for item in detected),
                "manual_preserved": sum(item["source"] == "manual" for item in detected),
                "existing_auto_count": sum(item["source"] == "auto" and not item["added"] for item in detected)}


_CASE_ACCOUNTS_SQL = """
    with account_rows as (
        select trim(payer_account) as account, trim(coalesce(payer_name, '')) as name
        from flows where case_id=? and payer_account is not null
        union all
        select trim(payee_account), trim(coalesce(payee_name, ''))
        from flows where case_id=? and payee_account is not null
    ), accounts as (
        select account, max(name) as name from account_rows where account<>'' group by account
    )
"""


@app.get("/api/main-accounts/candidates")
def main_account_candidates(case_id: int, q: str = "", exact: bool = False, page: int = Query(1, ge=1),
                            page_size: int = Query(30, ge=1, le=100)):
    """Search accounts, rather than a truncated page of transaction matches."""
    with _case_transaction(case_id):
        keyword = q.strip()
        if exact:
            criterion, search_params = "a.account=?", [keyword]
        else:
            keyword = unicodedata.normalize("NFKC", q).strip().lower()
            criterion = """contains(lower(a.account), ?) or exists (
                select 1 from account_rows names
                where names.account=a.account and contains(lower(names.name), ?)
            ) or contains(lower(coalesce(m.name,'')), ?) or exists (
                select 1 from account_ids identity
                where identity.case_id=? and identity.account=a.account
                  and contains(lower(coalesce(identity.id_no,'')), ?)
            )"""
            search_params = [keyword] * 3 + [case_id, keyword]
        matching = _CASE_ACCOUNTS_SQL + """
            , matching as (
                select a.account, coalesce(nullif(m.name,''), a.name) as name,
                       m.account is not null as is_main,
                       case when m.account is null then null when m.source='auto' then 'auto' else 'manual' end as source
                from accounts a left join main_accounts m on m.case_id=? and m.account=a.account
                where """ + criterion + """
            )
        """
        params = [case_id, case_id, case_id, *search_params]
        total = db.q(matching + "select count(*) total from matching", params)[0]["total"]
        items = db.q(matching + "select * from matching order by account limit ? offset ?",
                     [*params, page_size, (page - 1) * page_size])
        return {"items": items, "total": total, "page": page, "page_size": page_size}

@app.get("/api/main-accounts")
def list_main_accounts(case_id: int = 1):
    with _case_transaction(case_id):
        return db.q("select case_id, account, name, source, marked_at from main_accounts where case_id = ? order by marked_at, account",
                    [case_id])


@app.post("/api/main-accounts/clear-auto")
def clear_auto_main_accounts(body: CaseIdIn):
    """清除本案件全部"自动识别"的主账号标记（手动标记保留）"""
    with _case_transaction(body.case_id):
        n = len(db.q("select account from main_accounts where case_id = ? and source = 'auto'", [body.case_id]))
        db.x("delete from main_accounts where case_id = ? and source = 'auto'", [body.case_id])
        return {"cleared": n}

@app.get("/api/debug/batch-accounts")
def debug_batch_accounts(case_id: int = 1):
    """诊断：各批次的模式、行数、出现占比最高的账号（用于排查主账号识别）"""
    out = []
    for b in db.q("select id, filename, mode, rows_imported from batches where case_id = ? order by id", [case_id]):
        total = db.q("select count(*) c from flows where batch_id = ?", [b["id"]])[0]["c"]
        top = db.q("""
            select acct, count(*) c, max(nm) nm from (
                select payer_account acct, payer_name nm from flows
                  where batch_id = ? and payer_account is not null and payer_account <> ''
                union all
                select payee_account, payee_name from flows
                  where batch_id = ? and payee_account is not null and payee_account <> ''
            ) group by acct order by c desc limit 3
        """, [b["id"], b["id"]])
        out.append({"batch_id": b["id"], "filename": b["filename"], "mode": b["mode"],
                    "rows": total, "top_accounts": top})
    return out


@app.post("/api/main-accounts")
def mark_main_account(body: MainAccountIn):
    with _case_transaction(body.case_id):
        acct = body.account.strip()
        if not acct:
            raise HTTPException(400, "账号不能为空")
        existing_account = db.q(_CASE_ACCOUNTS_SQL + "select name from accounts where account=?",
                                [body.case_id, body.case_id, acct])
        existing_mark = db.q("select name from main_accounts where case_id=? and account=?", [body.case_id, acct])
        if not existing_account and not existing_mark:
            raise HTTPException(404, "账号不存在或不属于当前案件")
        name = (body.name.strip() or (existing_mark[0]["name"] if existing_mark else "")
                or (existing_account[0]["name"] if existing_account else "") or "")
        db.x("insert or replace into main_accounts(case_id, account, name, source) values (?,?,?, 'manual')",
             [body.case_id, acct, name])
        return {"ok": True, "account": acct, "name": name, "source": "manual"}

@app.delete("/api/main-accounts")
def unmark_main_account(body: MainAccountIn):
    with _case_transaction(body.case_id):
        acct = body.account.strip()
        if not acct:
            raise HTTPException(400, "账号不能为空")
        db.x("delete from main_accounts where case_id = ? and account = ?", [body.case_id, acct])
        return {"ok": True}

# ---------------- 图谱 / 追踪 ----------------

@app.get("/api/graph")
def graph(case_id: int, canvas_id: int = 0):
    with db._lock:
        return _graph(case_id, canvas_id)


@app.post("/api/canvas/investigation/time")
def canvas_investigation_time(body: canvas_investigation.TimeInvestigationIn):
    with db._lock:
        require_case(body.case_id)
        _require_canvas(body.case_id, body.canvas_id)
        return canvas_investigation.time_projection(body)


@app.post("/api/canvas/investigation/paths")
def canvas_investigation_paths(body: canvas_investigation.PathInvestigationIn):
    with db._lock:
        require_case(body.case_id)
        _require_canvas(body.case_id, body.canvas_id)
        return canvas_investigation.paths(body)


def _graph_mirror_duplicate_ids(case_id: int, canvas_id: int):
    """Pair each tracked row with at most one oldest compatible ledger mirror."""
    rows = db.q_raw("""
        select f.id, f.payer_account, f.payee_account, f.amount,
               coalesce(f.currency, '') as currency, coalesce(f.self_account, '') as self_account,
               date_trunc('second', f.trade_time) as mirror_time
        from effective_flows f join tracked_flows tf on tf.flow_id = f.id
        where tf.case_id = ? and tf.canvas_id = ? and f.case_id = ?
        order by f.trade_time, f.id
    """, [case_id, canvas_id, case_id])
    return [flow_id for ids in canvas_investigation.mirror_groups(rows).values() for flow_id in ids]


def _graph(case_id: int, canvas_id: int):
    # 统计口径修正（两处历史问题）：
    # 1) 双方流水都导入时，同一笔交易会以两份记录入库（时间差秒级、余额/摘要/流水号不同，
    #    不满足全列去重）→ 同 付款方+收款方+金额 且时间相差 ≤120 秒、且「本方账号声明不同
    #    （或未声明）」的两行配对只计一笔（is_dup，保留较早一行）。每条记录最多配对一次，
    #    不能让已配对的镜像吞掉后续真实交易。同一 self_account 的连续多笔不相互合并。
    # 2) main_accounts 的 OR 连接（账号或户名匹配）在同户名多账号时会把行数放大数倍，
    #    导致笔数/金额虚增 → 改用 EXISTS 子查询，只做标记、不复制行。
    # 画布维度：只统计当前画布追踪的流水（canvas_id 0 = 默认画布）。
    dedup_params = [case_id, canvas_id, case_id, _graph_mirror_duplicate_ids(case_id, canvas_id)]
    dedup_cte = """
        with t as (select f.* from effective_flows f join tracked_flows tf on tf.flow_id = f.id
                   where tf.case_id = ? and tf.canvas_id = ? and f.case_id = ?
                     and coalesce(f.payer_account,'')<>'' and coalesce(f.payee_account,'')<>''),
        nd as (select f.*, f.id in (select unnest(?::bigint[])) as is_dup
               from t f)
    """
    nodes = db.q(dedup_cte + """
        select t2.account, max(t2.name) as name,
               sum(case when t2.ispay = 1 and not t2.is_dup then 1 else 0 end) as out_cnt,
               sum(case when t2.ispay = 0 and not t2.is_dup then 1 else 0 end) as in_cnt,
               coalesce(sum(case when t2.ispay = 1 and not t2.is_dup then t2.amount else 0 end), 0) as out_amt,
               coalesce(sum(case when t2.ispay = 0 and not t2.is_dup then t2.amount else 0 end), 0) as in_amt,
               max(case when exists(select 1 from main_accounts m
                                    where m.case_id = ? and m.account = t2.account) then 1 else 0 end) as is_main,
               max(case when exists(select 1 from main_accounts m2
                                    where m2.case_id = ? and m2.name <> '' and m2.name = t2.name
                                      and m2.account <> t2.account) then 1 else 0 end) as is_same_name,
               max(ai.id_no) as id_no,
               max(ap.updated_at) as photo_at
        from (
            select payer_account as account, payer_name as name, 1 as ispay, amount, is_dup from nd
            union all
            select payee_account, payee_name, 0, amount, is_dup from nd
        ) t2
        left join account_ids ai on ai.case_id = ? and ai.account = t2.account
        left join account_photos ap on ap.case_id = ? and ap.account = t2.account
        group by t2.account
    """, [*dedup_params, case_id, case_id, case_id, case_id])
    edges = db.q(dedup_cte + """
        select payer_account as source, max(payer_name) as source_name,
               payee_account as target, max(payee_name) as target_name,
               count(*) filter (where not is_dup) as cnt,
               sum(amount) filter (where not is_dup) as total,
               min(trade_time) as tmin, max(trade_time) as tmax,
               list(distinct summary) filter (summary is not null and summary <> '') as summaries,
               list(distinct currency) filter (currency is not null and currency <> '' and currency <> 'CNY') as currencies,
               list(id) as ids
        from nd group by payer_account, payee_account
    """, dedup_params)
    amounts = db.q_raw(dedup_cte + """
        select payer_account as source, payee_account as target, coalesce(currency, '') currency,
               sum(amount) amount
        from nd where not is_dup group by payer_account, payee_account, coalesce(currency, '') order by currency
    """, dedup_params)
    by_pair, by_node = {}, {}
    for row in amounts:
        total = {"currency": row["currency"], "amount": money_string(row["amount"])}
        by_pair.setdefault((row["source"], row["target"]), []).append(total)
        for account, direction in ((row["source"], "out"), (row["target"], "in")):
            grouped = by_node.setdefault(account, {}).setdefault(row["currency"], {
                "currency": row["currency"], "out_cnt": 0, "in_cnt": 0,
                "out_amt": money_decimal("0"), "in_amt": money_decimal("0")})
            grouped[direction + "_cnt"] += 1
            grouped[direction + "_amt"] += row["amount"]
    for edge in edges:
        edge["amount_totals"] = by_pair.get((edge["source"], edge["target"]), [])
        edge["currencies"] = [row["currency"] for row in edge["amount_totals"]]
        edge["total"] = edge["amount_totals"][0]["amount"] if len(edge["amount_totals"]) == 1 else None
    for node in nodes:
        node.update(_directional_money(list(by_node.get(node["account"], {}).values())))
    tracked = [r["flow_id"] for r in db.q(
        "select flow_id from tracked_flows where case_id = ? and canvas_id = ?", [case_id, canvas_id])]
    mains = [r["account"] for r in db.q("select account from main_accounts where case_id = ?", [case_id])]
    focus = [{"account": r["account"], "scale": r.get("scale") or 1.6}
             for r in db.q("select account, scale from focus_nodes where case_id = ?", [case_id])]
    return {"nodes": nodes, "edges": edges, "tracked_ids": tracked,
            "main_accounts": mains, "focus_nodes": focus}


class FocusIn(BaseModel):
    case_id: int = 1
    account: str
    on: bool
    scale: float = 1.6


# ---------------- 画布（多桌布） ----------------

class CanvasSaveIn(BaseModel):
    case_id: int = 1
    canvas_id: Optional[int] = None   # 空 = 新建（另存为）
    name: str
    snapshot: dict                    # 前端画布快照（与导出 HTML 同构）


class CanvasRenameIn(BaseModel):
    case_id: int = 1
    canvas_id: int
    name: str


class CanvasFromFlowsIn(BaseModel):
    case_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=60)
    flow_ids: List[int]
    snapshot: dict


@app.get("/api/canvases")
def canvases_list(case_id: int = 1):
    """画布列表（不含快照体，避免大响应）"""
    return db.q("select id, name, updated_at from canvases where case_id = ? order by updated_at desc",
                [case_id])


@app.post("/api/canvases/save")
def canvas_save(body: CanvasSaveIn):
    """保存画布：有 id 覆盖更新，无 id 新建"""
    with _case_transaction(body.case_id):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "画布名称不能为空")
        snap = json.dumps(body.snapshot, ensure_ascii=False)
        if body.canvas_id:
            row = db.q("select id from canvases where id = ? and case_id = ?",
                       [body.canvas_id, body.case_id])
            if not row:
                raise HTTPException(404, "画布不存在")
            db.x("update canvases set name = ?, snapshot = ?, updated_at = current_timestamp where id = ?",
                 [name, snap, body.canvas_id])
            return {"ok": True, "canvas_id": body.canvas_id, "name": name}
        r = db.q("insert into canvases(case_id, name, snapshot) values (?,?,?) returning id",
                 [body.case_id, name, snap])[0]
        return {"ok": True, "canvas_id": r["id"], "name": name}


@app.post("/api/canvases/from-flows")
def canvas_from_flows(body: CanvasFromFlowsIn):
    """Create a dedicated analysis canvas and its membership as one operation.

    Retries reuse only marked canvases with the exact current flow set. Display
    names are not identifiers; user canvases and modified sets remain untouched.
    The marker contains no IDs, so case-package remapping stays lossless.
    """
    with _case_transaction(body.case_id):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "画布名称不能为空")
        ids = sorted(_require_flows(body.case_id, body.flow_ids, effective=True))
        _require_graphable(body.case_id, ids)
        if not ids:
            raise HTTPException(400, "请选择需要上图的流水")
        candidates = db.q(
            "select id,name from canvases where case_id=? "
            "and json_extract_string(snapshot, '$.analysisOrigin')='cycle.v1' order by id",
            [body.case_id])
        for candidate in candidates:
            tracked = [row["flow_id"] for row in db.q(
                "select flow_id from tracked_flows where case_id=? and canvas_id=? order by flow_id",
                [body.case_id, candidate["id"]])]
            if tracked == ids:
                return {"ok": True, "canvas_id": candidate["id"], "name": candidate["name"], "added": 0}
        snapshot = {**body.snapshot, "analysisOrigin": "cycle.v1"}
        canvas_id = db.q("insert into canvases(case_id,name,snapshot) values (?,?,?) returning id",
                         [body.case_id, name, json.dumps(snapshot, ensure_ascii=False)])[0]["id"]
        marks = ",".join("?" for _ in ids)
        db.x(f"insert into tracked_flows(case_id,flow_id,canvas_id) "
             f"select ?,id,? from effective_flows where case_id=? and id in ({marks})",
             [body.case_id, canvas_id, body.case_id, *ids])
        return {"ok": True, "canvas_id": canvas_id, "name": name, "added": len(ids)}


@app.get("/api/canvases/{canvas_id}")
def canvas_load(canvas_id: int, case_id: int = 1):
    """调阅画布：返回快照 JSON"""
    row = db.q("select id, name, snapshot from canvases where id = ? and case_id = ?",
               [canvas_id, case_id])
    if not row:
        raise HTTPException(404, "画布不存在")
    return {"id": row[0]["id"], "name": row[0]["name"],
            "snapshot": json.loads(row[0]["snapshot"])}


class CanvasCloneIn(BaseModel):
    case_id: int = 1
    from_canvas: int = 0        # 源画布（0 = 默认画布也可另存）
    name: str
    snapshot: dict


@app.post("/api/canvases/clone")
def canvas_clone(body: CanvasCloneIn):
    """把源画布（含默认画布）的上图流水 + 形态另存为一张新画布；源画布保持不变"""
    with _case_transaction(body.case_id):
        _require_canvas(body.case_id, body.from_canvas)
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "画布名称不能为空")
        snap = json.dumps(body.snapshot, ensure_ascii=False)
        r = db.q("insert into canvases(case_id, name, snapshot) values (?,?,?) returning id",
                 [body.case_id, name, snap])[0]
        new_id = r["id"]
        db.x("insert or ignore into tracked_flows(case_id, flow_id, canvas_id) "
             "select case_id, flow_id, ? from tracked_flows where case_id = ? and canvas_id = ?",
             [new_id, body.case_id, body.from_canvas])
        copied = db.q("select count(*) c from tracked_flows where case_id = ? and canvas_id = ?",
                      [body.case_id, new_id])[0]["c"]
        return {"ok": True, "canvas_id": new_id, "name": name, "copied": copied}

@app.post("/api/canvases/rename")
def canvas_rename(body: CanvasRenameIn):
    with _case_transaction(body.case_id):
        if body.canvas_id <= 0:
            raise HTTPException(400, "默认画布不可重命名，请另存为命名画布")
        _require_canvas(body.case_id, body.canvas_id)
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "画布名称不能为空")
        db.x("update canvases set name = ?, updated_at = current_timestamp "
             "where id = ? and case_id = ?", [name, body.canvas_id, body.case_id])
        return {"ok": True}

@app.delete("/api/canvases/{canvas_id}")
def canvas_delete(canvas_id: int, case_id: int = 1):
    with _case_transaction(case_id):
        if canvas_id <= 0:
            raise HTTPException(400, "默认画布不可删除")
        _require_canvas(case_id, canvas_id)
        db.x("delete from canvases where id = ? and case_id = ?", [canvas_id, case_id])
        # 画布删除时连同该画布的追踪记录一起清理
        db.x("delete from tracked_flows where case_id = ? and canvas_id = ?", [case_id, canvas_id])
        return {"ok": True}

@app.post("/api/focus-nodes/set")
def focus_node_set(body: FocusIn):
    """设置/取消节点重点展示（画布按倍数放大，1~4 倍；on=false 或 scale<=1 取消）"""
    with _case_transaction(body.case_id):
        acct = body.account.strip()
        if not acct:
            raise HTTPException(400, "账号不能为空")
        if body.on and body.scale > 1.0:
            scale = min(4.0, max(1.1, float(body.scale)))
            db.x("insert or replace into focus_nodes(case_id, account, scale) values (?,?,?)",
                 [body.case_id, acct, scale])
            return {"ok": True, "account": acct, "on": True, "scale": scale}
        db.x("delete from focus_nodes where case_id = ? and account = ?", [body.case_id, acct])
        return {"ok": True, "account": acct, "on": False}

@app.post("/api/track")
def track(body: TrackIn):
    with _case_transaction(body.case_id):
        _require_canvas(body.case_id, body.canvas_id)
        ids = _require_flows(body.case_id, body.flow_ids, effective=True)
        _require_graphable(body.case_id, ids)
        if ids:
            marks = ",".join("?" for _ in ids)
            before = db.q("select count(*) n from tracked_flows where case_id=? and canvas_id=?", [body.case_id, body.canvas_id])[0]["n"]
            db.x(f"insert or ignore into tracked_flows(case_id,flow_id,canvas_id) "
                 f"select ?,id,? from effective_flows where case_id=? and id in ({marks})",
                 [body.case_id, body.canvas_id, body.case_id, *ids])
            after = db.q("select count(*) n from tracked_flows where case_id=? and canvas_id=?", [body.case_id, body.canvas_id])[0]["n"]
            return {"ok": True, "added": after - before}
        return {"ok": True, "added": 0}


@app.delete("/api/track")
def untrack(body: TrackIn):
    with _case_transaction(body.case_id):
        _require_canvas(body.case_id, body.canvas_id)
        ids = _require_flows(body.case_id, body.flow_ids)
        if ids:
            marks = ",".join("?" for _ in ids)
            db.x(f"delete from tracked_flows where case_id=? and canvas_id=? and flow_id in ({marks})",
                 [body.case_id, body.canvas_id, *ids])
        return {"ok": True}


@app.put("/api/track/state")
def restore_track_state(body: TrackStateIn):
    """按预期集合原子恢复当前画布追踪；过期历史不得覆盖新的追踪操作。"""
    with _case_transaction(body.case_id):
        _require_canvas(body.case_id, body.canvas_id)
        current = {row["flow_id"] for row in db.q(
            "select flow_id from tracked_flows where case_id=? and canvas_id=?",
            [body.case_id, body.canvas_id])}
        if current != set(body.expected_ids):
            raise HTTPException(409, "画布追踪已变更，请刷新后重试")
        desired = set(_require_flows(body.case_id, body.desired_ids))
        removed, added = sorted(current - desired), sorted(desired - current)
        _require_flows(body.case_id, added, effective=True)
        _require_graphable(body.case_id, added)
        if removed:
            marks = ",".join("?" for _ in removed)
            db.x(f"delete from tracked_flows where case_id=? and canvas_id=? and flow_id in ({marks})",
                 [body.case_id, body.canvas_id, *removed])
        if added:
            marks = ",".join("?" for _ in added)
            db.x(f"insert into tracked_flows(case_id,flow_id,canvas_id) "
                 f"select ?,id,? from effective_flows where case_id=? and id in ({marks})",
                 [body.case_id, body.canvas_id, body.case_id, *added])
        return {"ok": True, "tracked_ids": sorted(desired)}


@app.delete("/api/graph")
def clear_graph(case_id: int, canvas_id: int = 0):
    """清空图谱：只清当前画布的追踪（不影响其他画布）"""
    with _case_transaction(case_id):
        _require_canvas(case_id, canvas_id)
        db.x("delete from tracked_flows where case_id = ? and canvas_id = ?", [case_id, canvas_id])
        return {"ok": True}

class BulkTrackIn(BaseModel):
    case_id: int
    flow_ids: List[int]     # 用户勾选的锚点流水
    direction: str          # source=追踪资金来源 / target=追踪资金去向
    x: int = 5              # 每笔锚点取最近 X 笔
    canvas_id: int = 0
    expected_ids: Optional[List[int]] = None


@app.post("/api/track/bulk")
def track_bulk(body: BulkTrackIn):
    """批量追踪（本笔交易一并上图）：
    - direction=target 资金去向：看【收款方】(必须是主账号)在锚点之后转出的钱 → 收款方视角
    - direction=source 资金来源：看【付款方】(必须是主账号)在锚点之前收到的钱 → 付款方视角
    对应方不是主账号 → 该锚点拒绝追踪并在 skipped 中说明（本笔仍上图）。"""
    with _case_transaction(body.case_id):
        _require_canvas(body.case_id, body.canvas_id)
        if body.expected_ids is not None:
            current = {row["flow_id"] for row in db.q(
                "select flow_id from tracked_flows where case_id=? and canvas_id=?",
                [body.case_id, body.canvas_id])}
            if current != set(body.expected_ids):
                raise HTTPException(409, "画布追踪已变更，请刷新后重试")
        _require_flows(body.case_id, body.flow_ids, effective=True)
        if not body.flow_ids:
            raise HTTPException(404, "未找到所选流水")
        if body.direction not in ("source", "target"):
            raise HTTPException(400, "direction 应为 source 或 target")
        x = max(1, min(50, body.x))
        id_str = ",".join(str(int(i)) for i in body.flow_ids)
        anchors = db.q(f"select * from effective_flows where case_id = ? and id in ({id_str})", [body.case_id])
        if not anchors:
            raise HTTPException(404, "未找到所选流水")
        _require_graphable(body.case_id, [row["id"] for row in anchors])
        mains = {r["account"] for r in db.q("select account from main_accounts where case_id = ?",
                                            [body.case_id])}

        pick_ids = set()
        detail = []
        skipped_main = []
        for f in anchors:
            if body.direction == "source":
                # 资金来源：付款方（必须是主账号）在锚点【之前】收到的钱
                acct = f["payer_account"]
                acct_label = "付款方"
            else:
                # 资金去向：收款方（必须是主账号）在锚点【之后】转出的钱
                acct = f["payee_account"]
                acct_label = "收款方"
            picked = 0
            if acct in mains:
                if body.direction == "source":
                    rows = db.q("""
                        select id from effective_flows
                        where case_id = ? and payee_account = ? and self_account = ?
                          and coalesce(payer_account,'')<>'' and coalesce(payee_account,'')<>''
                          and (trade_time < ? or (trade_time = ? and id < ?))
                        order by trade_time desc, id desc limit ?
                    """, [body.case_id, acct, acct, f["trade_time"], f["trade_time"], f["id"], x])
                else:
                    rows = db.q("""
                        select id from effective_flows
                        where case_id = ? and payer_account = ? and self_account = ?
                          and coalesce(payer_account,'')<>'' and coalesce(payee_account,'')<>''
                          and (trade_time > ? or (trade_time = ? and id > ?))
                        order by trade_time asc, id asc limit ?
                    """, [body.case_id, acct, acct, f["trade_time"], f["trade_time"], f["id"], x])
                for r in rows:
                    pick_ids.add(r["id"])
                picked = len(rows)
            else:
                skipped_main.append({"anchor_id": f["id"], "account": acct, "kind": acct_label})
            detail.append({
                "anchor_id": f["id"],
                "anchor_time": f["trade_time"],
                "account": acct,
                "counter_kind": "in" if body.direction == "source" else "out",
                "picked": picked,
                "is_main": acct in mains,
            })
            pick_ids.add(f["id"])   # 锚点本身（本笔交易）也上图

        if pick_ids:
            ids2 = ",".join(str(i) for i in pick_ids)
            db.x(f"insert or ignore into tracked_flows(case_id, flow_id, canvas_id) "
                 f"select ?, id, ? from effective_flows where case_id = ? and id in ({ids2})",
                 [body.case_id, body.canvas_id, body.case_id])
        tracked_ids = [row["flow_id"] for row in db.q(
            "select flow_id from tracked_flows where case_id=? and canvas_id=? order by flow_id",
            [body.case_id, body.canvas_id])]
        return {"ok": True, "tracked": len(pick_ids), "anchors": len(anchors),
                "detail": detail, "skipped_not_main": skipped_main, "tracked_ids": tracked_ids}

@app.get("/api/context/{flow_id}")
def context(flow_id: int, x: int = Query(5, ge=1, le=50), side: str = "payee", case_id: int = 1):
    """某笔流水的交易对手在该账户流水中、位于本笔前后各 X 笔的流水（限当前案件）"""
    f = db.q("select * from effective_flows where id = ? and case_id = ?", [flow_id, case_id])
    if not f:
        raise HTTPException(404, "流水不存在")
    f = f[0]
    if side == "payer":
        acct = f["payer_account"]
    else:
        side = "payee"
        acct = f["payee_account"]
    if not acct:
        raise HTTPException(422, "该侧账号缺失，无法定位账户流水；原交易仍保留在明细中")
    anchor = db.q("""
        with acct as (select id, trade_time from effective_flows
                      where case_id = ? and (payer_account = ? or payee_account = ?))
        select count(*) as c from acct
        where trade_time < (select trade_time from effective_flows where id = ?)
           or (trade_time = (select trade_time from effective_flows where id = ?) and id < ?)
    """, [case_id, acct, acct, flow_id, flow_id, flow_id])[0]["c"] + 1
    rows = db.q("""
        with acct as (
            select id, trade_no, trade_time, amount, balance, currency, summary, channel,
                   payer_account, payer_name, payee_account, payee_name, self_account,
                   case when payer_account = ? then 'out' else 'in' end as dir,
                   case when self_account = ? then 'self'
                        when coalesce(self_account, '') <> '' then 'counter'
                        else '' end as book
            from effective_flows where case_id = ? and (payer_account = ? or payee_account = ?)
        ), ranked as (
            select *, row_number() over (order by trade_time, id) as rn from acct
        )
        select * from ranked where rn between ? and ? order by rn
    """, [acct, acct, case_id, acct, acct, anchor - x, anchor + x])
    total = db.q("select count(*) c from effective_flows where case_id = ? and (payer_account = ? or payee_account = ?)",
                 [case_id, acct, acct])[0]["c"]
    result = {"anchor": f, "side": side, "account": acct, "total": total, "pos": anchor, "rows": rows}
    result["suggestions"] = _suggest_counterparties(rows, f)
    return result


def _fmt_minutes(m):
    if m < 1:
        return "1 分钟内"
    if m < 60:
        return f"{int(m)} 分钟"
    if m < 1440:
        return f"{m / 60:.1f} 小时"
    return f"{m / 1440:.1f} 天"


def _tscore(dt_min):
    """时间紧邻性得分：快进快出的核心信号"""
    if dt_min <= 2:
        return 30
    if dt_min <= 10:
        return 25
    if dt_min <= 60:
        return 18
    if dt_min <= 360:
        return 10
    if dt_min <= 1440:
        return 5
    return 0


def _ascore(ratio):
    """金额匹配度得分：等额/近似额最高"""
    if amt_is_close(ratio, money_decimal("0.90"), money_decimal("1.10")):
        return 30
    if money_decimal("0.50") <= ratio < money_decimal("0.90") or money_decimal("1.10") < ratio <= money_decimal("1.50"):
        return 20
    if money_decimal("0.30") <= ratio < money_decimal("0.50"):
        return 12
    return 5


def amt_is_close(ratio, lo, hi):
    return lo <= ratio <= hi


def _suggest_counterparties(rows, anchor):
    """在前后 X 笔候选中，按启发式规则推荐疑似来源 / 去向对手（可解释评分）"""
    try:
        at = _dt.strptime(anchor["trade_time"], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return {"sources": [], "targets": []}
    amt = money_decimal(anchor["amount"] or "0")

    groups = {}
    for r in rows:
        if r["id"] == anchor["id"]:
            continue
        try:
            _dt.strptime(r["trade_time"], "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            continue
        if (r.get("currency") or "") != (anchor.get("currency") or ""):
            continue
        if r["dir"] == "in":       # 对手转入本方账户 → 对手是付款方
            acct, name = r["payer_account"], r["payer_name"]
        else:                       # 本方转出 → 对手是收款方
            acct, name = r["payee_account"], r["payee_name"]
        if not acct:
            continue
        g = groups.setdefault(acct, {"account": acct, "name": name or "", "flows": []})
        g["flows"].append(r)

    def build(g, kind):
        """kind='source'：锚点之前的转入；kind='target'：锚点之后的转出"""
        rel = "in" if kind == "source" else "out"
        fs = [f for f in g["flows"] if f["dir"] == rel]
        if not fs:
            return None
        if kind == "source":
            fs = [f for f in fs if f["trade_time"] < anchor["trade_time"]]
        else:
            fs = [f for f in fs if f["trade_time"] > anchor["trade_time"]]
        if not fs:
            return None
        ids = [f["id"] for f in fs]
        total = sum((money_decimal(f["amount"] or "0") for f in fs), money_decimal("0"))
        n = len(fs)
        ratio = (total / amt) if amt else 0
        dt_min = min(abs((_dt.strptime(f["trade_time"], "%Y-%m-%d %H:%M:%S") - at).total_seconds()) for f in fs) / 60
        score = 40 + _tscore(dt_min) + _ascore(ratio)
        reasons = []
        tags = []
        first = fs[0]["trade_time"]
        if n >= 2 and amt and money_decimal("0.80") <= ratio <= money_decimal("1.05"):
            tags.append("疑似拆分转移")
            score += 15
            reasons.append(f"{n} 笔合计 {money_display(total)} {anchor.get('currency') or '未标注币种'} ≈ 锚点金额的 {ratio:.0%}")
        elif amt and money_decimal("0.90") <= ratio <= money_decimal("1.10"):
            reasons.append(f"金额 {money_display(total)} 与锚点（{money_display(amt)}）基本一致")
        else:
            reasons.append(f"{'转入' if kind == 'source' else '转出'} {money_display(total)} {anchor.get('currency') or '未标注币种'}" +
                           (f"（锚点的 {ratio:.0%}）" if amt else ""))
        reasons.insert(0, f"距锚点 {_fmt_minutes(dt_min)}")
        # 快进快出：本账户在锚点附近既有转入又有转出
        other = [f for f in g["flows"] if f["dir"] != rel]
        if other:
            o_dt = min(abs((_dt.strptime(f["trade_time"], "%Y-%m-%d %H:%M:%S") - at).total_seconds()) for f in other) / 60
            if o_dt <= 60:
                tags.append("疑似过账")
                score += 10
        # 方向纯度
        opp = len([f for f in g["flows"] if f["dir"] != rel])
        if opp == 0:
            score += 5
        elif opp > n:
            score -= 15
        return {"account": g["account"], "name": g["name"], "score": min(100, score),
                "cnt": n, "total": total, "currency": anchor.get("currency") or "", "ids": ids,
                "first_time": first, "reasons": reasons, "tags": tags}

    sources, targets = [], []
    for g in groups.values():
        s = build(g, "source")
        if s:
            sources.append(s)
        t = build(g, "target")
        if t:
            targets.append(t)
    # 组合模式识别：按时间顺序累加，前缀合计 ≈ 锚点金额（分散转出 / 归集转入）
    # —— 快进快出场景资金按时间流出，前缀匹配可排除期间无关大额的干扰
    for kind, lst in (("source", sources), ("target", targets)):
        if amt and len(lst) >= 2:
            acc, picked = money_decimal("0"), []
            for x in sorted(lst, key=lambda i: i["first_time"]):
                acc += x["total"]
                picked.append(x)
                r_acc = acc / amt
                if money_decimal("0.85") <= r_acc <= money_decimal("1.05"):
                    tag = ("前 {} 个来源合计 ≈ 锚点金额的 {:.0%}，疑似归集转入".format(len(picked), r_acc) if kind == "source"
                           else "前 {} 个去向合计 ≈ 锚点金额的 {:.0%}，疑似分散转出".format(len(picked), r_acc))
                    for y in picked:
                        if tag not in y["tags"]:
                            y["tags"].append(tag)
                            y["score"] = min(100, y["score"] + 12)
                    break
                if r_acc > money_decimal("1.05"):
                    break
    sources.sort(key=lambda x: -x["score"])
    targets.sort(key=lambda x: -x["score"])
    return db.clean({"sources": sources[:3], "targets": targets[:3]})


# ---------------- 账号身份证 ----------------

_ID_RE = re.compile(r"^\d{15}$|^\d{17}[0-9Xx]$")


def _norm_acct(v) -> str:
    """账号规范化：Excel 数值列防科学计数法/丢 .0"""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


class AccountIdIn(BaseModel):
    case_id: int = 1
    account: str
    id_no: str = ""


@app.post("/api/account-ids/set")
def account_id_set(body: AccountIdIn):
    """设置/清除单个账号的身份证号（图谱抽屉编辑用）"""
    with _case_transaction(body.case_id):
        acct = body.account.strip()
        idno = body.id_no.strip().replace(" ", "").upper()
        if not acct:
            raise HTTPException(400, "账号不能为空")
        if idno and not _ID_RE.match(idno):
            raise HTTPException(400, "身份证号格式不正确（应为 15 位或 18 位）")
        if idno:
            db.x("insert or replace into account_ids(case_id, account, id_no) values (?,?,?)",
                 [body.case_id, acct, idno])
            return {"ok": True, "account": acct, "id_no": idno}
        db.x("delete from account_ids where case_id = ? and account = ?", [body.case_id, acct])
        return {"ok": True, "account": acct, "cleared": True}

@app.get("/api/account-ids/list")
def account_ids_list(case_id: int = 1):
    return db.q("select account, id_no, updated_at from account_ids where case_id = ? order by updated_at desc",
                [case_id])


@app.get("/api/account-ids/template")
def account_ids_template():
    """下载账号-身份证对照表导入模板（卡号必填、身份证号必填、户名选填）"""
    df = pd.DataFrame({
        "卡号": ["SYN-A", "SYN-B"],
        "户名": ["合成用户甲", "合成用户乙"],
        "身份证号": ["SYN-ID-REPLACE-LOCALLY", "SYN-ID-REPLACE-LOCALLY"],
    })
    buf = io.BytesIO()
    df.to_excel(buf, index=False, sheet_name="账号身份证对照表")
    fname = urllib.parse.quote("账号身份证对照表模板.xlsx")
    return Response(buf.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"})


@app.post("/api/account-ids/import")
async def account_ids_import(case_id: int = Form(...), file: UploadFile = File(...)):
    """批量导入账号-身份证对照表（xlsx/csv，自动识别「账号」「身份证」列）"""
    require_case(case_id)
    suffix = Path(file.filename or "").suffix.lower() or ".csv"
    if suffix == ".xls":
        raise HTTPException(400, "旧版 .xls 请另存为 .xlsx 后导入")
    upload_dir = db.DATA_DIR / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    import_id = uuid.uuid4().hex[:12]
    path = upload_dir / f"{import_id}{suffix}"
    content = await file.read()
    path.write_bytes(content)
    try:
        pairs = importer.read_id_table(path)
        rows, invalid, empty = [], 0, 0
        seen = set()
        for acct, idno in pairs:
            acct = _norm_acct(acct)
            idno = str(idno or "").strip().replace(" ", "").upper()
            if idno.lower() in ("nan", "none", ""):
                continue
            if not acct:
                empty += 1
                continue
            if not _ID_RE.match(idno):
                invalid += 1
                continue
            if acct in seen:
                continue
            seen.add(acct)
            rows.append((case_id, acct, idno))
        with _case_transaction(case_id):
            if rows:
                db.executemany("insert or replace into account_ids(case_id, account, id_no) values (?,?,?)", rows)
        return {"ok": True, "imported": len(rows), "invalid": invalid, "blank_account": empty}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"解析失败：{e}")
    finally:
        try:
            path.unlink()
        except Exception:
            pass


# ---------------- 账号照片（节点头像） ----------------

_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _photo_dir(case_id: int) -> Path:
    d = db.DATA_DIR / "photos" / f"c{case_id}"
    return d


@contextmanager
def _photo_transaction(case_id):
    """Keep a filesystem backup until the matching database change commits."""
    with db._lock, tempfile.TemporaryDirectory(prefix="funds-trace-photo-") as tmp:
        directory = _photo_dir(case_id)
        backup = Path(tmp) / "photos"
        had_photos = False
        prepared = False
        try:
            with _case_transaction(case_id):
                if directory.exists():
                    shutil.copytree(directory, backup)
                    had_photos = True
                prepared = True
                yield directory
        except BaseException:
            # A failed backup has not authorized any mutation of the source.
            if prepared:
                if directory.exists():
                    shutil.rmtree(directory)
                if had_photos:
                    shutil.copytree(backup, directory)
            raise


def _photo_hash(account: str) -> str:
    import hashlib
    return hashlib.md5(account.encode("utf-8")).hexdigest()[:16]


def _circle_avatar(src: bytes, ring: str, cx: float = 0.5, cy: float = 0.5, r: float = 0.5) -> bytes:
    """按裁剪参数（中心 cx/cy、半径比例 r，均为相对原图宽高的 0~1 值）取方框 →
    圆形 → 按角色描边，输出 144px PNG（透明四角）"""
    from PIL import Image, ImageDraw
    img = Image.open(io.BytesIO(src)).convert("RGBA")
    w, h = img.size
    side = max(8, min(int(2 * r * min(w, h)), 4 * min(w, h)))
    x0 = int(cx * w - side / 2)
    y0 = int(cy * h - side / 2)
    img = img.crop((x0, y0, x0 + side, y0 + side))
    img = img.resize((144, 144), Image.LANCZOS)
    mask = Image.new("L", (144, 144), 0)
    ImageDraw.Draw(mask).ellipse((2, 2, 142, 142), fill=255)
    img.putalpha(mask)
    ImageDraw.Draw(img).ellipse((2, 2, 142, 142), outline=ring + "ff", width=5)
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


@app.post("/api/account-photos/set")
async def account_photo_set(case_id: int = Form(...), account: str = Form(...),
                            file: UploadFile = File(...),
                            crop_cx: float = Form(0.5), crop_cy: float = Form(0.5),
                            crop_r: float = Form(0.5)):
    """上传节点照片（存原图与裁剪参数，展示时按需生成圆形头像）"""
    require_case(case_id)
    acct = account.strip()
    if not acct:
        raise HTTPException(400, "账号不能为空")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _PHOTO_EXTS:
        raise HTTPException(400, "仅支持 jpg/png/webp/bmp 图片")
    content = await file.read()
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(400, "图片不能超过 8MB")
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(content))
        img.verify()
    except Exception:
        raise HTTPException(400, "图片文件无效")
    crop_cx = min(1.0, max(0.0, crop_cx))
    crop_cy = min(1.0, max(0.0, crop_cy))
    crop_r = min(1.0, max(0.05, crop_r))
    h = _photo_hash(acct)
    with _photo_transaction(case_id) as directory:
        directory.mkdir(parents=True, exist_ok=True)
        for old in directory.glob(f"{h}.*"):
            old.unlink()
        orig = directory / f"{h}.orig{ext}"
        orig.write_bytes(content)
        db.x("insert or replace into account_photos(case_id, account, fname, crop_cx, crop_cy, crop_r) "
             "values (?,?,?,?,?,?)", [case_id, acct, orig.name, crop_cx, crop_cy, crop_r])
    return {"ok": True, "account": acct}


@app.post("/api/account-photos/clear")
def account_photo_clear(body: AccountIdIn):
    acct = body.account.strip()
    if not acct:
        raise HTTPException(400, "账号不能为空")
    with _photo_transaction(body.case_id) as directory:
        h = _photo_hash(acct)
        for old in directory.glob(f"{h}.*"):
            old.unlink()
        db.x("delete from account_photos where case_id = ? and account = ?", [body.case_id, acct])
    return {"ok": True, "cleared": True}


@app.get("/api/account-photos/orig")
def account_photo_orig(case_id: int, account: str):
    """原图（重新裁剪用）"""
    with _case_transaction(case_id):
        row = db.q("select fname from account_photos where case_id = ? and account = ?",
                   [case_id, account])
        if not row:
            raise HTTPException(404, "无照片")
        if not photo_name(row[0]["fname"]):
            raise HTTPException(409, "照片文件名无效")
        p = _photo_dir(case_id) / row[0]["fname"]
        if not p.exists():
            raise HTTPException(404, "照片文件缺失")
        media = {".png": "image/png", ".webp": "image/webp", ".bmp": "image/bmp"}.get(p.suffix.lower(), "image/jpeg")
        return Response(p.read_bytes(), media_type=media)

@app.get("/api/account-photos/img")
def account_photo_img(case_id: int, account: str, ring: str = "#4caf7d"):
    """圆形头像（ring=描边色；按存的裁剪参数生成，重新裁剪后自动更新）"""
    with _case_transaction(case_id):
        if not re.match(r"^#[0-9a-fA-F]{6}$", ring):
            ring = "#4caf7d"
        row = db.q("select fname, updated_at, crop_cx, crop_cy, crop_r "
                   "from account_photos where case_id = ? and account = ?", [case_id, account])
        if not row:
            raise HTTPException(404, "无照片")
        d = _photo_dir(case_id)
        if not photo_name(row[0]["fname"]):
            raise HTTPException(409, "照片文件名无效")
        orig = d / row[0]["fname"]
        if not orig.exists():
            raise HTTPException(404, "照片文件缺失")
        cx = 0.5 if row[0].get("crop_cx") is None else row[0]["crop_cx"]
        cy = 0.5 if row[0].get("crop_cy") is None else row[0]["crop_cy"]
        cr = 0.5 if row[0].get("crop_r") is None else row[0]["crop_r"]
        h = _photo_hash(account)
        stamp = str(row[0].get("updated_at") or "").replace("-", "").replace(":", "").replace(" ", "")
        cache = d / f"{h}.{stamp}.{ring.lstrip('#')}.png"
        if not cache.exists():
            try:
                cache.write_bytes(_circle_avatar(orig.read_bytes(), ring.upper(), cx, cy, cr))
            except Exception as e:
                raise HTTPException(500, f"头像生成失败: {e}")
        return Response(cache.read_bytes(), media_type="image/png",
                        headers={"Cache-Control": "max-age=3600"})

# ---------------- 导出 ----------------

class GraphRelationFilter(BaseModel):
    """A display-only aggregate relation filter; never combines currencies."""
    currency: str = Field(max_length=32, strict=True)
    min: Optional[str] = Field(default=None, max_length=40, strict=True)
    max: Optional[str] = Field(default=None, max_length=40, strict=True)

    @model_validator(mode="after")
    def validate_amounts(self):
        if self.min == "":
            self.min = None
        if self.max == "":
            self.max = None
        bounds = []
        for value in (self.min, self.max):
            if value is None:
                bounds.append(None)
                continue
            if not re.fullmatch(r"\d+(?:\.\d{1,2})?", value):
                raise ValueError("relation filter amounts must be non-negative decimal strings")
            bounds.append(money_decimal(value))
        if bounds[0] is not None and bounds[1] is not None and bounds[0] > bounds[1]:
            raise ValueError("relation filter minimum cannot exceed maximum")
        return self


class GraphAmountWidth(BaseModel):
    """The full tracked graph's exact single-currency visual encoding range."""
    currency: str = Field(max_length=32, strict=True)
    min_amount: Optional[str] = Field(default=None, max_length=40, strict=True)
    max_amount: Optional[str] = Field(default=None, max_length=40, strict=True)
    missing_edges: int = Field(ge=0, le=2_147_483_647, strict=True)
    invalid_edges: int = Field(ge=0, le=2_147_483_647, strict=True)
    scale: Literal["full-graph"]
    transform: Literal["sqrt"]
    min_width: Literal[1.5]
    max_width: Literal[7]

    @model_validator(mode="after")
    def validate_range(self):
        if (self.min_amount is None) != (self.max_amount is None):
            raise ValueError("amount width range must have both bounds or neither")
        for value in (self.min_amount, self.max_amount):
            if value is not None and not re.fullmatch(r"\d+(?:\.\d{1,2})?", value):
                raise ValueError("amount width bounds must be non-negative decimal strings")
        if self.min_amount is not None and money_decimal(self.min_amount) > money_decimal(self.max_amount):
            raise ValueError("amount width minimum cannot exceed maximum")
        return self


class GraphTimeWindow(BaseModel):
    start: Optional[str] = Field(default=None, max_length=32)
    end: Optional[str] = Field(default=None, max_length=32)
    include_undated: bool = True
    preview: bool = False

    @model_validator(mode="after")
    def validate_window(self):
        # Reuse the same local-time input contract as the live projection.
        canvas_investigation.TimeInvestigationIn(case_id=1, start=self.start, end=self.end)
        return self


class GraphExportView(BaseModel):
    """Display scope only; hiding a neighborhood never deletes tracked facts."""
    scope: Literal["visible", "all"]
    mode: Literal["overview", "neighborhood", "filtered"]
    total_nodes: int = Field(ge=0, le=2_147_483_647, strict=True)
    total_edges: int = Field(ge=0, le=2_147_483_647, strict=True)
    visible_nodes: int = Field(ge=0, le=2_147_483_647, strict=True)
    visible_edges: int = Field(ge=0, le=2_147_483_647, strict=True)
    label_mode: Literal["auto", "all", "focus"]
    relation_filter: Optional[GraphRelationFilter] = None
    amount_width: Optional[GraphAmountWidth] = None
    time_window: Optional[GraphTimeWindow] = None

    @model_validator(mode="after")
    def validate_counts(self):
        if self.visible_nodes > self.total_nodes or self.visible_edges > self.total_edges:
            raise ValueError("visible graph counts cannot exceed total graph counts")
        return self


class GraphHtmlIn(BaseModel):
    """前端画布快照：所见即所得导出（保留节点位置/边线型/折点/标注及拖动位置）"""
    case_name: str = "资金图谱"
    w: int = 1200
    h: int = 800
    nodes: list = []
    edges: list = []
    view: Optional[GraphExportView] = None

    @model_validator(mode="after")
    def validate_view(self):
        if self.view is not None:
            if self.view.scope == "all":
                expected = (self.view.total_nodes, self.view.total_edges)
            else:
                expected = (self.view.visible_nodes, self.view.visible_edges)
            if expected != (len(self.nodes), len(self.edges)):
                raise ValueError("export graph counts do not match its declared scope")
        return self


@app.post("/api/export/graph.html")
def export_graph_html(body: GraphHtmlIn):
    """按前端画布快照导出可交互 HTML：与原图完全一致，节点可拖拽（自包含单文件）"""
    g6 = (STATIC_DIR / "vendor" / "g6.min.js").read_text(encoding="utf-8")
    g6 = g6.replace("</script", "<\\/script")
    presentation = (STATIC_DIR / "graph-presentation.js").read_text(encoding="utf-8")
    presentation = re.sub(r"</script", r"<\\/script", presentation, flags=re.I)
    label_geometry = (STATIC_DIR / "graph-label-geometry.js").read_text(encoding="utf-8")
    label_geometry = re.sub(r"</script", r"<\\/script", label_geometry, flags=re.I)
    graph_routing = (STATIC_DIR / "graph-routing.js").read_text(encoding="utf-8")
    graph_routing = re.sub(r"</script", r"<\\/script", graph_routing, flags=re.I)
    route_quality = (STATIC_DIR / "graph-route-quality.js").read_text(encoding="utf-8")
    route_quality = re.sub(r"</script", r"<\\/script", route_quality, flags=re.I)
    data = body.model_dump()
    # Keep older export payloads unchanged when optional display metadata is absent.
    if body.view is not None:
        for key in ("relation_filter", "amount_width", "time_window"):
            if getattr(body.view, key) is None:
                data["view"].pop(key, None)
    payload = json.dumps(data, ensure_ascii=False)
    payload = payload.replace("</", "<\\/")   # 防止 JSON 中的字符串提前闭合 script
    if body.view is None:
        scope_text = f"导出图谱 {len(body.nodes)} 个账户 · {len(body.edges)} 条关系"
    else:
        view = body.view
        if view.scope == "all":
            scope_text = f"全部图谱 {view.total_nodes} 个账户 · {view.total_edges} 条关系"
        else:
            name = {"neighborhood": "当前邻域视图", "filtered": "当前筛选视图"}.get(view.mode, "当前图谱视图")
            scope_text = (f"{name} {view.visible_nodes} / {view.total_nodes} 个账户 · "
                          f"{view.visible_edges} / {view.total_edges} 条关系；"
                          "图谱仅显示当前视图，追踪数据未删除")
        if view.relation_filter is not None:
            rule = view.relation_filter
            currency = rule.currency or "未标注币种"
            conditions = []
            if rule.min is not None:
                conditions.append(f"不少于 {rule.min}")
            if rule.max is not None:
                conditions.append(f"不高于 {rule.max}")
            amount_range = "、".join(conditions) if conditions else "不限金额"
            prefix = "当前视图筛选设置" if view.scope == "all" else "筛选条件"
            scope_text += f"；{prefix}：按 {currency} 聚合关系金额，{amount_range}（不同币种不相加）"
        labels = {"auto": "自动分级", "all": "全部", "focus": "仅重点"}
        scope_text += f" · 标签：{labels[view.label_mode]}"
        if view.time_window is not None:
            window = view.time_window
            scope_text += (f"；交易时间：{window.start or '不限起点'} 至 {window.end or '不限终点'}（原始本地时间，含边界）"
                           f"；{'包含' if window.include_undated else '排除'}无时间流水；按窗口内实际流水重算金额")
            if window.preview:
                scope_text += "；时间回放预览帧"
    amount_legend = ""
    if body.view is not None and body.view.amount_width is not None:
        width = body.view.amount_width
        extent = (f"{width.min_amount} — {width.max_amount}"
                  if width.min_amount is not None else "无可编码金额")
        text = (f"{width.currency or '未标注币种'} 金额→线宽 · {extent} · 全图统一比例 · "
                "平方根映射 sqrt（1.5–7px）")
        if width.missing_edges:
            text += f" · {width.missing_edges} 条无该币种，使用普通线宽"
        if width.invalid_edges:
            text += f" · {width.invalid_edges} 条金额无效，未编码"
        amount_legend = f'<span id="amountWidthLegend">{escape(text, quote=True)}</span>'
    replacements = {
        "__DATA__": payload,
        "__G6__": g6,
        "__PRESENTATION__": presentation,
        "__LABEL_GEOMETRY__": label_geometry,
        "__GRAPH_ROUTING__": graph_routing,
        "__ROUTE_QUALITY__": route_quality,
        "__AMOUNT_WIDTH__": amount_legend,
        "__CASE__": escape(body.case_name or "资金图谱", quote=True),
        "__VIEW__": escape(scope_text, quote=True),
    }
    # One pass preserves literal marker text inside case names and graph data.
    html = re.sub(r"__(?:DATA|G6|PRESENTATION|LABEL_GEOMETRY|GRAPH_ROUTING|ROUTE_QUALITY|AMOUNT_WIDTH|CASE|VIEW)__", lambda match: replacements[match.group()], HTML_TEMPLATE)
    fname = urllib.parse.quote(f"{body.case_name or '资金图谱'}_图谱.html")
    return Response(html, media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"})


class GraphPdfIn(BaseModel):
    image: str   # 高清 PNG dataURL
    description: Optional[str] = Field(default=None, max_length=8192, strict=True)


def _graph_png_bytes(image: str) -> bytes:
    """Validate the canvas PNG without trusting its data URL's MIME label."""
    from PIL import Image
    prefix = "data:image/png;base64,"
    if not image.startswith(prefix):
        raise ValueError("expected a PNG data URL")
    png = base64.b64decode(image[len(prefix):], validate=True)
    with Image.open(io.BytesIO(png)) as decoded:
        if decoded.format != "PNG":
            raise ValueError("expected PNG image data")
        decoded.verify()
    return png


@app.post("/api/export/graph.png")
def export_graph_png(body: GraphPdfIn):
    """Keep view scope in PNG metadata, leaving the canvas pixels unchanged."""
    from PIL import Image, PngImagePlugin
    try:
        png = _graph_png_bytes(body.image)
        metadata = PngImagePlugin.PngInfo()
        if body.description is not None:
            metadata.add_itxt("Description", body.description)
        out = io.BytesIO()
        with Image.open(io.BytesIO(png)) as image:
            image.save(out, "PNG", pnginfo=metadata)
    except Exception:
        raise HTTPException(400, "图像转换失败：请提供有效的 PNG 图像") from None
    fname = urllib.parse.quote("资金图谱.png")
    return Response(out.getvalue(), media_type="image/png",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"})


@app.post("/api/export/graph.pdf")
def export_graph_pdf(body: GraphPdfIn):
    """把前端高清图谱 PNG 包装为单页 PDF，并保存范围元数据。"""
    try:
        png = _graph_png_bytes(body.image)
        pdf = _png_to_pdf_lossless(png, body.description)
    except Exception:
        raise HTTPException(400, "图像转换失败：请提供有效的 PNG 图像") from None
    fname = urllib.parse.quote("资金图谱.pdf")
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"})


def _png_to_pdf_lossless(png: bytes, description: Optional[str] = None) -> bytes:
    """优先 img2pdf 无损嵌入；Pillow 回退保留尺寸但量化为 256 色。"""
    from PIL import Image
    img = Image.open(io.BytesIO(png))
    if img.mode != "RGB":
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (20, 22, 26))   # 铺深色底再拍平，避免透明通道
        bg.paste(img, mask=img.split()[-1])
        img = bg
    flat = io.BytesIO()
    img.save(flat, "PNG")
    metadata = {"title": "资金图谱", "creator": "FundsTrace"}
    if description is not None:
        metadata["subject"] = description
    try:
        import img2pdf
        return img2pdf.convert(flat.getvalue(), **metadata)   # 默认按 96dpi 定页面尺寸
    except Exception:
        from PIL import PdfParser
        # PDF literal strings normalize raw CR bytes, including the low byte of
        # Chinese characters such as 不 (U+4E0D). Hex strings preserve UTF-16BE.
        pdf_metadata = {key: PdfParser.PdfBinary(PdfParser.encode_text(value))
                        for key, value in metadata.items()}
        quantized = img.convert("P", palette=Image.ADAPTIVE, colors=256)
        out = io.BytesIO()
        quantized.save(out, "PDF", resolution=288.0, **pdf_metadata)
        return out.getvalue()


@app.get("/api/export/tracked.xlsx")
def export_tracked(case_id: int, canvas_id: int = 0):
    with db._lock:
        rows, pending = _tracked_export_data(case_id, canvas_id)
        # Preserve the existing CSV/Excel export shape. The additional coverage
        # sheet applies only when this case has versioned import evidence.
        coverage = import_coverage(case_id) if db.q_raw(
            "select 1 from import_sources where case_id=? and row_kind in ('archive_record','table_record') limit 1", [case_id]) else None
    return _tracked_workbook(case_id, rows, pending, coverage)


def _tracked_export_data(case_id, canvas_id):
    rows = db.q_raw("""
        select f.trade_time, f.trade_no, f.payer_name, f.payer_account, f.payer_bank,
               f.payee_name, f.payee_account, f.payee_bank, f.amount, f.currency, f.summary, f.channel, f.id
        from effective_flows f join tracked_flows tf on tf.flow_id = f.id
        where tf.case_id = ? and tf.canvas_id = ? and f.case_id = ? order by f.trade_time
    """, [case_id, canvas_id, case_id])
    if not rows:
        raise HTTPException(400, "当前画布没有已追踪的流水")
    return rows, _pending_accounts(case_id, canvas_id)


def _tracked_workbook(case_id, rows, pending, coverage):
    cols = {"trade_time": "交易时间", "trade_no": "交易流水号",
            "payer_name": "付款户名", "payer_account": "付款账号", "payer_bank": "付款开户行",
            "payee_name": "收款户名", "payee_account": "收款账号", "payee_bank": "收款开户行",
            "amount": "交易金额", "currency": "币种", "summary": "摘要", "channel": "渠道", "id": "流水ID"}
    df = pd.DataFrame(rows).rename(columns=cols)[list(cols.values())]
    # Excel numbers cannot preserve all DECIMAL(18,2) cents or bigint IDs.
    df["交易金额"] = df["交易金额"].map(lambda value: format(value, ".2f"))
    df["流水ID"] = df["流水ID"].map(str)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="追踪流水")
        # 第二张表：待调取账号（当前画布图谱中出现、但尚未导入其本人流水的对手账户）
        pending.to_excel(w, index=False, sheet_name="待调取账号")
        if coverage is not None:
            pd.DataFrame([
                ("流水口径", "当前画布的有效追踪流水；已匹配的冲正／撤销双方退出统计，普通退款仍保留，不代表结算净额或完整清算结果。"),
                ("覆盖范围", "以下完整性仅限本案已导入反馈来源的资金语义，不表示银行提供的数据齐全。"),
                ("资金效果未确定记录数", coverage["unresolved_records"]),
                ("非资金事件记录数", coverage["non_monetary_records"]),
                ("已匹配冲正撤销事件数", coverage.get("cancelled_records", 0)),
                ("保留原始流水中退出分析的笔数", coverage.get("excluded_flows", 0)),
                ("已导入来源资金语义是否完整", "是" if coverage["financial_interpretation_complete"] else "否"),
                ("未确定记录", "资金效果未确定的记录未计入流水统计；非资金事件单独保留，不计入金额与交易笔数。"),
            ], columns=["项目", "说明"]).to_excel(w, index=False, sheet_name="统计口径")
        # openpyxl otherwise interprets user strings beginning '=' as formulas
        # (and strings such as '#N/A' as errors). Preserve their literal value.
        for sheet in w.sheets.values():
            for row in sheet:
                for cell in row:
                    if isinstance(cell.value, str):
                        cell.data_type = "s"
    fname = f"追踪流水_案件{case_id}.xlsx"
    return Response(
        buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(fname)}"})


def _pending_accounts(case_id: int, canvas_id: int = 0) -> "pd.DataFrame":
    """待调取账号清单：当前画布图谱中出现、但未导入其本人流水的账户。
    排除口径：本案仍有以该账号为 self_account 的流水。主账号角色不代表已取得账簿。
    本方流水不限当前画布；同币种内按交易总额降序。"""
    ledgers = {r["account"] for r in db.q("""
        select distinct self_account as account from flows
        where case_id = ? and self_account is not null and self_account <> ''
    """, [case_id])}
    nodes = db.q_raw("""
        with t as (select f.* from effective_flows f join tracked_flows tf on tf.flow_id = f.id
                   where tf.case_id = ? and tf.canvas_id = ? and f.case_id = ?)
        select x.account as account, coalesce(x.currency, '') currency, max(x.name) as name,
               sum(case when x.ispay = 1 then 1 else 0 end) as out_cnt,
               coalesce(sum(case when x.ispay = 1 then x.amount else 0 end), 0) as out_amt,
               sum(case when x.ispay = 0 then 1 else 0 end) as in_cnt,
               coalesce(sum(case when x.ispay = 0 then x.amount else 0 end), 0) as in_amt,
               min(x.trade_time) as tmin, max(x.trade_time) as tmax,
               max(case when exists(select 1 from main_accounts ma where ma.case_id=? and ma.account=x.account) then 1 else 0 end) as is_main,
               max(case when exists(select 1 from main_accounts ma where ma.case_id=? and ma.account<>x.account and ma.name<>'' and ma.name=x.name) then 1 else 0 end) as is_same_name
        from (
            select payer_account as account, payer_name as name, 1 as ispay, amount, trade_time, currency from t
            union all
            select payee_account, payee_name, 0, amount, trade_time, currency from t
        ) x where coalesce(x.account,'')<>''
        group by x.account, coalesce(x.currency, '')
    """, [case_id, canvas_id, case_id, case_id, case_id])
    pending = [n for n in nodes if n["account"] not in ledgers]
    pending.sort(key=lambda x: (x["currency"], -((x["out_amt"] or 0) + (x["in_amt"] or 0)), x["account"]))
    if not pending:
        return pd.DataFrame([{"序号": 1, "户名": "（无待调取账号：图谱中的对手账户均已导入流水）"}])
    out = []
    for i, n in enumerate(pending, 1):
        note = "同户名主账号的关联账号（其流水尚未导入）" if n.get("is_same_name") == 1 else ""
        out.append({
            "序号": i,
            "户名": n.get("name") or "",
            "账号": n["account"],
            "币种": n["currency"],
            "转出笔数": n["out_cnt"],
            "转出金额": format(n["out_amt"] or 0, ".2f"),
            "转入笔数": n["in_cnt"],
            "转入金额": format(n["in_amt"] or 0, ".2f"),
            "交易总额": format((n["out_amt"] or 0) + (n["in_amt"] or 0), ".2f"),
            "首次交易": n["tmin"],
            "末次交易": n["tmax"],
            "备注": note,
        })
    return pd.DataFrame(out)


# ---------------- 案件包（跨电脑分发案件数据） ----------------

@app.get("/api/cases/{case_id}/export")
def export_case(case_id: int):
    content, case_name = export_package(case_id)
    fname = urllib.parse.quote(f"案件包_{case_name}.fundcase")
    return Response(content, media_type="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"})


@app.post("/api/cases/import")
async def import_case(file: UploadFile = File(...)):
    """Validate a format-1 package, then atomically import new IDs and photos."""
    return import_package(await file.read())


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def static_no_cache(request, call_next):
    """静态资源每次回源校验（304 也极快）：版本升级后刷新即生效，
    避免浏览器长时间缓存旧 JS 导致新功能『看不见』。"""
    resp = await call_next(request)
    if request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# r""" 原始字符串：模板内的 \n、\uXXXX 等转义必须留给浏览器的 JS 引擎解释，
# 若被 Python 提前转义（\n 变真实换行）会导致导出 HTML 的脚本语法错误、图谱空白。
# 数据来自前端画布快照（所见即所得）：节点坐标/样式、边线型/折点、标注及拖动位置全部按原图还原。
HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>资金图谱 - __CASE__</title>
<style>
body{margin:0;background:#14161a;font:13px/1.5 "Microsoft YaHei",sans-serif;color:#e8ebf0;overflow:hidden}
#exportHeader{position:absolute;top:0;left:0;right:0;z-index:10;background:#1b1e24}
#topbar{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;min-height:46px;box-sizing:border-box;padding:8px 14px;border-bottom:1px solid #2a2e37}
#topbar b{font-size:15px}
#topbar .sub{color:#8a94a2;font-size:11px;margin-left:8px}
#topbar .tip{margin-left:auto;color:#8a94a2;font-size:11px}
#viewScope{padding:6px 14px 8px;color:#bbc4d0;font-size:12px;border-bottom:1px solid #2a2e37}
#mount{position:absolute;top:90px;left:0;right:0;bottom:0}
#legend{position:fixed;left:12px;bottom:10px;z-index:9;display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;max-width:calc(100vw - 48px);
background:rgba(27,30,36,.82);border:1px solid #2a2e37;border-radius:6px;padding:6px 12px;
font-size:11px;color:#aeb6c2;pointer-events:none}
#legend i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px;vertical-align:-1px}
#amountWidthLegend{flex-basis:100%;color:#c7d3dc;overflow-wrap:anywhere}
.wm{position:fixed;right:14px;bottom:10px;color:rgba(255,255,255,.14);font-size:12px;z-index:9;pointer-events:none}
#watermark{position:fixed;inset:0;z-index:8;pointer-events:none;background-repeat:repeat}
</style></head>
<body>
<header id="exportHeader"><div id="topbar"><b>资金图谱</b><span class="sub">__CASE__ · FundsTrace</span>
<span class="tip">节点可拖拽 · 滚轮缩放 · 拖动空白平移 · 双击节点恢复视图</span></div>
<div id="viewScope" role="note">__VIEW__</div></header>
<div id="mount"></div>
<div id="legend">
<span><i style="background:#d77d79"></i>只转出(源头)</span>
<span><i style="background:#63b69d"></i>中转</span>
<span><i style="background:#7ca6d5"></i>只转入(沉淀)</span>
<span style="color:#e8c46a">★ 主账号</span>
<span style="color:#a79be0">◆ 同户名账号</span>
__AMOUNT_WIDTH__
</div>
<div class="wm">FundsTrace</div>
<div id="watermark"></div>
<script>__G6__</script>
<script id="graph-presentation">__PRESENTATION__</script>
<script id="graph-label-geometry">__LABEL_GEOMETRY__</script>
<script id="graph-routing">__GRAPH_ROUTING__</script>
<script id="graph-route-quality">__ROUTE_QUALITY__</script>
<script>
var DATA = __DATA__;
function buildWM(){
  var c=document.createElement('canvas');c.width=340;c.height=220;
  var g=c.getContext('2d');g.translate(170,110);g.rotate(-28*Math.PI/180);
  g.font='15px "Microsoft YaHei"';g.textAlign='center';g.fillStyle='rgba(255,255,255,0.055)';
  g.fillText('FundsTrace',0,0);
  var d=document.createElement('div');d.id='watermark';
  d.style.cssText='position:fixed;inset:0;z-index:8;pointer-events:none;background-image:url('+c.toDataURL()+');background-repeat:repeat';
  document.body.appendChild(d);
}
buildWM();
(function(){
  var presentation=window.FundsGraphPresentation,labelGeometry=window.FundsGraphLabelGeometry,routing=window.FundsGraphRouting;
  var routeQuality=typeof FundsGraphRouteQuality==='undefined'?null:FundsGraphRouteQuality;
  function finite(value){return typeof value==='number'&&isFinite(value);}
  function point(value){return value&&finite(value.x)&&finite(value.y)?{x:value.x,y:value.y}:null;}
  function ratio(value){return finite(value)&&value>=0&&value<=1?value:null;}
  function color(value){return typeof value==='string'&&/^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i.test(value)?value:null;}
  function nodeStyle(node){
    if(node.img)return {};
    var input=node.style||{},data=node.data||{},role=data.out_cnt>0?(data.in_cnt>0?'mid':'src'):'sink';
    return {fill:color(input.fill)||{src:'#d77d79',mid:'#63b69d',sink:'#7ca6d5'}[role],
      stroke:color(input.stroke)||(data.is_main===1?'#e8c46a':data.is_same_name===1?'#a79be0':{src:'#e7a19a',mid:'#9bd6bf',sink:'#b0c8e5'}[role]),
      lineWidth:finite(input.lineWidth)&&input.lineWidth>=.5&&input.lineWidth<=5?input.lineWidth:data.is_main===1?2.8:1.5,
      shadowColor:'rgba(0,0,0,.22)',shadowBlur:5};
  }
  function edgeStyle(input){
    var safe=presentation.sanitizeEdge(input),stroke=safe.stroke||'#7f8b99';
    return Object.assign({stroke:stroke,lineWidth:1.4,lineDash:[],lineCap:'round',lineJoin:'round',lineAppendWidth:8},safe,
      {endArrow:{path:'M 0,0 L 10,5 L 10,-5 Z',fill:stroke}});
  }
  var nodes=DATA.nodes.map(function(n){
    var raw=n.annotation||{},annotation=presentation.create({annotations:[{id:n.id,text:raw.text,anchor:raw.anchor,
      dx:raw.dx,dy:raw.dy,size:raw.size,fontFamily:raw.fontFamily,color:raw.color}]}).annotations[0]||null;
    var displayStyle=presentation.sanitizeNode(n.displayStyle||{});
    var o={id:n.id,x:n.x,y:n.y,label:n.label,size:n.size,style:nodeStyle(n),data:n.data,labelVisible:n.labelVisible,
      type:['circle','rect','diamond','ellipse'].indexOf(n.type)>=0?n.type:'circle',
      displayStyle:displayStyle,groups:n.groups||[],pinned:n.pinned===true,labelStyle:n.labelStyle||{},
      labelOffset:point(n.labelOffset),photoBorder:presentation.sanitizeNode(n.photoBorder),annotation:annotation,
      labelCfg:{position:'bottom',offset:6,style:presentation.effectiveLabel('node',n.labelStyle)}};
    if(n.img){o.type='image';o.img=n.img;}
    return o;});
  var simpleGraph=labelGeometry.isSimpleGraph({nodes:DATA.nodes,edges:DATA.edges});
  var edges=DATA.edges.map(function(e){return {source:e.source,target:e.target,type:e.type,
    controlPoints:(e.controlPoints&&e.controlPoints.length)?e.controlPoints:undefined,
    label:(e.label==null?'':e.label),data:e.data,labelVisible:e.labelVisible,style:edgeStyle(e.style),
    lx:finite(e.lx)?e.lx:null,ly:finite(e.ly)?e.ly:null,labelRatio:ratio(e.labelRatio),
    labelManual:e.labelManual===true,labelPlacement:e.labelPlacement==='above'?'above':e.labelPlacement==='inline'?'inline':null,
    curvePolicy:['compact-arc','screen-right','obstacle-aware'].indexOf(e.curvePolicy)>=0?e.curvePolicy:null,
    routeManual:typeof e.routeManual==='boolean'?e.routeManual:undefined,
    routeAutoEligible:typeof e.routeAutoEligible==='boolean'?e.routeAutoEligible:undefined,
    labelStyle:e.labelStyle||{},labelCfg:{autoRotate:false,style:presentation.effectiveLabel('edge',e.labelStyle)}};});
  function graphHeight(){
    var top=document.getElementById('exportHeader').getBoundingClientRect().height;
    document.getElementById('mount').style.top=top+'px';
    return Math.max(1,window.innerHeight-top);
  }
  var graph=new G6.Graph({container:'mount',width:window.innerWidth,height:graphHeight(),
    modes:{default:['drag-canvas','zoom-canvas','drag-node']},
    defaultNode:{type:'circle',size:32,labelCfg:{position:'bottom',offset:6,style:presentation.effectiveLabel('node')}},
    defaultEdge:{type:'quadratic',style:edgeStyle(),labelCfg:{autoRotate:false,style:presentation.effectiveLabel('edge')}}});
  graph.data({nodes:nodes,edges:edges});
  graph.render();
  // Accepted obstacle-aware routes keep their saved drawing and follow later
  // endpoint drags. Automatic default arcs migrate to the compact policy below.
  var routeFrames=new WeakMap();
  function routeFrame(item,positions){
    var m=item.getModel(),source=point(positions.get(m.source)),target=point(positions.get(m.target));
    var controls=m.controlPoints;
    if(!source||!target||!Array.isArray(controls)||!controls.length||!controls.every(point))return null;
    return {source:source,target:target,controls:controls.map(point)};
  }
  var originalPositions=new Map(graph.getNodes().map(function(item){var m=item.getModel();return [m.id,m];}));
  graph.getEdges().forEach(function(item){var frame=routeFrame(item,originalPositions);if(frame)routeFrames.set(item,frame);});
  function children(item){var group=item.getContainer();return group.get('children')||[];}
  function named(item,name){return children(item).find(function(shape){return shape.get('name')===name;});}
  function textPoint(item){var text=named(item,'text-shape');return text?point({x:text.attr('x'),y:text.attr('y')}):null;}
  function clearOffsets(item){
    children(item).forEach(function(shape){var saved=shape._fundsExportOffset;if(!saved)return;
      if(shape.attr('x')===saved.x&&shape.attr('y')===saved.y)shape.attr({x:saved.baseX,y:saved.baseY});
      delete shape._fundsExportOffset;
    });
  }
  function offsetLabel(item,dx,dy){
    children(item).forEach(function(shape){
      if(shape.get('name')!=='text-shape')return;
      var base=point({x:shape.attr('x'),y:shape.attr('y')});if(!base)return;
      shape.attr({x:base.x+dx,y:base.y+dy});
      shape._fundsExportOffset={baseX:base.x,baseY:base.y,x:base.x+dx,y:base.y+dy};
    });
  }
  function pathAt(item,value){
    var shape=item.getKeyShape&&item.getKeyShape();if(!shape||typeof shape.getPoint!=='function')return null;
    // Convert the same geometric path into a bounded arc-length ratio, including curves/loops.
    var points=[],lengths=[0],total=0;
    for(var i=0;i<=64;i++){
      var p=point(shape.getPoint(i/64));if(!p)return null;points.push(p);
      if(i){total+=Math.hypot(p.x-points[i-1].x,p.y-points[i-1].y);lengths.push(total);}
    }
    if(!total)return points[0];
    var target=value*total;
    for(var j=1;j<points.length;j++)if(lengths[j]>=target){
      var t=(target-lengths[j-1])/(lengths[j]-lengths[j-1]||1);
      return {x:points[j-1].x+(points[j].x-points[j-1].x)*t,y:points[j-1].y+(points[j].y-points[j-1].y)*t};
    }
    return points[points.length-1];
  }
  function decoration(item,name,kind,attrs){
    var existing=named(item,name),group=item.getContainer();
    if(!attrs){if(existing&&existing.remove)existing.remove();return;}
    if(existing)existing.attr(attrs);
    else if(typeof group.addShape==='function')existing=group.addShape(kind,{name:name,capture:false,attrs:attrs});
  }
  function identity(item){
    var m=item.getModel(),data=m.data||{},size=Array.isArray(m.size)?Math.max.apply(Math,m.size):m.size||32;
    var stroke=data.is_main===1?'#e8c46a':data.is_same_name===1?'#a79be0':null;
    decoration(item,'funds-identity-ring','circle',stroke?{x:0,y:0,r:size/2+2.5,stroke:stroke,lineWidth:1.2,fill:null,shadowBlur:0}:null);
    var border=m.photoBorder||{};
    decoration(item,'funds-photo-border','circle',m.type==='image'&&(border.stroke||border.lineWidth)?
      {x:0,y:0,r:size/2,stroke:border.stroke||stroke||'#7ca6d5',lineWidth:border.lineWidth||1.5,fill:null}:null);
  }
  function annotation(item){
    var m=item.getModel(),note=m.annotation;if(!note){decoration(item,'funds-node-annotation','text',null);return;}
    var body=item.getKeyShape&&item.getKeyShape(),text=named(item,'text-shape'),zoom=Math.abs(graph.getZoom?graph.getZoom():1)||1;
    var bodyBox=body&&body.getCanvasBBox&&body.getCanvasBBox(),labelBox=text&&text.getCanvasBBox&&text.getCanvasBBox();
    var size=Array.isArray(m.size)?Math.max.apply(Math,m.size):m.size||32;
    var y=note.anchor==='above'?-size/2-10/zoom:size/2+10/zoom;
    if(bodyBox&&[bodyBox.minX,bodyBox.minY,bodyBox.maxX,bodyBox.maxY].every(finite)&&typeof graph.getPointByCanvas==='function'){
      var edge=note.anchor==='above'?bodyBox.minY-10:Math.max(bodyBox.maxY,labelBox&&finite(labelBox.maxY)?labelBox.maxY:bodyBox.maxY)+10;
      y=graph.getPointByCanvas((bodyBox.minX+bodyBox.maxX)/2,edge).y-m.y;
    }else if(note.anchor!=='above'){
      var labelLocal=text&&text.getBBox&&text.getBBox();if(labelLocal&&finite(labelLocal.maxY))y=Math.max(y,labelLocal.maxY+10/zoom);
    }
    decoration(item,'funds-node-annotation','text',{x:note.dx,y:y+note.dy,text:note.text,fontSize:note.size,
      lineHeight:Math.round(note.size*1.4),fontFamily:note.fontFamily,fill:note.color,fontWeight:'normal',fontStyle:'normal',
      textAlign:'center',textBaseline:note.anchor==='above'?'bottom':'top'});
  }
  function applyGeometry(){
    graph.setAutoPaint(false);
    graph.getNodes().concat(graph.getEdges()).forEach(function(item){
      labelGeometry.plain(item);
      clearOffsets(item);
      var m=item.getModel(),isEdge=m.source!==undefined,base=textPoint(item),visible=m.labelVisible!==false;
      var text=named(item,'text-shape');if(text){if(!visible)text.hide();else if(text.show)text.show();}
      if(isEdge){
        var key=item.getKeyShape&&item.getKeyShape();
        // Automatic labels belong to the actual parameter midpoint. Only an
        // explicit manual path ratio retains a user-selected path position.
        var anchor=m.labelManual&&m.labelRatio!==null?pathAt(item,m.labelRatio)
          :key&&typeof key.getPoint==='function'?point(key.getPoint(.5)):base;
        var before=key&&typeof key.getPoint==='function'?point(key.getPoint(.49)):null;
        var after=key&&typeof key.getPoint==='function'?point(key.getPoint(.51)):null;
        var horizontal=before&&after&&Math.abs(after.y-before.y)<=Math.abs(after.x-before.x)*.12;
        var above=simpleGraph&&m.routeAutoEligible!==false&&m.labelPlacement!=='inline'&&horizontal;
        labelGeometry.placeEdge(item,{anchor:anchor,above:above,gap:4,style:m.style,visible:visible});
      }else{
        labelGeometry.placeNode(item,{zoom:graph.getZoom?graph.getZoom():1});
        identity(item);annotation(item);
        presentation.decorateNode(item,m.displayStyle,{groups:m.groups,pinned:m.pinned,ink:'#24394c'});
      }
      presentation.decorateLabel(item,m.labelStyle);
    });
    graph.setAutoPaint(true);graph.paint();
  }
  function automaticRoute(m){
    return m.routeManual!==true&&m.type==='quadratic'&&(m.routeManual===false
      ||['compact-arc','screen-right','obstacle-aware'].indexOf(m.curvePolicy)>=0
      ||!Array.isArray(m.controlPoints)||!m.controlPoints.length);
  }
  function updateCurves(event,initial,eligible){
    var positions=new Map(graph.getNodes().map(function(item){var m=item.getModel();return [m.id,m];}));
    var moved=event&&event.item&&event.item.getModel&&event.item.getModel().id;
    graph.getEdges().forEach(function(item){
      var m=item.getModel();
      if(!automaticRoute(m)||eligible&&!eligible.has(item))return;
      if(!initial&&(moved==null||m.source!==moved&&m.target!==moved))return;
      var source=point(positions.get(m.source)),target=point(positions.get(m.target));if(!source||!target)return;
      var controls,policy=m.curvePolicy;
      // Partial exports lack the complete source topology. Preserve their saved
      // geometry instead of inventing a new automatic route from fewer facts.
      var compactRoute=completeTopology&&m.routeAutoEligible!==false&&m.curvePolicy!=='obstacle-aware'
        &&(initial||m.curvePolicy==='compact-arc');
      if(compactRoute){
        // A self-relation or temporarily coincident endpoints has no directed
        // chord. Retain its drawing rather than collapsing it into a point.
        if(m.source===m.target||Math.hypot(target.x-source.x,target.y-source.y)<1e-9)return;
        var control=labelGeometry.compactControlPoint(source,target);if(!control)return;
        controls=[control];policy='compact-arc';
      }else{
        if(initial)return;
        var frame=routeFrames.get(item);if(!frame)return;
        controls=routing.rebase(frame.controls,frame.source,frame.target,source,target);
      }
      if(JSON.stringify(m.controlPoints||[])!==JSON.stringify(controls)||m.curvePolicy!==policy){
        if(typeof graph.updateItem==='function')graph.updateItem(item,{type:'quadratic',curvePolicy:policy,controlPoints:controls});
        else {m.type='quadratic';m.curvePolicy=policy;m.controlPoints=controls;}
      }
      if(compactRoute){var next=routeFrame(item,positions);if(next)routeFrames.set(item,next);}
    });
  }
  var completeTopology=!DATA.view||DATA.view.scope==='all'
    ||DATA.view.total_nodes===DATA.nodes.length&&DATA.view.total_edges===DATA.edges.length;
  var gatePositions=new Map(graph.getNodes().map(function(item){var m=item.getModel();return [m.id,point(m)];}));
  function worldBox(shape){
    if(!shape||typeof shape.getCanvasBBox!=='function'||typeof graph.getPointByCanvas!=='function')return null;
    var box;try{box=shape.getCanvasBBox();}catch(_){return null;}
    if(!box||![box.minX,box.minY,box.maxX,box.maxY].every(finite))return null;
    var corners=[[box.minX,box.minY],[box.minX,box.maxY],[box.maxX,box.minY],[box.maxX,box.maxY]];
    var values;try{values=corners.map(function(p){return point(graph.getPointByCanvas(p[0],p[1]));});}catch(_){return null;}
    if(!values.every(Boolean))return null;
    return {minX:Math.min.apply(Math,values.map(function(p){return p.x;})),
      minY:Math.min.apply(Math,values.map(function(p){return p.y;})),
      maxX:Math.max.apply(Math,values.map(function(p){return p.x;})),
      maxY:Math.max.apply(Math,values.map(function(p){return p.y;}))};
  }
  function actualScene(eligible){
    var scene={nodes:[],edges:[],qualityEdges:[],items:new Map()},boxes=[],valid=true,workLeft=120000,segmentsLeft=36000;
    function label(item){
      var m=item.getModel();if(m.labelVisible===false||!m.label)return null;
      var box=worldBox(named(item,'text-shape'));if(!box)valid=false;else boxes.push(box);return box;
    }
    graph.getNodes().forEach(function(item){
      var m=item.getModel(),p=point(m),body=worldBox(item.getKeyShape&&item.getKeyShape()),text=label(item);
      if(!p||!body){valid=false;return;}boxes.push(body);
      scene.nodes.push({id:m.id,x:p.x,y:p.y,body:body,label:text});
    });
    graph.getEdges().forEach(function(item,index){
      if(!valid)return;
      var m=item.getModel(),id=String(index),key=item.getKeyShape&&item.getKeyShape(),path=key&&key.attr('path');
      var sampled=routeQuality.sample(path,{steps:24,maxSegments:Math.max(1,Math.min(4096,segmentsLeft)),
        maxWork:Math.max(1,workLeft)}),text=label(item);
      workLeft-=sampled.work;segmentsLeft-=sampled.segments.length;
      if(!sampled.valid||sampled.truncated||workLeft<0||segmentsLeft<0){valid=false;return;}boxes.push(sampled.bounds);
      scene.items.set(id,item);
      scene.qualityEdges.push({id:id,source:m.source,target:m.target,path:path,label:text});
      scene.edges.push({id:id,source:m.source,target:m.target,type:m.type,controlPoints:m.controlPoints||[],
        manual:!eligible.has(item),sampledPoints:sampled.points,
        labelWidth:text?text.maxX-text.minX:0,labelHeight:text?text.maxY-text.minY:0,
        labelRatio:m.labelManual?m.labelRatio:null,labelAnchor:text?{x:(text.minX+text.maxX)/2,y:(text.minY+text.maxY)/2}:null});
    });
    if(!valid||!boxes.length)return null;
    scene.bounds={minX:Math.min.apply(Math,boxes.map(function(b){return b.minX;})),
      minY:Math.min.apply(Math,boxes.map(function(b){return b.minY;})),
      maxX:Math.max.apply(Math,boxes.map(function(b){return b.maxX;})),
      maxY:Math.max.apply(Math,boxes.map(function(b){return b.maxY;}))};
    return scene;
  }
  function measure(scene){
    return scene&&routeQuality.evaluate({nodes:scene.nodes,edges:scene.qualityEdges,maxChecks:120000,steps:24});
  }
  function noWorse(before,after){
    if(!before||!after||!before.valid||!after.valid||!before.coverage.complete||!after.coverage.complete)return false;
    var kinds=['edgeNode','labelNode','edgeNodeLabel','edgeLabel','labelOverlap','collinearOverlap','crossings'];
    if(kinds.some(function(key){return after.counts[key]>before.counts[key];}))return false;
    var prior=before.collisionKeys,next=after.collisionKeys;
    // This self-contained export embeds the matching evaluator. Missing or
    // incomplete identities cannot certify that a different label stayed clear.
    if(!prior||!next)return false;
    return kinds.every(function(kind){
      var oldKeys=prior[kind],newKeys=next[kind];
      if(!Array.isArray(oldKeys)||!Array.isArray(newKeys)||oldKeys.length!==before.counts[kind]
        ||newKeys.length!==after.counts[kind]||!oldKeys.concat(newKeys).every(function(key){return typeof key==='string';}))return false;
      var known=new Set(oldKeys),current=new Set(newKeys);
      return known.size===oldKeys.length&&current.size===newKeys.length
        &&newKeys.every(function(key){return known.has(key);});
    });
  }
  function improveRoutes(event,initial){
    var moved=event&&event.item&&event.item.getModel&&event.item.getModel().id;
    if(!initial){
      var node=graph.getNodes().find(function(item){return item.getModel().id===moved;}),next=node&&point(node.getModel()),last=gatePositions.get(moved);
      if(!next||last&&next.x===last.x&&next.y===last.y)return;
      gatePositions.set(moved,next);
    }
    if(!completeTopology){graph._fundsRouteQuality={accepted:false,reason:'partial-topology'};return;}
    if(!routeQuality||!routing){graph._fundsRouteQuality={accepted:false,reason:'missing-quality'};return;}
    if(graph.getEdges().length>1500||graph.getNodes().length>3000){
      graph._fundsRouteQuality={accepted:false,reason:'obstacle-budget'};return;}
    var eligible=new Set(graph.getEdges().filter(function(item){
      var m=item.getModel();return automaticRoute(m)&&m.routeAutoEligible!==false&&m.source!==m.target
        &&(!initial||m.curvePolicy!=='obstacle-aware')&&(initial||m.source===moved||m.target===moved);
    }));
    if(!eligible.size)return;
    if(eligible.size>320){graph._fundsRouteQuality={accepted:false,reason:'route-budget'};return;}
    var beforeScene=actualScene(eligible),before=measure(beforeScene);
    if(!beforeScene||!before||!before.valid||!before.coverage.complete){
      graph._fundsRouteQuality={before:before,accepted:false,reason:beforeScene?'quality-budget':'missing-geometry'};return;}
    var saved=new Map(),positions=new Map(graph.getNodes().map(function(item){var m=item.getModel();return [m.id,m];}));
    eligible.forEach(function(item){var m=item.getModel();saved.set(item,{type:m.type,curvePolicy:m.curvePolicy,
      controlPoints:(m.controlPoints||[]).map(point),frame:routeFrames.get(item)});});
    function restore(){
      saved.forEach(function(prior,item){
        graph.updateItem(item,{type:prior.type,curvePolicy:prior.curvePolicy,controlPoints:prior.controlPoints});
        if(prior.frame)routeFrames.set(item,prior.frame);else routeFrames.delete(item);
      });applyGeometry();
    }
    try{
      if(initial){updateCurves(null,true,eligible);applyGeometry();}
      var proposalScene=actualScene(eligible);
      if(!proposalScene)throw new Error('missing-geometry');
      var proposal=routing.plan({nodes:proposalScene.nodes,edges:proposalScene.edges,conservative:true,
        routingBounds:beforeScene.bounds,maxChecks:120000,maxEdges:320,maxObstacleEdges:1500,maxObstacleSegments:36000});
      if(proposal.stats.limited)throw new Error('quality-budget');
      proposal.routes.forEach(function(route){
        var item=proposalScene.items.get(route.id);if(!eligible.has(item))return;
        graph.updateItem(item,{type:'quadratic',curvePolicy:'obstacle-aware',controlPoints:route.controlPoints});
      });applyGeometry();
      var afterScene=actualScene(eligible),after=measure(afterScene),box=beforeScene.bounds;
      var changed=Array.from(saved).filter(function(entry){var m=entry[0].getModel(),prior=entry[1];
        return m.curvePolicy!==prior.curvePolicy||JSON.stringify(m.controlPoints||[])!==JSON.stringify(prior.controlPoints);});
      var within=afterScene&&changed.every(function(entry){
        var item=entry[0],data=afterScene.qualityEdges.find(function(edge){return afterScene.items.get(edge.id)===item;});
        if(!data)return false;var sampled=routeQuality.sample(data.path,{steps:24,maxSegments:4096});
        return sampled.valid&&!sampled.truncated&&sampled.points.every(function(p){return p.x>=box.minX-1e-6
          &&p.x<=box.maxX+1e-6&&p.y>=box.minY-1e-6&&p.y<=box.maxY+1e-6;})
          &&(!data.label||data.label.minX>=box.minX-1e-6&&data.label.maxX<=box.maxX+1e-6
            &&data.label.minY>=box.minY-1e-6&&data.label.maxY<=box.maxY+1e-6);
      });
      var accepted=within&&noWorse(before,after);
      if(!accepted)restore();
      else changed.forEach(function(entry){var frame=routeFrame(entry[0],positions);if(frame)routeFrames.set(entry[0],frame);});
      graph._fundsRouteQuality={before:before,after:accepted?after:before,proposed:accepted?null:after,
        accepted:accepted,changed:accepted?changed.length:0,planner:proposal.stats,
        reason:accepted?'actual-quality-retained':'actual-quality-regression'};
    }catch(error){restore();graph._fundsRouteQuality={before:before,after:before,accepted:false,reason:error.message||'route-recovery'};}
  }
  function afterDrag(event){
    simpleGraph=labelGeometry.isSimpleGraph({
      nodes:graph.getNodes().map(function(item){return item.getModel();}),
      edges:graph.getEdges().map(function(item){return item.getModel();})});
    updateCurves(event);applyGeometry();
  }
  applyGeometry();
  graph.on('node:drag',afterDrag);
  graph.on('node:dragend',function(event){afterDrag(event);improveRoutes(event,false);});
  graph.on('viewportchange',applyGeometry);
  var refit=function(){try{
    var legend=document.getElementById('legend'),bounds=legend&&typeof legend.getBoundingClientRect==='function'?legend.getBoundingClientRect():null;
    var height=bounds&&finite(bounds.height)?bounds.height:legend&&finite(legend.offsetHeight)?legend.offsetHeight:0;
    graph.fitView([40,40,Math.max(40,height+28),40]);
  }catch(e){}};
  refit();
  improveRoutes(null,true);
  setTimeout(refit,150);
  window.__g=graph;
  window.addEventListener('resize',function(){try{graph.changeSize(window.innerWidth,graphHeight());refit();}catch(e){}});
  graph.on('node:dblclick',refit);
})();
</script></body></html>"""


if __name__ == "__main__":
    import webbrowser

    import uvicorn
    port = int(os.environ.get("FUND_PORT", "8300"))
    url = f"http://127.0.0.1:{port}"
    # 打包运行（双击 exe）时自动打开浏览器；源码调试时不弹
    if getattr(sys, "frozen", False):
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
        print(f"FundsTrace FundsTrace | {url} （浏览器将自动打开，关闭本窗口即退出系统）")
    uvicorn.run(app, host="127.0.0.1", port=port)
