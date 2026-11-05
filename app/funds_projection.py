"""Selected, evidence-bounded economic funds without changing source ledgers.

Callers hold db._lock across selection, revision and projection. SQL inputs are
trusted application queries returning original flow columns, including balance;
they must not contain this module's reserved fact/observation column names.
Representative IDs only make display stable: they confer no temporal ordering.
"""
from __future__ import annotations

from decimal import Decimal, localcontext
import re

from . import db
from .bank_single_postings import POLICY as SINGLE_POSTING_POLICY
from .money import money_decimal
from .table_import import _ISO_CURRENCIES

POLICY = "evidence-bounded-funds-v1"
SOURCE_POLICY = "source-observations-v1"
SELECTION_POLICY = "selected-transactions-v1"
CHUNK = 4096
_CURRENCIES_SQL = "(" + ",".join("'" + value + "'" for value in sorted(_ISO_CURRENCIES)) + ")"


def ordinary_source(source_sql):
    """Exclude globally contradicted event groups, retaining source history."""
    return f"""select eligible.* from ({source_sql}) eligible where not exists (
        select 1 from event_members conflict where conflict.case_id=eligible.case_id
            and conflict.flow_id=eligible.id and conflict.status='conflict')"""


def ordinary_rows(case_id, rows):
    """Filter already loaded representatives using the current case decision."""
    ids = [int(row['id']) for row in rows]
    blocked = set()
    for start in range(0, len(ids), CHUNK):
        blocked.update(str(row['flow_id']) for row in db.q_raw(
            "select flow_id from event_members where case_id=? and status='conflict' "
            "and flow_id in (select unnest(?::bigint[]))", [case_id, ids[start:start + CHUNK]]))
    return [row for row in rows if str(row['id']) not in blocked]


def transaction_fields(rows):
    """Ordinary scalars; pending linkage keeps observation amounts separate."""
    totals = {}
    pending = sum(bool(row.get("fact_time_pending") or row.get("fact_pair_pending")) for row in rows)
    with localcontext() as context:
        context.prec = 50
        for row in rows:
            currency = row.get('currency') or ''
            value = money_decimal(row['amount']) if row.get('amount') is not None else None
            previous = totals.get(currency, Decimal(0))
            totals[currency] = previous + value if previous is not None and value is not None else None
    amounts = [{'currency': currency, 'amount': format(value, '.2f') if value is not None else None}
               for currency, value in sorted(totals.items())]
    return {**({'pending_observation_count': pending} if pending else {}), 'cnt': len(rows), 'total': '0.00' if not rows else amounts[0]['amount'] if len(amounts) == 1 and amounts[0]['currency'] else None,
            'amount_totals': amounts}


