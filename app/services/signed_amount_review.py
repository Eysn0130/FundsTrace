"""Read signed amounts from the registered, active bank table."""
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import re

from .. import db, table_import
from . import trial_pair_comparison


_DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_EXAMPLES = 8


def review(case_id, source_id, member_path):
    with db.read_lock():
        material = trial_pair_comparison.read_registered_feedback_table(int(case_id), source_id)
    if material is None or material["member_path"] != member_path:
        return None

    amount_counts, directions, references = Counter(), Counter(), Counter()
    groups = defaultdict(lambda: {"negative": [], "positive": []})
    for position, cells in material["rows"].items():
        if position == 1:
            continue
        if len(cells) != len(material["headings"]):
            amount_counts["blank"] += 1
            continue
        raw = cells[3].strip()
        ref = cells[20].strip()
        if ref:
            references[ref] += 1
        if not raw:
            amount_counts["blank"] += 1
            continue
        try:
            amount = Decimal(raw) if len(raw) <= 128 and _DECIMAL.fullmatch(raw) else None
        except InvalidOperation:
            amount = None
        if amount is None:
            amount_counts["unreadable"] += 1
            continue
        if not amount:
            amount_counts["zero"] += 1
            continue
        sign = "negative" if amount < 0 else "positive"
        amount_counts[sign] += 1
        marker = cells[5].strip()
        if sign == "negative":
            directions[marker if table_import._enum(marker) in table_import._IN | table_import._OUT else ""] += 1
            if cells[10].strip() and not any(cells[column].strip() for column in (6, 8, 9)):
                amount_counts["negative_bank_field_only"] += 1
        _, digits, exponent = amount.as_tuple()
        extra_places = -exponent - 2
        if extra_places > 0 and any(digits[-extra_places:]):
            amount_counts["finer_than_cent"] += 1
            continue
        raw_currency = cells[12].strip()
        currency = table_import._CURRENCY.get(raw_currency, raw_currency.upper())
        if currency in table_import._ISO_CURRENCIES and trial_pair_comparison._matching_reference(ref, ref):
            key = ref, currency, amount.copy_abs()
            groups[key][sign].append({"source_row": position, "raw_amount": cells[3], "direction_marker": marker})

    if not amount_counts["negative"]:
        return None
    candidates = []
    for (ref, _, _), rows in groups.items():
        if not rows["negative"] or not rows["positive"]:
            continue
        selected = rows["negative"] + rows["positive"]
        candidates.append({
            "negative_rows": rows["negative"][:_EXAMPLES], "positive_rows": rows["positive"][:_EXAMPLES],
            "negative_count": len(rows["negative"]), "positive_count": len(rows["positive"]),
            "other_same_reference_count": references[ref] - len(selected),
            "own_account_missing": any(not material["rows"][row["source_row"]][1].strip() for row in selected),
        })
    candidates.sort(key=lambda group: group["negative_rows"][0]["source_row"])
    return {"case_id": str(case_id), "source_id": source_id, "member_path": member_path,
            **{key + "_count": amount_counts[key] for key in
               ("negative", "positive", "zero", "blank", "unreadable", "finer_than_cent",
                "negative_bank_field_only")},
            "negative_directions": [{"marker": marker, "count": count} for marker, count in sorted(directions.items())],
            "candidate_group_count": len(candidates), "candidate_groups": candidates[:_EXAMPLES]}
