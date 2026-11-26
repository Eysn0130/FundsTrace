"""Source checks for column decisions in the desktop trial."""
from collections import Counter
import json
import re
from types import SimpleNamespace

from .. import table_import, table_source_roles
from ..table_mapping import identity_field
from . import assistance, import_ready_evidence


_INTRO = re.compile(r"^(?:字段口径|字段说明|口径说明|导出说明)[:：]")
_VERB = re.compile(r"^(?:列|栏)?\s*(?:是|为|指|对应|表示|记载|记录)\s*(.+)$")
_POSTING_OWNER = re.compile(r"(?:每笔|逐笔)(?:资金|款项)?(?:实际)?(?:记入|入账|收入)(?:或|与)"
                            r"(?:付出|支出|出账)的本方(?:账簿)?(?:账号|账户)")
_QUERY_NUMBER = re.compile(r"(?:发起查询时)?(?:保留的|使用的|登记的)?(?:检索|查询)(?:号码|编号)"
                           r"(?:[,，]不是逐笔本方(?:账号|账户)?)?")
_EXCLUDED_USE = r"(?:查询(?:条件)?|检索(?:条件)?|(?:历史)?档案(?:查阅|查询)|资料索引|文件保管用途)"
_EXCLUDED = re.compile(r"(?:列|栏)?(?:只|仅)(?:用于|供|作|是)" + _EXCLUDED_USE
                       + r"(?:[,，](?:不参加这些交易|不用于逐笔交易|不在本流水中发生收付款|不表示逐笔记账账户))?")
_NON_POSTING = re.compile(r"(?:归集备用|备用|历史归档|历史档案|归档|代理人|经办人|代办人)"
                          r"(?:本方|本端|本户)?(?:账号|帐号|账户|帐户|卡号|户名|姓名|账户名称)")
_CONTEXT = re.compile(r"^(?:本次流水|本表区|当前表区|该表区|本表|本笔)?(?:逐笔交易|每笔交易|交易|逐笔收支|实际收付|逐笔)?(?:的)?")
_TYPES = {"account": ("账号", "帐号", "账户", "帐户", "account", "acct"),
          "card": ("卡号", "账卡", "card"),
          "name": ("户名", "姓名", "名称", "持有人", "name", "holder")}
_SIDES = {"self": ("本方", "本端", "本户", "调单", "自有", "self", "own"),
          "counter": ("对方", "对端", "对手", "counter"),
          "payer": ("付款", "汇出", "转出", "payer", "sender"),
          "payee": ("收款", "汇入", "转入", "payee", "recipient")}
_VALUES = {"supported", "refuted"}
_FIELDS = assistance._FIELDS
OWN_ACCOUNT_ALIASES = {"本端账号", "本端帐号", "本端账户"}
_BUSINESS_MARKERS = frozenset({"存", "取", "存款", "取款", "收", "付", "进", "出", "借", "贷", "贷/收", "借/付",
                               "收入", "支出", "入账", "出账", "转入", "转出", "转账", "现金"})


def own_account_alias(value):
    return re.sub(r"\s*\[列[1-9]\d*\]$", "", table_import._norm(value)) in OWN_ACCOUNT_ALIASES


def _description(text, field, *, local=False):
    # A complete, scoped role declaration can be checked. Qualifiers and
    # conditional statements outside this grammar remain for manual review.
    normalized = table_import._norm(text)
    if local and field == "self_account":
        if _POSTING_OWNER.fullmatch(normalized):
            return "supported"
        if _QUERY_NUMBER.fullmatch(normalized):
            return "refuted"
    if _NON_POSTING.fullmatch(normalized) or re.fullmatch(_EXCLUDED_USE, normalized):
        return "refuted"
    declared = _CONTEXT.sub("", normalized, count=1)
    target = table_import._norm(_FIELDS[field])
    if declared in {target, target.replace("本方", "本端"), target.replace("账号", "账户")} or identity_field(declared) == field:
        return "supported"
    other = identity_field(declared)
    if other is not None or declared in {table_import._norm(label) for label in _FIELDS.values()}:
        return "refuted"
    return None


