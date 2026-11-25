"""Locate a ledger through dated registration rows retained in this case."""
from contextlib import closing
import csv
from datetime import date
from hashlib import sha256
from io import StringIO
import json
import re
import unicodedata

from .. import analysis_snapshot, db, feedback_import, import_originals, table_import, table_source_roles
from . import assistance, cases, imports, trace_review, trial_pair_comparison


_MEMBER_LIMIT = 32
_MEMBER_BYTES = 128 * 1024
_FLOW_LIMIT = 24
_OBSERVATION_MEMBERS = 8
_OBSERVATION_BYTES = 8 * 1024 * 1024
_OBSERVATION_ROWS = 10_000
_OBSERVATION_EXAMPLES = 8
_SNAPSHOT_FIELDS = {"name": "账户开户名称", "document": "开户人证件号码", "card": "交易卡号", "account": "交易账号",
                    "currency": "币种", "opening": "账号开户时间", "closing": "销户日期", "branch": "账号开户银行",
                    "balance": "账户余额", "available": "可用余额", "last_transaction": "最后交易时间"}


def _text(value):
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _date(value):
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("expected ISO calendar date")
    return parsed


def _full_card(value):
    card = value.strip()
    return re.fullmatch(r"[0-9]{8,24}", card) is not None and bool(card.strip("0"))


def _registrations(data):
    """Classify the complete dated source shape before reading its rows."""
    try:
        raw = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raw = data.decode("gb18030")
    reader = csv.reader(StringIO(raw, newline=""), strict=True)
    if tuple(next(reader, ())) != table_source_roles.DATED_ACCOUNT_REGISTRY:
        return ()
    result = []
    for row in reader:
        if not any(row):
            continue
        result.append((reader.line_num, *table_source_roles.dated_account_record(row)))
    return result


def _members(case_id):
    return db.q_raw("""select distinct m.source_id,m.member_path,m.sha256,m.size_bytes,s.sha256 source_sha256
        from import_members m
        join import_sources s on s.case_id=m.case_id and s.source_id=m.source_id and s.origin='upload'
        join import_source_operations o on o.case_id=m.case_id and o.source_id=m.source_id and o.active
        join import_sessions u on u.case_id=m.case_id and u.import_id=m.source_id and u.stage='done'
        where m.case_id=? and right(lower(m.member_path),4)='.csv'
          and json_extract_string(u.request_json,'$._intake_policy')=?
          and exists(select 1 from import_records r where r.case_id=m.case_id and r.source_id=m.source_id
            and r.member_path=m.member_path and (r.disposition='unresolved'
             or json_extract_string(r.details_json,'$.intake_material_role')='ledger_registration'))
        order by m.source_id,m.member_path limit ?""", [case_id, imports._INTAKE_POLICY, _MEMBER_LIMIT + 1])


def _account_catalog(case_id):
    accounts = {}
    for row in db.q_raw("select entity_id,details_json from resolution_entities where case_id=?", [case_id]):
        data = json.loads(row["details_json"])
        if data["kind"] == "account" and data.get("bank") and data.get("account_number"):
            accounts.setdefault((_text(data["bank"]), data["account_number"]), []).append(row["entity_id"])
    return accounts


def _observations(case_id, account, start, end):
    return db.q_raw("""select cast(id as varchar) id,cast(trade_time as varchar) clock,
               cast(amount as varchar) amount,currency,case when payer_account=self_account then 'out' else 'in' end direction
        from effective_visible_flows where case_id=? and self_account=? and
            cast(trade_time as date) between ? and ? and
            ((payer_account=self_account)<>(payee_account=self_account))
        order by trade_time,id limit ?""", [case_id, account, start, end, _FLOW_LIMIT + 1])