def cte(source_sql, *, include_ids=False):
    """WITH prefix exposing original selected_observations and selected_facts.

    Source joins may repeat an ID; an observation is one (case_id,id). Confirmed
    events merge only selected observations, using their selected minimum ID.
    Unresolved observations remain distinct rows without a uniqueness claim.
    """
    selected_ids = """,list(cast(id as varchar) order by id) over (partition by case_id,fact_event_id,
        case when fact_event_id is null then id end) observation_ids""" if include_ids else ""
    # Isolate joined source filters from DuckDB 1.5.6's window rewrite, whose
    # nullability analysis can miscast a BETWEEN expression.
    return f"""with _funds_source as materialized ({source_sql}),
    _funds_selected as (
        select * from _funds_source
        qualify row_number() over (partition by case_id,id order by id)=1
    ), _funds_pending_references as (
        select c.case_id,c.flow_id,min(c.reference_key) reference_key
        from event_observation_cache c join _funds_selected s on s.case_id=c.case_id and s.id=c.flow_id
        group by c.case_id,c.flow_id
    ), selected_observations as (
        select s.*,
            case when m.status='unknown' and m.reason='time_basis_incomparable' then p.reference_key end fact_pending_reference,
            m.status='unknown' and m.reason='time_basis_incomparable' and
                (p.reference_key is null or count(*) filter(where m.status='unknown' and m.reason='time_basis_incomparable')
                    over(partition by s.case_id,p.reference_key)>1) fact_time_pending,
            m.status='unknown' and m.pending_group is not null fact_pair_pending,
            coalesce(json_extract_string(e.evidence_json,'$.policy')='{SINGLE_POSTING_POLICY}',false) fact_single_posting,
            case when m.status='confirmed' and e.event_id is not null
                and e.amount>0 and e.currency in {_CURRENCIES_SQL}
                then 'confirmed' else 'unresolved' end fact_status,
            case when m.status='confirmed' and e.event_id is not null
                and e.amount>0 and e.currency in {_CURRENCIES_SQL}
                then e.event_id else null end fact_event_id,
            case when m.status='confirmed' and e.event_id is not null
                and e.amount>0 and e.currency in {_CURRENCIES_SQL}
                then e.amount else s.amount end fact_amount,
            case when m.status='confirmed' and e.event_id is not null
                and e.amount>0 and e.currency in {_CURRENCIES_SQL}
                then e.currency else s.currency end fact_currency
        from _funds_selected s
        left join event_members m on m.case_id=s.case_id and m.flow_id=s.id
        left join _funds_pending_references p on p.case_id=s.case_id and p.flow_id=s.id
        left join resolved_events e on e.case_id=m.case_id and e.event_id=m.event_id
    ), _funds_ranked as (
        select *,
            row_number() over (partition by case_id,fact_event_id,
                case when fact_event_id is null then id end order by id) _funds_rank,
            count(*) over (partition by case_id,fact_event_id,
                case when fact_event_id is null then id end) observation_count{selected_ids}
        from selected_observations
    ), selected_facts as (
        select * exclude (amount,currency,balance,fact_amount,fact_currency,_funds_rank),
            amount observation_amount,currency observation_currency,balance observation_balance,
            fact_amount amount,fact_currency currency,
            case when fact_status='confirmed' and not fact_single_posting then null else balance end balance
        from _funds_ranked where _funds_rank=1
    ), ordinary_facts as (select * from selected_facts) """


def _result(aggregates):
    meta = next(row for row in aggregates if row["is_total"])
    if meta.get("case_count", 0) > 1 or meta.get("missing_case", False):
        raise ValueError("funds projection requires one case scope")
    observations = int(meta["observations"] or 0)
    confirmed = int(meta["confirmed"] or 0)
    unresolved = int(meta["unresolved"] or 0)
    unknown_currency = bool(meta["unknown_currency"])
    unknown_amount = bool(meta["unknown_amount"])
    totals = []
    with localcontext() as context:
        context.prec = 50
        for row in sorted((row for row in aggregates if not row["is_total"]),
                          key=lambda row: (row["funds_currency"] is None, row["funds_currency"] or "")):
            lower = row["lower_amount"] or Decimal(0)
            upper = None if unknown_currency or row["unknown_amount"] else lower + (row["unresolved_amount"] or Decimal(0))
            totals.append({"currency": row["funds_currency"],
                           "amount": format(lower, ".2f") if upper is not None and upper == lower else None,
                           "lower": format(lower, ".2f"), "upper": format(upper, ".2f") if upper is not None else None})
    return {"policy": POLICY,
            "status": "unknown" if unknown_currency or unknown_amount else "bounded" if unresolved else "exact",
            "observation_count": observations, "confirmed_event_count": confirmed,
            "unresolved_observation_count": unresolved,
            "count": {"lower": confirmed, "upper": confirmed + unresolved}, "totals": totals}


def summary_query(source_sql, group_by=(), *, rollup_groups=None):
    """Aggregation SQL; decode each selected group's small result separately."""
    grouped, aggregate = _summary_parts(group_by, rollup_groups=rollup_groups)
    return cte(source_sql) + grouped + aggregate


def _summary_parts(group_by=(), *, rollup_groups=None):
    if any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name) for name in group_by):
        raise ValueError("invalid funds grouping column")
    columns = ",".join(group_by)
    prefix = columns + "," if columns else ""
    grouping_sets = f"({columns}),({prefix}funds_currency)"
    grouping_marker = ""
    if rollup_groups is not None:
        if not group_by or any(not set(group) <= set(group_by) for group in rollup_groups):
            raise ValueError("rollup keys must belong to selected groups")
        grouping_sets = ",".join("(" + ",".join(group) + ")" for keys in rollup_groups
                                 for group in (tuple(keys), (*keys, "funds_currency")))
        grouping_marker = f"grouping_id({columns}) _account_group,"
    grouped = f""", _funds_grouped as (
        select *,case when currency in {_CURRENCIES_SQL} then currency end funds_currency,
            count(distinct case_id) over () _funds_case_count,
            bool_or(case_id is null) over () _funds_missing_case
        from selected_facts
    ) """
    return grouped, f"""select {prefix}{grouping_marker}grouping(funds_currency) is_total,funds_currency,
        coalesce(max(_funds_case_count),0) case_count,bool_or(_funds_missing_case) missing_case,
        sum(observation_count) observations,
        count(*) filter(where fact_status='confirmed') confirmed,
        sum(case when fact_status='unresolved' then observation_count else 0 end) unresolved,
        count(*) filter(where funds_currency is null) unknown_currency,
        count(*) filter(where amount is null or amount<0) unknown_amount,
        sum(case when fact_status='confirmed' then amount else 0 end) lower_amount,
        sum(case when fact_status='unresolved' and amount>=0 then amount else 0 end) unresolved_amount
        from _funds_grouped group by grouping sets ({grouping_sets})"""