def _independent_note(cells, columns, facts):
    """Locate the physical header a source note addresses, before interpreting it."""
    if len(cells) == 3 and facts.get("region_last_row") and not facts["more_columns"] and facts["column_start"] == 1:
        first = next(iter(columns.values()))["locator"]
        physical = [row["raw"]["heading"] for row in columns.values()]
        match = table_source_roles.scoped_field_note(
            cells, first["sheet"], facts["header_row"], facts["region_last_row"], physical,
        )
        if match:
            return f"C{match[0]}", match[1], True
    if len(cells) != 2:
        return None
    link = re.fullmatch(r"(.+)!([A-Z]{1,3})([1-9][0-9]*) (.+)", cells[0])
    if not link or int(link[3]) != facts["header_row"]:
        return None
    column = 0
    for letter in link[2]:
        column = column * 26 + ord(letter) - 64
    ref = f"C{column}"
    if (ref in columns and link[1] == columns[ref]["locator"]["sheet"]
            and table_import._norm(link[4]) == table_import._norm(columns[ref]["raw"]["heading"])):
        return ref, cells[1], False
    return None


def _claims(packet, columns, notes):
    facts = packet["facts"]
    headings = {ref: table_import._norm(row["raw"]["heading"]) for ref, row in columns.items()}
    frequency = Counter(headings.values())
    # Longer headings take precedence, for example 本端账号(查询条件).
    headings = dict(sorted(headings.items(), key=lambda entry: -len(entry[1])))
    # A comma separates declarations only when a new physical heading and
    # its verb follow. Keep commas carrying limits or exceptions in the claim.
    actors = "|".join(re.escape(heading) for heading in headings.values() if heading)
    separator = (rf"[；;。\n]|[,，](?=[“\"]?(?:{actors})[”\"]?(?:列|栏)?"
                 r"(?:是|为|指|对应|表示|记载|记录|只|仅))")
    bindings = {ref: {} for ref in columns}
    unclear = set()
    for note, row in notes.items():
        cells = [cell for cell in row["raw"]["cells"] if cell.strip()]
        location = row["locator"]
        same_member = location["member_path"] == facts["member_path"]
        if same_member:
            if location["source_row"] != facts["header_row"] - 1 or not cells:
                continue
            stated = cells[0].strip()
            local = table_source_roles.INLINE_FIELD_NOTE.match(stated)
            intro = local or _INTRO.match(stated)
            if intro is None or local and not facts.get("inline_note_scope_complete"):
                continue
            body = stated[intro.end():]
            whole = row["structured"].get("whole_note") and len(cells) == 1
            incomplete_local = False
            for clause in re.split(separator, table_import._norm(body)):
                clause = clause.lstrip("“\"").rstrip("”\"")
                for ref, heading in headings.items():
                    if not heading or not clause.startswith(heading):
                        continue
                    suffix = clause[len(heading):].lstrip("”\"")
                    match = _VERB.fullmatch(suffix)
                    if not match and not _EXCLUDED.fullmatch(suffix):
                        continue
                    verdict = ("refuted" if not match else _description(match[1], facts["field"], local=bool(local))) if whole else None
                    if frequency[heading] != 1:
                        unclear.update(key for key, label in headings.items() if label == heading)
                        incomplete_local = True
                    elif verdict is None or bindings[ref].get(note, verdict) != verdict:
                        unclear.add(ref)
                        incomplete_local = True
                    else:
                        bindings[ref][note] = verdict
                    break
                else:
                    if clause:
                        incomplete_local = True
                        # An unparsed qualification mentioning a column can
                        # change a completed declaration in the same note.
                        unclear.update(ref for ref, heading in headings.items() if heading and heading in clause)
            if local and incomplete_local:
                unclear.update(ref for ref, votes in bindings.items() if note in votes)
        elif (binding := _independent_note(cells, columns, facts)) is not None:
            ref, text, scoped = binding
            if scoped:
                suffix = table_import._norm(text)
                use = _VERB.fullmatch(suffix)
                verdict = ("refuted" if _EXCLUDED.fullmatch(suffix) else _description(use[1], facts["field"]) if use else None)
            else:
                verdict = _description(text, facts["field"])
            if verdict is not None and row["structured"].get("whole_note"):
                bindings[ref][note] = verdict
            else:
                unclear.add(ref)
    conflicts = {ref for ref, votes in bindings.items() if len(set(votes.values())) > 1}
    unclear.update(conflicts)
    return {ref: votes for ref, votes in bindings.items() if ref not in unclear and votes}, unclear, conflicts


