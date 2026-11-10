"""Source ledger observations, distinct from economic transfer conclusions."""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, localcontext
import json

from . import db
from .money import money_decimal

POLICY = "source-ledger-observations-v1"
UNORIENTED_POLICY = "source-ledger-unoriented-v1"
SOURCE_OBSERVATION_POLICY = "source-ledger-unoriented-v2"
FIELD = "source_ledger_evidence"
MAX_BYTES = 8 * 1024 * 1024
FLOW_FIELDS = ("trade_no", "trade_time", "payer_name", "payer_account", "payer_bank", "payee_name",
               "payee_account", "payee_bank", "amount", "balance", "currency", "summary", "channel",
               "self_account", "self_name", "trade_time_utc", "trade_time_original", "trade_time_offset")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def capture(fields, *, amount, balance, currency, source_currency, stamp, direction, raw_direction,
            card, issues, signed, mode, reported_status, before=None, zoned=None, allow_unoriented=False,
            allow_source_observation=False, original_direction=""):
    """Capture already validated source roles; a label never assigns net effect."""
    unoriented = (allow_unoriented and mode == "B" and amount is not None and amount >= 0
                  and not raw_direction and not any(fields.get("counter_" + key) for key in ("account", "name", "bank"))
                  and set(issues) == {"missing_direction"} and stamp is not None and bool(source_currency))
    source_observation = (allow_source_observation and mode == "B" and amount is not None and amount >= 0
        and direction not in {"in", "out"} and bool(issues) and set(issues) <= {"missing_direction", "invalid_direction"}
        and stamp is not None and bool(source_currency))
    unoriented = unoriented or source_observation
    if amount is None or not fields.get("self_account") or direction not in {"in", "out"} and not unoriented:
        return None
    errors = set(issues)
    allowed = {"negative_amount", "unknown_status", "balance_amount_conflict", "balance_direction_conflict"}
    valid = stamp is not None and not (errors - allowed)
    financial = valid and not errors and amount != 0
    ledger = unoriented or valid and amount <= 0 and not financial
    row = {key: fields.get(key) or "" for key in FLOW_FIELDS if key not in {
        "trade_time", "amount", "balance", "currency", "trade_time_utc", "trade_time_original", "trade_time_offset"}}
    row.update(trade_time=stamp.isoformat(sep=" ") if stamp else None,
               amount=format(amount if ledger else abs(amount), ".2f"),
               balance=format(balance, ".2f") if balance is not None else None,
               currency=source_currency if ledger else currency,
               trade_time_utc=None, trade_time_original=None, trade_time_offset=None)
    if unoriented:
        for side in ("payer", "payee"):
            for field in ("account", "name", "bank"):
                row[side + "_" + field] = ""
    if zoned:
        row.update({key: value.isoformat(sep=" ") if isinstance(value, datetime) else value for key, value in zoned.items()})
    result = {"policy": SOURCE_OBSERVATION_POLICY if source_observation else UNORIENTED_POLICY if unoriented else POLICY, "kind": "ledger" if ledger else "financial" if financial else "context", "header_scope": 0,
              "row": row, "signed_amount": format(amount, ".2f"), "source_currency": source_currency,
              "source_direction": raw_direction, "direction": "unknown" if unoriented else direction, "own_card": card,
              "reported_status": reported_status or "unknown", "issues": sorted(errors),
              "balance_before": format(before, ".2f") if before is not None else None}
    if source_observation:
        result["source_direction"] = original_direction
        result["counterparty"] = {key: fields.get("counter_" + key) or "" for key in ("account", "name", "bank")}
    encoded = _json(result)
    if len(encoded.encode("utf-8")) > MAX_BYTES:
        raise ValueError("来源记账语义超出单条处理范围")
    return encoded


