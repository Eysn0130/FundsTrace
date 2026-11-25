"""Read-only evidence packets and positional replies for a local adviser."""
from contextlib import closing
from hashlib import file_digest, sha256
import json
import stat
from types import SimpleNamespace
from urllib.parse import urlencode

from .. import bank_posting_pairs, db, event_resolution, feedback_import, source_ledger, table_import, table_source_roles, transaction_time
from ..money import money_decimal
from ..table_mapping import ACCOUNT_CARD_FIELDS, document_number_header, identity_field
from . import import_ready_evidence, imports, trace_group_evidence, trace_review
from .cases import ServiceError


_LIMIT = 8
_SELECTED_ORDER_SOURCE_BYTES = 8 * 1024 * 1024
_SOURCE_ERRORS = {"import_not_found", "source_missing", "source_changed", "unsafe_upload_storage"}
_FIELDS = {"self_account": "本方账号", "self_card": "本方卡号", "self_name": "本方户名",
           "counter_account": "对方账号", "counter_card": "对方卡号", "counter_name": "对方户名",
           "payer_account": "付款账号", "payer_name": "付款户名", "payee_account": "收款账号", "payee_name": "收款户名"}
_SCOPED_NOTE_SHEETS = {"字段说明", "明细口径"}
_GAPS = {"source_role": "原件未证明字段角色", "source_proof": "缺少同笔的独立来源或余额见证",
         "time_basis": "两边时钟或区间原行不能按同一时基比较", "coverage": "未覆盖所需表区或账簿原行",
         "balance_basis": "缺明确账面余额或衔接依据", "currency": "缺同币种或换汇依据"}


