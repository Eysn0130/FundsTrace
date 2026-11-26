"""Prepare complete, source-checked physical mappings in the desktop trial."""
from types import SimpleNamespace

from app import table_import, table_source_roles
from app.api.models import ImportLayoutApplyRequest
from app.table_mapping import document_number_header
from . import assistance, import_column_trial, import_layout_trial, import_ready_evidence, imports
from .cases import ServiceError


_REQUIRED = {"counter_account", "currency"}
_EXPECTED_LAYOUT_ERRORS = {"invalid_layout_mapping", "invalid_table_file", "layout_preview_changed",
                           "trial_source_role_conflict", "layout_trial_limit", "outside_layout_review"}


def _header_columns(cells, profile):
    if not cells or len(cells) > 16:
        return None
    candidate = table_import._candidates([cell["value"] if cell["kind"] == "text" else "" for cell in cells], profile)
    if not all(len(candidate.get(field, ())) == 1 for field in _REQUIRED):
        return None
    direction, amount = candidate.get("direction", ()), candidate.get("amount", ())
    single_amount = len(direction) == len(amount) == 1
    split_amounts = not direction and not amount and all(
        len(candidate.get(field, ())) == 1 for field in ("income_amt", "expense_amt"))
    if not (single_amount or split_amounts):
        return None
    if not any(entry.get("time_role") == "transaction" for entry in candidate.get("trade_time", ())):
        return None
    if candidate.get("payer_account") or candidate.get("payee_account"):
        return None
    return candidate


def _account_column(packet, candidates, profile, selection=None):
    if packet["facts"].get("support_coverage_complete") is False:
        return None
    early, view, proofs = import_column_trial.prepare(packet, lambda text: text)
    columns = {row["id"]: row for row in packet["records"] if "column" in row["locator"]}
    notes = {row["id"]: row for row in packet["records"] if row["id"].startswith("N")}
    if selection is not None:
        selected = selection["choice"]
        cited = set(selection["refs"]) - {selected}
        if (early is not None or selected not in view["candidates"] or not cited
                or any(proofs.get(selected, {}).get(ref) != "supported" for ref in cited)):
            return None
    elif early == "single_original_lead":
        selected = view
    elif early is None:
        verdicts = {column: next(iter(proofs[column].items())) for column in view["candidates"] if column in proofs}
        checked = {column: (finding, note) for column, (note, finding) in verdicts.items()}
        choice = import_column_trial.complete(view, checked)
        selected = choice[0] if choice else None
    elif early == "insufficient_evidence" and not candidates.get("self_account"):
        aliases = [ref for ref, row in columns.items() if table_import._norm(row["raw"]["heading"])
                   in import_column_trial.OWN_ACCOUNT_ALIASES]
        selected = aliases[0] if len(aliases) == 1 else None
    else:
        return None
    if not selected or packet["facts"]["more_columns"]:
        return None
    _, unclear, _ = import_column_trial._claims(packet, columns, notes)
    if (selected in unclear or "refuted" in proofs.get(selected, {}).values()
            or any(ref != selected and "supported" in votes.values() for ref, votes in proofs.items())):
        return None
    # A second account-like column without an established different role
    # prevents a role lead from settling the whole region.
    distinct = {item["index"] + 1 for field, entries in candidates.items() if field != "self_account"
                for item in entries}
    for ref, row in columns.items():
        heading = row["raw"]["heading"]
        normalized = table_import._norm(heading)
        if (ref == selected or row["locator"]["column"] in distinct or "refuted" in proofs.get(ref, {}).values()
                or document_number_header(normalized) or table_source_roles.query_condition(heading)):
            continue
        base = table_import._header_parts(heading, profile)[0]
        if any(term in base for term in import_column_trial._TYPES["account"] + import_column_trial._TYPES["card"]):
            return None
    cited = [ref for ref in selection["refs"] if ref != selected] if selection else [
        ref for ref, finding in proofs.get(selected, {}).items() if finding == "supported"]
    note = notes[cited[0]]["locator"] if cited else None
    return columns[selected]["locator"]["column"], note


