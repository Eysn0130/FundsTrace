"""Case-bound main-account management and classification.

HTTP adapters own case guards and transaction boundaries. Import callers retain
their existing import transaction when recording automatic evidence.
"""

import unicodedata

from app import db, evidence_resolution
from .cases import ServiceError


def mark_automatic(case_id, account, name=""):
    """Record automatic evidence without replacing an explicit human decision.

    Callers own the encompassing case transaction. Repeated evidence retains
    the original marking time and fills a missing automatic name only.
    """
    account = account.strip()
    name = (name or "").strip()
    existing = db.q("select name, source from main_accounts where case_id=? and account=?",
                    [case_id, account])
    if existing:
        row = existing[0]
        if row["source"] != "auto":
            return {"account": account, "name": row["name"] or "", "source": "manual", "added": False}
        if name and not row["name"]:
            db.x("update main_accounts set name=? where case_id=? and account=?", [name, case_id, account])
        return {"account": account, "name": row["name"] or name, "source": "auto", "added": False}
    db.x("insert into main_accounts(case_id,account,name,source) values (?,?,?,'auto')",
         [case_id, account, name])
    return {"account": account, "name": name, "source": "auto", "added": True}


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


_CASE_ACCOUNTS_SQL = """
    with account_rows as (
        select trim(payer_account) as account, trim(coalesce(payer_name, '')) as name
        from effective_visible_flows where case_id=? and payer_account is not null
        union all
        select trim(payee_account), trim(coalesce(payee_name, ''))
        from effective_visible_flows where case_id=? and payee_account is not null
        union all
        select trim(f.self_account), trim(coalesce(f.self_name, ''))
        from effective_visible_flows f join source_ledger_index l on l.case_id=f.case_id and l.flow_id=f.id
        where f.case_id=? and coalesce(f.self_account,'')<>''
    ), accounts as (
        select account, max(name) as name from account_rows where account<>'' group by account
    )
"""


def auto_detect_all(case_id: int):
    """扫描当前案件的全部单账户（模式B）批次，自动识别主账号（存量数据一键识别）"""
    batches = db.q("select id, filename from batches where case_id = ? and mode = 'B' order by id", [case_id])
    detected, seen = [], set()
    for b in batches:
        d = _auto_detect_main(b["id"], case_id)
        for account in (d.get("accounts", [d]) if d else []):
            if account["account"] not in seen:
                seen.add(account["account"])
                detected.append({**account, "batch": b["filename"]})
    return {"detected": detected, "batches_scanned": len(batches),
            "added_count": sum(item["added"] for item in detected),
            "manual_preserved": sum(item["source"] == "manual" for item in detected),
            "existing_auto_count": sum(item["source"] == "auto" and not item["added"] for item in detected)}


def main_account_candidates(case_id: int, q: str = "", exact: bool = False, page: int = 1,
                            page_size: int = 30):
    """Search accounts, rather than a truncated page of transaction matches."""
    keyword = q.strip()
    if exact:
        criterion, search_params = "a.account=?", [keyword]
    else:
        keyword = unicodedata.normalize("NFKC", q).strip().lower()
        criterion = """contains(lower(a.account), ?) or exists (
            select 1 from account_rows names
            where names.account=a.account and contains(lower(names.name), ?)
        ) or contains(lower(coalesce(m.name,'')), ?) or exists (
            select 1 from effective_account_ids identity
            where identity.case_id=? and identity.account=a.account
              and contains(lower(coalesce(identity.id_no,'')), ?)
        )"""
        criterion = "(" + criterion + """ or exists (select 1 from resolution_entities e where e.case_id=? and e.entity_id=a.account and
            (contains(lower(e.account_number),?) or contains(lower(e.display_account),?)
             or contains(lower(array_to_string(json_extract_string(e.details_json,'$.cards[*]'),' ')),?)
             or contains(lower(array_to_string(json_extract_string(e.details_json,'$.names[*]'),' ')),?))))"""
        search_params = [keyword] * 3 + [case_id, keyword, case_id] + [keyword] * 4
    matching = _CASE_ACCOUNTS_SQL + """
        , matching as (
            select a.account, coalesce(nullif(m.name,''), a.name) as name,
                   m.account is not null as is_main,
                   case when m.account is null then null when m.source='auto' then 'auto' else 'manual' end as source
            from accounts a left join effective_main_accounts m on m.case_id=? and m.account=a.account
            where """ + criterion + """
        )
    """
    params = [case_id, case_id, case_id, case_id, *search_params]
    total = db.q(matching + "select count(*) total from matching", params)[0]["total"]
    items = db.q(matching + "select * from matching order by account limit ? offset ?",
                 [*params, page_size, (page - 1) * page_size])
    evidence_resolution.decorate_accounts(case_id, items)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def list_main_accounts(case_id: int = 1):
    rows = db.q("select case_id, account, name, source, marked_at from effective_main_accounts where case_id = ? order by marked_at, account", [case_id])
    evidence_resolution.decorate_accounts(case_id, rows)
    return rows


def clear_auto_main_accounts(case_id: int):
    """清除本案件全部"自动识别"的主账号标记（手动标记保留）"""
    n = len(db.q("select account from effective_main_accounts where case_id = ? and source = 'auto'", [case_id]))
    db.x("delete from main_accounts where case_id = ? and source = 'auto'", [case_id])
    return {"cleared": n}


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


def mark_main_account(case_id: int, account: str, name: str = ""):
    acct = account.strip()
    if not acct:
        raise ServiceError(400, "invalid_account", "账号不能为空")
    existing_account = db.q(_CASE_ACCOUNTS_SQL + "select name from accounts where account=?",
                            [case_id, case_id, case_id, acct])
    existing_mark = db.q("select name from effective_main_accounts where case_id=? and account=?", [case_id, acct])
    if not existing_account and not existing_mark:
        raise ServiceError(404, "account_not_found", "账号不存在或不属于当前案件")
    name = (name.strip() or (existing_mark[0]["name"] if existing_mark else "")
            or (existing_account[0]["name"] if existing_account else "") or "")
    db.x("insert or replace into main_accounts(case_id, account, name, source) values (?,?,?, 'manual')",
         [case_id, acct, name])
    return {"ok": True, "account": acct, "name": name, "source": "manual"}


def unmark_main_account(case_id: int, account: str):
    acct = account.strip()
    if not acct:
        raise ServiceError(400, "invalid_account", "账号不能为空")
    db.x("delete from main_accounts where case_id=? and (account=? or account in "
         "(select raw_account from effective_main_account_declarations where case_id=? and account=?))",
         [case_id, acct, case_id, acct])
    return {"ok": True}
