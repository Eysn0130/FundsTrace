"""Small, case-bound account candidates without profiles or a full result count."""

import unicodedata

from app import db, evidence_resolution
from .cases import ServiceError, require_case


_CANDIDATES = """
with flow_names as (
    select payer_account as account, coalesce(payer_name, '') as name
    from effective_flows where case_id=? and payer_account is not null and trim(payer_account)<>''
    union all
    select payee_account, coalesce(payee_name, '')
    from effective_flows where case_id=? and payee_account is not null and trim(payee_account)<>''
), flow_accounts as (
    select account, max(name) as name from flow_names group by account
), identity_accounts as (
    select distinct s.account from effective_account_subjects s
    join account_ids i on i.case_id=s.case_id and i.account=s.raw_account
    where s.case_id=? and s.account is not null and trim(s.account)<>''
), eligible_accounts as (
    {source_sql}
), metadata as (
    select a.account, coalesce(nullif(m.name,''), f.name, '') as name, i.id_no
    from eligible_accounts a
    left join flow_accounts f on f.account=a.account
    left join effective_account_ids i on i.case_id=? and i.account=a.account
    left join effective_main_accounts m on m.case_id=? and m.account=a.account
), search_fields as (
    select account, account as matched_text, 'account' as matched_field, 0 as field_order
    from metadata
    union all
    select e.entity_id, e.account_number, 'account', 0 from resolution_entities e join metadata m on m.account=e.entity_id where e.case_id=?
    union all
    select e.entity_id, e.display_account, 'account', 0 from resolution_entities e join metadata m on m.account=e.entity_id where e.case_id=?
    union all
    select e.entity_id, card.value, 'account', 0 from resolution_entities e join metadata m on m.account=e.entity_id,
      unnest(json_extract_string(e.details_json,'$.cards[*]')) card(value) where e.case_id=?
    union all
    select account, id_no, 'id', 1 from metadata where id_no is not null and trim(id_no)<>''
    union
    select n.account, n.name, 'name', 2 from flow_names n
    join eligible_accounts a on a.account=n.account where trim(n.name)<>''
    union
    select account, name, 'name', 2 from metadata where trim(name)<>''
), matches as (
    select *, case when lower(trim(matched_text))=? then 0
                   when starts_with(lower(trim(matched_text)), ?) then 1 else 2 end as match_order
    from search_fields where contains(lower(trim(matched_text)), ?)
), ranked as (
    select *, row_number() over (
        partition by account order by match_order, field_order, matched_text
    ) as field_rank from matches
)
select m.account, m.name, m.id_no, r.matched_field, r.matched_text
from ranked r join metadata m on m.account=r.account
where r.field_rank=1
order by r.match_order, r.field_order, m.account
limit ? offset ?
"""


def suggest_accounts(case_id, q="", source="flows", page_size=30, offset=0):
    """Keep original account/identity strings; normalization affects search only."""
    with db._lock:
        cid = require_case(str(case_id))
        if source not in {"flows", "identity", "all"}:
            raise ServiceError(422, "invalid_account_source", "账户来源无效")
        if not 1 <= page_size <= 50 or not 0 <= offset <= 2_147_483_647 or len(q) > 200:
            raise ServiceError(422, "invalid_suggestion_query", "账户候选查询范围无效")
        query = unicodedata.normalize("NFKC", q).strip()
        result = {"case_id": str(cid), "source": source, "query": query,
                  "items": [], "has_more": False, "next_offset": None}
        # Empty input never scans or counts all case accounts.
        if not query:
            return result
        source_sql = {
            "flows": "select account from flow_accounts",
            "identity": "select account from identity_accounts",
            "all": "select account from flow_accounts union select account from identity_accounts",
        }[source]
        keyword = query.lower()
        rows = db.q_raw(_CANDIDATES.format(source_sql=source_sql),
                        [cid] * 8 + [keyword] * 3 + [page_size + 1, offset])
        evidence_resolution.decorate_accounts(cid, rows)
        result["items"] = rows[:page_size]
        result["has_more"] = len(rows) > page_size
        result["next_offset"] = offset + page_size if result["has_more"] else None
        return result