def validate(encoded):
    """Validate the package boundary before using a persisted source snapshot."""
    if not isinstance(encoded, str) or len(encoded.encode("utf-8")) > MAX_BYTES:
        raise ValueError("来源记账语义无效")
    value = json.loads(encoded)
    source_observation = isinstance(value, dict) and value.get("policy") == SOURCE_OBSERVATION_POLICY
    fields = {"policy", "kind", "row", "signed_amount", "source_currency", "header_scope",
              "source_direction", "direction", "own_card", "reported_status", "issues", "balance_before"}
    if source_observation:
        fields.add("counterparty")
    if (not isinstance(value, dict) or set(value) != fields
            or value["policy"] not in {POLICY, UNORIENTED_POLICY, SOURCE_OBSERVATION_POLICY} or value["kind"] not in {"ledger", "financial", "context"}
            or type(value["header_scope"]) is not int or value["header_scope"] < 1
            or value["direction"] not in ({"unknown"} if value["policy"] in {UNORIENTED_POLICY, SOURCE_OBSERVATION_POLICY} else {"in", "out"}) or not isinstance(value["issues"], list)
            or any(not isinstance(item, str) for item in value["issues"])):
        raise ValueError("来源记账语义无效")
    row = value["row"]
    unoriented = value["policy"] in {UNORIENTED_POLICY, SOURCE_OBSERVATION_POLICY}
    if source_observation and (not isinstance(value["counterparty"], dict)
            or set(value["counterparty"]) != {"account", "name", "bank"}
            or any(not isinstance(item, str) for item in value["counterparty"].values())):
        raise ValueError("来源对方字段无效")
    if (not isinstance(row, dict) or set(row) != set(FLOW_FIELDS)
            or any(not isinstance(item, str) for key, item in row.items() if key not in {
                "trade_time", "balance", "trade_time_utc", "trade_time_original", "trade_time_offset"})
            or not row["self_account"]
            or not unoriented and row["self_account"] != row["payer_account" if value["direction"] == "out" else "payee_account"]
            or any(not isinstance(value[key], str) for key in ("signed_amount", "source_currency", "source_direction",
                                                            "own_card", "reported_status"))):
        raise ValueError("来源记账字段无效")
    for key in ("trade_time", "balance", "trade_time_utc", "trade_time_original", "trade_time_offset"):
        if row[key] is not None and not isinstance(row[key], str):
            raise ValueError("来源记账字段无效")
    if value["balance_before"] is not None and not isinstance(value["balance_before"], str):
        raise ValueError("来源余额无效")
    signed = money_decimal(value["signed_amount"])
    canonical = money_decimal(row["amount"])
    allowed_direction_issues = {"missing_direction", "invalid_direction"} if source_observation else {"missing_direction"}
    if unoriented and (value["kind"] != "ledger" or signed < 0 or not source_observation and value["source_direction"]
            or not value["issues"] or set(value["issues"]) - allowed_direction_issues or not value["source_currency"]
            or any(row[side + "_" + field] for side in ("payer", "payee") for field in ("account", "name", "bank"))):
        raise ValueError("未知方向来源记账字段无效")
    if value["kind"] == "ledger" and (signed > 0 and not unoriented or canonical != signed or row["currency"] != value["source_currency"]):
        raise ValueError("来源记账符号或币种不一致")
    if value["kind"] == "financial" and (canonical <= 0 or canonical != abs(signed) or value["issues"]):
        raise ValueError("有效交易与来源金额或校验结论不一致")
    if value["source_currency"] and row["currency"] != value["source_currency"]:
        raise ValueError("来源币种与保留分录不一致")
    if value["kind"] == "ledger" and set(value["issues"]) - {
            "negative_amount", "unknown_status", "balance_amount_conflict", "balance_direction_conflict", *(allowed_direction_issues if unoriented else set())}:
        raise ValueError("存在未解决的来源字段冲突")
    from . import table_import
    if unoriented and table_import._enum(value["source_direction"]) in table_import._IN | table_import._OUT:
        raise ValueError("已识别方向不能声明为未知")
    directions = table_import._IN if value["direction"] == "in" else table_import._OUT
    if not unoriented and table_import._enum(value["source_direction"]) not in directions:
        raise ValueError("来源标注方向不一致")
    if value["source_currency"] and value["source_currency"] not in table_import._ISO_CURRENCIES:
        raise ValueError("来源币种无效")
    for item in (row["balance"], value["balance_before"]):
        if item is not None:
            money_decimal(item)
    if value["kind"] != "context" and row["trade_time"] is None:
        raise ValueError("来源记账时间缺失")
    for key in ("trade_time", "trade_time_utc"):
        if row[key] is not None and datetime.fromisoformat(row[key]).tzinfo is not None:
            raise ValueError("来源壁钟与UTC瞬间须分别保留")
    for key in ("trade_time_utc", "trade_time_original", "trade_time_offset"):
        if row[key] is not None and not isinstance(row[key], str):
            raise ValueError("来源记账时间无效")
    return value