def page_with_summary(source_sql, params, page_size, offset, *, observations=False, unique_source=False):
    """Share a request's database selection; return only its page and totals.

    The two independent scalar aggregates keep totals on an empty/overflow
    page. Structs preserve Decimal and list types without a JSON conversion or
    a union that coerces page values to the summary's column types.
    """
    # Late payload lookup requires source columns to depend on (case_id,id).
    # Generic joined/projection sources can have different payloads for a
    # repeated key, so they retain the single selected-row interpretation.
    from . import transaction_time
    order = transaction_time.sql_order() if unique_source else "trade_time desc nulls last,id desc"
    grouped, aggregate = _summary_parts()
    if observations and unique_source:
        narrow = f"select case_id,id,trade_time,trade_time_utc,amount,currency,balance from ({source_sql}) _funds_input"
        query = cte(narrow) + grouped + f""",
            _funds_summary as ({aggregate}),
            _funds_page_keys as materialized (select * from selected_observations
                order by {order} limit ? offset ?),
            _funds_payload as materialized (
                select s.* from ({source_sql}) s join _funds_page_keys p
                    on s.case_id=p.case_id and s.id=p.id
            ), _funds_page as (
                select s.*,p.fact_pending_reference,p.fact_time_pending,p.fact_pair_pending,p.fact_single_posting,
                    p.fact_status,p.fact_event_id,p.fact_amount,p.fact_currency
                from _funds_payload s join _funds_page_keys p
                    on s.case_id=p.case_id and s.id=p.id
            )
            select (select list(p order by {order}) from _funds_page p) page,
                   (select list(s) from _funds_summary s) summary"""
        with db._lock:
            result = db.q_raw(query, [*params, page_size, offset, *params])[0]
            return result["page"] or [], decode_summary(result["summary"])
    relation = "selected_observations" if observations else "selected_facts"
    query = cte(source_sql, include_ids=not observations) + grouped + f""",
        _funds_summary as ({aggregate}),
        _funds_page as (select * from {relation}
            order by {order} limit ? offset ?)
        select (select list(p order by {order}) from _funds_page p) page,
               (select list(s) from _funds_summary s) summary"""
    with db._lock:
        result = db.q_raw(query, [*params, page_size, offset])[0]
        return result["page"] or [], decode_summary(result["summary"])


def decode_summary(aggregates):
    """Decode one group's rows returned by summary_query, without requerying."""
    return _result(aggregates)


def summary(source_sql, params=None, *, observations=False, selected=False):
    """Aggregate a single selected case in SQL; return only small currency rows."""
    with db._lock:
        query = (source_summary_query(source_sql, canonical=selected)
                 if observations or selected else summary_query(source_sql))
        return (decode_source_summary if observations or selected else decode_summary)(db.q_raw(query, params))


def grouped_summary(source_sql, params=None, group_by=()):
    """One SQL read returns bounded funds per direction/pair, without ID lists."""
    with db._lock:
        rows = db.q_raw(summary_query(source_sql, group_by), params)
        groups = {}
        for row in rows:
            groups.setdefault(tuple(row[name] for name in group_by), []).append(row)
        return {key: decode_summary(values) for key, values in groups.items()}