def _observation_members(case_id, *, snapshots_only=False):
    return db.q_raw("""select s.source_id,s.sha256,s.size_bytes,s.parser_version,m.member_path,
            m.sha256 member_sha256,m.role,m.encoding,u.suffix
        from import_sources s join import_source_operations o
          on o.source_id=s.source_id and o.case_id=s.case_id and o.active
        join import_sessions u on u.case_id=s.case_id and u.import_id=s.source_id and u.stage='done'
          and u.sha256=s.sha256 and u.size_bytes=s.size_bytes
        join import_members m on m.case_id=s.case_id and m.source_id=s.source_id
        where s.case_id=? and s.origin='upload' and json_extract_string(u.request_json,'$._intake_policy')=?
          and (not ? or m.role='auxiliary')
          and ((u.suffix='.xlsx' and s.parser_version=? and m.role='table' and m.encoding='xlsx')
            or (u.suffix='.csv' and s.row_kind='table_record' and m.member_path='table.csv'
              and m.sha256=s.sha256 and m.size_bytes=s.size_bytes
              and ((s.parser_version=? and m.role='table'
                  and (json_extract_string(u.result_json,'$.table_summary.documents[0].columns[*]')=?::varchar[]
                    or list_sort(json_extract_string(u.result_json,'$.table_summary.documents[0].columns[*]'))=?::varchar[]))
                or (s.parser_version=? and m.role='auxiliary'
                  and json_extract_string(u.result_json,'$.table_summary.documents[0].role')='account'))))
        qualify row_number() over (partition by s.sha256,m.sha256 order by s.source_id)=1
        order by (u.suffix='.xlsx') desc,(m.role='auxiliary') desc,s.source_id,m.member_path limit ?""",
                    [case_id, imports._INTAKE_POLICY, snapshots_only, table_import.VOUCHER_REFERENCE_PROFILE,
                     table_import.VOUCHER_REFERENCE_PROFILE, list(feedback_import.COLUMNS),
                     sorted(table_import.BANK_FEEDBACK_32_COLUMNS), table_import.VOUCHER_REFERENCE_EVIDENCE_PROFILE,
                     _OBSERVATION_MEMBERS + 1])


def _flow_links(case_id, member, positions):
    with db._lock:
        links = db.q_raw("""select r.source_row,f.flow_id
            from import_records r join flow_sources f
              on f.case_id=r.case_id and f.source_id=r.source_id and f.source_row=r.record_no
            where r.case_id=? and r.source_id=? and r.member_path=? and r.disposition in ('imported','duplicate')
              and r.source_row in (select unnest(?::bigint[]))""", [case_id, member["source_id"], member["member_path"], positions])
        if not links:
            return {}
        # Resolve the original links first, then check them in the context's view.
        active = {row["id"] for row in db.q_raw("""select id from effective_visible_flows
            where case_id=? and id in (select unnest(?::bigint[]))""", [case_id, [row["flow_id"] for row in links]])}
    return {row["source_row"]: str(row["flow_id"]) for row in links if row["flow_id"] in active}