def _profiles(*, unoriented=False, extended=False):
    from . import table_import
    profiles = table_import.SOURCE_OBSERVATION_PROFILES if extended else table_import.UNORIENTED_PROFILES if unoriented else table_import.SOURCE_LEDGER_PROFILES
    return ",".join("'" + item + "'" for item in sorted(profiles))


def install_source_views(connection):
    from .table_cancellation_links import active_support_sql
    connection.execute(f"""create or replace view source_ledger_records as
        select r.*,json_extract_string(r.details_json,'$.{FIELD}') ledger_evidence,
            m.sha256 member_sha256
        from import_records r join import_sources p on p.case_id=r.case_id and p.source_id=r.source_id
        join import_members m on m.case_id=r.case_id and m.source_id=r.source_id and m.member_path=r.member_path
        where p.parser_version in ({_profiles()}) and {active_support_sql('p')}
          and contains(r.details_json,'\"{FIELD}\"')
          and json_extract_string(r.details_json,'$.{FIELD}') is not null
          and (json_extract_string(json_extract_string(r.details_json,'$.{FIELD}'),'$.policy')<>'{UNORIENTED_POLICY}'
               or p.parser_version in ({_profiles(unoriented=True)}))
          and (json_extract_string(json_extract_string(r.details_json,'$.{FIELD}'),'$.policy')<>'{SOURCE_OBSERVATION_POLICY}'
               or p.parser_version in ({_profiles(extended=True)}))""")
    connection.execute("""create table if not exists source_ledger_index (
        case_id bigint not null,flow_id bigint not null,primary key(case_id,flow_id))""")
    for column in ("counter_account", "counter_name", "counter_bank"):
        connection.execute(f"alter table source_ledger_index add column if not exists {column} varchar")
    # Per-posting own card is a display/search alias for the account, never a
    # new identity. It is kept beside the ledger index because financial
    # observations also carry it while source_ledger_index stays ledger-only.
    connection.execute("""create table if not exists flow_source_cards (
        case_id bigint not null,flow_id bigint not null,own_card varchar not null,primary key(case_id,flow_id))""")
    refresh_index(connection)


def refresh_index(connection, case_id=None):
    scope, parameters = (" where case_id=?", [case_id]) if case_id is not None else ("", [])
    connection.execute("delete from source_ledger_index" + scope, parameters)
    connection.execute("""insert into source_ledger_index(case_id,flow_id,counter_account,counter_name,counter_bank)
        select f.case_id,f.flow_id,
            max(json_extract_string(r.ledger_evidence,'$.counterparty.account')),
            max(json_extract_string(r.ledger_evidence,'$.counterparty.name')),
            max(json_extract_string(r.ledger_evidence,'$.counterparty.bank'))
        from source_ledger_records r join flow_sources f
          on f.case_id=r.case_id and f.source_id=r.source_id and f.source_row=r.record_no
        where (json_extract_string(r.ledger_evidence,'$.kind')='ledger' or r.disposition='cancelled')""" +
        (" and r.case_id=?" if case_id is not None else "") + " group by f.case_id,f.flow_id", parameters)
    refresh_cards(connection, case_id)


