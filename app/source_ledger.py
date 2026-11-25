"""Source ledger observations, distinct from economic transfer conclusions."""
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, localcontext
import hashlib
import json
import os
import tempfile

from . import db, table_direction_recheck
from .money import money_decimal

POLICY = "source-ledger-observations-v1"
UNORIENTED_POLICY = "source-ledger-unoriented-v1"
SOURCE_OBSERVATION_POLICY = "source-ledger-unoriented-v2"
FIELD = "source_ledger_evidence"
MAX_BYTES = 8 * 1024 * 1024
_COMMIT_CHUNK = 128
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
    if not unoriented and fields["self_account"] != fields.get("payer_account" if direction == "out" else "payee_account"):
        # The unresolved original stays in the import journal without a typed posting.
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
    has_direction_heading = isinstance(value, dict) and "direction_heading" in value
    has_direction_literal = isinstance(value, dict) and "direction_literal" in value
    fields = {"policy", "kind", "row", "signed_amount", "source_currency", "header_scope",
              "source_direction", "direction", "own_card", "reported_status", "issues", "balance_before"}
    if source_observation:
        fields.add("counterparty")
    if has_direction_heading:
        fields.add("direction_heading")
    if has_direction_literal:
        fields.add("direction_literal")
    if (not isinstance(value, dict) or set(value) != fields
            or has_direction_heading and has_direction_literal
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
    if has_direction_heading:
        declared = table_direction_recheck.directions(value["direction_heading"]).get(value["source_direction"])
        if value["policy"] != POLICY or value["kind"] != "financial" or value["direction"] != declared:
            raise ValueError("来源收付标题与记账方向不一致")
    elif has_direction_literal:
        proof = value["direction_literal"]
        if (value["policy"] != POLICY or value["kind"] not in {"financial", "context"} or signed <= 0
                or value["header_scope"] != 1 or value["direction"] != "in" or value["source_direction"] != "存款"
                or not isinstance(proof, dict) or set(proof) != {"policy", "column", "column_index"}
                or proof["policy"] != table_import.DEPOSIT_DIRECTION_POLICY
                or not isinstance(proof["column"], str) or table_import._norm(proof["column"]) not in table_import.DEPOSIT_DIRECTION_HEADINGS
                or type(proof["column_index"]) is not int or not 1 <= proof["column_index"] <= table_import.MAX_COLUMNS):
            raise ValueError("来源存款字面与记账方向不一致")
    elif not unoriented and table_import._enum(value["source_direction"]) not in directions:
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
        with _ledger_active_sources as materialized (
            select p.case_id,p.source_id,p.parser_version from import_sources p
            where p.parser_version in ({_profiles()}) and {active_support_sql('p')}
        )
        select r.*,json_extract_string(r.details_json,'$.{FIELD}') ledger_evidence,
            m.sha256 member_sha256
        from import_records r join _ledger_active_sources p on p.case_id=r.case_id and p.source_id=r.source_id
        join import_members m on m.case_id=r.case_id and m.source_id=r.source_id and m.member_path=r.member_path
        where contains(r.details_json,'\"{FIELD}\"')
          and json_extract_string(r.details_json,'$.{FIELD}') is not null
          and (json_extract_string(json_extract_string(r.details_json,'$.{FIELD}'),'$.policy')<>'{UNORIENTED_POLICY}'
               or p.parser_version in ({_profiles(unoriented=True)}))
          and (json_extract_string(json_extract_string(r.details_json,'$.{FIELD}'),'$.policy')<>'{SOURCE_OBSERVATION_POLICY}'
               or p.parser_version in ({_profiles(extended=True)}))""")
    connection.execute("""create table if not exists source_ledger_index (
        case_id bigint not null,flow_id bigint not null,primary key(case_id,flow_id))""")
    for column in ("counter_account", "counter_name", "counter_bank"):
        connection.execute(f"alter table source_ledger_index add column if not exists {column} varchar")
    # Financial deduplication can link opposing own ledgers to one flow. Keep
    # each source role/account and every card, rather than max(card) per flow.
    columns = {row[0] for row in connection.execute("""select column_name from information_schema.columns
        where table_schema='main' and table_name='flow_source_cards'""").fetchall()}
    if columns and not {"role", "own_account"} <= columns:
        connection.execute("drop table flow_source_cards")
    connection.execute("""create table if not exists flow_source_cards (
        case_id bigint not null,flow_id bigint not null,role varchar not null,
        own_account varchar not null,own_card varchar not null,
        primary key(case_id,flow_id,role,own_account,own_card))""")
    refresh_index(connection)
    connection.execute("""create or replace view flow_source_card_aliases as
        select c.*,coalesce(e.entity_id,c.own_account) account,
            coalesce(e.card_state,'')<>'conflict' and
              count(distinct c.own_card) over(partition by c.case_id,c.flow_id,c.role,c.own_account)=1 display_eligible
        from flow_source_cards c join flows f on f.case_id=c.case_id and f.id=c.flow_id
        left join resolved_endpoints e on e.case_id=c.case_id and e.flow_id=c.flow_id and e.role=c.role
        where c.own_card<>c.own_account and c.own_account=case c.role
            when 'payer' then f.payer_account when 'payee' then f.payee_account when 'self' then f.self_account end""")
    # Aggregate before joining a flow reader: aliases must not multiply money
    # rows. Multiple cards for one posting/owner have no preferred card.
    connection.execute("""create or replace view flow_source_card_projection as
        select c.case_id,c.flow_id,
            min(c.own_card) filter(where c.role='payer') payer_card,
            min(c.own_card) filter(where c.role='payee') payee_card,
            case when count(distinct c.own_card) filter(where c.own_account=f.self_account)=1
                then min(c.own_card) filter(where c.own_account=f.self_account) end self_card
        from flow_source_card_aliases c join flows f on f.case_id=c.case_id and f.id=c.flow_id
        where c.display_eligible group by c.case_id,c.flow_id""")


def _source_record_pages(connection, case_id, source_id=None):
    if source_id is not None:
        sources = [(case_id, source_id)]
    else:
        scope, parameters = (" where case_id=?", [case_id]) if case_id is not None else ("", [])
        sources = connection.execute("select case_id,source_id from import_sources" + scope +
                                     " order by case_id,source_id", parameters).fetchall()
    for source_case, source_id in sources:
        lower = None
        while True:
            record_nos = [row[0] for row in connection.execute("""select record_no from import_records
                where case_id=? and source_id=? and (?::bigint is null or record_no>?::bigint)
                order by record_no limit 4096""", [source_case, source_id, lower, lower]).fetchall()]
            if not record_nos:
                break
            lower = record_nos[-1]
            yield source_case, source_id, record_nos


def refresh_index(connection, case_id=None, source_id=None):
    """Rebuild by default; a source scope only adds immutable source support."""
    if source_id is None:
        scope, parameters = (" where case_id=?", [case_id]) if case_id is not None else ("", [])
        connection.execute("delete from source_ledger_index" + scope, parameters)
    for source_case, record_source, record_nos in _source_record_pages(connection, case_id, source_id):
        connection.execute("""insert into source_ledger_index(case_id,flow_id,counter_account,counter_name,counter_bank)
            with _index_inputs as materialized (
                select r.case_id,r.source_id,r.record_no,
                    json_extract_string(r.ledger_evidence,'$.counterparty.account') counter_account,
                    json_extract_string(r.ledger_evidence,'$.counterparty.name') counter_name,
                    json_extract_string(r.ledger_evidence,'$.counterparty.bank') counter_bank
                from source_ledger_records r
                where r.case_id=? and r.source_id=?
                  and r.record_no between ?::bigint and ?::bigint
                  and r.record_no in (select unnest(?::bigint[]))
                  and (json_extract_string(r.ledger_evidence,'$.kind')='ledger' or r.disposition='cancelled'))
            select f.case_id,f.flow_id,max(r.counter_account),max(r.counter_name),max(r.counter_bank)
            from _index_inputs r join flow_sources f
              on f.case_id=r.case_id and f.source_id=r.source_id and f.source_row=r.record_no
            where f.case_id=? and f.source_id=? and f.source_row between ?::bigint and ?::bigint
            group by f.case_id,f.flow_id
            on conflict(case_id,flow_id) do update set
                counter_account=greatest(source_ledger_index.counter_account,excluded.counter_account),
                counter_name=greatest(source_ledger_index.counter_name,excluded.counter_name),
                counter_bank=greatest(source_ledger_index.counter_bank,excluded.counter_bank)""",
            [source_case, record_source, record_nos[0], record_nos[-1], record_nos,
             source_case, record_source, record_nos[0], record_nos[-1]])
    refresh_cards(connection, case_id, source_id)


def refresh_cards(connection, case_id=None, source_id=None):
    """Keep source-owned card aliases independently of financial deduplication."""
    if source_id is None:
        scope, parameters = (" where case_id=?", [case_id]) if case_id is not None else ("", [])
        connection.execute("delete from flow_source_cards" + scope, parameters)
    for source_case, record_source, record_nos in _source_record_pages(connection, case_id, source_id):
        connection.execute("""insert into flow_source_cards(case_id,flow_id,role,own_account,own_card)
            with _card_inputs as materialized (
                select r.case_id,r.source_id,r.record_no,
                    json_extract_string(r.ledger_evidence,'$.direction') direction,
                    json_extract_string(r.ledger_evidence,'$.row.self_account') own_account,
                    json_extract_string(r.ledger_evidence,'$.own_card') own_card
                from source_ledger_records r
                where r.case_id=? and r.source_id=?
                  and r.record_no between ?::bigint and ?::bigint
                  and r.record_no in (select unnest(?::bigint[])))
            select distinct f.case_id,f.flow_id,
                case r.direction when 'out' then 'payer'
                    when 'in' then 'payee' else 'self' end,
                r.own_account,r.own_card
            from _card_inputs r join flow_sources f
              on f.case_id=r.case_id and f.source_id=r.source_id and f.source_row=r.record_no
            where nullif(r.own_card,'') is not null
              and nullif(r.own_account,'') is not null
              and f.case_id=? and f.source_id=?
              and f.source_row between ?::bigint and ?::bigint
            on conflict(case_id,flow_id,role,own_account,own_card) do nothing""",
            [source_case, record_source, record_nos[0], record_nos[-1], record_nos,
             source_case, record_source, record_nos[0], record_nos[-1]])


def install_visible_view(connection, projection_sql):
    connection.execute("""create or replace view source_visible_flows as
        with financial as materialized(select * from source_effective_flows),
        ledger as materialized(select f.* from flows f join source_ledger_index l on l.case_id=f.case_id and l.flow_id=f.id),
        visible as (select * from financial union all select l.* from ledger l
            where not exists(select 1 from financial e where e.case_id=l.case_id and e.id=l.id))
        select f.*,coalesce(l.counter_account,'') source_counter_account,
            coalesce(l.counter_name,'') source_counter_name,coalesce(l.counter_bank,'') source_counter_bank,
            coalesce(c.self_card,'') source_own_card
        from visible f left join source_ledger_index l on l.case_id=f.case_id and l.flow_id=f.id
        left join flow_source_card_projection c on c.case_id=f.case_id and c.flow_id=f.id""")
    connection.execute("create or replace view effective_visible_flows as " +
        "select f.*,coalesce(e.entity_id,'') source_counter_id from (" +
        projection_sql.replace("from source_effective_flows f", "from source_visible_flows f") +
        ") f left join resolved_endpoints e on e.case_id=f.case_id and e.flow_id=f.id and e.role='counter'")


def _commit_records(case_id, source_id, batch_id, records):
    """Match and write one bounded set of distinct physical source positions."""
    import pandas as pd
    from . import table_import
    supported = {item["source_row"] for item in db.q_raw("""select source_row from flow_sources
        where case_id=? and source_id=? and source_row in (select unnest(?::bigint[]))""",
        [case_id, source_id, [record["record_no"] for record, _ in records]])}
    records = [(record, row) for record, row in records if record["record_no"] not in supported]
    if not records:
        return 0, 0
    expected = {record["record_no"]: table_import.flow_digest(row) for record, row in records}
    remaining = {record["record_no"]: record for record, _ in records}
    matched, after = {}, None
    # Exact source position is support identity. Bound candidate results too:
    # one position may have arbitrarily many historical links or changed facts.
    while remaining:
        requested = list(remaining.values())
        parameters = [[record[key] for record in requested]
                      for key in ("record_no", "member_path", "member_sha256", "source_row")]
        parameters.append(case_id)
        condition = ""
        if after is not None:
            condition = " and (requested.record_no,f.id)>(?,?)"
            parameters.extend(after)
        parameters.append(_COMMIT_CHUNK)
        candidates = db.q_raw("""select distinct requested.record_no matched_record_no,f.id,""" +
            ",".join("f." + field for field in FLOW_FIELDS) + """
            from (select unnest(?::bigint[]) record_no,unnest(?::varchar[]) member_path,
                         unnest(?::varchar[]) member_sha256,unnest(?::bigint[]) source_row) requested
            join import_records r on r.member_path=requested.member_path and r.source_row=requested.source_row
            join import_members m on m.case_id=r.case_id and m.source_id=r.source_id and m.member_path=r.member_path
              and m.sha256=requested.member_sha256
            join flow_sources s on s.case_id=r.case_id and s.source_id=r.source_id and s.source_row=r.record_no
            join flows f on f.case_id=s.case_id and f.id=s.flow_id
            where r.case_id=?""" + condition + " order by matched_record_no,f.id limit ?", parameters)
        for prior in candidates:
            number = prior["matched_record_no"]
            if number in remaining and table_import.flow_digest(prior) == expected[number]:
                matched[number] = prior["id"]
                del remaining[number]
        if len(candidates) < _COMMIT_CHUNK:
            break
        after = candidates[-1]["matched_record_no"], candidates[-1]["id"]
    new = [(record, row) for record, row in records if record["record_no"] not in matched]
    ids = db.q_raw("select nextval('flow_seq') id from range(?)", [len(new)]) if new else []
    columns = ["id", "case_id", "batch_id", *FLOW_FIELDS]
    if new:
        data = [[item["id"], case_id, batch_id, *[row[key] for key in FLOW_FIELDS]]
                for item, (_, row) in zip(ids, new)]
        db.insert_dataframe("flows", pd.DataFrame(data, columns=columns, dtype=object), columns)
    assigned = {record["record_no"]: item["id"] for item, (record, _) in zip(ids, new)}
    columns = ["case_id", "source_id", "source_row", "flow_id", "disposition"]
    data = [[case_id, source_id, record["record_no"],
             matched.get(record["record_no"], assigned.get(record["record_no"])),
             "duplicate" if record["record_no"] in matched else "imported"] for record, _ in records]
    db.insert_dataframe("flow_sources", pd.DataFrame(data, columns=columns, dtype=object), columns)
    return len(new), len(matched)


@contextmanager
def _commit_snapshot(case_id, source_id):
    """Drain the transaction's shared cursor before source publication writes."""
    from .import_staging import MAX_SPOOL_BYTES
    from .import_validation import IntegrityError

    with tempfile.TemporaryFile(mode="w+b", prefix="funds-trace-ledger-", dir=db.DATA_DIR) as stream:
        if hasattr(os, "fchmod"):
            os.fchmod(stream.fileno(), 0o600)
        cursor = db.con().execute("""select record_no,member_path,member_sha256,source_row,disposition,ledger_evidence
            from source_ledger_records where case_id=? and source_id=? order by record_no""",
                                  [case_id, source_id])
        columns = [column[0] for column in cursor.description]
        digest, count, size = hashlib.sha256(), 0, 0
        while batch := cursor.fetchmany(_COMMIT_CHUNK):
            for values in batch:
                encoded = (json.dumps(dict(zip(columns, values)), ensure_ascii=False,
                                      separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
                size += len(encoded)
                if size > MAX_SPOOL_BYTES:
                    raise IntegrityError("source ledger snapshot exceeds temporary storage limit")
                if stream.write(encoded) != len(encoded):
                    raise IntegrityError("incomplete source ledger snapshot write")
                digest.update(encoded)
                count += 1
        # No raw ledger value or fetched batch overlaps the decoded write batch.
        values = encoded = None
        stream.flush()
        stream.seek(0)

        def records():
            actual, read = hashlib.sha256(), 0
            for encoded in stream:
                actual.update(encoded)
                read += 1
                if read > count:
                    raise IntegrityError("incomplete source ledger snapshot")
                try:
                    record = json.loads(encoded)
                except (UnicodeError, json.JSONDecodeError):
                    raise IntegrityError("incomplete source ledger snapshot") from None
                if not isinstance(record, dict) or record.keys() != set(columns):
                    raise IntegrityError("incomplete source ledger snapshot")
                yield record
            if read != count or actual.digest() != digest.digest():
                raise IntegrityError("incomplete source ledger snapshot")

        yield records()


def commit(case_id, source_id, batch_id):
    """Publish typed observations in the same transaction as their journal."""
    added = duplicates = 0
    pending, positions = [], set()
    def publish():
        nonlocal added, duplicates
        if pending:
            new, reused = _commit_records(case_id, source_id, batch_id, pending)
            added += new
            duplicates += reused
            pending.clear()
            positions.clear()
    with _commit_snapshot(case_id, source_id) as records:
        for record in records:
            try:
                evidence = validate(record.pop("ledger_evidence"))
            except (ValueError, TypeError, ArithmeticError):
                # Preserve sequence consumption by the valid prefix; the caller's
                # enclosing import transaction still rolls all its writes back.
                publish()
                raise
            if evidence["kind"] != "ledger" and record["disposition"] != "cancelled":
                continue
            position = record["member_path"], record["member_sha256"], record["source_row"]
            if position in positions:
                # Read a repeated position only after the earlier write is visible,
                # using its actual persisted facts and the established digest.
                publish()
            pending.append((record, evidence["row"]))
            positions.add(position)
            if len(pending) == _COMMIT_CHUNK:
                publish()
    publish()
    # The import writer registers one fresh source and only adds its links.
    # Retirement, restoration and other mutations retain the full refresh path.
    refresh_index(db.con(), case_id, source_id)
    return {"added": added, "duplicates": duplicates}


@contextmanager
def _record_pages(case_id, ids, *, members=(), originals=()):
    """Validate full snapshots before retaining only source-read fields.

    Every payload uses an exact source PK page; 4096 bounds rows, not bytes.
    Yield only after its borrowed result is exhausted so consumers may query.
    """
    wanted, members, originals = set(ids), set(members), set(originals)
    with db.read_lock():
        connection = getattr(db._read_state, "connection", None)
        if connection is None:
            connection = db.con()

        def iterate():
            for source_case, source_id, record_nos in _source_record_pages(connection, case_id):
                bounds = [source_case, source_id, record_nos[0], record_nos[-1], record_nos]
                locations = db.q_raw("""select r.record_no,r.member_path,s.flow_id
                    from import_records r left join (
                        select case_id,source_id,source_row,flow_id from flow_sources
                        where case_id=? and source_id=? and source_row between ?::bigint and ?::bigint
                          and source_row in (select unnest(?::bigint[]))) s
                      on s.case_id=r.case_id and s.source_id=r.source_id and s.source_row=r.record_no
                    where r.case_id=? and r.source_id=? and r.record_no between ?::bigint and ?::bigint
                      and r.record_no in (select unnest(?::bigint[]))""", [*bounds, *bounds])
                selected = {row["record_no"] for row in locations if row["flow_id"] in wanted
                            or (source_id, row["member_path"]) in members
                            or (source_id, row["record_no"]) in originals}
                numbers = [number for number in record_nos if number in selected]
                if not numbers:
                    continue
                bounds = [source_case, source_id, numbers[0], numbers[-1], numbers]
                rows = []
                with db.raw_rows("""select r.case_id,r.source_id,r.record_no,r.member_path,r.source_row,
                    r.disposition,r.reason_code,r.ledger_evidence,s.flow_id,
                    case when r.disposition='cancelled' and r.reason_code='matched_cancellation'
                        then json_object('cancellation_role',json_extract_string(r.details_json,'$.cancellation_role'),
                            'paired_record_no',json_extract_string(r.details_json,'$.paired_record_no'))
                        else '{}' end details_json
                    from source_ledger_records r left join (
                        select case_id,source_id,source_row,flow_id from flow_sources
                        where case_id=? and source_id=? and source_row between ?::bigint and ?::bigint
                          and source_row in (select unnest(?::bigint[]))) s
                      on s.case_id=r.case_id and s.source_id=r.source_id and s.source_row=r.record_no
                    where r.case_id=? and r.source_id=? and r.record_no between ?::bigint and ?::bigint
                      and r.record_no in (select unnest(?::bigint[]))""", [*bounds, *bounds]) as raw:
                    for row in raw:
                        if (row["flow_id"] not in wanted and (source_id, row["member_path"]) not in members
                                and (source_id, row["record_no"]) not in originals):
                            continue
                        evidence = validate(row.pop("ledger_evidence"))
                        row["evidence"] = {key: evidence[key] for key in (
                            "policy", "kind", "header_scope", "source_currency", "direction", "own_card",
                            "signed_amount", "source_direction", "balance_before", "reported_status")}
                        row["evidence"]["row"] = {key: evidence["row"][key] for key in (
                            "self_account", "balance", "trade_time", "trade_time_utc")}
                        if "counterparty" in evidence:
                            row["evidence"]["counterparty"] = evidence["counterparty"]
                        rows.append(row)
                        del evidence
                yield rows

        pages = iterate()
        try:
            yield pages
        finally:
            pages.close()


def _records(case_id, ids):
    """Load selected observations and their required source context, per read.

    A complete member is retained when chronology is needed: context records,
    incomparable clocks and equal-time competitors must still block a witness.
    Unoriented/same-entry observations need no preceding financial ledger.
    """
    with db.read_lock():
        with _record_pages(case_id, ids) as pages:
            rows = [row for page in pages for row in page]
        members, originals = set(), set()
        for row in rows:
            evidence = row["evidence"]
            if (evidence["direction"] != "unknown" and evidence["kind"] != "context"
                    and evidence["source_currency"] and evidence["row"]["balance"] is not None
                    and evidence["row"]["trade_time"] and evidence["balance_before"] is None):
                members.add((row["source_id"], row["member_path"]))
            if row["disposition"] == "cancelled" and row["reason_code"] == "matched_cancellation":
                details = json.loads(row["details_json"])
                if details["cancellation_role"] != "original":
                    originals.add((row["source_id"], int(details["paired_record_no"])))
        if members or originals:
            del rows
            with _record_pages(case_id, ids, members=members, originals=originals) as pages:
                rows = [row for page in pages for row in page]
        rows.sort(key=lambda row: (row["source_id"], row["member_path"], row["source_row"]))
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


def _summary_counts(case_id, ids):
    result = {"count": 0, "unknown_effect_count": 0, "zero_count": 0, "paired_count": 0}
    if not ids:
        return result, set()
    first, originals = {}, set()
    with _record_pages(case_id, ids) as pages:
        for page in pages:
            for row in page:
                original = None
                if row["disposition"] == "cancelled" and row["reason_code"] == "matched_cancellation":
                    details = json.loads(row["details_json"])
                    original = (row["source_id"], row["record_no"] if details["cancellation_role"] == "original"
                                else int(details["paired_record_no"]))
                    originals.add(original)
                order = row["source_id"], row["member_path"], row["source_row"]
                previous = first.get(row["flow_id"])
                if previous is None or order < previous[0]:
                    record = {key: row[key] for key in (
                        "source_id", "record_no", "disposition", "reason_code", "details_json", "flow_id")}
                    first[row["flow_id"]] = (order, money_decimal(row["evidence"]["signed_amount"]) == 0,
                                             original, record)
    original_records = defaultdict(list)
    if originals:
        with _record_pages(case_id, (), originals=originals) as pages:
            for page in pages:
                for row in page:
                    original_records[row["source_id"], row["record_no"]].append(
                        {key: row[key] for key in ("source_id", "record_no", "flow_id")})
    for _, zero, original, record in first.values():
        paired = _current_pair(case_id, record, original_records[original]) if original is not None else False
        result["paired_count" if paired else "zero_count" if zero else "unknown_effect_count"] += 1
    result["count"] = len(first)
    return result, set(first)


def summary(case_id, *, account=None, include_rows=False):
    # Counts need source effects, not a balance neighborhood for every posting.
    # Keep selection, current cancellation authority and optional full details
    # in one lock or the caller's existing committed private snapshot.
    with db.read_lock():
        indexed = [row["flow_id"] for row in db.q_raw(
            "select flow_id from source_ledger_index where case_id=?", [case_id])]
        query = """select f.id from effective_visible_flows f where f.case_id=?
            and f.id in (select unnest(?::bigint[]))"""
        params = [case_id, indexed]
        if account is not None:
            query += " and (f.payer_account=? or f.payee_account=? or f.self_account=? or f.source_counter_id=?)"
            params += [account] * 4
        ids = [row["id"] for row in db.q_raw(query + " order by f.trade_time,f.id", params)] if indexed else []
        # Retain the original independent effective-flow read boundary: joining
        # both expanded views here can fail DuckDB's column-binding rewrite.
        financial = {row["id"] for row in db.q_raw("""select id from source_effective_flows
            where case_id=? and id in (select unnest(?::bigint[]))""", [case_id, ids])} if ids else set()
        ids = [value for value in ids if value not in financial]
        result, retained = _summary_counts(case_id, ids)
        if include_rows:
            tail = [value for value in ids if value in retained][-50:]
            rows = db.q_raw("""select f.* from effective_visible_flows f where f.case_id=?
                and f.id in (select unnest(?::bigint[])) order by f.trade_time,f.id""", [case_id, tail]) if tail else []
            entries = information(case_id, tail)
            result["records"] = [{"id": str(row["id"]), "trade_no": row["trade_no"] or "",
                "trade_time": str(row["trade_time"]), "amount": format(money_decimal(row["amount"]), ".2f"),
                "currency": row["currency"] or "", "summary": row["summary"] or "",
                "source_ledger": entries[str(row["id"])]} for row in rows]
            result["has_more"] = result["count"] > 50
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