def account_summaries(source_sql, params=None, *, observations=False, selected=False):
    """Compute total, payer, payee and pair funds from one selected-facts scan.

    Grouping flags distinguish an absent key from a genuine null endpoint.
    Aggregation stays in DuckDB; no observation-sized Python list or cache.
    """
    with db._lock:
        keys = ("payer_account", "payee_account")
        groups = ((), ("payer_account",), ("payee_account",), keys)
        query = (source_summary_query(source_sql, keys, rollup_groups=groups, canonical=selected)
                 if observations or selected else summary_query(source_sql, keys, rollup_groups=groups))
        rows = db.q_raw(query, params)
        groups = {}
        for row in rows:
            key = (row["_account_group"], row["payer_account"], row["payee_account"])
            groups.setdefault(key, []).append(row)
        return {key: (decode_source_summary if observations or selected else decode_summary)(values) for key, values in groups.items()}


def profile_summaries(source_sql, params, account, *, observations=False, selected=False):
    """Trim the selected own key while retaining original counterparty keys."""
    source = f"""select f.*,trim(payer_account)=? _own_payer,trim(payee_account)=? _own_payee
        from ({source_sql}) f"""
    columns = ("payer_account", "payee_account", "_own_payer", "_own_payee")
    groupings = ((), ("_own_payer",), ("_own_payee",),
                 ("payer_account", "_own_payee"), ("payee_account", "_own_payer"))
    with db._lock:
        query = (source_summary_query(source, columns, rollup_groups=groupings, canonical=selected)
                 if observations or selected else summary_query(source, columns, rollup_groups=groupings))
        rows = db.q_raw(query, [account, account, *params])
        groups = {}
        for row in rows:
            flag = row["_account_group"]
            # Omitted-key bits: 15 total, 14 incoming, 13 outgoing,
            # 6 incoming by original payer, 9 outgoing by original payee.
            if flag != 15 and not row["_own_payee" if flag in (6, 14) else "_own_payer"]:
                continue
            key = (flag, row["payer_account"] if flag == 6 else row["payee_account"] if flag == 9 else None)
            groups.setdefault(key, []).append(row)
        return {key: (decode_source_summary if observations or selected else decode_summary)(values) for key, values in groups.items()}


def _row_summary(rows):
    groups = {}
    meta = {"is_total": 1, "observations": 0, "confirmed": 0, "unresolved": 0,
            "unknown_currency": 0, "unknown_amount": 0}
    with localcontext() as context:
        context.prec = 50
        for row in rows:
            currency = row["currency"] if row["currency"] in _ISO_CURRENCIES else None
            value = row["amount"]
            known_amount = value is not None and value >= 0
            confirmed = row["fact_status"] == "confirmed"
            group = groups.setdefault(currency, {"is_total": 0, "funds_currency": currency,
                "lower_amount": Decimal(0), "unresolved_amount": Decimal(0), "unknown_amount": 0})
            meta["observations"] += row["observation_count"]
            meta["confirmed"] += confirmed
            meta["unresolved"] += 0 if confirmed else row["observation_count"]
            meta["unknown_currency"] += currency is None
            meta["unknown_amount"] += not known_amount
            group["unknown_amount"] += not known_amount
            if known_amount:
                group["lower_amount" if confirmed else "unresolved_amount"] += value
    return _result([meta, *groups.values()])


def summarize_rows(rows):
    """Pure summary of already canonical selected_facts, including wire money."""
    facts, cases, seen = [], set(), set()
    for row in rows:
        status, event_id, count = row.get("fact_status"), row.get("fact_event_id"), row.get("observation_count")
        if (status not in {"confirmed", "unresolved"} or type(count) is not int or count < 1
                or status == "unresolved" and (count != 1 or event_id is not None)
                or status == "confirmed" and not event_id):
            raise ValueError("invalid canonical funds fact")
        if row.get("case_id") is not None:
            cases.add(str(row["case_id"]))
        if status == "confirmed":
            if event_id in seen:
                raise ValueError("funds facts must contain one representative per event")
            seen.add(event_id)
        try:
            amount = money_decimal(row.get("amount"))
        except ValueError:
            amount = None
        if status == "confirmed" and (amount is None or amount <= 0 or row.get("currency") not in _ISO_CURRENCIES):
            raise ValueError("invalid confirmed funds amount")
        facts.append({**row, "amount": amount})
    if len(cases) > 1:
        raise ValueError("funds projection requires one case scope")
    return _row_summary(facts)


