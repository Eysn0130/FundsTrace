"""Read-only, revision-bound conditional principal attribution and export."""
from datetime import datetime
from html import escape
import json
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Literal

from . import analysis_snapshot, db, principal_ledger

router = APIRouter(prefix="/api/analysis/principal", tags=["principal"])


class LedgerConditions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    coverage: Literal["unknown", "complete_declared"] = "unknown"
    no_overdraft: bool = False
    funds_available: bool = False
    opening_balance: str | None = Field(default=None, max_length=40)
    balance_kind: Literal["unknown", "post_book"] = "unknown"

    @field_validator("opening_balance")
    @classmethod
    def exact_money(cls, value):
        if value is not None:
            if not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?", value):
                raise ValueError("期初余额须为非负十进制字符串，最多两位小数")
            principal_ledger.units(value)
        return value


class PrincipalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(pattern=r"^[1-9][0-9]{0,9}$")
    account: str = Field(min_length=1, max_length=512)
    currency: str = Field(min_length=1, max_length=32)
    start: str = Field(max_length=30)
    end: str = Field(max_length=30)
    source_flow_id: str = Field(pattern=r"^[1-9][0-9]*$", max_length=20)
    target_flow_ids: list[str] = Field(min_length=1, max_length=20)
    expected_revision: str | None = Field(default=None, max_length=64)
    conditions: LedgerConditions = Field(default_factory=LedgerConditions)

    @model_validator(mode="after")
    def valid_interval(self):
        start, end = datetime.fromisoformat(self.start), datetime.fromisoformat(self.end)
        if start.tzinfo or end.tzinfo or start > end:
            raise ValueError("区间须为有序、无时区的原始记账时间")
        if len(set(self.target_flow_ids)) != len(self.target_flow_ids) or any(
                not re.fullmatch(r"[1-9][0-9]{0,19}", item) for item in self.target_flow_ids):
            raise ValueError("目标流水ID须为不重复的正整数字符串")
        return self


def calculate(body):
    with db._lock:
        revision = analysis_snapshot.revision(int(body.case_id), body.expected_revision)
        value = principal_ledger.calculate(int(body.case_id), body)
        return {"case_id": body.case_id, "revision": revision, "allocation_policy": "exact-source-share-v1", **value}


@router.post("")
def run(body: PrincipalIn):
    return calculate(body)


class NetworkAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account: str = Field(min_length=1,max_length=512)
    conditions: LedgerConditions = Field(default_factory=LedgerConditions)


class NetworkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(pattern=r"^[1-9][0-9]{0,9}$")
    currency: str = Field(min_length=1,max_length=32)
    start: str = Field(max_length=30)
    end: str = Field(max_length=30)
    source_flow_id: str = Field(pattern=r"^[1-9][0-9]*$",max_length=20)
    target_flow_ids: list[str] = Field(min_length=1,max_length=20)
    accounts: list[NetworkAccount] = Field(min_length=1,max_length=20)
    boundary: Literal["unknown","terminal_no_return_declared"] = "unknown"
    expected_revision: str | None = Field(default=None,max_length=64)

    @model_validator(mode="after")
    def valid_network(self):
        # Reuse the identical interval/ID boundary, without loading any facts.
        PrincipalIn(case_id=self.case_id,account=self.accounts[0].account,currency=self.currency,
                    start=self.start,end=self.end,source_flow_id=self.source_flow_id,target_flow_ids=self.target_flow_ids)
        if len({item.account for item in self.accounts}) != len(self.accounts):
            raise ValueError("每个账务账户只能声明一次")
        return self


def calculate_network(body):
    from . import principal_multihop
    with db._lock:
        revision = analysis_snapshot.revision(int(body.case_id),body.expected_revision)
        return {"case_id":body.case_id,"revision":revision,**principal_multihop.calculate(int(body.case_id),body)}


@router.post("/network")
def run_network(body: NetworkIn):
    return calculate_network(body)


@router.post("/network/export")
def export_network(body: NetworkIn):
    if body.expected_revision is None:
        raise HTTPException(422,"导出须提供已分析结果的修订")
    return export_snapshot(calculate_network(body))


@router.post("/export")
def export(body: PrincipalIn):
    if body.expected_revision is None:
        raise HTTPException(422, "导出须提供已分析结果的修订")
    return export_snapshot(calculate(body))


def export_snapshot(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # A self-contained exact snapshot shared by table and graph annotations.
    # HTML escaping prevents account or source text becoming active content.
    rows = "".join("<tr>" + "".join(f"<td>{escape(str(target.get(key, '')))}</td>"
                   for key in ("id", "amount", "min", "max", "fifo")) + "</tr>" for target in value["targets"])
    content = ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>条件来源份额</title>"
               "<h1>条件来源份额</h1><p>只有账本完整、期初余额、顺序、无透支及资金全额可用等条件明确时才计算；条件与缺口见下文，不认证来源真实性。"
               "独立上界不可相加；FIFO为规则结果，不是来源证明。</p>"
               f"<p>状态：{escape(value['status'])} · 修订：{escape(value['revision'])}</p>"
               "<table><thead><tr><th>出账ID</th><th>交易金额</th><th>最小来源份额</th><th>最大来源份额</th><th>FIFO</th></tr></thead>"
               f"<tbody>{rows}</tbody></table><h2>约束、缺口与完整分配见证</h2><pre>{escape(payload)}</pre></html>")
    return Response(content, media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="principal-attribution.html"'})
