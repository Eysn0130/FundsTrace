# -*- coding: utf-8 -*-
"""Case-scoped analysis, with explicit exact-money JSON boundaries."""
import urllib.parse
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import analytics, db
from .money import money_decimal, wire_money

router = APIRouter(prefix="/api/analysis", tags=["analysis"])
analytics.set_db(db.q_raw)


class RunIn(BaseModel):
    case_id: int = Field(gt=0, le=2_147_483_647)
    params: dict = Field(default_factory=dict)


class PathsIn(BaseModel):
    case_id: int = Field(gt=0, le=2_147_483_647)
    # 账号或户名：户名会展开成全部同名账号（同名账户不丢弃）。
    src: str
    dst: str
    max_hops: int = Field(default=4, ge=1, le=12)
    max_paths: int = Field(default=50, ge=1, le=500)
    order: str = "amount"


def _require_case(case_id):
    if not db.q_raw("select id from cases where id = ?", [case_id]):
        raise HTTPException(status_code=404, detail="案件不存在，请重新选择案件")


def _params(params):
    """Validate exact thresholds before computing any result or report."""
    result = dict(params)
    try:
        if "algorithms" in result:
            algorithms = result["algorithms"]
            if (not isinstance(algorithms, list) or not algorithms
                    or any(not isinstance(name, str) or name not in analytics.ALGORITHMS for name in algorithms)
                    or len(set(algorithms)) != len(algorithms)):
                raise ValueError("algorithms 必须是非空、不重复的算法名称列表：cycles、fast_flows、outliers、paths")
        for name, minimum, maximum in (("cycle_window_days", 1, 365), ("ff_window_minutes", 5, 1440)):
            if name in result:
                value = result[name]
                if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                    raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
        for name in ("ff_min_amount", "cycle_min_total"):
            if name in result:
                result[name] = money_decimal(result[name])
                if result[name] < 0:
                    raise ValueError(f"{name} must be nonnegative")
        if "ff_tolerance" in result:
            value = result["ff_tolerance"]
            if isinstance(value, bool):
                raise ValueError("ff_tolerance must be a finite ratio between 0 and 1")
            tolerance = Decimal(str(value))
            if not tolerance.is_finite() or not 0 <= tolerance <= 1:
                raise ValueError("ff_tolerance must be a finite ratio between 0 and 1")
            result["ff_tolerance"] = tolerance
        if ("paths" in result.get("algorithms", [])
                or any(name in result for name in ("path_src", "path_dst", "path_max_hops", "path_max_paths",
                                                   "path_order"))):
            for name in ("path_src", "path_dst"):
                value = result.get(name)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("资金路径必须同时指定非空起点和终点（账号或户名）")
                result[name] = value.strip()
            for name, default, maximum in (("path_max_hops", 4, 12), ("path_max_paths", 50, 500)):
                value = result.get(name, default)
                if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
                    raise ValueError(f"{name} must be an integer between 1 and {maximum}")
                result[name] = value
            order = result.get("path_order", "amount")
            if order not in analytics.PATH_ORDERS:
                raise ValueError("path_order must be amount or time")
            result["path_order"] = order
    except (ValueError, InvalidOperation, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return result


@router.post("/run")
def run_analysis(body: RunIn):
    """Exact monetary results and explicitly approximate statistical metrics."""
    _require_case(body.case_id)
    return wire_money(analytics.run_all(body.case_id, _params(body.params)))


@router.post("/paths")
def trace_paths(body: PathsIn):
    """Currency-isolated multi-hop paths; totals sum their transaction legs."""
    _require_case(body.case_id)
    if body.order not in analytics.PATH_ORDERS:
        raise HTTPException(status_code=422, detail="order 必须是 amount 或 time")
    return wire_money(analytics.trace_paths(body.case_id, body.src, body.dst,
                                           max_hops=body.max_hops, max_paths=body.max_paths,
                                           order=body.order))


@router.post("/report")
def download_report(body: RunIn):
    """Self-contained HTML with exact labels and a lossless JSON snapshot."""
    _require_case(body.case_id)
    results = analytics.run_all(body.case_id, _params(body.params))
    case_name = db.q("select name from cases where id = ?", [body.case_id])[0]["name"]
    stats = results["source_snapshot"]
    from . import report

    content = report.build_report(body.case_id, results, {"case_name": case_name, "stats": stats})
    fname = urllib.parse.quote(f"资金分析报告_{case_name}.html")
    return Response(content, media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"})
