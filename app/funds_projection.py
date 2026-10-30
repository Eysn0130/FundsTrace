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
from .money import money_decimal
from .table_import import _ISO_CURRENCIES

POLICY = "evidence-bounded-funds-v1"
CHUNK = 4096
_CURRENCIES_SQL = "(" + ",".join("'" + value + "'" for value in sorted(_ISO_CURRENCIES)) + ")"


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
    ), selected_observations as (
        select s.*,
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
            case when fact_status='confirmed' then null else balance end balance
        from _funds_ranked where _funds_rank=1
    ) """


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


def page_with_summary(source_sql, params, page_size, offset, *, observations=False):
    """Share a request's database selection; return only its page and totals.

    The two independent scalar aggregates keep totals on an empty/overflow
    page. Structs preserve Decimal and list types without a JSON conversion or
    a union that coerces page values to the summary's column types.
    """
    grouped, aggregate = _summary_parts()
    relation = "selected_observations" if observations else "selected_facts"
    query = cte(source_sql, include_ids=not observations) + grouped + f""",
        _funds_summary as ({aggregate}),
        _funds_page as (select * from {relation}
            order by trade_time desc nulls last,id desc limit ? offset ?)
        select (select list(p order by trade_time desc nulls last,id desc) from _funds_page p) page,
               (select list(s) from _funds_summary s) summary"""
    with db._lock:
        result = db.q_raw(query, [*params, page_size, offset])[0]
        return result["page"] or [], decode_summary(result["summary"])


def decode_summary(aggregates):
    """Decode one group's rows returned by summary_query, without requerying."""
    return _result(aggregates)


def summary(source_sql, params=None):
    """Aggregate a single selected case in SQL; return only small currency rows."""
    with db._lock:
        return decode_summary(db.q_raw(summary_query(source_sql), params))


def grouped_summary(source_sql, params=None, group_by=()):
    """One SQL read returns bounded funds per direction/pair, without ID lists."""
    with db._lock:
        rows = db.q_raw(summary_query(source_sql, group_by), params)
        groups = {}
        for row in rows:
            groups.setdefault(tuple(row[name] for name in group_by), []).append(row)
        return {key: decode_summary(values) for key, values in groups.items()}


def account_summaries(source_sql, params=None):
    """Compute total, payer, payee and pair funds from one selected-facts scan.

    Grouping flags distinguish an absent key from a genuine null endpoint.
    Aggregation stays in DuckDB; no observation-sized Python list or cache.
    """
    with db._lock:
        rows = db.q_raw(summary_query(source_sql, ("payer_account", "payee_account"),
            rollup_groups=((), ("payer_account",), ("payee_account",), ("payer_account", "payee_account"))), params)
        groups = {}
        for row in rows:
            key = (row["_account_group"], row["payer_account"], row["payee_account"])
            groups.setdefault(key, []).append(row)
        return {key: decode_summary(values) for key, values in groups.items()}


def profile_summaries(source_sql, params, account):
    """Trim the selected own key while retaining original counterparty keys."""
    source = f"""select f.*,trim(payer_account)=? _own_payer,trim(payee_account)=? _own_payee
        from ({source_sql}) f"""
    columns = ("payer_account", "payee_account", "_own_payer", "_own_payee")
    groupings = ((), ("_own_payer",), ("_own_payee",),
                 ("payer_account", "_own_payee"), ("payee_account", "_own_payer"))
    with db._lock:
        rows = db.q_raw(summary_query(source, columns, rollup_groups=groupings), [account, account, *params])
        groups = {}
        for row in rows:
            flag = row["_account_group"]
            # Omitted-key bits: 15 total, 14 incoming, 13 outgoing,
            # 6 incoming by original payer, 9 outgoing by original payee.
            if flag != 15 and not row["_own_payee" if flag in (6, 14) else "_own_payer"]:
                continue
            key = (flag, row["payer_account"] if flag == 6 else row["payee_account"] if flag == 9 else None)
            groups.setdefault(key, []).append(row)
        return {key: decode_summary(values) for key, values in groups.items()}


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
            observed = db.q_raw(query + "select id,amount,currency,balance,fact_status,fact_event_id,fact_amount,fact_currency "
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
                "balance": None if fact["fact_status"] == "confirmed" else original.get("balance")})
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