def _selected_id(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 < value < 2 ** 63 else None
    if isinstance(value, str) and value.isascii() and value.isdigit():
        number = int(value)
        return number if 0 < number < 2 ** 63 else None
    return None


def _same_amount(left, right):
    if left is None or right is None:
        return left is right
    try:
        return money_decimal(left) == right
    except ValueError:
        return False


def _as_input_money(value, original):
    return format(value, ".2f") if value is not None and isinstance(original, str) else value


def from_rows(case_id, rows):
    """Return (representative copies, funds) for loaded rows of this case only.

    No returned member list grows with an event. Original detail fields remain
    on the representative; observation_* retain its original ledger values.
    Hold the caller's snapshot lock while loading rows and calling this helper.
    """
    case_id = _selected_id(case_id)
    if case_id is None:
        raise ValueError("invalid funds case scope")
    selected = {}
    for row in rows:
        flow_id = _selected_id(row.get("id"))
        if flow_id is not None and (row.get("case_id") is None or _selected_id(row["case_id"]) == case_id):
            selected.setdefault(flow_id, row)
    grouped = {}
    with db._lock:
        ids = sorted(selected)
        for start in range(0, len(ids), CHUNK):
            query = cte("select * from flows where case_id=? and id in (select unnest(?::bigint[]))")
            observed = db.q_raw(query + "select id,amount,currency,balance,fact_status,fact_event_id,fact_amount,fact_currency,fact_time_pending,fact_pending_reference,fact_pair_pending,fact_single_posting "
                                "from selected_observations order by id", [case_id, ids[start:start + CHUNK]])
            for fact in observed:
                original = selected[fact["id"]]
                if (not _same_amount(original.get("amount"), fact["amount"])
                        or original.get("currency") != fact["currency"]
                        or "balance" in original and not _same_amount(original["balance"], fact["balance"])):
                    raise ValueError("loaded funds rows do not match the current selection snapshot")
                key = ("event", fact["fact_event_id"]) if fact["fact_status"] == "confirmed" else ("observation", fact["id"])
                if key in grouped:
                    grouped[key]["observation_count"] += 1
                    continue
                grouped[key] = {**fact, "amount": fact["fact_amount"], "currency": fact["fact_currency"],
                                "observation_count": 1}
        facts = sorted(grouped.values(), key=lambda row: row["id"])
        funds = _row_summary(facts)
        canonical = []
        for fact in facts:
            original = selected[fact["id"]]
            canonical.append({**original, "fact_status": fact["fact_status"], "fact_event_id": fact["fact_event_id"],
                "observation_count": fact["observation_count"], "observation_amount": original["amount"],
                "observation_currency": original["currency"], "observation_balance": original.get("balance"),
                "amount": _as_input_money(fact["amount"], original["amount"]), "currency": fact["currency"],
                "balance": None if fact["fact_status"] == "confirmed" and not fact["fact_single_posting"] else original.get("balance"),
                "fact_time_pending": fact.get("fact_time_pending", False), "fact_pending_reference": fact.get("fact_pending_reference"),
                "fact_pair_pending": fact.get("fact_pair_pending", False), "fact_single_posting": fact["fact_single_posting"]})
        pending_counts = {}
        for row in canonical:
            reference = row.get("fact_pending_reference")
            if reference:
                pending_counts[reference] = pending_counts.get(reference, 0) + 1
        for row in canonical:
            if row.get("fact_pending_reference"):
                row["fact_time_pending"] = pending_counts[row["fact_pending_reference"]] > 1
        return canonical, funds


def public_fields(funds):
    """Legacy display fields expose only exact values; ranges remain in funds."""
    totals = funds["totals"]
    return {"cnt": funds["count"]["lower"] if funds["count"]["lower"] == funds["count"]["upper"] else None,
            "total": "0.00" if not funds["observation_count"] else totals[0]["amount"] if funds["status"] == "exact" and len(totals) == 1 and totals[0]["currency"] is not None else None,
            "amount_totals": [{"currency": row["currency"], "amount": row["amount"]} for row in totals]}


def directional_fields(incoming, outgoing):
    result = {"in_funds": incoming, "out_funds": outgoing}
    for direction, funds in (("in", incoming), ("out", outgoing)):
        fields = public_fields(funds)
        result.update({direction + "_cnt": fields["cnt"], direction + "_amt": fields["total"],
                       direction + "_totals": fields["amount_totals"],
                       direction + "_observation_count": funds["observation_count"]})
    currencies = {item["currency"] for funds in (incoming, outgoing) for item in funds["totals"]}
    unknown_in = any(item["currency"] is None for item in incoming["totals"])
    unknown_out = any(item["currency"] is None for item in outgoing["totals"])
    result["currency_totals"] = [{"currency": currency,
        "in_amt": next((item["amount"] for item in incoming["totals"] if item["currency"] == currency), None if unknown_in else "0.00"),
        "out_amt": next((item["amount"] for item in outgoing["totals"] if item["currency"] == currency), None if unknown_out else "0.00")}
        for currency in sorted(currencies, key=lambda item: item or "")]
    return result


def source_summary_query(source_sql, group_by=(), *, rollup_groups=None, canonical=False):
    """Exact selected amounts; confirmed events collapse only in canonical mode.

    Unconfirmed observations remain separate, without asserting that proximity
    proves one event or that an observed amount proves settlement. This is a
    direct sum over selected records, never an event interval's upper bound.
    Pending links retain original display values; economic uncertainty is separate.
    """
    if any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name) for name in group_by):
        raise ValueError("invalid source grouping column")
    columns = ",".join(group_by)
    prefix = columns + "," if columns else ""
    grouping_sets = f"({columns}),({prefix}source_currency)"
    marker = ""
    if rollup_groups is not None:
        if not group_by or any(not set(group) <= set(group_by) for group in rollup_groups):
            raise ValueError("rollup keys must belong to selected groups")
        grouping_sets = ",".join("(" + ",".join(group) + ")" for keys in rollup_groups
                                 for group in (tuple(keys), (*keys, "source_currency")))
        marker = f"grouping_id({columns}) _account_group,"
    relation = "ordinary_facts" if canonical else "_funds_selected"
    money = "amount"
    pending = "case when fact_time_pending or fact_pair_pending then 1 else 0 end" if canonical else "0"
    pending_pair = "case when fact_pair_pending then 1 else 0 end" if canonical else "0"
    return cte(source_sql) + f""", _source_grouped as (
        select *,coalesce(currency,'') source_currency,{money} _source_selected_amount,{pending} _source_pending,{pending_pair} _source_pair_pending,
            count(distinct case_id) over () _source_cases,
            bool_or(case_id is null) over () _source_missing
        from {relation}
    ) select {prefix}{marker}grouping(source_currency) is_total,source_currency,
        coalesce(max(_source_cases),0) case_count,bool_or(_source_missing) missing_case,
        count(*) source_count,count(_source_selected_amount) known_amounts,sum(_source_selected_amount) source_amount,sum(_source_pending) pending_observation_count,
        sum(_source_pair_pending) pending_pair_observation_count
        from _source_grouped group by grouping sets ({grouping_sets})"""