def _has_data_row_note(notes, columns, facts):
    if facts["more_columns"] or not facts.get("region_last_row"):
        return False
    for note in notes.values():
        if not note["structured"].get("whole_note") or note["locator"]["member_path"] == facts["member_path"]:
            continue
        cells = [value for value in note["raw"]["cells"] if value.strip()]
        if not (link := table_import._field_note_address(cells)):
            continue
        sheet, position, column, heading = link
        candidate = columns.get(f"C{column}")
        if (candidate and candidate["locator"]["sheet"] == sheet
                and table_import._norm(candidate["raw"]["heading"]) == table_import._norm(heading)
                and facts["header_row"] < int(position) <= facts["region_last_row"]):
            return True
    return False


def account_use_code_review(packet):
    facts = packet["facts"]
    coverage = facts.get("column_coverage")
    if (facts["field"] != "self_account" or not facts.get("account_use_code_notes")
            or facts["column_start"] != 1 or facts["more_columns"] or not facts.get("support_coverage_complete")
            or not coverage or not coverage["complete"]):
        return None, None
    columns = [row for row in packet["records"] if "column" in row["locator"]]
    notes = {row["locator"]["source_row"]: row for row in packet["records"]
             if row["locator"]["member_path"] == facts["resolved_support_member"]}
    if not columns or any(not row["structured"].get("whole_note") for row in notes.values()):
        return None, None
    headings = [row["raw"]["heading"] for row in columns]
    if any(len(head) >= 100 for head in headings):
        return None, None
    outcome = table_source_roles.review_account_use_codes(
        {number: row["raw"]["cells"] for number, row in notes.items()},
        sheet=columns[0]["locator"]["sheet"], header_row=facts["header_row"], last_row=facts["region_last_row"],
        headings=headings, columns=table_import._account_use_code_columns(headings, facts["parser_version"]),
    )
    if isinstance(outcome, table_source_roles.AccountUseCodeIssue):
        return None, {
            "reason": outcome.reason, "code": outcome.code, "member_path": facts["member_path"],
            "header_row": facts["header_row"], "last_row": facts["region_last_row"],
            "notes": [{key: notes[row]["locator"][key] for key in ("member_path", "source_row")}
                      for row in outcome.source_rows],
        }
    if outcome is None or f"C{outcome.column}" in {item["id"] for item in packet["candidates"] if item["refuted"]}:
        return None, None
    return (outcome.column, notes[outcome.binding_row]["locator"], notes[outcome.definition_row]["locator"]), None


def _sample_shape(value, kind, groups, *, complete=True):
    if value is None:
        return {"类型": "缺格"}
    if value == "":
        return {"类型": "空格"}
    if kind in {"formula", "error", "boolean"}:
        return {"类型": kind}
    result = {"类型": kind or "文本"}
    if value.isdecimal():
        result["字面数字"] = True
        if len(value) > 1 and value.startswith("0"):
            result["有前导零"] = True
    if not complete:
        result["原值截断"] = True
        return result
    if value in _BUSINESS_MARKERS:
        result["业务原词"] = value
    if re.fullmatch(r"[+-]?0+(?:\.0+)?", value):
        result["原值为零"] = True
    result["相同原值组"] = groups.setdefault(value, f"V{len(groups) + 1}")
    return result