def refresh_cards(connection, case_id=None):
    """Project the posting's own card once; ordinary table sources keep it too."""
    scope, parameters = (" where case_id=?", [case_id]) if case_id is not None else ("", [])
    connection.execute("delete from flow_source_cards" + scope, parameters)
    connection.execute("""insert into flow_source_cards(case_id,flow_id,own_card)
        select f.case_id,f.flow_id,max(nullif(json_extract_string(r.ledger_evidence,'$.own_card'),''))
        from source_ledger_records r join flow_sources f
          on f.case_id=r.case_id and f.source_id=r.source_id and f.source_row=r.record_no
        where nullif(json_extract_string(r.ledger_evidence,'$.own_card'),'') is not null""" +
        (" and r.case_id=?" if case_id is not None else "") + " group by f.case_id,f.flow_id", parameters)


def install_visible_view(connection, projection_sql):
    connection.execute("""create or replace view source_visible_flows as
        with financial as materialized(select * from source_effective_flows),
        ledger as materialized(select f.* from flows f join source_ledger_index l on l.case_id=f.case_id and l.flow_id=f.id),
        visible as (select * from financial union all select l.* from ledger l
            where not exists(select 1 from financial e where e.case_id=l.case_id and e.id=l.id))
        select f.*,coalesce(l.counter_account,'') source_counter_account,
            coalesce(l.counter_name,'') source_counter_name,coalesce(l.counter_bank,'') source_counter_bank,
            coalesce(c.own_card,'') source_own_card
        from visible f left join source_ledger_index l on l.case_id=f.case_id and l.flow_id=f.id
        left join flow_source_cards c on c.case_id=f.case_id and c.flow_id=f.id""")
    connection.execute("create or replace view effective_visible_flows as " +
        "select f.*,coalesce(e.entity_id,'') source_counter_id from (" +
        projection_sql.replace("from source_effective_flows f", "from source_visible_flows f") +
        ") f left join resolved_endpoints e on e.case_id=f.case_id and e.flow_id=f.id and e.role='counter'")


def commit(case_id, source_id, batch_id):
    """Publish typed observations in the same transaction as their journal."""
    records = db.q_raw("""select record_no,member_path,member_sha256,source_row,disposition,ledger_evidence
        from source_ledger_records where case_id=? and source_id=? order by record_no""",
                       [case_id, source_id])
    added = duplicates = 0
    for record in records:
        evidence = validate(record.pop("ledger_evidence"))
        if evidence["kind"] != "ledger" and record["disposition"] != "cancelled":
            continue
        row = evidence["row"]
        if db.q_raw("select 1 from flow_sources where case_id=? and source_id=? and source_row=?",
                    [case_id, source_id, record["record_no"]]):
            continue
        # Exact source position is support identity. References, amounts and
        # balances cannot merge two source ledger observations.
        matches = db.q_raw("""select distinct f.id from import_records r
            join import_members m on m.case_id=r.case_id and m.source_id=r.source_id and m.member_path=r.member_path
            join flow_sources s on s.case_id=r.case_id and s.source_id=r.source_id and s.source_row=r.record_no
            join flows f on f.case_id=s.case_id and f.id=s.flow_id
            where r.case_id=? and r.member_path=? and m.sha256=? and r.source_row=? order by f.id""",
            [case_id, record["member_path"], record["member_sha256"], record["source_row"]])
        fid = None
        from . import table_import
        expected = table_import.flow_digest(row)
        for match in matches:
            prior = db.q_raw("select * from flows where case_id=? and id=?", [case_id, match["id"]])[0]
            if table_import.flow_digest(prior) == expected:
                fid = prior["id"]
                break
        reused = fid is not None
        if fid is None:
            fid = db.q_raw("select nextval('flow_seq') id")[0]["id"]
            columns = ",".join(FLOW_FIELDS)
            db.x(f"insert into flows(id,case_id,batch_id,{columns}) values (?,?,?,{','.join('?' for _ in FLOW_FIELDS)})",
                 [fid, case_id, batch_id, *[row[key] for key in FLOW_FIELDS]])
            added += 1
        else:
            duplicates += 1
        db.x("insert into flow_sources(case_id,source_id,source_row,flow_id,disposition) values (?,?,?,?,?)",
             [case_id, source_id, record["record_no"], fid, "duplicate" if reused else "imported"])
    refresh_index(db.con(), case_id)
    return {"added": added, "duplicates": duplicates}


