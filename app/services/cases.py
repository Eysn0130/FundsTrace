"""Case-scoped queries with lossless storage-to-JSON conversion."""
import datetime
import json
from decimal import Decimal

import duckdb

from app import db
from app.import_coverage import import_coverage


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def raw_query(sql, params=None):
    # Services calculate with Decimal; both HTTP surfaces encode money as text.
    return db.q_raw(sql, params)


def require_case(case_id: str):
    # Current storage is INTEGER; avoid exposing a DuckDB conversion exception
    # when a syntactically valid but oversized identifier is requested.
    if len(case_id) > 10 or int(case_id) > 2_147_483_647:
        raise ServiceError(404, "case_not_found", "案件不存在，请重新选择案件")
    if not raw_query("select id from cases where id = ?", [int(case_id)]):
        raise ServiceError(404, "case_not_found", "案件不存在，请重新选择案件")
    return int(case_id)


def timestamp(value):
    return value.isoformat(sep=" ") if isinstance(value, datetime.datetime) else value


def amount(value):
    if not isinstance(value, Decimal):
        raise ValueError("Expected a decimal amount at the API boundary")
    return format(value, ".2f")


def case_json(row):
    return {"id": str(row["id"]), "name": row["name"], "created_at": timestamp(row["created_at"])}


def list_cases():
    return {"items": [case_json(row) for row in raw_query("select id,name,created_at from cases order by id")]}


def create_case(name):
    try:
        with db.transaction():
            row = raw_query("insert into cases(name) values (?) returning id,name,created_at", [name])[0]
    except duckdb.ConstraintException:
        raise ServiceError(409, "case_name_exists", "案件名已存在，请使用其他名称") from None
    return case_json(row)


def stats(case_id):
    from app import analysis_snapshot, evidence_resolution, event_resolution, funds_projection
    with db._lock:
        cid = require_case(case_id)
        source = "select * from effective_flows where case_id=?"
        funds = funds_projection.summary(source, [cid])
        fields = funds_projection.public_fields(funds)
        rows = raw_query(funds_projection.cte(source) + """select
            (select count(*) from selected_facts) flows,
            (select count(distinct account) from (
                select payer_account account from selected_facts union all select payee_account from selected_facts
            ) where account is not null and account<>'') accounts,
            (select count(*) from batches where case_id=?) batches,
            (select count(*) from flows where case_id=?) raw_flows""", [cid,cid,cid])
        row = rows[0]
        row["flows"] = fields["cnt"]
        coverage = import_coverage(cid, known_excluded=row.pop("raw_flows") - funds["observation_count"])
        resolution = evidence_resolution.summary(cid)
        return {"case_id": case_id, "funds": funds, "event_summary": event_resolution.summary(cid),
                "analysis_revision": analysis_snapshot.revision(cid), "statistics_policy": funds_projection.POLICY,
                "resolution": resolution, "resolution_revision": resolution["revision"] if resolution else None,
                **row, "amount_total": fields["total"] if funds["totals"] else "0.00",
                "currency": funds["totals"][0]["currency"] if len(funds["totals"]) == 1 else None,
                "amount_totals": fields["amount_totals"], "import_coverage": coverage}


TEXT_FIELDS = ("trade_no", "payer_name", "payer_account", "payer_bank", "payee_name", "payee_account",
               "payee_bank", "currency", "summary", "channel", "self_account", "self_name")


def preview_flow(row):
    return {**{field: row.get(field) or "" for field in TEXT_FIELDS},
            "trade_time": timestamp(row.get("trade_time")), "amount": amount(row["amount"]) if row["amount"] is not None else None,
            "balance": amount(row["balance"]) if row.get("balance") is not None else None}