def _workbook_observation(case_id, member, body, start, end):
    document, _ = trace_review._original_layout(case_id, member)
    last = document.get("source_rows")
    if document.get("header_rows") != [1]:
        return None, False
    if type(last) is not int or not 1 < last <= _OBSERVATION_ROWS or member["size_bytes"] > _OBSERVATION_BYTES:
        return None, False
    try:
        stream, _ = imports.get_source_file(case_id, member["source_id"])
        with closing(stream):
            raw = stream.read(_OBSERVATION_BYTES + 1)
        if sha256(raw).hexdigest() != member["sha256"]:
            return None, False
        sheets = table_import._excel(raw, ".xlsx", mark_errors=True)
    except (OSError, ValueError, table_import.TableImportError):
        return None, False
    except cases.ServiceError as error:
        if error.code not in assistance._SOURCE_ERRORS:
            raise
        return None, False
    sheet = next((item for item in sheets if item["path"] == member["member_path"]
                  and item["sha256"] == member["member_sha256"] and item["name"] == document.get("sheet")), None)
    if sheet is None or len(sheet["rows"]) != last:
        return None, False
    header, *rows = sheet["rows"]
    headings = [value.strip() if type(value) is str else "" for value in header]
    card_heading, name_heading, clock_heading = "本方卡号", "姓名(查询条件)", "交易时间"
    if (table_source_roles.classify(headings, query_conditions=True) != "transaction_explicit"
            or not {card_heading, name_heading, clock_heading} <= set(headings)):
        return None, True
    card_column, name_column, clock_column = (headings.index(value) for value in (card_heading, name_heading, clock_heading))
    matches = []
    wanted = body.value.strip()
    for position, row in enumerate(rows, 2):
        card, query_name, clock = row[card_column], row[name_column], row[clock_column]
        card = card.strip() if type(card) is str and _full_card(card) else None
        match = card == wanted if body.kind == "card" else type(query_name) is str and _text(query_name) == _text(wanted)
        if not match or not (stamp := table_import._time(clock)) or not start <= stamp.date() <= end:
            continue
        matches.append((position, card))
    if not matches:
        return None, True
    first_by_card = {}
    for position, card in matches:
        if card:
            first_by_card.setdefault(card, position)
    chosen = list(dict.fromkeys([*((position, card) for card, position in first_by_card.items()), *matches]))[:_OBSERVATION_EXAMPLES]
    ids = _flow_links(case_id, member, [position for position, _ in chosen])
    return {"source_id": member["source_id"], "member_path": member["member_path"], "header_row": 1,
            "role": "own_card" if body.kind == "card" else "query_name",
            "match_column": (card_column if body.kind == "card" else name_column) + 1,
            "card_column": card_column + 1, "matching_rows": len(matches), "distinct_cards": len(first_by_card),
            "rows_without_full_card": sum(card is None for _, card in matches),
            "examples": [{"source_row": position, "card_suffix": card[-4:] if card else None, "flow_id": ids.get(position)}
                         for position, card in chosen]}, True


def _full_account(value):
    return re.fullmatch(r"[0-9]{1,128}", value) is not None and bool(value.strip("0"))


def _csv_observation(case_id, member, matches, *, role, match_column, card_column, account_column):
    if not matches:
        return None
    first_by_key, first_by_direction, first_by_key_and_direction = {}, {}, {}
    for row, (card, account, _, side) in matches.items():
        first_by_key.setdefault((card, account), row)
        if side:
            first_by_direction.setdefault(side, row)
            first_by_key_and_direction.setdefault((card, account, side), row)
    priorities = dict.fromkeys([*first_by_direction.values(), *first_by_key.values(),
                               *first_by_key_and_direction.values(), *matches])
    chosen = list(priorities)[:_OBSERVATION_EXAMPLES]
    ids = {} if role == "account_snapshot" else _flow_links(case_id, member, chosen)
    examples = []
    for row in sorted(chosen):
        card, account, clock, direction = matches[row]
        examples.append({"source_row": row, "card_suffix": card[-4:] if card else None, "account": account,
                         "flow_id": ids.get(row), "clock": clock, "direction": direction,
                         **({"card_equals_account": True} if role == "account_snapshot" and card == account else {})})
    return {"source_id": member["source_id"], "member_path": member["member_path"], "header_row": 1,
            "role": role, "match_column": match_column, "card_column": card_column, "account_column": account_column,
            "matching_rows": len(matches), "distinct_cards": len({card for card, _ in first_by_key if card}),
            "distinct_accounts": len({account for _, account in first_by_key if account is not None}),
            "rows_without_full_card": sum(not card for card, *_ in matches.values()), "examples": examples}