def _spans(value, session, manifest, profile, proposal=None, certificate=None, *, pending_issue=None):
    entries = manifest["layout_members"]
    supports = [entry["scope"]["member_path"] for entry in entries
                if entry["scope"]["sheet"] in assistance._SCOPED_NOTE_SHEETS]
    if len(supports) > 1:
        return None
    support = certificate["note"]["member_path"] if certificate is not None else supports[0] if supports else None
    spans, source_rows, expected, evidence = [], {}, {}, []
    with imports._source(session, snapshot=True) as source:
        for entry in entries:
            member = entry["scope"]
            path, last = member["member_path"], member["source_rows"]
            if path == support:
                continue
            source.seek(0)
            source_rows[path] = import_ready_evidence._cells(source, session, entry, set(range(1, last + 1)),
                                                             profile, for_layout=True)
    for entry in entries:
        member = entry["scope"]
        path, last = member["member_path"], member["source_rows"]
        if path == support:
            continue
        rows = source_rows[path]
        headers = member["header_rows"] or [number for number in (1, 2)
                                             if number in rows and _header_columns(rows[number], profile)]
        if not headers:
            return None
        for head, end in zip(headers, [next_head - 1 for next_head in headers[1:]] + [last], strict=True):
            candidates = _header_columns(rows[head], profile)
            if not candidates or head >= end:
                return None
            requested = proposal and (path, head) == (proposal["facts"]["member_path"], proposal["facts"]["header_row"])
            body = SimpleNamespace(revision=value["layout_review"]["revision"], member_path=path,
                                   header_row=head, column_start=1,
                                   support_member_path=proposal["facts"]["support_member_path"] if requested else support,
                                   field="self_account", reply=None)
            packet = assistance.import_assistance(value["case_id"], value["import_id"], body, column_trial=True)
            selection = None
            if requested:
                if packet["packet_sha256"] != proposal["packet_sha256"]:
                    return None
                selection, proposal = proposal["selection"], None
            definition = None
            if certificate is not None and (path, head, end) == certificate["region"]:
                decision = certificate["column"], certificate["note"]
                certificate = None
            elif packet["facts"].get("account_use_code_notes"):
                if selection is not None or len(entries) != 2 or len(headers) != 1:
                    return None
                coded, issue = import_column_trial.account_use_code_review(packet)
                if coded is None:
                    if issue and pending_issue is not None:
                        pending_issue.update(issue)
                    return None
                own, note, definition = coded
                decision = own, note
            else:
                decision = _account_column(packet, candidates, profile, selection)
            if decision is None:
                return None
            own, note = decision
            spans.append({"member_path": path, "header_row": head, "last_row": end, "mode": "B",
                          "mapping": {"self_account": own}})
            evidence.append({"member_path": path, "header_row": head, "last_row": end, "column": own,
                             "basis": "source_role_statement" if note else "scoped_own_account_alias",
                             "note": {key: note[key] for key in ("member_path", "source_row")} if note else None})
            if definition:
                evidence[-1]["definition"] = {key: definition[key] for key in ("member_path", "source_row")}
            counterpart = candidates["counter_account"][0]
            direction = candidates.get("direction")
            currency = candidates["currency"][0]
            for number in range(head + 1, end + 1):
                cells = rows[number]
                if not any(cell["value"] for cell in cells):
                    continue
                if len(cells) != len(rows[head]):
                    return None
                own_cell = cells[own - 1]
                counter_cell = cells[counterpart["index"]]
                own_value, counter_value = own_cell["value"].strip(), counter_cell["value"].strip()
                if (own_cell["kind"] not in {"text", "number"} or counter_cell["kind"] not in {"text", "number"}
                        or not own_value or not counter_value or own_value == counter_value
                        or not cells[currency["index"]]["value"].strip()):
                    return None
                sign = None
                if direction:
                    sign = table_import._enum(cells[direction[0]["index"]]["value"])
                    if sign not in table_import._IN | table_import._OUT:
                        return None
                expected[(path, number)] = (own_value, counter_value, sign)
    return (spans, expected, evidence) if proposal is None and certificate is None else None


