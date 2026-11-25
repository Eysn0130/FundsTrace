"""Discover and adopt a cited account role from an unindexed workbook note."""
import json
import re

from .. import table_import, table_source_roles
from . import assistance, import_ready_evidence, import_trial_rules, imports


_OWN = re.compile(
    r"(?:该列|本列)(?:是|为|记载|记录)(?:(?:该区域|本表区|本区域)?每笔交易(?:发生时)?的)?"
    r"本方(?:账簿)?(?:账号|账户)(?:[,，]不表示对方(?:账户|账号))?"
)
_QUERY = re.compile(
    r"(?:该列|本列)(?:仅|只)(?:保存|记录|存放)(?:提交查询时使用的)?(?:登记编号|查询编号|查询条件)"
    r"(?:[,，](?:不可|不得)当作(?:该区域|本表区)?每笔交易的本方(?:账簿)?(?:账号|账户))?"
)
_MAX_MEMBERS = 8
_MAX_NOTE_ROWS = 24


def _cells(source, session, entry, profile):
    source.seek(0)
    scope = entry["scope"]
    return import_ready_evidence._cells(source, session, entry, set(range(1, scope["source_rows"] + 1)),
                                        profile, for_layout=True)


def discover(case_id, import_id, body):
    """Expose both the unfamiliar columns and physical, unlabelled explanations."""
    if body.field != "self_account" or body.column_start != 1 or not body.prepare_layout:
        return None
    session, manifest, profile = import_ready_evidence._ready(case_id, import_id, body.revision, for_layout=True)
    if (session["suffix"] != ".xlsx" or profile not in table_import.CONFIRMED_LAYOUT_PROFILES
            or json.loads(session["request_json"] or "{}").get(imports._NARRATIVE_DECISIONS) is not True):
        return None
    entries = manifest["layout_members"]
    if not 2 <= len(entries) <= _MAX_MEMBERS:
        return None
    main = next((entry for entry in entries if entry["scope"]["member_path"] == body.member_path), None)
    if main is None:
        return None
    limit = import_ready_evidence.import_layout_trial._MAX_SOURCE_ROWS
    if sum(entry["scope"]["source_rows"] for entry in entries) > limit:
        return None
    base = assistance.import_assistance(case_id, import_id, body, column_trial=True)
    facts = base["facts"]
    if facts["more_columns"] or facts.get("wide_role_conflict"):
        return None
    with imports._source(session, snapshot=True) as source:
        rows = _cells(source, session, main, profile)
        heads = rows.get(body.header_row)
        columns = import_trial_rules._header_columns(heads, profile)
        if not columns or columns.get("self_account"):
            return None
        headings = [item["value"] for item in heads]
        known = {item["index"] + 1 for entries in columns.values() for item in entries}
        candidates = [record for record, candidate in zip(base["records"], base["candidates"])
                      if "column" in record["locator"] and record["locator"]["column"] not in known
                      and not candidate["refuted"] and not record["structured"]["empty"]]
        if not 2 <= len(candidates) <= 6:
            return None
        notes = []
        for entry in manifest["layout_members"]:
            member = entry["scope"]
            if member["member_path"] == body.member_path or not 2 <= member["source_rows"] <= _MAX_NOTE_ROWS:
                continue
            raw = _cells(source, session, entry, profile)
            if tuple(cell["value"] for cell in raw.get(1, ())) != table_source_roles.NARRATIVE_FIELD_NOTE_HEADER:
                continue
            for pos, cells in sorted(raw.items()):
                if pos == 1 or not any(cell["value"] for cell in cells):
                    continue
                if len(cells) != 3 or any(cell["kind"] != "text" or not cell["value"].strip()
                                          or len(cell["value"]) > 160 for cell in cells):
                    return None
                notes.append({"id": f"N{len(notes) + 1}", "raw": {"cells": [cell["value"] for cell in cells]},
                              "structured": {}, "locator": {"member_path": member["member_path"],
                                                            "sheet": member["sheet"], "source_row": pos}})
    if not notes or len(notes) > 12:
        return None
    wanted = {record["id"]: record["locator"]["column"] for record in candidates}
    packet = assistance._packet("import", "选择该表区实际承担每笔收支的本方账号列及解释原行。",
                                {**facts, "decision_note_count": len(notes)},
                                [assistance._candidate(ref, f"物理第{column}列") for ref, column in wanted.items()],
                                candidates + notes, ("source_role",))
    return packet, headings, wanted, notes


def _explicit_certificate(packet, headings, wanted, notes):
    facts = packet["facts"]
    region_sheet = packet["records"][0]["locator"]["sheet"]
    verdicts = {}
    for row in notes:
        binding = table_source_roles.narrative_field_note(
            row["raw"]["cells"], region_sheet, facts["header_row"], facts["region_last_row"], headings)
        if binding is None:
            continue
        column, sentence = binding
        if column not in wanted.values() or column in verdicts:
            return None
        text = table_import._norm(sentence)
        verdicts[column] = (_OWN.fullmatch(text) is not None, _QUERY.fullmatch(text) is not None, row)
    if verdicts.keys() != set(wanted.values()):
        return None
    owners = [(choice, column, verdicts[column][2]) for choice, column in wanted.items()
              if verdicts[column][:2] == (True, False)]
    if len(owners) != 1 or any((owner, excluded) not in {(True, False), (False, True)}
                               for owner, excluded, _ in verdicts.values()):
        return None
    choice, column, note = owners[0]
    return choice, note["id"], {"column": column, "note": note["locator"], "packet_sha256": packet["packet_sha256"],
            "region": (facts["member_path"], facts["header_row"], facts["region_last_row"])}


def run(case_id, import_id, body):
    found = discover(case_id, import_id, body)
    if found is None:
        return None
    packet, headings, wanted, notes = found
    certified = _explicit_certificate(packet, headings, wanted, notes)
    if certified is None:
        # The only accepted certificate for this envelope is an explicit role
        # declaration covering all competing columns. A model cannot replace it.
        return {"state": "pending", "reason": "decision_unproved", "packet": packet,
                "first_calls": 0, "recheck_calls": 0}
    choice, witness, certificate = certified
    result = {"state": "pending", "reason": "decision_layout_not_admitted", "packet": packet,
              "first_calls": 0, "recheck_calls": 0}
    current = discover(case_id, import_id, body)
    if current is None or current[0]["packet_sha256"] != packet["packet_sha256"]:
        raise assistance.ServiceError(409, "assistance_stale", "原件或核对范围已更新，请重新读取")
    prepared = import_trial_rules.prepare_certified(case_id, import_id, body.revision, certificate)
    if prepared is None:
        return result
    packet["selection"] = {"status": "review_only", "choice": choice, "refs": [choice, witness], "gap": None}
    return {**result, "state": "rule", "reason": "rule_evidence", "prepared_preview": prepared}