def _snapshot_original(case_id, member):
    """Read the declared snapshot columns from a registered, active account table."""
    with db._lock:
        original = imports._registered_original(case_id, member["source_id"])
        document, _ = trace_review._original_layout(case_id, member)
        last = document.get("source_rows")
        if (original["active_batch_id"] is None or original["suffix"] != ".csv"
                or original["sha256"] != member["member_sha256"] or original["size_bytes"] > _OBSERVATION_BYTES
                or document.get("role") != "account" or document.get("header_rows") != [1]
                or type(last) is not int or not 1 < last <= _OBSERVATION_ROWS):
            return None
        positions = {row["source_row"] for row in db.q_raw("""select source_row from import_records
            where case_id=? and source_id=? and member_path=? and disposition='auxiliary'
              and json_extract_string(details_json,'$.source_role')='account'""",
            [case_id, member["source_id"], member["member_path"]])}
    try:
        with closing(imports._source(original, snapshot=True)) as stream:
            rows = import_originals._csv_rows(stream, member["encoding"], document, set(range(1, last + 1)),
                                             member["parser_version"])
    except (OSError, ValueError, csv.Error):
        return None
    except cases.ServiceError as error:
        if error.code not in assistance._SOURCE_ERRORS:
            raise
        return None
    headings = tuple(cell.strip() for cell in rows.get(1, ()))
    if (len(rows) != last or headings != tuple(document.get("columns", ()))
            or any(headings.count(label) != 1 for label in _SNAPSHOT_FIELDS.values())):
        return None
    required = {_SNAPSHOT_FIELDS[key] for key in ("account", "card", "name")}
    # Repeated role headings mean that a physical row may belong to a
    # different account listing, even if both were classified as auxiliary.
    if any(len(required.intersection(cell.strip() for cell in cells)) >= 2
           for number, cells in rows.items() if number != 1):
        return None
    columns = {role: headings.index(label) + 1 for role, label in _SNAPSHOT_FIELDS.items()}
    return columns, {number: {role: cells[column - 1].strip() for role, column in columns.items()}
                    for number, cells in rows.items() if number in positions and len(cells) == len(headings)}


def _account_snapshot(case_id, member, body):
    # Opening, closing and last-transaction dates in this snapshot do not
    # certify cardholder coverage for a queried transaction period.
    found = _snapshot_original(case_id, member)
    if found is None:
        return None, set(), False
    columns, rows = found
    wanted = body.value.strip()
    matches = {}
    keys = set()
    for position, row in rows.items():
        card, account = row["card"], row["account"]
        selected = row[body.kind] == wanted
        if selected and _full_card(card) and _full_account(account):
            matches[position] = (card, account, None, None)
            keys.add((card, account, trial_pair_comparison._feedback_currency(row["currency"])))
    observation = _csv_observation(case_id, member, matches, role="account_snapshot",
                                   match_column=columns[body.kind], card_column=columns["card"], account_column=columns["account"])
    if observation:
        observation["registration_columns"] = columns
    return observation, keys, True


def account_registration_context(case_id, postings):
    """Find same-case lexical account and currency references without a time binding."""
    with db._lock:
        revision = analysis_snapshot.revision(case_id)
        members = _observation_members(case_id, snapshots_only=True)
    complete = len(members) <= _OBSERVATION_MEMBERS
    matched = []
    for member in members[:_OBSERVATION_MEMBERS]:
        found = _snapshot_original(case_id, member)
        if found is None:
            complete = False
            continue
        columns, rows = found
        for position, row in rows.items():
            if not _full_account(row["account"]):
                continue
            currency = trial_pair_comparison._feedback_currency(row["currency"])
            for source_row, account, posted_currency, original_card in postings:
                if (row["account"], currency) != (account, posted_currency):
                    continue
                if len(matched) >= _OBSERVATION_EXAMPLES:
                    complete = False
                    break
                matched.append(({
                    "posting_source_row": source_row, "source_row": position,
                    "source_id": member["source_id"], "member_path": member["member_path"],
                    **{f"{key}_column": column for key, column in columns.items()},
                    "card_matches": row["card"] == original_card if original_card and row["card"] else None,
                    "card_equals_account": row["card"] == row["account"],
                    "posting_card_present": bool(original_card),
                    "name_present": bool(row["name"]), "document_present": bool(row["document"]),
                }, row))
    with db._lock:
        analysis_snapshot.revision(case_id, revision)
    if not matched:
        return None
    result = {"matches": [entry for entry, _ in matched], "complete": complete, "source_role_only": True}
    if len(matched) == 2 and len({entry["posting_source_row"] for entry, _ in matched}) == 2:
        first, second = (value for _, value in matched)
        for key in ("name", "document"):
            if first[key] and second[key]:
                result[f"different_{key}"] = first[key] != second[key]
    return result


