"""Read-only account registries; account identity never implies a person merge."""

import unicodedata

from app import db, evidence_resolution
from .cases import ServiceError, require_case


_FLOW_ACCOUNTS = """
with case_flows as materialized (
    select payer_account,payer_name,payee_account,payee_name
    from effective_flows where case_id=?
), flow_names as (
    select trim(payer_account) as account, trim(coalesce(payer_name, '')) as name
    from case_flows where payer_account is not null
    union all
    select trim(payee_account), trim(coalesce(payee_name, ''))
    from case_flows where payee_account is not null
), flow_accounts as (
    select account, max(name) as name from flow_names where account<>'' group by account
)
"""

_ITEM_KEYS = ("account", "name", "id_no", "identity_updated_at", "photo_at", "is_main",
              "main_source", "main_marked_at", "has_flows")


def _metadata(source):
    return f"""
    select a.account, coalesce(nullif(m.name,''), f.name, '') as name,
      i.id_no, i.updated_at identity_updated_at, p.updated_at photo_at,
      p.crop_cx, p.crop_cy, p.crop_r,
      m.account is not null is_main,
      case when m.account is null then null when m.source='auto' then 'auto' else 'manual' end main_source,
      m.marked_at main_marked_at, f.account is not null has_flows
    from {source} a
    left join flow_accounts f on f.account=a.account
    left join effective_account_ids i on i.case_id=? and i.account=a.account
    left join effective_account_photos p on p.case_id=? and p.account=a.account
    left join effective_main_accounts m on m.case_id=? and m.account=a.account
    """


def _item(row):
    return {key: row[key] for key in (*_ITEM_KEYS, "display_account", "account_number", "bank", "kind", "cards", "names", "status") if key in row}


def list_registry(case_id, source="flows", q="", page=1, page_size=30, account_exact=False):
    with db._lock:
        cid = require_case(str(case_id))
        if source not in {"flows", "identity"}:
            raise ServiceError(422, "invalid_account_source", "账户来源无效")
        keyword = q.strip() if account_exact else unicodedata.normalize("NFKC", q).strip().lower()
        if source == "flows" and not account_exact and not keyword:
            # Metadata is at most one row per account and cannot affect an
            # unfiltered count or page membership. Attach it after pagination.
            total = db.q_raw(_FLOW_ACCOUNTS + "select count(*) total from flow_accounts", [cid])[0]["total"]
            query = _FLOW_ACCOUNTS + """, page_accounts as materialized (
                select account from flow_accounts order by account limit ? offset ?
            ) """ + _metadata("page_accounts") + " order by a.account"
            rows = db.q(query, [cid, page_size, (page - 1) * page_size, cid, cid, cid])
            evidence_resolution.decorate_accounts(cid, rows)
            return {"case_id": str(cid), "source": source, "total": total, "page": page,
                    "page_size": page_size, "items": [_item(row) for row in rows]}
        source_sql = "flow_accounts" if source == "flows" else """(
            select distinct s.account from effective_account_subjects s
            join account_ids i on i.case_id=s.case_id and i.account=s.raw_account
            where s.case_id=? and s.account<>''
        )"""
        params = [cid] + ([cid] if source == "identity" else []) + [cid] * 3
        if account_exact:
            predicate = "where a.account=?"
            params += [keyword]
        else:
            predicate = """where contains(lower(a.account), ?) or contains(lower(coalesce(i.id_no,'')), ?)
              or contains(lower(coalesce(m.name,'')), ?)
              or exists (select 1 from flow_names n where n.account=a.account and contains(lower(n.name), ?))
              or exists (select 1 from resolution_entities e where e.case_id=? and e.entity_id=a.account and
                (contains(lower(e.account_number),?) or contains(lower(e.display_account),?)
                or contains(lower(array_to_string(json_extract_string(e.details_json,'$.cards[*]'),' ')),?)
                or contains(lower(array_to_string(json_extract_string(e.details_json,'$.names[*]'),' ')),?)))"""
            params += [keyword] * 4 + [cid] + [keyword] * 4
        query = _FLOW_ACCOUNTS + ", registry as (" + _metadata(source_sql) + predicate + ") "
        total = db.q_raw(query + "select count(*) total from registry", params)[0]["total"]
        rows = db.q(query + "select * from registry order by account limit ? offset ?",
                    [*params, page_size, (page - 1) * page_size])
        evidence_resolution.decorate_accounts(cid, rows)
        return {"case_id": str(cid), "source": source, "total": total, "page": page,
                "page_size": page_size, "items": [_item(row) for row in rows]}