def _records(case_id, ids):
    """Load selected observations and their required source context, per read.

    A complete member is retained when chronology is needed: context records,
    incomparable clocks and equal-time competitors must still block a witness.
    Unoriented/same-entry observations need no preceding financial ledger.
    """
    query = """select r.case_id,r.source_id,r.record_no,r.member_path,r.source_row,
        r.disposition,r.reason_code,r.ledger_evidence,s.flow_id,
        case when r.disposition='cancelled' and r.reason_code='matched_cancellation'
            then json_object('cancellation_role',json_extract_string(r.details_json,'$.cancellation_role'),
                'paired_record_no',json_extract_string(r.details_json,'$.paired_record_no'))
            else '{}' end details_json
        from source_ledger_records r left join flow_sources s
        on s.case_id=r.case_id and s.source_id=r.source_id and s.source_row=r.record_no
        where r.case_id=? and """
    selected = "s.flow_id in (select unnest(?::bigint[]))"
    order = " order by r.source_id,r.member_path,r.source_row"
    rows = db.q_raw(query + selected + order, [case_id, ids])
    members, originals = set(), set()
    for row in rows:
        evidence = row["evidence"] = validate(row.pop("ledger_evidence"))
        if (evidence["direction"] != "unknown" and evidence["kind"] != "context"
                and evidence["source_currency"] and evidence["row"]["balance"] is not None
                and evidence["row"]["trade_time"] and evidence["balance_before"] is None):
            members.add((row["source_id"], row["member_path"]))
        if row["disposition"] == "cancelled" and row["reason_code"] == "matched_cancellation":
            details = json.loads(row["details_json"])
            if details["cancellation_role"] != "original":
                originals.add((row["source_id"], int(details["paired_record_no"])))
    if not members and not originals:
        return rows
    members, originals = sorted(members), sorted(originals)
    # Drop the seed snapshots before the larger context query materializes.
    del rows
    rows = db.q_raw(query + "(" + selected + """ or (r.source_id,r.member_path) in
        (select unnest(?::varchar[]),unnest(?::varchar[])) or (r.source_id,r.record_no) in
        (select unnest(?::varchar[]),unnest(?::bigint[])))""" + order,
        [case_id, ids, [item[0] for item in members], [item[1] for item in members],
         [item[0] for item in originals], [item[1] for item in originals]])
    for row in rows:
        row["evidence"] = validate(row.pop("ledger_evidence"))
    return rows


def _scope(record):
    e = record["evidence"]
    row = e["row"]
    return (record["source_id"], record["member_path"], e["header_scope"], e["source_currency"], row["self_account"], e["own_card"])


def _balance_change(target, group, *, unlocated=False):
    e = target["evidence"]
    row = e["row"]
    result = {"status": "unknown", "difference": None, "before": None, "after": row["balance"],
              "basis": None, "reason": None, "source_id": target["source_id"], "previous_source_row": None}
    if e["direction"] == "unknown":
        return {**result, "reason": "direction_unknown"}
    if not e["source_currency"]:
        return {**result, "reason": "currency_unknown"}
    if row["balance"] is None:
        return {**result, "reason": "balance_missing"}
    if e["balance_before"] is not None:
        with localcontext() as ctx:
            ctx.prec = 50
            delta = money_decimal(row["balance"]) - money_decimal(e["balance_before"])
        return {**result, "status": "observed", "difference": format(delta, ".2f"),
                "before": e["balance_before"], "basis": "same_entry_source_balances"}
    if unlocated or any(item["evidence"]["kind"] == "context" for item in group):
        return {**result, "reason": "unlocated_source_record"}
    if not row["trade_time"] or any(not item["evidence"]["row"]["trade_time"] for item in group):
        return {**result, "reason": "unlocated_source_record"}
    bases = {bool(item["evidence"]["row"]["trade_time_utc"]) for item in group}
    if len(bases) != 1:
        return {**result, "reason": "time_basis_incomparable"}
    def clock(item):
        source = item["evidence"]["row"]
        return datetime.fromisoformat(source["trade_time_utc"] or source["trade_time"])
    stamp = clock(target)
    peers = [item for item in group if clock(item) == stamp]
    if len(peers) != 1:
        return {**result, "reason": "posting_order_unknown"}
    earlier = [item for item in group if clock(item) < stamp]
    if not earlier:
        return {**result, "reason": "no_preceding_source_balance"}
    latest = max(map(clock, earlier))
    previous = [item for item in earlier if clock(item) == latest]
    if len(previous) != 1:
        return {**result, "reason": "posting_order_unknown"}
    previous = previous[0]
    before = previous["evidence"]["row"]["balance"]
    if before is None:
        return {**result, "reason": "preceding_balance_missing"}
    with localcontext() as ctx:
        ctx.prec = 50
        delta = money_decimal(row["balance"]) - money_decimal(before)
    return {**result, "status": "observed", "difference": format(delta, ".2f"), "before": before,
            "basis": "unique_preceding_source_time", "previous_source_row": previous["source_row"]}


