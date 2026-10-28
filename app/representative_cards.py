"""Scope-bound card labels beside stable accounting identities.

Only endpoint evidence in selected observations supplies a temporal label.
Association fallback is explicitly non-temporal. Original observation values,
ledger IDs and source evidence are never rewritten by this display projection.
"""
from __future__ import annotations

from collections import defaultdict
import json

from . import db


def decorate(case_id, rows, *, source_sql, params=None, key="account"):
    """Decorate a batch in one selected scope; callers hold the database lock.

    ``source_sql`` is trusted application SQL returning original effective-flow
    columns. Same-second ordering belongs only to this entity's own ledger.
    """
    ids = sorted({row.get(key) for row in rows if row.get(key)})
    if not ids:
        return rows
    # SQL keeps the latest dated cards, or undated cards if no dated one exists.
    # No case-wide source JSON or per-node scan is needed to choose a label.
    # Card evidence needs original scoped observations, not event-money
    # aggregation. DISTINCT below removes repeated source joins by endpoint.
    query = f"with _card_source as materialized ({source_sql})" + """, _card_observations as (
        select distinct ep.entity_id,cast(f.id as varchar) id,f.trade_time,
            f.self_account,ep.endpoint_card card
        from _card_source f
        join resolved_endpoints ep on ep.case_id=f.case_id and ep.flow_id=f.id
        where f.case_id=? and ep.entity_id in (select unnest(?::varchar[]))
          and ep.state='resolved' and ep.card_state='confirmed' and ep.endpoint_card<>''
          and case ep.role when 'payer' then f.payer_account when 'payee' then f.payee_account
              when 'self' then f.self_account end=ep.entity_id
    ), _card_latest as (
        select * from _card_observations
        qualify trade_time=max(trade_time) over(partition by entity_id)
            or trade_time is null and max(trade_time) over(partition by entity_id) is null
    ) select * from _card_latest
        qualify count(distinct card) over(partition by entity_id)>1
            or row_number() over(partition by entity_id order by id)=1
        order by entity_id,card,id"""
    # Named source parameters cannot mix with positional endpoint parameters.
    if isinstance(params, dict):
        query = query.replace("where f.case_id=? and ep.entity_id in (select unnest(?::varchar[]))",
                              "where f.case_id=$_cards_case_id and ep.entity_id in (select unnest($_cards_ids::varchar[]))")
        query_params = {**params, "_cards_case_id": case_id, "_cards_ids": ids}
    else:
        query_params = [*(params or []), case_id, ids]
    candidates = defaultdict(list)
    for item in db.q_raw(query, query_params):
        candidates[item["entity_id"]].append(item)
    selected = {}
    tied_owned_ids = {item["id"] for entity, items in candidates.items() if len({item["card"] for item in items}) > 1
                      and all(item["self_account"] == entity and item["trade_time"] is not None for item in items)
                      for item in items}
    from . import event_resolution, principal_ledger
    metadata = event_resolution.ledger_metadata(case_id, sorted(tied_owned_ids)) if tied_owned_ids else {}
    for entity, items in candidates.items():
        cards = sorted({item["card"] for item in items})
        tied, order = False, "timestamp"
        chosen = items[0]["card"]
        if any(item["trade_time"] is None for item in items):
            tied, order = len(cards) > 1, "undated"
        elif len(cards) > 1:
            # The same observation cannot settle distinct endpoint-card claims.
            unique = {item["id"]: item for item in items}
            ordered = (principal_ledger.source_order(list(unique.values()), metadata)
                       if len(unique) == len(items) and all(item["self_account"] == entity for item in items) else None)
            if ordered is None:
                tied, order = True, "tied"
            else:
                chosen, order = ordered[-1]["card"], "source_posting"
        selected[entity] = {"display_account": chosen,
            "display_basis": "tied_transaction_cards" if tied else "transaction_card",
            "display_card_tied": tied, "display_card_candidates": cards,
            "display_card_order": order}
    # Reliable relations were established during the identity replay. The full
    # cards list deliberately retains conflicting declarations for inspection.
    fallback_ids = [entity for entity in ids if entity not in selected]
    details = {item["entity_id"]: json.loads(item["details_json"]) for item in db.q_raw(
        "select entity_id,details_json from resolution_entities where case_id=? and entity_id in (select unnest(?::varchar[]))",
        [case_id, fallback_ids])} if fallback_ids else {}
    for row in rows:
        entity = row.get(key)
        if entity in selected:
            row.update(selected[entity])
        elif entity in details:
            item = details[entity]
            reliable = item.get("reliable_cards", [])
            row.update(display_account=reliable[0] if reliable else item["account_number"],
                       display_basis="associated_card" if reliable else "account_number",
                       display_card_tied=False, display_card_candidates=reliable,
                       display_card_order="association" if reliable else "none")
    return rows