def _packet(domain, question, facts, candidates, records, gaps, *, scope_secret=None):
    packet = {"domain": domain, "question": question, "facts": facts, "candidates": candidates,
              "records": records, "gaps": [{"id": gap, "description": _GAPS[gap]} for gap in gaps], "selection": None}
    material = json.dumps([packet, scope_secret], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    packet["packet_sha256"] = sha256(material.encode()).hexdigest()
    return packet


def _candidate(key, description, refs=None, *, refuted=False):
    return {"id": key, "description": description, "refs": refs if refs is not None else [key], "refuted": refuted}


def _finish(packet, reply, *, source_case=None):
    if reply is None:
        return packet
    if reply.packet_sha256 != packet["packet_sha256"]:
        raise ServiceError(409, "assistance_stale", "原件或核对范围已更新，请重新读取")
    records = {row["id"]: row for row in packet["records"]}
    refs = set(reply.refs)
    if len(refs) != len(reply.refs) or not refs <= records.keys():
        raise ServiceError(422, "assistance_reference_outside", "所选依据不属于当前证据包")
    gap = None if reply.gap == "NONE" else reply.gap
    if gap is not None and gap not in {item["id"] for item in packet["gaps"]}:
        raise ServiceError(422, "assistance_reference_outside", "缺口类型不在当前证据包")
    if reply.choice != "DEFER":
        option = next((item for item in packet["candidates"] if item["id"] == reply.choice), None)
        if option is None or not refs.intersection(option["refs"]):
            raise ServiceError(422, "assistance_reference_outside", "提议须有当前候选的原始位置")
        if option["refuted"]:
            raise ServiceError(422, "assistance_refuted", "产品已核明该候选的矛盾")
    if source_case is not None and reply.choice != "DEFER":
        # Validate retained bytes prior to handing off a physical row for review.
        if any(not records[key]["locator"].get("file_url") for key in refs):
            raise ServiceError(422, "assistance_reference_unavailable", "所引位置的原件未能回查")
        source_ids = {records[key]["locator"]["source_id"] for key in refs}
        for source in sorted(source_ids):
            stream, _ = imports.get_source_file(source_case, source)
            with closing(stream):
                pass
    packet["selection"] = {"status": "deferred" if reply.choice == "DEFER" else "review_only",
                           "choice": reply.choice, "refs": reply.refs, "gap": gap}
    return packet


def _import_locator(page, row, column=None):
    return {"member_path": page["member"]["member_path"], "sheet": page["member"]["sheet"],
            "source_row": row, **({"column": column} if column else {})}


def _trial_support_rows(session, manifest, profile, member, header, headings):
    """Read a bounded field-note sheet before filtering to this physical region."""
    entry = next((item for item in manifest["layout_members"] if item["scope"]["member_path"] == member), None)
    if entry is None:
        raise ServiceError(422, "outside_layout_review", "字段说明不属于这份预览原件")
    scope = entry["scope"]
    complete = scope["source_rows"] <= 32 and session["size_bytes"] <= import_ready_evidence.import_layout_trial._MAX_FILE_BYTES
    if not complete:
        return {"member": scope}, [], False
    with imports._source(session, snapshot=True) as source:
        rows = import_ready_evidence._cells(source, session, entry, set(range(1, scope["source_rows"] + 1)),
                                           profile, for_layout=True)
    label = tuple(cell["value"] for cell in rows.get(1, ()) if cell["kind"] == "text")
    scoped_note = label == table_source_roles.SCOPED_FIELD_NOTE_HEADER
    complete = (scope["sheet"] in _SCOPED_NOTE_SHEETS and scoped_note
                or scope["sheet"] == "字段说明" and label == ("字段", "字段说明"))
    if (scoped_note and scope["sheet"] == table_source_roles.CODED_FIELD_NOTE_SHEET
            and json.loads(session["request_json"] or "{}").get(imports._ACCOUNT_USE_CODE_TRIAL) is True):
        typed = {number: [cell["value"] if cell["kind"] == "text" else None for cell in cells]
                 for number, cells in rows.items()}
        if table_source_roles.has_account_use_code_notes(typed):
            selected = [{"source_row": number, "cells": cells} for number, cells in sorted(rows.items())]
            return {"member": scope, "account_use_code_notes": True}, selected[:12], len(selected) <= 12
    prefix = f"{header['sheet']}!"
    table_labels = {cell["value"] for cell in headings}
    selected = [{"source_row": number, "cells": cells} for number, cells in sorted(rows.items())
                if number == 1 or (cells and cells[0]["value"].startswith(prefix)
                                   and ((scoped_note and len(cells) >= 2 and cells[1]["value"] in table_labels)
                                        or any(cells[0]["value"].endswith(" " + title) for title in table_labels)))]
    return {"member": scope}, selected[:12], complete and len(selected) <= 12


def import_assistance(case_id, import_id, body, *, column_trial=False):
    def page(member, row, column):
        return import_ready_evidence.layout(case_id, import_id, SimpleNamespace(
            revision=body.revision, member_path=member, row_start=row, column_start=column))

    main = page(body.member_path, max(1, body.header_row - 1), body.column_start)
    headings = next((row["cells"] for row in main["rows"] if row["source_row"] == body.header_row), [])
    if not headings:
        raise ServiceError(422, "outside_layout_review", "当前表头页没有可阅的物理列")
    member = main["member"]
    last = min((row - 1 for row in member["header_rows"] if row > body.header_row), default=member["source_rows"])
    samples = [(row["source_row"], {cell["column"]: cell for cell in row["cells"]})
               for row in main["rows"] if body.header_row < row["source_row"] <= last][:2]
    records, candidates = [], []
    verdicts = import_ready_evidence.layout_column_verdicts(
        case_id, import_id, body.revision, body.member_path, body.header_row, body.field,
        [head["column"] for head in headings],
    )
    for head in headings:
        column = head["column"]
        cells = [row.get(column) for _, row in samples]
        values = [cell["value"] if cell else None for cell in cells]
        heading = head["value"]
        normalized = table_import._norm(heading)
        role = identity_field(normalized)
        credential = document_number_header(normalized) or table_source_roles.query_condition(heading)
        invalid_type = any(cell and cell["kind"] in {"formula", "error", "boolean", "date"} for cell in cells)
        verdict, _ = verdicts.get(column, ("insufficient", None))
        refuted = (bool(import_ready_evidence.column_role_refutation(body.field, normalized))
                   or body.field in ACCOUNT_CARD_FIELDS and (credential or invalid_type)
                   or verdict == "refuted"
                   or column not in verdicts and role is not None and role != body.field)
        key = f"C{column}"
        records.append({"id": key, "raw": {"heading": heading[:100], "rows": [row for row, _ in samples],
                                           "cells": [value[:100] if value is not None else None for value in values]},
                        "structured": {"role_heading": role, "role_check": verdict, "credential": credential, "invalid_type": invalid_type,
                                       "empty": not any(values),
                                       "text_excerpt": len(heading) > 100 or any(v and len(v) > 100 for v in values)},
                        "locator": _import_locator(main, body.header_row, column)})
        candidates.append(_candidate(key, f"物理第{column}列", refuted=refuted))
    session, manifest, profile = import_ready_evidence._ready(case_id, import_id, body.revision, for_layout=True)
    evidence = None
    if column_trial and body.header_row < last:
        physical = [int(item["id"][1:]) for item in candidates if not item["refuted"]]
        evidence = import_ready_evidence.column_evidence(case_id, import_id, SimpleNamespace(
            revision=body.revision, member_path=body.member_path, header_row=body.header_row,
            column_start=body.column_start, compare_columns=physical,
        ))
        layout = next(item for item in manifest["layout_members"] if item["scope"]["member_path"] == body.member_path)
        parents = next((row["cells"] for row in main["rows"] if row["source_row"] == body.header_row - 1), ())
        parent_header = body.header_row - 1 in layout["document"].get("tier_parent_rows", ())
        parent_names = {cell["column"]: cell["value"] for cell in parents} if parent_header else {}
        parent_names.update(import_ready_evidence.grouped_parent_labels(session, layout, profile, body.header_row))
        profiles = {item["column"]: item for item in evidence["columns"]}
        for record in records:
            info = profiles.get(record["locator"]["column"])
            if not info:
                continue
            record["structured"]["empty"] = not info["nonempty"]
            change_rows = {item["first_difference"] for item in info["comparisons"] if item["first_difference"]}
            witnesses = sorted(info["samples"], key=lambda row: (row["source_row"] not in change_rows, row["source_row"]))
            record["structured"]["column_profile"] = {
                "scanned_rows": evidence["coverage"]["scanned_rows"], "nonempty": info["nonempty"],
                "leading_zero": info["leading_zero"], "kinds": info["kinds"],
                "sample_positions": [[row["source_row"], row["kind"]] for row in witnesses[:6]],
                "comparisons": info["comparisons"],
            }
            if parent := parent_names.get(record["locator"]["column"]):
                record["raw"]["parent_heading"] = parent[:100]
    note_pages = [(main, [row for row in main["rows"] if row["source_row"] < body.header_row],
                   body.column_start == 1 and not main["more_columns"])]
    support_member = body.support_member_path
    if column_trial and support_member is None:
        matches = [item["scope"]["member_path"] for item in manifest["layout_members"]
                   if item["scope"]["sheet"] in _SCOPED_NOTE_SHEETS and item["scope"]["member_path"] != body.member_path]
        if len(matches) == 1:
            support_member = matches[0]
    notes_complete = None
    if support_member and support_member != body.member_path:
        if column_trial:
            support = _trial_support_rows(session, manifest, profile, support_member, member, headings)
            note_pages.append(support)
            notes_complete = support[2]
        else:
            support = page(support_member, 1, 1)
            note_pages.append((support, support["rows"][:4], False))
    for from_page, rows, complete in note_pages:
        for row in rows:
            if any(cell["value"] for cell in row["cells"]):
                cells = [cell for cell in row["cells"] if cell["value"]]
                proof = {"whole_note": complete and all(
                    cell["column"] <= 3 and cell["kind"] == "text" and len(cell["value"]) <= 120 for cell in cells
                )} if column_trial else {}
                records.append({"id": f"N{len(records)}", "raw": {"cells": [
                    cell["value"][:180] for cell in row["cells"][:3]]}, "structured": proof,
                                "locator": _import_locator(from_page, row["source_row"])})
    facts = {key: main[key] for key in ("case_id", "import_id", "revision", "sha256", "parser_version")}
    facts.update(field=body.field, member_path=body.member_path, header_row=body.header_row,
                 column_start=body.column_start, support_member_path=body.support_member_path,
                 header_registered=body.header_row in member["header_rows"], sampled_rows=[row for row, _ in samples],
                 more_columns=main["more_columns"], more_rows=last - body.header_row > len(samples))
    if column_trial:
        local_scope = False
        if body.column_start == 1 and not main["more_columns"] and facts["header_registered"] and last == member["source_rows"]:
            note_row = next((row for row in note_pages[0][1] if row["source_row"] == body.header_row - 1), None)
            note_cells = [cell for cell in note_row["cells"] if cell["value"]] if note_row else []
            if (len(note_cells) == 1 and note_cells[0]["kind"] == "text"
                    and table_source_roles.INLINE_FIELD_NOTE.match(note_cells[0]["value"].strip())):
                before = body.header_row - 2
                front = page(body.member_path, 1, 1)
                earlier = [row for row in front["rows"] if row["source_row"] <= before]
                if (not front["more_columns"] and len(earlier) == before
                        and all(row["source_row"] == number for number, row in enumerate(earlier, 1))):
                    local_scope = True
                    for row in earlier:
                        cells = [cell for cell in row["cells"] if cell["value"]]
                        if cells and (len(cells) != 1 or cells[0]["kind"] != "text"
                                      or not table_source_roles.inline_field_caption(cells[0]["value"])):
                            local_scope = False
                            break
        if local_scope:
            facts["inline_note_scope_complete"] = True
        facts.update(region_last_row=last, resolved_support_member=support_member, support_coverage_complete=notes_complete,
                     column_coverage=evidence["coverage"] if evidence else None)
        if any(page.get("account_use_code_notes") for page, _, _ in note_pages):
            facts["account_use_code_notes"] = True
    question = f"从可见来源中定位有据的{_FIELDS[body.field]}原列。只有同值或猜测时请待核。"
    if (column_trial and body.field == "self_account" and (main["more_columns"] or body.column_start != 1)
            and any(entry["scope"]["member_path"] == body.member_path and entry["scope"]["header_row"] == body.header_row
                    and entry["rules"].get("self_account") for entry in manifest["regions"])):
        from . import import_column_trial

        entry = next(item for item in manifest["layout_members"] if item["scope"]["member_path"] == body.member_path)
        if import_column_trial._wide_own_alias_competes(session, entry, body.header_row):
            facts["wide_role_conflict"] = True
    import_ready_evidence._ready(case_id, import_id, body.revision, for_layout=True)
    return _finish(_packet("import", question, facts, candidates, records, ("source_role", "coverage")), body.reply)


def _flow_rows(case_id, ids):
    return {int(row["id"]): row for row in db.q_raw(
        """select * from effective_visible_flows where case_id=? and id in (select unnest(?::bigint[]))""",
        [case_id, list(ids)])}


def _row_record(case_id, row, source, metadata, available):
    temporal = json.loads(source["time_evidence"]) if source and source["time_evidence"] else {}
    ledger = json.loads(source["ledger_evidence"]) if source and source["ledger_evidence"] else {}
    raw = {key: (str(row[key]) if row.get(key) is not None else None) for key in (
        "trade_time", "trade_time_original", "raw_self_account", "raw_payer_account", "raw_payee_account",
        "amount", "currency", "balance")}
    raw["posted_times"] = [entry["raw"] for entry in temporal.get("values", []) if entry["role"] == "posting"][:3]
    raw["source_direction"] = ledger.get("source_direction")
    cells = json.loads(source["field_evidence"]) if source and source["field_evidence"] else []
    raw["reference_cells"] = [[item["role"], item["column_index"], item["raw"][:120]] for item in cells
                              if item["field"] in {"trade_no", "voucher_reference"} and item["raw"]][:3]
    locator = {}
    if source:
        locator = {key: source[key] for key in ("source_id", "sha256", "member_path", "sheet", "source_row", "record_no")}
        query = urlencode({"member_path": source["member_path"], "source_row": source["source_row"]})
        locator["record_url"] = f"/api/v1/cases/{case_id}/imports/{source['source_id']}/records?{query}"
        locator["file_url"] = f"/api/v1/cases/{case_id}/sources/{source['source_id']}/file" if available else None
    return {"id": f"F{row['id']}", "raw": raw, "structured": {"clock_basis": transaction_time.basis(row),
            "book_direction": ledger.get("direction"), "balance_kind": metadata.get("balance_kind"),
            "posting_order": metadata.get("posting_order"),
            "trade_time_utc": str(row["trade_time_utc"]) if row.get("trade_time_utc") is not None else None}, "locator": locator}


def _preceding_leads(rows, subjects, origins=None):
    """Return only unique, earlier observations on the same recorded book."""
    leads = {}
    for subject in subjects:
        root = rows[subject]
        stamp = transaction_time.clock(root)
        if stamp is None or not root.get("self_account"):
            continue
        best, chosen = None, []
        for key, row in rows.items():
            earlier = transaction_time.clock(row)
            if (earlier is None or earlier >= stamp or transaction_time.basis(row) != transaction_time.basis(root)
                    or row.get("self_account") != root["self_account"] or row.get("currency") != root["currency"]):
                continue
            if origins is not None and (not origins.get(subject) or origins.get(key) != origins[subject]
                                        or not root.get("raw_self_account")
                                        or row.get("raw_self_account") != root["raw_self_account"]):
                continue
            if best is None or earlier > best:
                best, chosen = earlier, [key]
            elif earlier == best:
                chosen.append(key)
        if len(chosen) == 1:
            leads[subject] = chosen[0]
    return leads


def _confirmed_witnesses(case_id, anchor_id):
    data = db.q_raw("""select e.evidence_json from event_members m join resolved_events e
        on e.case_id=m.case_id and e.event_id=m.event_id where m.case_id=? and m.flow_id=? and m.status='confirmed'""",
        [case_id, anchor_id])
    if not data:
        return []
    proof = json.loads(data[0]["evidence_json"])
    if proof.get("policy") != bank_posting_pairs.POLICY:
        return []
    source = proof["source_id"]
    available = imports.source_is_available(case_id, source)
    rows = []
    for n, side in enumerate(proof["sides"], 1):
        position = side["previous_source_row"]
        member = proof["member_path"]
        query = urlencode({"member_path": member, "source_row": position})
        rows.append({"id": f"W{n}", "raw": {"book_balance": side["before"]},
                     "structured": {"proved_preceding_balance": True, "posted_row": side["source_row"]},
                     "locator": {"source_id": source, "member_path": member, "source_row": position,
                                 "record_url": f"/api/v1/cases/{case_id}/imports/{source}/records?{query}",
                                 "file_url": f"/api/v1/cases/{case_id}/sources/{source}/file" if available else None}})
    return rows


def _other_book_postings(case_id, rows, subjects):
    """Locate separate book entries whose opposite ledger still needs review."""
    anchor = rows[subjects[0]]
    comparable = ("payer_account", "payee_account", "amount", "currency")
    books = {rows[key]["self_account"] for key in subjects}
    candidates = sorted(key for key, row in rows.items() if key not in subjects and row["self_account"] in books
                        and all(row[field] == anchor[field] for field in comparable))
    if not candidates:
        return []
    memberships = {int(row["flow_id"]): row for row in event_resolution.members(case_id, candidates)}
    ids = [*subjects, *(key for key in candidates if memberships.get(key, {}).get("status") != "confirmed")]
    declarations = {}
    for item in db.q_raw("""select distinct flow_id,evidence from event_observation_cache
            where case_id=? and effective and flow_id in (select unnest(?::bigint[]))""", [case_id, ids]):
        declarations.setdefault(item["flow_id"], []).append(event_resolution.validate_reference(item["evidence"]))
    ledger = event_resolution.ledger_metadata(case_id, ids)
    open_postings = []
    for key in ids[len(subjects):]:
        candidates_refs = declarations.get(key, ())
        pending_reason = memberships.get(key, {}).get("reason", "")
        if pending_reason in {"settlement_bridge_missing_posting_payer", "settlement_bridge_missing_posting_payee"}:
            if len(candidates_refs) == 1:
                open_postings.append({"ref": f"F{key}", "reference": candidates_refs[0]["reference"], "reason": pending_reason})
            continue
        entry = ledger.get(str(key), {})
        if len(candidates_refs) != 1 or entry.get("balance_kind") != "post_book" or not entry.get("posting_order"):
            continue
        for witness in subjects:
            matched_refs = declarations.get(witness, ())
            other = ledger.get(str(witness), {})
            if (len(matched_refs) != 1 or other.get("balance_kind") != "post_book" or not other.get("posting_order")
                    or rows[key]["self_account"] != rows[witness]["self_account"]
                    or len(entry["source_ids"]) != 1 or entry["source_ids"] != other["source_ids"]
                    or len(set(entry["order_scopes"]) & set(other["order_scopes"])) != 1):
                continue
            left, right = candidates_refs[0], matched_refs[0]
            if left["reference"] == right["reference"] or any(left[k] != right[k] for k in ("issuer", "channel", "business_scope")):
                continue
            earlier, later = sorted((key, witness), key=lambda ref: int(ledger[str(ref)]["posting_order"]))
            before, after = rows[earlier], rows[later]
            direction = trace_group_evidence._direction(after, after["self_account"])
            if (direction and before["balance"] is not None and after["balance"] is not None
                    and int(ledger[str(later)]["posting_order"]) == int(ledger[str(earlier)]["posting_order"]) + 1
                    and after["balance"] - before["balance"] == after["amount"] * (1 if direction == "in" else -1)):
                open_postings.append({"ref": f"F{key}", "witness_ref": f"F{witness}", "reference": left["reference"],
                                      "reason": "book_posting_needs_opposite_ledger"})
                break
    return open_postings


def _pair(case_id, review, anchor_id, *, product=False):
    rows = _flow_rows(case_id, review.flow_ids)
    anchor = rows.get(anchor_id)
    if not anchor:
        raise ServiceError(422, "assistance_anchor_required", "同笔核对需要当前流水原行")
    pair = trace_review.pair_review(case_id, anchor, limit=_LIMIT, product=product)
    ids = [int(key) for key in pair["candidate_ids"] if int(key) != anchor_id and int(key) in rows]
    official = set(ids)
    comparable = ("payer_account", "payee_account", "amount", "currency")
    clock_window = pair.get("source_card_clock_window_seconds")
    if pair["status"] != "confirmed" and clock_window is None:
        ends = {anchor.get("payer_account"), anchor.get("payee_account")}
        for other in sorted(rows):
            value = rows[other]
            if (other != anchor_id and other not in official
                    and all(anchor.get(k) not in ("", None) and anchor[k] == value[k] for k in comparable)
                    and {anchor.get("self_account"), value.get("self_account")} == ends and len(ends) == 2):
                ids.append(other)
    facts = {"anchor_ref": f"F{anchor_id}", "rule_status": pair["status"], "rule_reason": pair["reason"],
             "more_candidates": pair["more"] or len(ids) > _LIMIT,
             "needs_model": pair["status"] != "confirmed"}
    subjects = [anchor_id, *ids[:_LIMIT]]
    separate = _other_book_postings(case_id, rows, subjects) if product and pair["status"] == "confirmed" else []
    origins = None
    if product and pair["status"] != "confirmed":
        origins = {row["flow_id"]: (row["source_id"], row["member_path"], row["header_scope"])
                   for row in trace_review._locators(case_id, rows)}
    leads = _preceding_leads(rows, subjects if product else subjects[:2], origins) if pair["status"] != "confirmed" else {}
    records = _records(case_id, list(dict.fromkeys([*subjects, *leads.values(),
                                                   *(int(item["ref"][1:]) for item in separate[:_LIMIT])])), rows)
    named = {record["id"] for record in records}
    for record in records:
        row = rows[int(record["id"][1:])]
        record["structured"]["official_candidate"] = row["id"] in official
        previous = f"F{leads[row['id']]}" if row["id"] in leads else None
        if previous in named:
            record["structured"]["earlier_book_lead"] = previous
        if row["id"] != anchor_id:
            record["structured"]["same_amount_currency"] = all(row[k] == anchor[k] for k in ("amount", "currency"))
            record["structured"]["same_endpoints"] = all(row[k] == anchor[k] for k in ("payer_account", "payee_account"))
    description = ("同币同额且账卡号原文双向相符，缺同笔凭据"
                   if pair["reason"] == "source_card_text_pending" else "待查候选原行")
    candidates = [_candidate(record["id"], description) for record in records
                  if int(record["id"][1:]) in ids[:_LIMIT] and record["locator"].get("file_url")]
    facts["more_candidates"] |= len(candidates) != len(ids[:_LIMIT])
    facts["needs_model"] &= bool(candidates)
    if "candidate_total" in pair:
        facts["candidate_total"] = pair["candidate_total"]
        facts["candidate_total_exact"] = pair["candidate_total_exact"]
    if clock_window is not None:
        facts["source_card_clock_window_seconds"] = clock_window
        facts["needs_model"] = False
    if ledger := pair.get("ledger_correspondence"):
        if any(item["id"] == "F" + ledger["peer_id"] for item in candidates):
            facts.update(ledger_correspondence=ledger, needs_model=False)
    if (lead := pair.get("account_pair_lead")) and any(int(item["id"][1:]) in official for item in candidates):
        facts.update(account_pair_lead=lead, needs_model=False)
    if pair["status"] == "confirmed":
        settlement = pair.get("proof") if product else None
        if settlement:
            sides = {f"F{row['flow_id']}": row for row in settlement["sides"]}
            for record in records:
                if side := sides.get(record["id"]):
                    record["locator"] = {key: side[key] for key in (
                        "source_id", "sha256", "member_path", "source_row", "record_url", "file_url")}
                    record["structured"]["posting_clock_role"] = side["clock_kind"]
            original = settlement["witness"]
            records.append({"id": "W1", "raw": {"reference": settlement["reference"]},
                            "structured": {"witness_type": "independent_settlement_declaration"},
                            "locator": {key: original[key] for key in (
                                "source_id", "sha256", "member_path", "source_row", "record_url", "file_url")}})
            if scope := settlement.get("scope_witness"):
                records.append({"id": "W2", "raw": {"issuer": settlement["issuer"],
                                                    "channel": settlement["channel"],
                                                    "business_scope": settlement["business_scope"]},
                                "structured": {"witness_type": "independent_settlement_scope"},
                                "locator": {key: scope[key] for key in (
                                    "source_id", "sha256", "member_path", "source_row", "record_url", "file_url")}})
            facts["pair_proof_kind"] = settlement["policy"]
            facts["proof_clock_basis"] = settlement["clock_basis"]
        else:
            records.extend(_confirmed_witnesses(case_id, anchor_id))
    if product:
        if separate:
            facts["open_book_postings"] = separate[:_LIMIT]
            facts["more_open_book_postings"] = len(separate) > _LIMIT
        expected = {int(key) for key in pair["candidate_ids"]} - {anchor_id}
        facts["more_candidates"] |= bool(expected - set(ids))
        if pair["status"] == "confirmed" and expected and not facts["more_candidates"]:
            [member] = event_resolution.members(case_id, [anchor_id])
            facts["pair_receipt"] = {"event_id": member["event_id"], "observations": len(expected) + 1,
                                     "amount": format(anchor["amount"], ".2f"), "currency": anchor["currency"]}
    return facts, candidates, records


def _records(case_id, ids, rows):
    original = {r["flow_id"]: r for r in trace_review._locators(case_id, ids, include_ledger=True)}
    sources = {item["source_id"] for item in original.values()}
    available = {key for key in sources if imports.source_is_available(case_id, key)}
    metadata = event_resolution.ledger_metadata(case_id, ids)
    return [_row_record(case_id, rows[key], original.get(key), metadata.get(str(key), {}),
                        key in original and original[key]["source_id"] in available)
            for key in ids if key in rows]


def _original_stamp(path):
    try:
        value = path.stat(follow_symlinks=False)
    except OSError:
        return None
    if stat.S_ISREG(value.st_mode):
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns
    return None


def _verified_product_original(case_id, source_id, expected_sha):
    try:
        with db._lock:
            original = imports._registered_original(case_id, source_id)
            session = imports._source_session(case_id, source_id)
        if not original["active"] or original["active_batch_id"] is None:
            return None
        if expected_sha is not None and session["sha256"] != expected_sha:
            return None
        path = imports._path(source_id, session["suffix"])
        stamp = _original_stamp(path)
        if stamp is None:
            return None
        imports._source(session)
        return session["sha256"], path, stamp
    except ServiceError as exc:
        if exc.code not in _SOURCE_ERRORS:
            raise
        return None


def verified_product_evidence(case_id, sources):
    checked = ((source, _verified_product_original(case_id, source, digest))
               for source, digest in sorted(sources.items()))
    captured = {source: evidence for source, evidence in checked if evidence is not None}
    return {source: evidence for source, evidence in captured.items()
            if _original_stamp(evidence[1]) == evidence[2]}


def verified_product_originals(case_id, sources):
    return set(verified_product_evidence(case_id, sources))


def _joint_trace_card(case_id, cards, side, groups):
    group_key, anchor_key = ("source_ids", "target_ids") if side == "sources" else ("target_ids", "source_ids")
    requested = set(groups)
    matching = [card for card in cards if requested.intersection(card[group_key])]
    supported = {ref for card in matching for ref in card[group_key]}
    anchors = {frozenset(card[anchor_key]) for card in matching}
    if not requested <= supported or len(anchors) != 1:
        raise ServiceError(422, "assistance_group_outside_card", "所选原行须属于同一张追踪卡及同一本方账簿")
    anchored, = anchors
    sources, targets = (requested, anchored) if side == "sources" else (anchored, requested)
    return _selected_trace_card(case_id, sources, targets)


def _physical_split_direction(row, account, headings, cells):
    core = {name: cells[index] for index, name in enumerate(headings)
            if index < len(cells) and name in table_import._SPLIT_LEDGER_CORE}
    if core.keys() != table_import._SPLIT_LEDGER_CORE:
        return None
    if (core["账户号"]["kind"] not in {"text", "number"} or core["账户号"]["value"].strip() != account
            or core["对方账号"]["kind"] != "text" or core["对方账号"]["value"].strip()):
        return None
    stamp = row["trade_time"]
    if stamp is None or row["trade_time_utc"] is not None:
        return None
    day = stamp.date().isoformat().replace("-", "")
    if any(core[heading]["kind"] != "text" or core[heading]["value"] != value for heading, value in (
        ("交易日期", day), ("交易时间", f"{day} {stamp.time().isoformat()}"),
    )):
        return None
    currency_cell = core["币种"]
    currency = currency_cell["value"].strip()
    if currency_cell["kind"] != "text" or table_import._CURRENCY.get(currency, currency.upper()) != row["currency"]:
        return None
    amounts = {}
    for side, heading in (("in", "收入"), ("out", "支出")):
        cell = core[heading]
        if cell["kind"] not in {"text", "number"}:
            return None
        value, issue = table_import._money(cell["value"], {"factor": 1})
        if issue or value is not None and value < 0:
            return None
        amounts[side] = value
    expected = {"in": ("", account), "out": (account, "")}
    raw_parties = (row["raw_payer_account"] or "", row["raw_payee_account"] or "")
    for direction, other in (("in", "out"), ("out", "in")):
        if (amounts[direction] == row["amount"] and row["amount"] > 0
                and not amounts[other] and raw_parties == expected[direction]):
            return direction
    return None


def _physical_uniform_direction(row, account, headings, cells, *, query_owner=False):
    headings_expected = table_import.UNIFORM_FEEDBACK_35_COLUMNS
    width = len(headings_expected)
    if query_owner:
        if table_source_roles.classify(headings, query_conditions=True) != "transaction_query_only":
            return None
        owner = "查询帐号"
        peers = ("交易对方账卡号", "交易对方名称")
    elif (frozenset(headings[:width]) != headings_expected or any(headings[width:])
          or any(cell["kind"] not in {"text", "number"} or cell["value"].strip() for cell in cells[width:])):
        return None
    else:
        owner = "本方账号"
        peers = ("交易对方账号", "交易对方卡号", "交易对方名称")
    fields = {owner, *peers, "交易时间", "交易金额", "币种", "借贷标志"}
    physical = {heading: cell for heading, cell in zip(headings, cells) if heading in fields}
    if physical.keys() != fields:
        return None
    own = physical[owner]
    own_value = own["value"].strip()
    if (own["kind"] not in ({"text"} if query_owner else {"text", "number"}) or own_value != account
            or query_owner and not (own_value.isascii() and own_value.isdecimal() and own_value.strip("0"))
            or any(physical[field]["kind"] not in {"text", "number"} or physical[field]["value"].strip()
                   for field in peers)):
        return None
    clock = physical["交易时间"]
    stamp = clock["value"].strip()
    if (clock["kind"] != "text" or len(stamp) != 14 or not stamp.isascii() or not stamp.isdigit()
            or row["trade_time"] is None or row["trade_time_utc"] is not None
            or table_import._time(stamp) != row["trade_time"]):
        return None
    amount_cell, currency_cell, marker_cell = (physical[field] for field in ("交易金额", "币种", "借贷标志"))
    if amount_cell["kind"] not in {"text", "number"} or currency_cell["kind"] != "text" or marker_cell["kind"] != "text":
        return None
    amount, issue = table_import._money(amount_cell["value"], {"factor": 1})
    currency = currency_cell["value"].strip()
    if (issue or amount != row["amount"] or not amount or amount <= 0
            or not currency or table_import._CURRENCY.get(currency, currency.upper()) != row["currency"]):
        return None
    marker = table_import._enum(marker_cell["value"])
    raw_parties = (row["raw_payer_account"] or "", row["raw_payee_account"] or "")
    if marker in table_import._IN and raw_parties == ("", account):
        return "in"
    if marker in table_import._OUT and raw_parties == (account, ""):
        return "out"
    return None


def _native_csv_direction(row, account, headings, cells):
    if len(headings) != len(feedback_import.COLUMNS) or set(headings) != set(feedback_import.COLUMNS):
        return None
    named = dict(zip(headings, cells))
    required = ("交易账号", "交易时间", "交易金额", "收付标志", "交易币种", "交易对手账卡号")
    if any(key not in named or named[key]["kind"] not in {"text", "number"} for key in required):
        return None
    own, clock, amount, marker, currency, peer = (named[key]["value"].strip() for key in required)
    amount, issue = table_import._money(amount, {"factor": 1})
    if (own != account or peer or not currency or issue or not amount or amount != row["amount"]
            or table_import._CURRENCY.get(currency, currency.upper()) != row["currency"]
            or row["trade_time"] is None or row["trade_time_utc"] is not None
            or table_import._time(clock) != row["trade_time"]):
        return None
    marker = table_import._enum(marker)
    raw_parties = (row["raw_payer_account"] or "", row["raw_payee_account"] or "")
    if marker in table_import._IN and raw_parties == ("", account):
        return "in"
    if marker in table_import._OUT and raw_parties == (account, ""):
        return "out"
    return None


def _is_native_csv_origin(locator):
    return (locator["suffix"] == ".zip" and locator["parser_version"] == table_import.BANK_SOURCE_EVIDENCE_ARCHIVE_PROFILE
            and locator["member_path"].lower().endswith(".csv") and locator["header_scope"] == "1")


def _split_ledger_directions(case_id, account, selected):
    blank_peer = {key: row for key, row in selected.items() if row["raw_self_account"]
                  and bool(row["raw_payer_account"]) != bool(row["raw_payee_account"])}
    if not blank_peer:
        return {}
    locators = trace_review._locators(case_id, selected, include_ledger=True)
    raw_accounts = {row["raw_self_account"] for row in selected.values()}
    native_csv = (len(locators) == len(selected) and len(raw_accounts) == 1 and bool(account)
                  and len({(row["source_id"], row["member_path"]) for row in locators}) == 1
                  and all(_is_native_csv_origin(row) for row in locators))
    incomplete = blank_peer if native_csv else {key: row for key, row in blank_peer.items() if row["raw_self_account"] == account}
    # Unqualified compact clocks and a blank peer can be verified only inside
    # the one active workbook and physical header of this selection.
    uniform = (len(locators) == len(selected)
               and len({(row["source_id"], row["member_path"]) for row in locators}) == 1
               and all(row["suffix"] == ".xlsx" and row["parser_version"] == table_import.VOUCHER_REFERENCE_PROFILE
                       and row["header_scope"] == "1" and row["disposition"] in {"imported", "duplicate"}
                       for row in locators))
    members = {}
    for locator in locators:
        if locator["flow_id"] not in incomplete:
            continue
        standalone = (locator["suffix"] == ".xlsx"
                      and locator["parser_version"] in table_import.BANK_SPLIT_LEDGER_PROFILES)
        archived = (locator["suffix"] == ".zip" and locator["parser_version"] == table_import.TRIAL_MEMBER_ARCHIVE_PROFILE
                    and locator["trial_member_profile"] in table_import.BANK_SPLIT_LEDGER_PROFILES)
        if (((standalone or archived or uniform) and locator["encoding"] == "xlsx" or native_csv)
                and locator["disposition"] in {"imported", "duplicate"}):
            members.setdefault((locator["source_id"], locator["member_path"]), []).append(locator)
    directions = {}
    for (source_id, _), locations in members.items():
        origin = locations[0]
        document, evidence = trace_review._original_layout(case_id, origin)
        headers = document.get("header_rows", ())
        scoped_csv = native_csv and headers == [1]
        if uniform:
            prefix, _, index = origin["member_path"].rpartition("/")
            if (prefix != "worksheets" or len(index) != 4 or not index.isascii() or not index.isdigit()
                    or not 1 <= int(index) <= table_import.MAX_SHEETS or headers != [1]
                    or not origin["sheet"] or document.get("sheet") != origin["sheet"]
                    or table_import.xlsx_member_digest(origin["sha256"], int(index), origin["sheet"]) != origin["member_sha256"]):
                continue
        header_rows = {row["source_row"]: max((head for head in headers if head < row["source_row"]), default=0)
                       for row in locations}
        if not all(header_rows.values()):
            continue
        positions = set(header_rows) | set(header_rows.values())
        try:
            registered = imports._registered_original(case_id, source_id)
            if not registered["active"] or registered["active_batch_id"] is None or registered["sha256"] != origin["sha256"]:
                continue
            if origin["suffix"] == ".zip" and not scoped_csv:
                member = origin["member_path"]
                workbook = table_import.archive_workbook_path(member)
                if (not workbook or not workbook.lower().endswith(".xlsx")
                        or not origin["sheet"] or document.get("sheet") != origin["sheet"]):
                    continue
                index = int(member[-4:])
                if not 1 <= index <= table_import.MAX_SHEETS:
                    continue
                leaf, _ = imports.get_source_member_file(case_id, source_id, member)
                with closing(leaf):
                    digest = file_digest(leaf, "sha256").hexdigest()
                    if table_import.xlsx_member_digest(digest, index, origin["sheet"]) != origin["member_sha256"]:
                        continue
                    leaf.seek(0)
                    physical = trace_review._original_cells(leaf, {**origin, "suffix": ".xlsx"}, document, (), positions)
            else:
                original, _ = imports.get_source_file(case_id, source_id)
                physical = trace_review._original_cells(original, origin, document, (), positions)
        except ServiceError as error:
            if error.code not in _SOURCE_ERRORS:
                raise
            continue
        for location in locations:
            position, key = location["source_row"], location["flow_id"]
            if not (source_row := physical.get(position)):
                continue
            headings = [cell["value"].strip() if cell["kind"] == "text" else ""
                        for cell in physical.get(header_rows[position], ())]
            if scoped_csv:
                row = incomplete[key]
                if direction := _native_csv_direction(row, row["raw_self_account"], headings, source_row):
                    directions[key] = direction
            elif uniform:
                query_owner = False
                if "查询帐号" in headings and "借贷标志" in headings:
                    from .trial_pair_comparison import _certified_posting_columns

                    columns = tuple(headings.index(heading) + 1 for heading in ("查询帐号", "借贷标志"))
                    query_owner = _certified_posting_columns(
                        document, evidence, columns, "查询帐号", "借贷标志", "uniform_query_owner")
                if direction := _physical_uniform_direction(incomplete[key], account, headings, source_row,
                                                             query_owner=query_owner):
                    directions[key] = direction
            elif table_import.is_bank_split_ledger(headings, table_import._candidates(headings, table_import.VOUCHER_REFERENCE_PROFILE)):
                if direction := _physical_split_direction(incomplete[key], account, headings, source_row):
                    directions[key] = direction
    return directions


def _selected_order_columns(document, evidence, member_path, header_row, original_header, *, workbook=False):
    if document.get("header_rows") != [header_row] or document.get("mode") != "B":
        return None
    if any(table_import._norm(cell["value"]) in trace_group_evidence._CLOCK_COLUMNS for cell in original_header):
        return None
    fields = {"self_account", "direction", "trade_time", "amount", "currency"}
    entries = [entry for entry in evidence if entry["field"] in fields and entry["member_path"] == member_path
               and entry["header_row"] == str(header_row)]
    headings = {cell["column"]: cell["value"].strip() for cell in original_header}
    normalized = [table_import._norm(heading) for heading in headings.values()]
    columns = {}
    if workbook:
        query_entries = [entry for entry in entries if entry["field"] == "self_account"
                         and entry["basis"] == "uniform_query_owner"]
        if len(query_entries) != 1:
            return None
        [query] = query_entries
        column = int(query["column_index"])
        heading = headings.get(column)
        if (not heading or heading != query["column"] or table_import._norm(heading) not in {"查询帐号", "查询账号"}
                or normalized.count(table_import._norm(heading)) != 1 or query["candidate_count"] != "2"):
            return None
        columns["query_account"] = column
        entries.remove(query)
    if len(entries) != len(fields) or {entry["field"] for entry in entries} != fields:
        return None
    for entry in entries:
        column = int(entry["column_index"])
        count = "2" if workbook and entry["field"] == "self_account" else "1"
        if (entry["basis"] != "exact_header_alias" or entry["candidate_count"] != count
                or not headings.get(column) or headings[column] != entry["column"]
                or normalized.count(table_import._norm(entry["column"])) != 1):
            return None
        columns[entry["field"]] = column
    if len(set(columns.values())) != len(columns):
        return None
    if next(entry for entry in entries if entry["field"] == "amount")["unit_factor"] != "1":
        return None
    return columns


def _selected_order_posting(row, locator, side, columns, physical):
    cells = {cell["column"]: cell["value"].strip() for cell in physical if cell["kind"] == "text"}
    if not set(columns.values()) <= cells.keys():
        return None
    original = {field: cells[column] for field, column in columns.items()}
    clock_text = original["trade_time"]
    compact = ("query_account" in original and len(clock_text) == 14 and clock_text.isascii() and clock_text.isdecimal())
    if (not compact and not feedback_import._FRACTIONAL_TIME.fullmatch(clock_text)
            or row["trade_time_utc"] is not None):
        return None
    if "query_account" in original and original["query_account"] != original["self_account"]:
        return None
    clock = table_import._time(clock_text)
    if clock is None or clock != row["trade_time"] or locator["time_evidence_limited"]:
        return None
    try:
        time_proof = json.loads(locator["time_evidence"] or "{}")
        book = source_ledger.validate(locator["ledger_evidence"])
    except ValueError:
        return None
    times = [item for item in time_proof.get("values", ()) if item["role"] == "transaction"]
    if (time_proof.get("ordinary_time_role") != "transaction" or len(times) != 1
            or times[0]["column_index"] != columns["trade_time"] or times[0]["raw"].strip() != clock_text):
        return None
    marker = original["direction"]
    markers = table_import._IN if side == "in" else table_import._OUT
    currency_text = original["currency"]
    currency = table_import._CURRENCY.get(currency_text, currency_text.upper())
    amount, issue = table_import._money(original["amount"], {"factor": 1})
    snapshot = book["row"]
    if (not currency_text or currency != row["currency"] or currency != book["source_currency"]
            or table_import._enum(marker) not in markers or (book["direction"], book["source_direction"]) != (side, marker)
            or book["kind"] != "financial"
            or original["self_account"] != snapshot["self_account"] or original["self_account"] != row["raw_self_account"]
            or snapshot["trade_time_utc"] is not None or table_import._time(snapshot["trade_time"]) != clock
            or issue or amount != row["amount"] or money_decimal(book["signed_amount"]) != amount
            or money_decimal(snapshot["amount"]) != amount
            or (units := trace_group_evidence._positive_units(amount)) is None):
        return None
    return {"flow_id": row["id"], "source_row": locator["source_row"], "direction": side,
            "clock": clock, "units": units}


def _selected_order_bound(case_id, selected, sources, targets, currency):
    # Read one active original table region. Neither a resolved entity alone
    # nor normalized time parsed without its source precision establishes order.
    locators = trace_review._locators(case_id, selected, include_ledger=True)
    if len(locators) != len(selected) or len({row["source_row"] for row in locators}) != len(locators):
        return None
    first = locators[0]
    workbook = first["suffix"] == ".xlsx"
    if not first["header_scope"] or not first["header_scope"].isdecimal():
        return None
    header_row = int(first["header_scope"])
    scope_keys = ("source_id", "sha256", "header_scope", "member_path", "member_sha256", "suffix")
    scope = tuple(first[key] for key in scope_keys)
    if any(tuple(row[key] for key in scope_keys) != scope or row["parser_version"] != table_import.VOUCHER_REFERENCE_PROFILE
           or row["disposition"] not in {"imported", "duplicate"} or row["source_row"] <= header_row for row in locators):
        return None
    if workbook:
        prefix, _, index = first["member_path"].rpartition("/")
        if (prefix != "worksheets" or len(index) != 4 or not index.isascii() or not index.isdigit()
                or not 1 <= int(index) <= table_import.MAX_SHEETS or header_row != 1 or not first["sheet"]
                or table_import.xlsx_member_digest(first["sha256"], int(index), first["sheet"]) != first["member_sha256"]):
            return None
    elif first["member_path"] != "table.csv" or first["member_sha256"] != first["sha256"] or first["suffix"] != ".csv":
        return None
    if {row["currency"] for row in selected.values()} != {currency}:
        return None
    accounts = {row["raw_self_account"] for row in selected.values()}
    if len(accounts) != 1:
        return None
    [account] = accounts
    if not account.isascii() or not account.isdecimal() or not account.strip("0"):
        return None
    document, evidence = trace_review._original_layout(case_id, first)
    if workbook and document.get("sheet") != first["sheet"]:
        return None
    positions = {header_row, *(row["source_row"] for row in locators)}
    try:
        original = imports._registered_original(case_id, first["source_id"])
        if (original["active_batch_id"] is None or original["sha256"] != first["sha256"]
                or original["size_bytes"] > _SELECTED_ORDER_SOURCE_BYTES):
            return None
        file = imports._path(first["source_id"], first["suffix"])
        stamp = _original_stamp(file)
        physical = trace_review._original_cells(imports._source(original, snapshot=True), first, document, (), positions)
        if stamp is None or _original_stamp(file) != stamp or set(physical) != positions:
            return None
    except ServiceError as error:
        if error.code not in _SOURCE_ERRORS:
            raise
        return None
    columns = _selected_order_columns(document, evidence, first["member_path"], header_row, physical[header_row], workbook=workbook)
    if columns is None:
        return None
    postings = []
    sides = {**dict.fromkeys(sources, "in"), **dict.fromkeys(targets, "out")}
    for locator in locators:
        key = locator["flow_id"]
        row = _selected_order_posting(selected[key], locator, sides[key], columns, physical[locator["source_row"]])
        if row is None:
            return None
        postings.append(row)
    return trace_group_evidence.selected_order_upper_bound(postings, currency)


def _selected_trace_card(case_id, sources, targets, *, selected_order=False):
    source_ids, target_ids = sorted(map(int, sources)), sorted(map(int, targets))
    selected = _flow_rows(case_id, [*source_ids, *target_ids])
    accounts = {row["self_account"] for row in selected.values()}
    if set(selected) != set(source_ids + target_ids) or len(accounts) != 1 or not next(iter(accounts)):
        raise ServiceError(422, "assistance_group_outside_card", "所选原行须属于同一本方账簿")
    [account] = accounts
    directions = _split_ledger_directions(case_id, account, selected)
    sides = {**dict.fromkeys(source_ids, "in"), **dict.fromkeys(target_ids, "out")}

    def sides_verified():
        return all(trace_group_evidence._direction(selected[key], account, proved_direction=directions.get(key)) == side
                   for key, side in sides.items())

    bound = None
    if selected_order and not sides_verified():
        # With a blank counterparty, the original posting marker and ledger
        # snapshot must independently support the selected side.
        if bound := _selected_order_bound(case_id, selected, source_ids, target_ids, selected[source_ids[0]]["currency"]):
            directions.update(sides)
    if not sides_verified():
        raise ServiceError(422, "assistance_wrong_ledger_direction", "来源须选本账入账，去向须选本账出账")
    interval = trace_group_evidence.prepare(case_id, account, list(selected.values()), strict_source_clock=True)
    result = trace_group_evidence.evidence(account, [selected[key] for key in source_ids],
                                           [selected[key] for key in target_ids], interval,
                                           retain_scope=True, proved_directions=directions)
    if selected_order and result["conditional_group_share"] is None and result["checks"]["currency_amount"]:
        if bound := bound or _selected_order_bound(case_id, selected, source_ids, target_ids, result["currency"]):
            result["selected_order_upper_bound"] = bound
    return {**result, "_split_ledger_directions": directions}


def _trace(case_id, review, kind, group, groups, *, product=False, ledger_scope=None, selected=None):
    side = "sources" if kind == "trace_source" else "targets"
    cards = review.trace_cards.get(side, ())
    if selected is not None:
        cards = (_selected_trace_card(case_id, *selected, selected_order=product),)
    elif groups:
        cards = (_joint_trace_card(case_id, cards, side, groups),)
    elif group is not None:
        target = "source_ids" if side == "sources" else "target_ids"
        cards = tuple(card for card in cards if group in card[target])
    if len(cards) != 1:
        raise ServiceError(422, "assistance_group_required", "请选择当前可核对的单个追踪卡")
    [card] = cards
    sources, targets = (list(dict.fromkeys(map(int, card[key]))) for key in ("source_ids", "target_ids"))
    subjects = set(sources) | set(targets)
    if ledger_scope is not None:
        rows = _flow_rows(case_id, subjects)
        if (ledger_scope["revision"] != review.revision or set(rows) != subjects
                or any(row["self_account"] != ledger_scope["ledger_id"] or row["trade_time"] is None
                       or not ledger_scope["start"] <= row["trade_time"].date().isoformat() <= ledger_scope["end"]
                       for row in rows.values())):
            raise ServiceError(409, "trace_ledger_scope_changed", "追踪原行超出所选账簿、登记期间或当前修订")
    other = [int(loc) for loc in card["evidence_row_ids"] if int(loc) not in subjects]
    anchor = [review.anchor_id] if review.anchor_id in subjects else []
    ids = list(dict.fromkeys([*anchor, *sources[:4], *targets[:4], *other[:_LIMIT]]))
    evidence_rows = _flow_rows(case_id, ids)
    records = _records(case_id, ids, evidence_rows)
    directions = card.get("_split_ledger_directions", {})
    for record in records:
        if direction := directions.get(int(record["id"][1:])):
            record["structured"]["book_direction"] = direction
    named = {record["id"] for record in records}
    candidates = [_candidate(record["id"], "待查区间原行") for record in records
                  if int(record["id"][1:]) in other and record["locator"].get("file_url")]
    facts = {key: card[key] for key in ("interval", "checks", "currency", "opening_balance", "closing_balance",
                                       "conditional_group_share", "other_entries", "reasons", "attribution_status")}
    facts.update(source_refs=[f"F{key}" for key in sources if f"F{key}" in named],
                 target_refs=[f"F{key}" for key in targets if f"F{key}" in named],
                 source_count=len(sources), target_count=len(targets), needs_model=bool(candidates),
                 more_evidence=card["other_evidence_limited"] or any(f"F{key}" not in named for key in subjects)
                 or len(candidates) < len(other))
    if product:
        bound = card.get("selected_order_upper_bound")
        if (bound is None and group is not None and len(sources) == len(targets) == 1
                and review.anchor_id in subjects and subjects <= evidence_rows.keys()
                and card["conditional_group_share"] is None and card["checks"]["currency_amount"]):
            bound = _selected_order_bound(case_id, {key: evidence_rows[key] for key in subjects},
                                          sources, targets, card["currency"])
        if bound is not None:
            facts["selected_order_upper_bound"] = bound
        facts["range_scope"] = sha256(json.dumps([sorted(sources), sorted(targets)]).encode()).hexdigest()
        if facts["conditional_group_share"] is None:
            from . import trial_trace_decision

            additional, choices, originals = trial_trace_decision.evidence_options(case_id, card)
            facts.update(additional)
            candidates.extend(choices)
            records.extend(originals)
    proof = None
    if product and facts["conditional_group_share"] is not None:
        full_ids = set(map(int, card.get("_range_record_ids", ())))
        locations = trace_review._locators(case_id, full_ids) if full_ids else []
        if subjects <= full_ids and {row["flow_id"] for row in locations} == full_ids:
            proof = {row["source_id"]: row["sha256"] for row in locations}
            facts["ledger_row_count"] = len(full_ids)
            facts["range_ref_ids"] = [row["id"] for row in records if int(row["id"][1:]) in full_ids]
        else:
            proof = {}
    if ledger_scope is not None:
        facts["ledger_scope_id"] = ledger_scope["scope_id"]
        facts["ledger_registry_sources"] = ledger_scope["sources"]
        if proof:
            proof.update(ledger_scope["proofs"])
    return facts, candidates, records, proof


def review_assistance(case_id, body, *, product=False, range_tickets=None, ledger_scope=None):
    cid, canvas, anchor = int(case_id), int(body.canvas_id or 0), int(body.anchor_id or 0)
    proof = None
    groups = getattr(body, "group_flow_ids", ())
    explicit_sources = getattr(body, "source_flow_ids", ())
    explicit_targets = getattr(body, "target_flow_ids", ())
    selected = None
    if groups and (not product or body.kind == "pair" or body.group_flow_id is not None or len(set(groups)) != len(groups) or len(groups) < 2):
        raise ServiceError(422, "assistance_invalid_joint_group", "请选择当前追踪卡中的至少两条不同原行")
    if explicit_sources or explicit_targets:
        if (not product or not explicit_sources or not explicit_targets or body.kind == "pair"
                or groups or body.group_flow_id is not None
                or len(set([*explicit_sources, *explicit_targets])) != len(explicit_sources) + len(explicit_targets)
                or body.anchor_id not in (explicit_targets if body.kind == "trace_source" else explicit_sources)):
            raise ServiceError(422, "assistance_invalid_joint_group", "请在同一原卡选择包含锚点的两组不同资金原行")
        selected = explicit_sources, explicit_targets
    with db._lock:
        review = trace_review._require(body.review_token, cid, canvas, anchor_id=anchor)
        if selected and not set(map(int, [*explicit_sources, *explicit_targets])) <= review.flow_ids:
            raise ServiceError(422, "assistance_group_outside_card", "选定原行超出当前追踪核对范围")
        if body.kind == "pair":
            facts, candidates, records = _pair(cid, review, anchor, product=product)
            domain, question = "pair", "定位最需要核证与锚行是否同笔的对向原行。不能排除竞争时保留待核。"
            gaps = ("source_proof", "time_basis", "coverage", "currency")
        else:
            facts, candidates, records, proof = _trace(cid, review, body.kind, body.group_flow_id, groups, product=product,
                                                       ledger_scope=ledger_scope, selected=selected)
            domain, question = "trace", "定位最值得核查的竞争或缺口原行。金额区间由产品计算，现实归属保持待核。"
            gaps = ("coverage", "time_basis", "balance_basis", "currency")
        facts.update(case_id=case_id, canvas_id=body.canvas_id, anchor_id=body.anchor_id,
                     revision=review.revision, kind=body.kind, group_flow_id=body.group_flow_id)
        if product:
            facts["product_review"] = True
            facts["group_flow_ids"] = list(groups)
            if selected:
                facts["selected_source_flow_ids"] = list(explicit_sources)
                facts["selected_target_flow_ids"] = list(explicit_targets)
    if range_tickets is not None:
        range_tickets.clear()
    if proof is not None:
        # The model's bounded row list is independent of the product's
        # interval. Check each full-interval source without holding DB locks.
        tickets = verified_product_evidence(cid, proof)
        facts["range_originals_verified"] = bool(proof) and set(tickets) == set(proof)
        if range_tickets is not None and facts["range_originals_verified"]:
            range_tickets.update(tickets)
    with db._lock:
        trace_review._require(body.review_token, cid, canvas, anchor_id=anchor)
        return _finish(_packet(domain, question, facts, candidates, records, gaps, scope_secret=body.review_token),
                       body.reply, source_case=cid)
