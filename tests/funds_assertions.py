"""Independent expectations for synthetic observations without event proof.

Callers supply hand-derived counts and per-currency upper bounds. No production
projection code or database query is used to derive expected results.
"""


def unresolved_funds(count, *totals):
    """Each total is (currency, upper); None means evidence cannot bound it."""
    return {"policy": "evidence-bounded-funds-v1",
            "status": "unknown" if any(upper is None for _, upper in totals) else "bounded" if count else "exact",
            "observation_count": count, "confirmed_event_count": 0,
            "unresolved_observation_count": count, "count": {"lower": 0, "upper": count},
            "totals": [{"currency": currency, "amount": "0.00" if upper == "0.00" else None,
                        "lower": "0.00", "upper": upper} for currency, upper in totals]}