def account_profile(case_id, account):
    with db._lock:
        cid = require_case(str(case_id))
        account = account.strip()
        # Metadata-only accounts are known without fabricating a transaction or node.
        query = _FLOW_ACCOUNTS + """, known_accounts as (
            select account from flow_accounts
            union select account from effective_account_subjects where case_id=?
            union select account from effective_main_accounts where case_id=?
            union select account from focus_nodes where case_id=?
        ) """ + _metadata("known_accounts") + " where a.account=? and a.account<>''"
        found = db.q(query, [cid] * 7 + [account])
        if not found:
            raise ServiceError(404, "account_not_found", "账号不存在或不属于当前案件")
        metadata = found[0]
        result = {"case_id": str(cid), **_item(metadata)}
        result["photo"] = None if metadata["photo_at"] is None else {
            "updated_at": metadata["photo_at"],
            **{key: 0.5 if metadata[key] is None else metadata[key]
               for key in ("crop_cx", "crop_cy", "crop_r")},
        }
        from app import funds_projection, analysis_snapshot
        source = "select * from effective_flows where case_id=? and (trim(payer_account)=? or trim(payee_account)=?)"
        selection = [cid, account, account]
        rollups = funds_projection.profile_summaries(source, selection, account)
        funds = rollups[(15, None)]
        result.update(funds=funds, cnt=funds_projection.public_fields(funds)["cnt"],
                      analysis_revision=analysis_snapshot.revision(cid), statistics_policy=funds_projection.POLICY)
        empty = funds_projection.summarize_rows([])
        result.update(funds_projection.directional_fields(
            rollups.get((14, None), empty), rollups.get((13, None), empty)))
        # Range and counterparty labels use exactly the same selected facts.
        # Share that selection instead of replaying its source/event joins.
        details = db.q(funds_projection.cte(source) + """
            select 0 detail_kind,null::varchar acct,null::varchar as name,
                   min(trade_time) tmin,max(trade_time) tmax from selected_facts
            union all
            select 1 detail_kind,acct,max(name) as name,null::timestamp tmin,null::timestamp tmax from (
                select payer_account as acct,payer_name as name from selected_facts where trim(payee_account)=?
                union all select payee_account,payee_name from selected_facts where trim(payer_account)=?
            ) where coalesce(acct,'')<>'' group by acct""", [*selection, account, account])
        bounds = next(row for row in details if row["detail_kind"] == 0)
        result.update(tmin=bounds["tmin"], tmax=bounds["tmax"])
        names = (row for row in details if row["detail_kind"] == 1)
        result["top_counterparties"] = [{"acct": row["acct"], "name": row["name"] or "",
            **funds_projection.directional_fields(rollups.get((6, row["acct"]), empty), rollups.get((9, row["acct"]), empty))}
            for row in names]
        # Counts of selected source observations order candidates only; they do
        # not become a claim that these are distinct economic events.
        result["top_counterparties"].sort(key=lambda row: (-row["out_observation_count"], -row["in_observation_count"], row["acct"]))
        result["top_counterparties"] = result["top_counterparties"][:20]
        # Resolve labels for the own account and its counterparties in one
        # scoped batch. The temporary alias never enters the response shape.
        metadata["acct"] = metadata["account"]
        evidence_resolution.decorate_accounts(cid, [metadata, *result["top_counterparties"]], key="acct")
        metadata.pop("acct")
        result.update(_item(metadata))
        return result