def _initial_samples(columns):
    groups, samples = {}, {}
    for ref, row in columns.items():
        raw = row["raw"]
        positions = dict((row["structured"].get("column_profile") or {}).get("sample_positions", ()))
        samples[ref] = [{**_sample_shape(value, positions.get(number), groups,
                                        complete=not (row["structured"].get("text_excerpt") and len(value or "") >= 100)),
                         "原行": number}
                        for number, value in zip(raw.get("rows", ()), raw["cells"], strict=True)]
    return groups, samples


def review_offers(packet, view):
    """Offer unseen first differences already found by the full-region scan."""
    facts = packet["facts"]
    coverage = facts.get("column_coverage")
    if not coverage or not coverage["complete"]:
        return {}
    shown = set(facts["sampled_rows"])
    references = set(view["candidates"])
    positions = {item["first_difference"]
                 for column in view["candidates"]
                 for item in (view["records"][column].get("全列统计") or {}).get("comparisons", ())
                 if f"C{item['column']}" in references and item["first_difference"] is not None
                 and item["first_difference"] not in shown
                 and coverage["first_row"] <= item["first_difference"] <= coverage["last_row"]}
    return {f"L{row}": {"原行": row, "物理列": [view["records"][ref]["物理列"] for ref in view["candidates"]],
                       "用途": "读取同一表区的候选列原值差异"}
            for row in sorted(positions)[:3]}


def read_review_row(packet, view, offer):
    facts = packet["facts"]
    row = offer["原行"]
    page = import_ready_evidence.layout(facts["case_id"], facts["import_id"], SimpleNamespace(
        revision=facts["revision"], member_path=facts["member_path"], row_start=row, column_start=facts["column_start"]))
    if page["sha256"] != facts["sha256"] or page["member"]["member_path"] != facts["member_path"]:
        raise assistance.ServiceError(409, "assistance_stale", "原件或核对范围已更新，请重新读取")
    [original] = (entry for entry in page["rows"] if entry["source_row"] == row)
    by_column = {cell["column"]: cell for cell in original["cells"]}
    initial = {record["id"]: record for record in packet["records"] if record["id"] in view["candidates"]}
    groups, _ = _initial_samples(initial)
    cells = []
    for ref in view["candidates"]:
        column = view["records"][ref]["物理列"]
        cell = by_column.get(column)
        evidence = _sample_shape(cell["value"], cell["kind"], groups) if cell else {"类型": "缺格"}
        cells.append({"引用": ref, "物理列": column, **evidence})
    return {"表头原行": facts["header_row"], "原行": row, "候选列样值": cells}


