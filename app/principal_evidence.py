"""Prepare source-backed attribution constraints for existing user actions.

Matching balances and a requester's booleans do not establish ledger coverage,
credit terms or historical availability. Missing evidence stays explicit. This
module performs no allocation policy selection and never writes source facts.
"""
from types import SimpleNamespace

from . import db, event_resolution, principal_ledger


def assess(case_id, rows):
    metadata = event_resolution.ledger_metadata(case_id, [row["id"] for row in rows])
    refs = sorted({source for item in metadata.values() for source in item.get("source_ids", [])})
    conditions = {"coverage": "unknown", "no_overdraft": False, "funds_available": False,
                  "opening_balance": None, "balance_kind": "unknown"}
    assessment = [
        {"condition": "coverage", "status": "unknown", "kind": "missing_evidence", "source_ids": [],
         "message": "缺少可证明该账户区间收支完整的请求范围与完整反馈依据"},
        {"condition": "no_overdraft", "status": "unknown", "kind": "missing_evidence", "source_ids": [],
         "message": "缺少该区间的透支或授信依据"},
        {"condition": "funds_available", "status": "unknown", "kind": "missing_evidence", "source_ids": [],
         "message": "缺少历史可用资金、冻结及在途资金依据"},
    ]
    book = bool(rows) and all(metadata.get(row["id"], {}).get("balance_kind") == "post_book" for row in rows)
    if book:
        conditions["balance_kind"] = "post_book"
    assessment.append({"condition": "balance_kind", "status": "established" if book else "unknown",
        "kind": "source_declaration" if book else "missing_evidence", "source_ids": refs if book else [],
        "message": "来源明确声明本笔交易后账面余额" if book else "余额字段尚未明确为本笔交易后账面余额"})
    # A unique first posting with an explicit after-book balance can establish
    # the immediately preceding book balance, but never coverage/availability.
    opening = None
    if rows and rows[0].get("trade_time") is not None and (len(rows) == 1 or rows[0]["trade_time"] != rows[1]["trade_time"]):
        first = rows[0]
        if metadata.get(first["id"], {}).get("balance_kind") == "post_book" and first.get("balance") is not None and first.get("direction"):
            try:
                value = principal_ledger.units(first["balance"]) - first["amount"] * (1 if first["direction"] == "in" else -1)
                if value >= 0:
                    opening = principal_ledger.money(value)
            except (ValueError, TypeError):
                pass
    conditions["opening_balance"] = opening
    assessment.append({"condition": "opening_balance", "status": "established" if opening is not None else "unknown",
        "kind": "derived_from_source" if opening is not None else "missing_evidence",
        "source_ids": metadata.get(rows[0]["id"], {}).get("source_ids", []) if opening is not None else [],
        "message": "由首笔明确的交易后账面余额及方向反推紧邻前余额" if opening is not None else "区间起点前账面余额未知"})
    return conditions, assessment


def context_result(case_id, account, anchor, visible_rows):
    """Use the existing anchor/window only to choose results, never coverage."""
    currency = anchor.get("currency") or ""
    source_id = str(anchor["id"])
    if anchor.get("payee_account") != account:
        return {"status": "unknown", "currency": currency, "targets": [], "condition_basis": "source_evidence",
            "assessment": [{"condition": "source", "status": "unknown", "kind": "missing_evidence", "source_ids": [],
                            "message": "当前锚点是出账；匹配评分不能指定其中某笔入账为已确认来源"}]}
    # An opposite observation can identify the own entry only through a
    # confirmed event. Similar amount/time or a copied balance is insufficient.
    source = db.q_raw("""select f.id from effective_flows f where f.case_id=? and f.self_account=?
        and (f.id=? or f.id in (
          select m.flow_id from event_members m join event_members a
            on a.case_id=m.case_id and a.event_id=m.event_id
          where a.case_id=? and a.flow_id=? and a.status='confirmed' and m.status='confirmed'))
        order by f.id limit 2""", [case_id, account, source_id, case_id, source_id])
    if len(source) == 1:
        source_id = str(source[0]["id"])
    targets = list(dict.fromkeys(str(row["id"]) for row in visible_rows
        if row.get("self_account") == account and row.get("payer_account") == account
        and row.get("payee_account") != account and row.get("currency") == currency))
    if len(targets) > 20:
        return {"status": "limited", "currency": currency, "targets": [], "condition_basis": "source_evidence",
            "assessment": [{"condition": "target_limit", "status": "limited", "kind": "resource_limit", "source_ids": [],
                            "message": "当前明细超过一次份额计算的目标上限，未将截断结果作为完整结论"}]}
    span = db.q_raw("""select min(trade_time) lo,max(trade_time) hi from effective_flows
        where case_id=? and self_account=? and currency=?""", [case_id, account, currency])[0]
    if span["lo"] is None or span["hi"] is None:
        return {"status": "unknown", "currency": currency, "targets": [], "condition_basis": "source_evidence",
            "assessment": [{"condition": "own_ledger", "status": "unknown", "kind": "missing_evidence", "source_ids": [],
                            "message": "未取得该账务账户的本方账簿；对手观察和余额不能替代"}]}
    request = SimpleNamespace(account=account, currency=currency, source_flow_id=source_id, target_flow_ids=targets,
        start=span["lo"].isoformat(), end=span["hi"].isoformat())
    return principal_ledger.calculate(case_id, request)