def _feedback_observations(case_id, member, body, accounts, start, end):
    raw = trial_pair_comparison.read_registered_feedback_table(case_id, member["source_id"], include_bank32=True)
    if raw is None or raw["member_path"] != member["member_path"] or raw["member_sha256"] != member["member_sha256"]:
        return [], False
    wanted = body.value.strip()
    headers = raw["headings"]
    card_col, account_col, clock_col, side_col, currency_col = (headers.index(label) for label in
        ("交易卡号", "交易账号", "交易时间", "收付标志", "交易币种"))
    native32 = len(headers) == len(table_import.BANK_FEEDBACK_32_COLUMNS)
    exact_pairs = {(card, account) for card, account, _ in accounts}
    account_currency = {(account, currency) for _, account, currency in accounts if currency in table_import._ISO_CURRENCIES}
    complete, matches = True, {}
    for position, cells in raw["rows"].items():
        if position == 1 or len(cells) != len(headers):
            continue
        card, account = cells[card_col].strip(), cells[account_col].strip()
        currency = trial_pair_comparison._feedback_currency(cells[currency_col].strip())
        if body.kind == "card" and card == wanted:
            role = "own_card"
        elif native32 and (account, currency) in account_currency:
            role = "matching_registered_account"
        elif body.kind == "name" and not native32 and (card, account) in exact_pairs:
            role = "matching_card_account"
        else:
            continue
        raw_clock = cells[clock_col]
        stamp = table_import._time(raw_clock)
        if stamp is None:
            complete = False
            continue
        if start <= stamp.date() <= end:
            clock = None if table_import._date_only(raw_clock) else raw_clock
            side = trial_pair_comparison._feedback_direction(cells[side_col])
            matches.setdefault(role, {})[position] = (
                card if not native32 or _full_card(card) else None, account if _full_account(account) else None, clock, side)
    return [_csv_observation(case_id, member, rows, role=role,
        match_column=(account_col if role == "matching_registered_account" else card_col) + 1,
        card_column=card_col + 1, account_column=account_col + 1) for role, rows in matches.items()], complete