def information(case_id, ids):
    wanted = {str(value) for value in ids}
    if not wanted:
        return {}
    indexed = {str(row["flow_id"]) for row in db.q_raw(
        "select flow_id from source_ledger_index where case_id=? and flow_id in (select unnest(?::bigint[]))",
        [case_id, list(map(int, wanted))])}
    if not indexed:
        return {}
    financial = {str(row["id"]) for row in db.q_raw(
        "select id from source_effective_flows where case_id=? and id in (select unnest(?::bigint[]))",
        [case_id, list(map(int, indexed))])}
    visible = indexed - financial
    if not visible:
        return {}
    records = _records(case_id, sorted(map(int, visible)))
    groups = defaultdict(list)
    for record in records:
        if record["evidence"]["direction"] != "unknown":
            groups[_scope(record)].append(record)
    from .table_cancellation_links import active_support_sql
    members = sorted({(record["source_id"], record["member_path"]) for record in records})
    unlocated = {(row["source_id"], row["member_path"]) for row in db.q_raw(f"""
        select distinct r.source_id,r.member_path from import_records r
        join import_sources p on p.case_id=r.case_id and p.source_id=r.source_id
        where r.case_id=? and (r.source_id,r.member_path) in
          (select unnest(?::varchar[]),unnest(?::varchar[]))
          and p.parser_version in ({_profiles()}) and {active_support_sql('p')}
          and r.disposition='unresolved' and (not contains(r.details_json,'\"{FIELD}\"')
            or json_extract_string(json_extract_string(r.details_json,'$.{FIELD}'),'$.policy') in ('{UNORIENTED_POLICY}','{SOURCE_OBSERVATION_POLICY}'))""",
        [case_id, [item[0] for item in members], [item[1] for item in members]])}
    result = {}
    for record in records:
        fid = str(record["flow_id"])
        if fid not in wanted or fid not in visible or fid in result:
            continue
        e = record["evidence"]
        cancelled = _current_pair(case_id, record, records)
        effect = "paired_cancellation" if cancelled else "no_movement" if money_decimal(e["signed_amount"]) == 0 else "unknown"
        result[fid] = {"policy": e["policy"], "signed_amount": e["signed_amount"],
            "source_direction": e["source_direction"], "direction": e["direction"],
            "economic_effect": effect, "economic_amount": "0.00" if effect != "unknown" else None,
            "reported_status": e["reported_status"], "balance_change": _balance_change(record, groups[_scope(record)],
                unlocated=(record["source_id"], record["member_path"]) in unlocated),
            "self_account": e["row"]["self_account"],
            **{"counter_" + key: e.get("counterparty", {}).get(key, "") for key in ("account", "name", "bank")},
            "source_id": record["source_id"], "member_path": record["member_path"], "source_row": record["source_row"]}
    return result


def _current_pair(case_id, record, records):
    if record["disposition"] != "cancelled" or record["reason_code"] != "matched_cancellation":
        return False
    details = json.loads(record["details_json"])
    number = record["record_no"] if details.get("cancellation_role") == "original" else int(details["paired_record_no"])
    original = next((item for item in records if item["source_id"] == record["source_id"] and item["record_no"] == number), None)
    return bool(original and original["flow_id"] is not None and not db.q_raw(
        "select 1 from source_effective_flows where case_id=? and id=?", [case_id, original["flow_id"]]))


