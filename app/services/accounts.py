"""Read-only account registries; account identity never implies a person merge."""

from app import db
from app.money import money_string
from .cases import ServiceError, require_case


_FLOW_ACCOUNTS = """
with flow_names as (
    select trim(payer_account) as account, trim(coalesce(payer_name, '')) as name
    from effective_flows where case_id=? and payer_account is not null
    union all
    select trim(payee_account), trim(coalesce(payee_name, ''))
    from effective_flows where case_id=? and payee_account is not null
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
    left join account_ids i on i.case_id=? and i.account=a.account
    left join account_photos p on p.case_id=? and p.account=a.account
    left join main_accounts m on m.case_id=? and m.account=a.account
    """


def _item(row):
    return {key: row[key] for key in _ITEM_KEYS}


def list_registry(case_id, source="flows", q="", page=1, page_size=30):
    with db._lock:
        cid = require_case(str(case_id))
        if source not in {"flows", "identity"}:
            raise ServiceError(422, "invalid_account_source", "账户来源无效")
        source_sql = "flow_accounts" if source == "flows" else "(select account from account_ids where case_id=? and account<>'')"
        params = [cid, cid] + ([cid] if source == "identity" else []) + [cid] * 3
        keyword = q.strip().lower()
        query = _FLOW_ACCOUNTS + ", registry as (" + _metadata(source_sql) + """
            where contains(lower(a.account), ?) or contains(lower(coalesce(i.id_no,'')), ?)
              or contains(lower(coalesce(m.name,'')), ?)
              or exists (select 1 from flow_names n where n.account=a.account and contains(lower(n.name), ?))
        ) """
        params += [keyword] * 4
        total = db.q_raw(query + "select count(*) total from registry", params)[0]["total"]
        rows = db.q(query + "select * from registry order by account limit ? offset ?",
                    [*params, page_size, (page - 1) * page_size])
        return {"case_id": str(cid), "source": source, "total": total, "page": page,
                "page_size": page_size, "items": [_item(row) for row in rows]}


def _directional_money(rows):
    result = {}
    for direction in ("in", "out"):
        totals = [{"currency": row["currency"], "amount": money_string(row[direction + "_amt"])}
                  for row in rows if row[direction + "_cnt"]]
        result[direction + "_totals"] = totals
        result[direction + "_amt"] = totals[0]["amount"] if len(totals) == 1 else None if totals else "0.00"
    return result


def account_profile(case_id, account):
    with db._lock:
        cid = require_case(str(case_id))
        account = account.strip()
        # Metadata-only accounts are known without fabricating a transaction or node.
        query = _FLOW_ACCOUNTS + """, known_accounts as (
            select account from flow_accounts
            union select account from account_ids where case_id=?
            union select account from account_photos where case_id=?
            union select account from main_accounts where case_id=?
            union select account from focus_nodes where case_id=?
        ) """ + _metadata("known_accounts") + " where a.account=? and a.account<>''"
        found = db.q(query, [cid] * 9 + [account])
        if not found:
            raise ServiceError(404, "account_not_found", "账号不存在或不属于当前案件")
        metadata = found[0]
        result = {"case_id": str(cid), **_item(metadata)}
        result["photo"] = None if metadata["photo_at"] is None else {
            "updated_at": metadata["photo_at"],
            **{key: 0.5 if metadata[key] is None else metadata[key]
               for key in ("crop_cx", "crop_cy", "crop_r")},
        }
        counts = db.q("""select count(*) cnt,
            count(*) filter (where trim(payer_account)=?) out_cnt,
            count(*) filter (where trim(payee_account)=?) in_cnt,
            min(trade_time) tmin, max(trade_time) tmax
            from effective_flows where case_id=? and (trim(payer_account)=? or trim(payee_account)=?)""",
                      [account, account, cid, account, account])[0]
        result.update(counts)
        own_money = db.q_raw("""select coalesce(currency,'') currency,
            count(*) filter (where trim(payer_account)=?) out_cnt,
            coalesce(sum(amount) filter (where trim(payer_account)=?),0) out_amt,
            count(*) filter (where trim(payee_account)=?) in_cnt,
            coalesce(sum(amount) filter (where trim(payee_account)=?),0) in_amt
            from effective_flows where case_id=? and (trim(payer_account)=? or trim(payee_account)=?)
            group by coalesce(currency,'') order by currency""",
                             [account, account, account, account, cid, account, account])
        result.update(_directional_money(own_money))
        counterparties = db.q_raw("""select acct, max(name) as name, coalesce(currency,'') currency,
            count(*) filter (where direction='out') out_cnt,
            coalesce(sum(amount) filter (where direction='out'),0) out_amt,
            count(*) filter (where direction='in') in_cnt,
            coalesce(sum(amount) filter (where direction='in'),0) in_amt
            from (
              select trim(payee_account) acct, payee_name as name, currency, amount, 'out' direction
              from effective_flows where case_id=? and trim(payer_account)=?
              union all
              select trim(payer_account), payer_name, currency, amount, 'in'
              from effective_flows where case_id=? and trim(payee_account)=?
            ) where coalesce(acct,'')<>'' group by acct, coalesce(currency,'') order by currency""",
                                  [cid, account, cid, account])
        grouped = {}
        for row in counterparties:
            target = grouped.setdefault(row["acct"], {"acct": row["acct"], "name": "", "in_cnt": 0, "out_cnt": 0, "money": []})
            target["name"] = max(target["name"], row["name"] or "")
            target["in_cnt"] += row["in_cnt"]
            target["out_cnt"] += row["out_cnt"]
            target["money"].append(row)
        ordered = sorted(grouped.values(), key=lambda row: (-row["out_cnt"], -row["in_cnt"], row["acct"]))[:20]
        result["top_counterparties"] = [{key: value for key, value in row.items() if key != "money"}
                                        | _directional_money(row["money"]) for row in ordered]
        return result