def lookup(case_id, body, *, include_source_observations=True):
    try:
        start, end = _date(body.start), _date(body.end)
        if start > end or not body.value.strip():
            raise ValueError("ledger search scope")
    except ValueError:
        raise cases.ServiceError(422, "invalid_ledger_search", "请输入完整的卡号或户名及有序日期") from None
    with db._lock:
        cid = cases.require_case(str(case_id))
        revision = analysis_snapshot.revision(cid)
        if body.kind == "card" and not _full_card(body.value):
            return {"case_id": str(cid), "revision": revision, "status": "partial_card",
                    "coverage_complete": False, "choices": []}
        members, accounts = _members(cid), _account_catalog(cid)
    complete = len(members) <= _MEMBER_LIMIT
    matches = {}
    for member in members[:_MEMBER_LIMIT]:
        if member["size_bytes"] > _MEMBER_BYTES:
            complete = False
            continue
        try:
            stream, _ = imports.get_source_member_file(cid, member["source_id"], member["member_path"])
            with closing(stream):
                raw = stream.read(_MEMBER_BYTES + 1)
            if sha256(raw).hexdigest() != member["sha256"]:
                raise ValueError("registration member digest changed")
            registrations = _registrations(raw)
        except (UnicodeError, ValueError, csv.Error):
            complete = False
            continue
        except cases.ServiceError as error:
            if error.code not in assistance._SOURCE_ERRORS:
                raise
            complete = False
            continue
        for position, subject, name, account, card, first, last, issuer in registrations:
            selected = card == body.value.strip() if body.kind == "card" else _text(name) == _text(body.value)
            if not selected or first > end or last < start:
                continue
            key = (subject, account, _text(issuer))
            item = matches.setdefault(key, {
                "account": account, "name": name, "issuer": issuer,
                "ledger_id": None, "covers_period": False, "proofs": [], "rows": [], "more_rows": False,
            })
            item["covers_period"] |= first <= start and end <= last
            item["proofs"].append({"source_id": member["source_id"], "member_path": member["member_path"],
                                   "source_row": position, "sha256": member["source_sha256"],
                                   "member_sha256": member["sha256"], "valid_from": first.isoformat(), "valid_until": last.isoformat()})
    with db._lock:
        analysis_snapshot.revision(cid, revision)
        for (subject, account, bank), item in matches.items():
            ids = accounts.get((bank, account), ())
            if len(ids) == 1:
                item["ledger_id"] = ids[0]
                rows = _observations(cid, ids[0], start, end)
                item["rows"] = rows[:_FLOW_LIMIT]
                item["more_rows"] = len(rows) > _FLOW_LIMIT
            scope = [cid, revision, body.kind, body.value.strip(), start.isoformat(), end.isoformat(),
                     subject, account, bank, item["proofs"]]
            item["scope_id"] = sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
    choices = list(matches.values())
    status = ("scope_limited" if not complete else "evidence_missing" if not choices
              else "multiple_ledgers" if len(choices) > 1 else "period_unproved" if not choices[0]["covers_period"]
              else "book_unavailable" if not choices[0]["rows"] else "found")
    result = {"case_id": str(cid), "revision": revision, "status": status, "coverage_complete": complete, "choices": choices}
    if include_source_observations and not choices:
        with db._lock:
            sources = _observation_members(cid)
        observations, observed_accounts = [], set()
        observed_all = len(sources) <= _OBSERVATION_MEMBERS
        sources = sources[:_OBSERVATION_MEMBERS]
        for member in sources:
            if member["role"] == "auxiliary":
                observation, keys, read = _account_snapshot(cid, member, body)
                observed_all &= read
                observed_accounts.update(keys)
                if observation:
                    observations.append(observation)
        for member in sources:
            if member["role"] != "table":
                continue
            if member["suffix"] == ".xlsx":
                observation, read = _workbook_observation(cid, member, body, start, end)
                if observation:
                    observations.append(observation)
            else:
                found, read = _feedback_observations(cid, member, body, observed_accounts, start, end)
                observations.extend(found)
            observed_all &= read
        with db._lock:
            analysis_snapshot.revision(cid, revision)
        if observations or not observed_all:
            result["source_observations"] = {"checked_sources": len(sources),
                                             "complete": observed_all, "matches": observations}
    return result


def require(case_id, binding, anchor_id):
    result = lookup(case_id, binding.lookup, include_source_observations=False)
    found = next((item for item in result["choices"] if item["scope_id"] == binding.scope_id), None)
    if (not result["coverage_complete"] or found is None or not found["covers_period"]
            or not any(row["id"] == anchor_id for row in found["rows"])):
        raise cases.ServiceError(409, "trace_ledger_scope_changed", "账簿原件、期间或所选流水已变化，请重新核对")
    needed = {proof["source_id"]: proof["sha256"] for proof in found["proofs"]}
    if assistance.verified_product_originals(int(case_id), needed) != set(needed):
        raise cases.ServiceError(409, "trace_ledger_scope_changed", "账簿登记原件已变化，请重新核对")
    return {"revision": result["revision"], "scope_id": binding.scope_id, "ledger_id": found["ledger_id"],
            "proofs": needed, "sources": found["proofs"], "start": binding.lookup.start, "end": binding.lookup.end}
