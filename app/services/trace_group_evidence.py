"""Read-only evidence for a group of credits and debits in one accounting ledger."""
from datetime import time, timezone
from itertools import groupby

from .. import db, event_resolution, principal_ledger, transaction_time


def _positive_units(value):
    if value is None:
        return None
    try:
        result = principal_ledger.units(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return result if result > 0 else None


def _money(value):
    return None if value is None else principal_ledger.money(value)


def _direction(row, account):
    payer, payee = row.get("payer_account"), row.get("payee_account")
    if row.get("self_account") != account or not payer or not payee or payer == payee:
        return None
    return "in" if payee == account else "out" if payer == account else None


def _ordered(rows, metadata):
    if any(row["trade_time"] is None for row in rows):
        return None
    ordered = []
    for _, group in groupby(rows, key=lambda row: row["trade_time"]):
        same_time = list(group)
        proven = principal_ledger.source_order(same_time, metadata)
        if proven is None:
            return None
        ordered.extend(proven)
    return ordered


def _other_ledger_balances(case_id, rows):
    ids = list({row["id"] for row in rows})
    if not ids:
        return {}
    # Only already proved, effective opposite ledger observations may supply
    # another account's balance. Same-side observation balances never do.
    peers = db.q_raw("""select a.flow_id::varchar anchor_id, p.id::varchar id,
                    p.self_account, p.balance
        from event_members a join event_members b
          on b.case_id=a.case_id and b.event_id=a.event_id and b.status='confirmed'
        join effective_flows f on f.case_id=a.case_id and f.id=a.flow_id
        join effective_flows p on p.case_id=b.case_id and p.id=b.flow_id
        where a.case_id=? and a.status='confirmed' and a.flow_id in (select unnest(?::bigint[]))
          and p.self_account<>f.self_account and p.self_account in (f.payer_account,f.payee_account)
          and f.self_account in (f.payer_account,f.payee_account)
          and p.balance is not null order by a.flow_id,p.id""", [case_id, ids])
    metadata = event_resolution.ledger_metadata(case_id, [int(row["id"]) for row in peers])
    grouped = {}
    for row in peers:
        grouped.setdefault(row["anchor_id"], []).append(row)
    return {key: {"flow_id": entry["id"], "account": entry["self_account"], "amount": str(entry["balance"]),
                  "book": metadata.get(entry["id"], {}).get("balance_kind") == "post_book"}
            for key, values in grouped.items() if len(values) == 1 for entry in values}


def prepare(case_id, account, observations):
    stamps = [transaction_time.clock(row) for row in observations]
    currencies = {row.get("currency") or "" for row in observations}
    kinds = {transaction_time.basis(row) for row in observations}
    ids = [row["id"] for row in observations]
    side_balances = _other_ledger_balances(case_id, observations)
    result = {"status": "unknown", "reasons": [], "rows": [], "metadata": {},
              "side_balances": side_balances, "currency_gaps": []}
    if len(kinds) != 1 or None in stamps:
        return {**result, "reasons": ["mixed_or_missing_time"],
                "currency_gaps": principal_ledger.source_currency_gaps(case_id, account, ids)}
    start, end = min(stamps), max(stamps)
    if kinds == {"utc"}:
        start, end = start.replace(tzinfo=timezone.utc), end.replace(tzinfo=timezone.utc)
    else:
        # A date-only record may have been materialized at midnight although
        # it belongs anywhere on that day. Read both boundary days in full.
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
    result["currency_gaps"] = principal_ledger.source_currency_gaps(case_id, account, ids, start=start, end=end)
    if len(currencies) != 1 or next(iter(currencies)).strip().casefold() in principal_ledger.UNKNOWN_CURRENCIES:
        return {**result, "reasons": ["currency_evidence_unknown"]}
    result.update(principal_ledger.read(case_id, account, currencies.pop(), start, end))
    result["metadata"] = event_resolution.ledger_metadata(case_id, [int(row["id"]) for row in result["rows"]])
    return result


def _own_balances(rows, metadata):
    if not rows:
        return None, None, False
    first, last = rows[0], rows[-1]
    book = lambda row: metadata.get(row["id"], {}).get("balance_kind") == "post_book"
    try:
        opening = principal_ledger.units(first["balance"]) if book(first) and first["balance"] is not None else None
        closing = principal_ledger.units(last["balance"]) if book(last) and last["balance"] is not None else None
        if opening is None or type(first["amount"]) is not int or not first.get("direction"):
            return None, closing, False
        opening -= first["amount"] if first["direction"] == "in" else -first["amount"]
        available = opening
        if opening < 0:
            return None, closing, False
        for row in rows:
            if type(row["amount"]) is not int or row.get("direction") not in {"in", "out"}:
                return opening, closing, False
            available += row["amount"] if row["direction"] == "in" else -row["amount"]
            if available < 0 or (book(row) and row["balance"] is not None
                                 and principal_ledger.units(row["balance"]) != available):
                return opening, closing, False
    except (TypeError, ValueError, ArithmeticError):
        return None, None, False
    return opening, closing, closing is not None and available == closing


def _competing(rows, selected, unproved):
    result = {"in": {"count": 0, "units": 0}, "out": {"count": 0, "units": 0}}
    unknown = 0
    for row in rows:
        if row["id"] in selected:
            continue
        direction, amount = row.get("direction"), row["amount"]
        if direction not in result or type(amount) is not int or row["id"] in unproved:
            unknown += 1
            continue
        result[direction]["count"] += 1
        result[direction]["units"] += amount
    return {key: {"count": entry["count"], "amount": _money(entry["units"])} for key, entry in result.items()}, unknown


def _joint_share(rows, opening, sources, targets, *, maximize):
    """Extremize total group contribution in one reconciled recorded ledger."""
    tagged, other, total = 0, opening, 0
    for row in rows:
        amount, key = row["amount"], row["id"]
        if row["direction"] == "in":
            tagged += amount if key in sources else 0
            other += amount if key not in sources else 0
        else:
            # For the maximum, spend tagged money on a target and preserve it
            # at other debits; reversing these preferences attains the minimum.
            target = key in targets
            spend = min(amount, tagged) if target == maximize else max(0, amount - other)
            tagged -= spend
            other -= amount - spend
            if target:
                total += spend
    return total


def evidence(account, incoming, outgoing, interval):
    selected = incoming + outgoing
    selected_ids = {str(row["id"]) for row in selected}
    checks = {"ledger_roles": all(_direction(row, account) == side for side, items in (("in", incoming), ("out", outgoing))
                                  for row in items)}
    currencies = {row.get("currency") or "" for row in selected}
    currency = next(iter(currencies)) if len(currencies) == 1 else ""
    unproved_ids = {row["id"] for row in interval["currency_gaps"]}
    unproved_selected = bool(selected_ids & unproved_ids)
    intake = [_positive_units(row["amount"]) for row in incoming]
    outtake = [_positive_units(row["amount"]) for row in outgoing]
    checks["currency_amount"] = (not unproved_selected and
        currency.strip().casefold() not in principal_ledger.UNKNOWN_CURRENCIES and None not in intake + outtake)
    times = [transaction_time.clock(row) for row in selected]
    comparable = None not in times and len({transaction_time.basis(row) for row in selected}) == 1
    start, end = (min(times), max(times)) if comparable else (None, None)
    time_basis = transaction_time.basis(selected[0]) if comparable else None
    end_date_unknown = comparable and time_basis == "unqualified" and end.time() == time.min
    unproved_interval = comparable and any(
        row["stamp"] is None or row["basis"] != time_basis or start <= row["stamp"] <= end
        or (end_date_unknown and row["stamp"].date() == end.date())
        or (time_basis == "unqualified" and row["stamp"].date() == start.date()
            and row["stamp"].time() == time.min)
        for row in interval["currency_gaps"])
    uncertain_start = comparable and time_basis == "unqualified" and any(
        row["trade_time"] is not None and row["trade_time"].date() == start.date()
        and row["trade_time"] < start and row["trade_time"] == start.replace(hour=0, minute=0, second=0, microsecond=0)
        for row in interval["rows"])
    uncertain_end = end_date_unknown and any(
                         row["trade_time"] is not None and row["trade_time"].date() == end.date() and row["trade_time"] > end
                         for row in interval["rows"])
    rows = [row for row in interval["rows"] if comparable and (row["trade_time"] is None or start <= row["trade_time"] <= end)]
    metadata = interval["metadata"]
    date_order_unknown = uncertain_start or uncertain_end or principal_ledger.has_day_only_order_conflict(rows)
    ordered = None if date_order_unknown else _ordered(rows, metadata)
    positions = {row["id"]: index for index, row in enumerate(ordered or [])}
    source_ids = [str(row["id"]) for row in incoming]
    target_ids = [str(row["id"]) for row in outgoing]
    located = selected_ids <= positions.keys()
    earliest_debit_has_credit = located and min(positions[key] for key in source_ids) < min(positions[key] for key in target_ids)
    last_credit_has_debit = located and max(positions[key] for key in source_ids) < max(positions[key] for key in target_ids)
    checks["order"] = bool(earliest_debit_has_credit and last_credit_has_debit)
    future_inputs = located and min(positions[key] for key in source_ids) >= max(positions[key] for key in target_ids)
    if located:
        rows = ordered[min(positions[key] for key in selected_ids):max(positions[key] for key in selected_ids) + 1]
    competitors, unknown = _competing(rows, selected_ids, unproved_ids) if rows else (None, 0)
    opening, closing, reconciled = _own_balances(rows if located else [], metadata)
    # prepare() reads a union of candidate intervals. This reason alone is
    # re-evaluated for the card's own narrower interval using the source gaps.
    ledger_ready = interval["status"] == "ready" or (
        interval["status"] == "unknown" and interval["reasons"] == ["source_currency_unproved"])
    checks["own_balance"] = bool(reconciled and ledger_ready and not unproved_interval)
    nearby = interval["side_balances"]
    opposite = {side: [nearby[key] for key in ids if key in nearby] for side, ids in
                (("before", source_ids), ("after", target_ids))}
    checks["adjacent_balances"] = all(len(opposite[side]) == len(ids) and all(item["book"] for item in opposite[side])
                                       for side, ids in (("before", source_ids), ("after", target_ids)))
    checks["interval_records"] = bool(located and ledger_ready and not unknown and not unproved_interval)
    pattern = ("整进" if len(incoming) == 1 else "散进") + ("整出" if len(outgoing) == 1 else "散出")
    amounts = f"入账 {len(incoming)} 笔、出账 {len(outgoing)} 笔"
    if checks["currency_amount"]:
        amounts += f"（{currency} {_money(sum(intake))} → {_money(sum(outtake))}）"
    balance = (f"本方首笔前余额 {currency} {_money(opening)}，末笔后余额 {_money(closing)}"
               if checks["own_balance"] else "本方区间的账面余额衔接待核")
    time_note = "先后可回查" if checks["order"] else "先后或组内资金接续待核"
    competition = "区间其他收支待核"
    if competitors:
        prefix = "区间另记录" if checks["interval_records"] else "本次读取中另见"
        competition = (f"{prefix}入 {competitors['in']['count']} 笔（{competitors['in']['amount']}）、"
                       f"出 {competitors['out']['count']} 笔（{competitors['out']['amount']}）")
    peers = (f"前一侧账户可核账面余额 {sum(item['book'] for item in opposite['before'])}/{len(incoming)}，"
             f"后一侧 {sum(item['book'] for item in opposite['after'])}/{len(outgoing)}")
    reasons = [reason for reason in interval["reasons"] if reason != "source_currency_unproved"]
    if date_order_unknown:
        reasons.append("boundary_date_precision_unknown")
        time_note = "边界日有午夜记录，原始时刻待核"
    if future_inputs:
        reasons.append("selected_debits_precede_all_credits")
        time_note = "所选出账均早于所选入账，本组来源不能承接这些出账"
    if unproved_selected or unproved_interval:
        reasons.append("source_currency_unproved")
        competition += "，另有原件币种未明的记录"
    mixed_currency = (not unproved_selected and len(currencies) > 1
                      and all(value.strip().casefold() not in principal_ledger.UNKNOWN_CURRENCIES for value in currencies))
    if mixed_currency:
        amounts += "，所选币种不同，分别核对"
        reasons.append("mixed_currency")
    joint = None
    if checks["own_balance"] and checks["currency_amount"] and checks["interval_records"]:
        joint = {bound: _money(_joint_share(rows, opening, set(source_ids), set(target_ids), maximize=is_max))
                 for bound, is_max in (("min", False), ("max", True))}
    share = (f"按已记录账面条件，所选入账在整组选定出账中的可行份额为 {currency} {joint['min']}–{joint['max']}。"
             if joint is not None else "")
    if joint is None and checks["currency_amount"] and sum(intake) < sum(outtake):
        share = f"所选入账合计比出账少 {currency} {_money(sum(outtake) - sum(intake))}。"
    if mixed_currency:
        judgment = "两侧币种不同，需要分别核对资金来源和去向。"
    elif future_inputs:
        judgment = "所选出账早于入账，这组记录无法支持该资金关联。"
    elif joint is not None:
        judgment = f"按已记录账面可关联 {currency} {joint['min']}–{joint['max']}，实际本金归属待核。"
    elif not checks["currency_amount"] or unproved_interval:
        judgment = "所选或区间竞争流水的币种、金额依据不足，本金归属待核。"
    elif not checks["order"]:
        judgment = "流水先后或时间口径待核，暂不能核实本金归属。"
    else:
        judgment = "可回查同一账簿的相关流水，余额或区间收支不足以核实本金归属。"
    values = [str(row["id"]) for row in rows if row["id"] not in selected_ids]
    refs = list(dict.fromkeys([*(str(row["id"]) for row in rows[:1] + rows[-1:]), *values[:6],
                              *(item["flow_id"] for items in opposite.values() for item in items[:2])]))
    return {"score": None if future_inputs or mixed_currency else round(100 * sum(checks.values()) / len(checks)),
            "checks": checks, "pattern": pattern,
            "source_ids": source_ids, "target_ids": target_ids, "currency": currency,
            "observed_in": _money(sum(intake)) if checks["currency_amount"] else None,
            "observed_out": _money(sum(outtake)) if checks["currency_amount"] else None,
            "opening_balance": _money(opening) if checks["own_balance"] else None,
            "closing_balance": _money(closing) if checks["own_balance"] else None,
            "other_entries": competitors, "adjacent_balances": opposite,
            "conditional_group_share": joint, "evidence_row_ids": refs, "other_evidence_limited": len(values) > 6,
            "interval": {"start": transaction_time.text(start), "end": transaction_time.text(end),
                         "basis": time_basis},
            "reasons": reasons, "attribution_status": "unknown",
            "judgment": judgment,
            "basis": f"{pattern}，{amounts}。{time_note}，{balance}。{competition}，{peers}。{share}现实账期完整性和本金归属待核。"}