def decorate(case_id, rows):
    entries = information(case_id, [row["id"] for row in rows])
    for row in rows:
        if str(row["id"]) in entries:
            row["source_ledger"] = entries[str(row["id"])]
    return rows


def financial_source(source_sql):
    financial = """(select f.*,
        ''::varchar source_counter_account,''::varchar source_counter_name,''::varchar source_counter_bank,''::varchar source_counter_id
        from effective_flows f)"""
    return source_sql.replace("from effective_visible_flows as flows", "from " + financial + " as flows").replace(
        "from effective_visible_flows", "from " + financial + " as effective_visible_flows")


def nonpositive_count(case_id):
    # Also excludes positive, unoriented source observations from the legacy
    # count of financial rows removed by cancellation.
    return db.q_raw("""select count(*) n from flows f join source_ledger_index l
        on l.case_id=f.case_id and l.flow_id=f.id where f.case_id=? and (f.amount<=0
        or coalesce(f.payer_account,'')='' and coalesce(f.payee_account,'')='')""", [case_id])[0]["n"]


def label(info):
    effect = {"unknown": "资金影响未确定", "no_movement": "零额", "paired_cancellation": "已匹配抵销"}[info["economic_effect"]]
    direction = "方向未提供" if info["direction"] == "unknown" else "来源标注" + info["source_direction"]
    return f"{direction} · 原值 {info['signed_amount']} · {effect}"


def selected_rows(case_id, *, canvas_id=None, account=None):
    selection = "select l.flow_id from source_ledger_index l where l.case_id=?"
    selected_params = [case_id]
    if canvas_id is not None:
        selection += " and exists(select 1 from tracked_flows t where t.case_id=l.case_id and t.canvas_id=? and t.flow_id=l.flow_id)"
        selected_params.append(canvas_id)
    ids = [row["flow_id"] for row in db.q_raw(selection, selected_params)]
    if not ids:
        return []
    query = """select f.* from effective_visible_flows f where f.case_id=?
        and f.id in (select unnest(?::bigint[]))"""
    params = [case_id, ids]
    if account is not None:
        query += " and (f.payer_account=? or f.payee_account=? or f.self_account=? or f.source_counter_id=?)"
        params += [account] * 4
    rows = db.q_raw(query + " order by f.trade_time,f.id", params)
    entries = information(case_id, [row["id"] for row in rows])
    rows = [row for row in rows if str(row["id"]) in entries]
    for row in rows:
        row["source_ledger"] = entries[str(row["id"])]
    return rows


def summary(case_id, *, account=None, include_rows=False):
    rows = selected_rows(case_id, account=account)
    result = {"count": len(rows),
              "unknown_effect_count": sum(row["source_ledger"]["economic_effect"] == "unknown" for row in rows),
              "zero_count": sum(row["source_ledger"]["economic_effect"] == "no_movement" for row in rows),
              "paired_count": sum(row["source_ledger"]["economic_effect"] == "paired_cancellation" for row in rows)}
    if include_rows:
        result["records"] = [{"id": str(row["id"]), "trade_no": row["trade_no"] or "",
            "trade_time": str(row["trade_time"]), "amount": format(money_decimal(row["amount"]), ".2f"),
            "currency": row["currency"] or "", "summary": row["summary"] or "",
            "source_ledger": row["source_ledger"]} for row in rows[-50:]]
        result["has_more"] = len(rows) > 50
    return result