def prepare(packet, excerpt):
    """Build the model's view and keep the independent citation checks local."""
    records = {record["id"]: record for record in packet["records"]}
    columns = {ref: row for ref, row in records.items() if "column" in row["locator"]}
    notes = {ref: row for ref, row in records.items() if ref.startswith("N")}
    facts = packet["facts"]
    if facts["field"] == "self_account" and facts.get("account_use_code_notes"):
        proof, issue = account_use_code_review(packet)
        return ("rule_confirmed" if proof else issue["reason"] if issue else "insufficient_evidence"), None, {}
    proofs, unclear, conflicts = _claims(packet, columns, notes)
    blocked = {item["id"] for item in packet["candidates"] if item["refuted"]}
    if facts.get("wide_role_conflict") or conflicts - blocked:
        return "source_role_conflict", None, proofs
    registered = {ref for ref, row in columns.items() if ref not in blocked and row["structured"]["role_check"] == "supported"}
    if registered:
        contradicted = {ref for ref in registered if ref in unclear or "refuted" in proofs.get(ref, {}).values()}
        competing_alias = facts["field"] == "self_account" and any(
            ref not in registered | blocked and own_account_alias(row["raw"]["heading"])
            and "refuted" not in proofs.get(ref, {}).values() for ref, row in columns.items()
        )
        if contradicted or competing_alias or any(ref not in registered and "supported" in votes.values()
                                                   for ref, votes in proofs.items()):
            return "source_role_conflict", None, proofs
        return "rule_confirmed", None, proofs
    side, field_type = facts["field"].rsplit("_", 1)
    candidates = {}
    for ref, row in columns.items():
        if ref in blocked or row["structured"]["empty"]:
            continue
        heading = table_import._norm(row["raw"]["heading"])
        if ref in proofs or (any(word in heading for word in _SIDES[side]) and any(term in heading for term in _TYPES[field_type])):
            candidates[ref] = row
    leads = [ref for ref in candidates if "supported" in proofs.get(ref, {}).values()]
    if not leads:
        reason = table_import.WRONG_FIELD_NOTE_ROW if _has_data_row_note(notes, columns, facts) else "insufficient_evidence"
        return reason, None, proofs
    if len(candidates) == 1:
        if facts["column_start"] == 1 and not facts["more_columns"]:
            return "single_original_lead", leads[0], proofs
        return "insufficient_evidence", None, proofs
    if facts["column_start"] != 1 or facts["more_columns"]:
        return "insufficient_evidence", None, proofs
    note_links = {column: [] for column in candidates}
    for ref, note in notes.items():
        cells = [cell for cell in note["raw"]["cells"] if cell.strip()]
        if not cells or not note["structured"].get("whole_note"):
            continue
        location = note["locator"]
        if location["member_path"] == facts["member_path"]:
            if location["source_row"] == facts["header_row"] - 1:
                text = table_import._norm(cells[0])
                for column, row in candidates.items():
                    if table_import._norm(row["raw"]["heading"]) in text:
                        note_links[column].append(ref)
        elif (binding := _independent_note(cells, columns, facts)) and binding[0] in note_links:
            note_links[binding[0]].append(ref)
    visible_notes = [ref for ref in notes if any(ref in links for links in note_links.values())]
    _, samples = _initial_samples(candidates)
    display = {ref: {"表头": excerpt(row["raw"]["heading"]),
                     "原行": row["locator"]["source_row"], "物理列": row["locator"]["column"],
                     "样本类型": ["empty" if not cell else "digits" if cell.isdecimal() else "text" for cell in row["raw"]["cells"]],
                     "样值形态": samples[ref],
                     "父表头": excerpt(row["raw"].get("parent_heading", "")),
                     "全列统计": row["structured"].get("column_profile"),
                     "说明位置": note_links[ref]}
               for ref, row in candidates.items()}
    display.update({ref: {"说明": [excerpt(cell) for cell in row["raw"]["cells"] if cell],
                          "说明原行": row["locator"]["source_row"],
                          "说明来源": "本表区前置说明" if row["locator"]["member_path"] == facts["member_path"] else "独立说明表",
                          "完整": row["structured"].get("whole_note", False)}
                    for ref, row in notes.items() if ref in visible_notes})
    target = ("每笔流水实际收支的本方账号" if facts["field"] == "self_account" else
              f"每笔流水的{_FIELDS[facts['field']]}")
    return None, {"domain": "import", "target": target, "candidates": list(candidates), "notes": visible_notes,
                  "records": display, "table_rows": [facts["header_row"], facts.get("region_last_row")]}, proofs


def checked(reply, view):
    if not isinstance(reply, dict) or set(reply) != set(view["candidates"]):
        return False
    for column, entry in reply.items():
        if not isinstance(entry, dict) or set(entry) != {"finding", "evidence"}:
            return False
        finding, evidence = entry["finding"], entry["evidence"]
        if not isinstance(finding, str) or finding not in _VALUES | {"unknown"} or not isinstance(evidence, list):
            return False
        if finding == "unknown":
            if evidence:
                return False
        elif len(evidence) > 2 or any(
            not isinstance(ref, str) or ref not in {column, *view["notes"]} for ref in evidence
        ) or len(set(evidence)) != len(evidence):
            return False
    return True


def verified(entries, proofs):
    """Verify cited polarity separately from model confidence."""
    valid = {}
    conflicts = []
    for column, entry in entries.items():
        evidence = entry["evidence"]
        if len(evidence) != 2 or column not in evidence:
            continue
        note = next(ref for ref in evidence if ref != column)
        actual = proofs.get(column, {}).get(note)
        if actual is None:
            continue
        if entry["finding"] == actual:
            valid[column] = (actual, note)
        else:
            conflicts.append({"column": column, "note": note, "claimed": entry["finding"], "verified": actual})
    return valid, conflicts