def flows(case_id, account, kw, page, page_size, granularity="events"):
    with db._lock:
        cid = require_case(case_id)
        conditions, params = ["case_id=?"], [cid]
        if account.strip():
            conditions.append("""(payer_account ilike ? or payee_account ilike ? or payer_display_account ilike ? or payee_display_account ilike ?
                or exists (select 1 from resolution_entities e,
                    unnest(json_extract_string(e.details_json,'$.cards[*]')) card(value)
                    where e.case_id=effective_flows.case_id
                      and e.entity_id in (effective_flows.payer_account,effective_flows.payee_account)
                      and card.value ilike ?)
                or exists (select 1 from resolution_entities e where e.case_id=effective_flows.case_id
                    and e.entity_id in (effective_flows.payer_account,effective_flows.payee_account)
                    and e.account_number ilike ?))""")
            params += [f"%{account.strip()}%"] * 6
        if kw.strip():
            conditions.append("(summary ilike ? or trade_no ilike ?)")
            params += [f"%{kw.strip()}%"] * 2
        where = " and ".join(conditions)
        from app import funds_projection
        source = f"select * from effective_flows where {where}"
        rows, funds = funds_projection.page_with_summary(source, params, page_size, (page - 1) * page_size,
                                                        observations=granularity == "observations")
        # The same locked selection already counts observations and one row per
        # confirmed event or unresolved observation, including on an empty page.
        total = funds["observation_count"] if granularity == "observations" else funds["count"]["upper"]
        from app import event_resolution, analysis_snapshot
        events = {item["flow_id"]: item for item in event_resolution.members(cid, [str(row["id"]) for row in rows])}
        revision = analysis_snapshot.revision(cid)
    return {"case_id": case_id, "total": total, "page": page, "page_size": page_size, "funds": funds, "analysis_revision": revision,
            "items": [{**preview_flow(row), "id": str(row["id"]), "case_id": str(row["case_id"]),
                       "analysis_revision": revision, "observation_count": row.get("observation_count", 1),
                       "observation_ids": row.get("observation_ids", [str(row["id"])]),
                       "granularity": "observation" if granularity == "observations" else "confirmed_event" if row["fact_status"] == "confirmed" else "unresolved_observation",
                       "event_id": events.get(str(row["id"]), {}).get("event_id"),
                       "event_state": events.get(str(row["id"]), {}).get("status", "unknown"),
                       "batch_id": str(row["batch_id"]) if row["batch_id"] is not None else None,
                       **{field: row.get(field) or "" for field in ("payer_display_account", "payee_display_account", "self_display_account", "raw_payer_account", "raw_payee_account", "raw_self_account",
                           "payer_card", "payee_card", "self_card", "payer_source_account", "payee_source_account", "self_source_account",
                           "payer_card_state", "payee_card_state", "self_card_state")}} for row in rows]}


def flow_sources(case_id, flow_id, page=1, page_size=50):
    from .imports import source_is_available

    with db._lock:
        cid = require_case(case_id)
        if len(flow_id) > 19 or int(flow_id) > 9_223_372_036_854_775_807 or not raw_query(
                "select id from flows where case_id=? and id=?", [cid, int(flow_id)]):
            raise ServiceError(404, "flow_not_found", "流水不存在或不属于当前案件")
        selected = """with observed_ids as (
            select ?::bigint flow_id union
            select member.flow_id from event_members anchor join event_members member
                on member.case_id=anchor.case_id and member.event_id=anchor.event_id
            where anchor.case_id=? and anchor.flow_id=?
                and anchor.status='confirmed' and member.status='confirmed') """
        scope = [int(flow_id), cid, int(flow_id), cid]
        total = raw_query(selected + "select count(*) n from flow_sources where case_id=? and flow_id in (select flow_id from observed_ids)", scope)[0]["n"]
        rows = raw_query(selected + """select s.*,f.flow_id,f.source_row,f.disposition,r.member_path,r.source_row member_row,r.details_json,
                            original.amount observation_amount,original.balance observation_balance,
                            original.currency observation_currency,original.self_account observation_account
                            from flow_sources f
                            join import_sources s on s.source_id=f.source_id and s.case_id=f.case_id
                            join flows original on original.case_id=f.case_id and original.id=f.flow_id
                            left join import_records r on r.case_id=f.case_id and r.source_id=f.source_id and r.record_no=f.source_row
                            where f.case_id=? and f.flow_id in (select flow_id from observed_ids)
                            order by s.imported_at,s.source_id,f.source_row,f.flow_id limit ? offset ?""",
                         [*scope, page_size, (page - 1) * page_size])
        items = []
        for row in rows:
            items.append({key: row[key] for key in ("source_id", "filename", "sha256", "size_bytes", "mode", "sheet",
                                                   "row_kind", "source_row", "parser_version", "disposition", "origin")})
            items[-1].update(observation_flow_id=str(row["flow_id"]),
                             observation_amount=amount(row["observation_amount"]) if row["observation_amount"] is not None else None,
                             observation_balance=amount(row["observation_balance"]) if row["observation_balance"] is not None else None,
                             observation_currency=row["observation_currency"], observation_account=row["observation_account"])
            items[-1].update(mapping=json.loads(row["mapping_json"]), imported_at=row["imported_at"].isoformat(timespec="seconds") + "Z",
                             source_available=source_is_available(case_id, row["source_id"]), member_path=row["member_path"],
                             member_row=row["member_row"], record_details=json.loads(row["details_json"]) if row["details_json"] else {})
    return {"case_id": case_id, "flow_id": flow_id, "items": items, "total": total, "page": page, "page_size": page_size}