def _prepare(value, proposal=None, certificate=None, *, pending_issue=None, describe_only=False):
    layout = value["layout_review"]
    session, manifest, profile = import_ready_evidence._ready(
        value["case_id"], value["import_id"], layout["revision"], for_layout=True,
    )
    if (session["suffix"] not in {".csv", ".xlsx"} or profile not in table_import.CONFIRMED_LAYOUT_PROFILES
            or session["size_bytes"] > import_layout_trial._MAX_FILE_BYTES
            or manifest["layout_source_rows"] > import_layout_trial._MAX_SOURCE_ROWS):
        return None
    try:
        plan = _spans(value, session, manifest, profile, proposal, certificate, pending_issue=pending_issue)
        if plan is None or describe_only:
            return None
        regions, expected, evidence = plan
        if not regions or len(regions) > 8:
            return None
        body = ImportLayoutApplyRequest(revision=layout["revision"], confirmed=True, regions=regions)
        trial = import_ready_evidence.trial_layout(value["case_id"], value["import_id"], body, evidence_layout=True)
        checked, has_accepted = set(), False
        for region in trial["regions"]:
            for row in region["rows"]:
                if row["reason"] == "blank_record":
                    continue
                position = (region["member_path"], row["source_row"])
                if position not in expected:
                    return None
                own, counter, sign = expected[position]
                if sign is None and row.get("retained_split_conflict"):
                    checked.add(position)
                    continue
                preview = row["preview"]
                if row["disposition"] != "accepted" or not preview:
                    return None
                parties = (preview["payer_account"], preview["payee_account"])
                # The ordinary interpreter decides the amount and direction of
                # split amounts. Check both endpoints and the physical owner.
                if sign is None:
                    if preview["self_account"] != own or set(parties) != {own, counter}:
                        return None
                elif parties != ((counter, own) if sign in table_import._IN else (own, counter)):
                    return None
                checked.add(position)
                has_accepted = True
        if not has_accepted or checked != expected.keys():
            return None
        evidence = {"source_sha256": session["sha256"], "regions": evidence}
        origin = {"origin": "model_assisted" if proposal else "rule", **evidence}
        if proposal or certificate:
            origin["packet_sha256"] = (proposal or certificate)["packet_sha256"]
        note_policy = (table_source_roles.NARRATIVE_FIELD_NOTE_POLICY if certificate is not None
                       else table_source_roles.CODED_FIELD_NOTE_POLICY if any("definition" in region for region in origin["regions"])
                       else None)
        prepared = imports.apply_layout(value["case_id"], value["import_id"], body, trial_roles=True,
                                        trial_layout_decision=origin, field_note_policy=note_policy)
    except ServiceError as exc:
        if exc.code in _EXPECTED_LAYOUT_ERRORS:
            return None
        raise
    return prepared, evidence


def prepare_upload(value, *, describe_only=False):
    """Resolve a complete layout, or explain a verified gap in the current preview."""
    if (not value.get("layout_review") or (not describe_only and not value.get("automatic"))
            or not value.get("layout_model_trial")
            or value.get("layout_model_trial_conflict") or value.get("reference_policy_available")
            or (value.get("table_summary") or {}).get("accepted")):
        return value
    if describe_only:
        members = value["layout_review"]["members"]
        if len(members) != 2 or not any(member["sheet"] == table_source_roles.CODED_FIELD_NOTE_SHEET for member in members):
            return value
    issue = {}
    checked = _prepare(value, pending_issue=issue, describe_only=describe_only)
    if issue:
        return {**value, "layout_review_issue": issue}
    if checked is None:
        return value
    prepared, evidence = checked
    return {**prepared, "automatic": True, "layout_rule_evidence": evidence}


def prepare_assisted(result):
    """Prepare a formal preview from a service-verified, full-current column proposal."""
    packet = result["packet"]
    facts = packet["facts"]
    if (result["state"] != "proposal" or packet["selection"] is None
            or facts["field"] != "self_account" or facts["column_start"] != 1 or facts["more_columns"]):
        return None
    checked = _prepare({"case_id": facts["case_id"], "import_id": facts["import_id"],
                        "layout_review": {"revision": facts["revision"]}}, packet)
    return {**checked[0], "layout_model_assisted": True} if checked else None


def prepare_certified(case_id, import_id, revision, certificate):
    checked = _prepare({"case_id": case_id, "import_id": import_id,
                        "layout_review": {"revision": revision}}, certificate=certificate)
    return {**checked[0], "layout_rule_evidence": checked[1]} if checked else None