def attach_graph(case_id, canvas_id, result, view=None):
    """Attach source relationships without modifying existing money edges."""
    rows = selected_rows(case_id, canvas_id=canvas_id)
    if not rows:
        return result
    if view is not None:
        from . import transaction_time
        clause, params, _ = transaction_time.window(view.start, view.end, "f")
        if clause:
            permitted = {str(row["id"]) for row in db.q_raw(
                "select f.id from effective_visible_flows f where f.case_id=? and ((" + clause + ")" +
                (" or f.trade_time is null)" if view.include_undated else ")"), [case_id, *params])}
            rows = [row for row in rows if str(row["id"]) in permitted]
        if view.scope == "visible":
            accounts = set(view.visible_accounts)
            pairs = {(edge.source, edge.target) for edge in view.visible_edges}
            rows = [row for row in rows if (row["source_ledger"]["direction"] != "unknown" or row["self_account"] in accounts)
                    and all(not row[side] or row[side] in accounts for side in ("payer_account", "payee_account"))
                    and (not row["payer_account"] or not row["payee_account"] or (row["payer_account"], row["payee_account"]) in pairs)]
    by_node = {node["account"]: node for node in result["nodes"]}
    by_edge = {(edge["source"], edge["target"]): edge for edge in result["edges"]}
    declarations = db.q_raw("select account,name from effective_main_accounts where case_id=?", [case_id])
    main_accounts = {item["account"] for item in declarations}
    main_names = defaultdict(set)
    for item in declarations:
        if item["name"]:
            main_names[item["name"]].add(item["account"])
    added_nodes = set()
    for row in rows:
        present_accounts = set()
        sides = ("self", "source_counter") if row["source_ledger"]["direction"] == "unknown" else ("payer", "payee")
        for side in sides:
            account = row["source_counter_id"] if side == "source_counter" else row[side + "_account"]
            if not account or account in present_accounts:
                continue
            present_accounts.add(account)
            if account not in by_node:
                node = {"account": account, "name": row[side + "_name"] or "", "is_main": int(account in main_accounts), "is_same_name": 0,
                        "in_cnt": 0, "out_cnt": 0, "in_amt": "0.00", "out_amt": "0.00", "in_totals": [], "out_totals": []}
                result["nodes"].append(node)
                by_node[account] = node
                added_nodes.add(account)
            if account in added_nodes and any(other != account for other in main_names.get(row[side + "_name"], ())):
                by_node[account]["is_same_name"] = 1
            by_node[account].setdefault("source_ledger", {"count": 0, "unknown_effect_count": 0})
            by_node[account]["source_ledger"]["count"] += 1
            by_node[account]["source_ledger"]["unknown_effect_count"] += row["source_ledger"]["economic_effect"] == "unknown"
        if not row["payer_account"] or not row["payee_account"]:
            continue
        key = row["payer_account"], row["payee_account"]
        if key not in by_edge:
            edge = {"source": key[0], "target": key[1], "source_name": row["payer_name"] or "",
                    "target_name": row["payee_name"] or "", "cnt": 0, "total": None, "amount_totals": [],
                    "currencies": [], "ids": [], "summaries": [], "kind": "source_ledger"}
            result["edges"].append(edge)
            by_edge[key] = edge
        edge = by_edge[key]
        edge.setdefault("source_ledger", {"count": 0, "records": []})
        edge["source_ledger"]["count"] += 1
        edge["source_ledger"]["records"].append({"id": str(row["id"]), "amount": format(money_decimal(row["amount"]), ".2f"),
            "currency": row["currency"] or "", "summary": row["summary"] or "", **row["source_ledger"]})
        edge["ids"] = list(dict.fromkeys([*edge["ids"], int(row["id"])]))
    for edge in result["edges"]:
        if "source_ledger" in edge:
            records = edge["source_ledger"]["records"]
            edge["source_ledger_label"] = "来源记账 " + "；".join(label(item) for item in records[:3])
            if len(records) > 3:
                edge["source_ledger_label"] += f" 等{len(records)}条"
    if added_nodes:
        from . import evidence_resolution
        evidence_resolution.decorate_accounts(case_id, [by_node[key] for key in added_nodes],
            source_sql="select * from effective_visible_flows where case_id=? and id in (select unnest(?::bigint[]))",
            params=[case_id, [int(row["id"]) for row in rows]])
    result["source_ledger"] = {"count": len(rows), "unknown_effect_count": sum(row["source_ledger"]["economic_effect"] == "unknown" for row in rows)}
    return result