def decode_source_summary(rows):
    meta = next(row for row in rows if row["is_total"])
    if meta.get("case_count", 0) > 1 or meta.get("missing_case", False):
        raise ValueError("source statistics require one case scope")
    totals = [{"currency": row["source_currency"],
               "amount": format(row["source_amount"], ".2f") if row["known_amounts"] == row["source_count"] else None}
              for row in sorted((row for row in rows if not row["is_total"]), key=lambda row: row["source_currency"] or "")]
    return {**({"pending_observation_count": int(meta["pending_observation_count"])} if meta.get("pending_observation_count") else {}),
            **({"pending_pair_observation_count": int(meta["pending_pair_observation_count"])} if meta.get("pending_pair_observation_count") else {}),
            "cnt": int(meta["source_count"]),
            "total": "0.00" if not meta["source_count"] else totals[0]["amount"] if len(totals) == 1 and totals[0]["currency"] else None,
            "amount_totals": totals}


def empty_source_summary():
    return {"cnt": 0, "total": "0.00", "amount_totals": []}


def source_directional_fields(incoming, outgoing):
    result = {}
    for direction, fields in (("in", incoming), ("out", outgoing)):
        result.update({direction + "_cnt": fields["cnt"], direction + "_amt": fields["total"],
                       direction + "_totals": fields["amount_totals"]})
    currencies = {row["currency"] for fields in (incoming, outgoing) for row in fields["amount_totals"]}
    result["currency_totals"] = [{"currency": currency,
        "in_amt": next((row["amount"] for row in incoming["amount_totals"] if row["currency"] == currency), "0.00"),
        "out_amt": next((row["amount"] for row in outgoing["amount_totals"] if row["currency"] == currency), "0.00")}
        for currency in sorted(currencies)]
    return result