def complete(view, verdicts):
    if set(verdicts) != set(view["candidates"]):
        return None
    matches = [(column, note) for column, (finding, note) in verdicts.items() if finding == "supported"]
    return matches[0] if len(matches) == 1 else None


def _mapped_column(columns, selected):
    if type(selected) is int:
        return f"C{selected}"
    if isinstance(selected, str):
        if match := re.fullmatch(r"(.*?)\s*\[列([1-9]\d*)\]", selected):
            ref = f"C{match[2]}"
            if ref in columns and table_import._norm(columns[ref]["raw"]["heading"]) == table_import._norm(match[1]):
                return ref
        candidates = [ref for ref, row in columns.items()
                      if table_import._norm(row["raw"]["heading"]) == table_import._norm(selected)]
        if len(candidates) == 1:
            return candidates[0]
    return None


def _wide_own_alias_competes(session, member, header):
    """Check the owning column window without sending another page to models."""
    from . import imports

    with imports._source(session, snapshot=True) as source:
        cells = import_ready_evidence._cells(source, session, member, {header}, imports._table_profile(session),
                                             for_layout=True)[header]
    return len(cells) > 16 and any(own_account_alias(cell["value"]) for cell in cells if cell["kind"] == "text")


def mapping_conflict(session, physical_regions=None):
    """Check the current ready mapping before a desktop trial can start it."""
    stored = json.loads(session["request_json"] or "{}")
    manifest = stored.get(import_ready_evidence._KEY)
    if not manifest or not manifest.get("layout_members") or session["stage"] != "ready":
        return False
    supports = [entry["scope"]["member_path"] for entry in manifest["layout_members"]
                if entry["scope"]["sheet"] in assistance._SCOPED_NOTE_SHEETS]
    if len(supports) > 1:
        return True
    support = supports[0] if supports else None
    if physical_regions is None:
        physical_regions = stored.get("_confirmed_layout_v1", {}).get("regions")
    if physical_regions is not None:
        areas = [(span["member_path"], span["header_row"], span["mapping"]) for span in physical_regions]
    else:
        manual = json.loads(session["mapping_json"] or "{}") if stored.get("_table_overrides") else None
        areas = [(area["scope"]["member_path"], area["scope"]["header_row"],
                  manual if manual is not None else {field: None for field, proof in area["rules"].items() if proof})
                 for area in manifest["regions"]]
    alias_members = {entry["scope"]["member_path"] for entry in manifest["layout_members"]
                     if any(own_account_alias(label) for label in entry["document"]["columns"])}
    for member, header, mapping in areas:
        if header <= 1 and not support and member not in alias_members:
            continue
        for field, selected in mapping.items():
            if field not in _FIELDS:
                continue
            body = SimpleNamespace(revision=manifest["revision"], member_path=member,
                                   header_row=header, column_start=1, support_member_path=support,
                                   field=field, reply=None)
            packet = assistance.import_assistance(str(session["case_id"]), session["import_id"], body, column_trial=True)
            if packet["facts"].get("support_coverage_complete") is False:
                return True
            if field == "self_account" and packet["facts"].get("account_use_code_notes"):
                code, _ = account_use_code_review(packet)
                columns = {row["id"]: row for row in packet["records"] if "column" in row["locator"]}
                if code is None or _mapped_column(columns, selected) != f"C{code[0]}":
                    return True
                continue
            if selected is None:
                early, _, _ = prepare(packet, lambda text: text)
                if early == "source_role_conflict":
                    return True
            else:
                columns = {row["id"]: row for row in packet["records"] if "column" in row["locator"]}
                notes = {row["id"]: row for row in packet["records"] if row["id"].startswith("N")}
                ref = _mapped_column(columns, selected)
                proofs, unclear, _ = _claims(packet, columns, notes)
                if ref in unclear or "refuted" in proofs.get(ref, {}).values():
                    return True
    return False
