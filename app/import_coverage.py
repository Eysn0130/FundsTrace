"""Current semantic coverage of imported feedback records, without changing history."""
import hashlib
import json

from . import db, table_import


def _table_cancellation_conflicts(case_id):
    """Find competing actual facts even if a later ordinary mapping supplied them."""
    proofs = db.q_raw("""select r.*,m.sha256 from import_records r
        join import_sources s on s.case_id=r.case_id and s.source_id=r.source_id
        join import_members m on m.case_id=r.case_id and m.source_id=r.source_id and m.member_path=r.member_path
        where r.case_id=? and s.row_kind='table_record' and s.parser_version='funds-trace-table-archive-v3'
          and r.disposition='cancelled' and r.reason_code='matched_cancellation'""", [case_id])
    if not proofs:
        return set()
    by_source = {}
    for record in proofs:
        record["details"] = json.loads(record["details_json"])
        by_source.setdefault(record["source_id"], {})[record["record_no"]] = record
    fields = ",".join("f." + key for key in (*table_import.FLOW_FIELDS, "self_account", "self_name"))
    conflicting = set()
    for previous in db.q_raw(f"""select distinct c.source_id proof_source_id,c.record_no proof_record_no,{fields}
        from import_records c
        join import_sources p on p.case_id=c.case_id and p.source_id=c.source_id
        join import_members cm on cm.case_id=c.case_id and cm.source_id=c.source_id and cm.member_path=c.member_path
        join import_records r on r.case_id=c.case_id and r.member_path=c.member_path and r.source_row=c.source_row
        join import_members rm on rm.case_id=r.case_id and rm.source_id=r.source_id
          and rm.member_path=r.member_path and rm.sha256=cm.sha256
        join flow_sources fs on fs.case_id=r.case_id and fs.source_id=r.source_id and fs.source_row=r.record_no
        join flows f on f.case_id=fs.case_id and f.id=fs.flow_id
        where c.case_id=? and p.row_kind='table_record' and p.parser_version='funds-trace-table-archive-v3'
          and c.disposition='cancelled' and c.reason_code='matched_cancellation'""", [case_id]):
        source_id, number = previous["proof_source_id"], previous["proof_record_no"]
        details = by_source[source_id][number]["details"]
        hash_value = lambda value: hashlib.sha256((value or "").encode()).hexdigest() if value else ""
        if details.get("cancellation_role") == "cancellation":
            conflicting.add((source_id, int(details["paired_record_no"])))
        elif (table_import.flow_digest(previous) != details.get("flow_sha256")
                or hash_value(previous["self_account"]) != details.get("cancellation_own_account_sha256")
                or hash_value(previous["self_name"]) != details.get("cancellation_own_name_sha256")):
            conflicting.add((source_id, number))
    gaps = set()
    for source_id, records in by_source.items():
        invalid = {number for source, number in conflicting if source == source_id}
        while True:
            revoked_legs = invalid | {int(records[number]["details"]["paired_record_no"]) for number in invalid}
            dependent = {number for number, record in records.items()
                         if record["details"].get("cancellation_role") == "original"
                         and int(record["details"]["cancellation_witness_record_no"]) in revoked_legs}
            if dependent <= invalid:
                break
            invalid |= dependent
        for number in invalid:
            for leg in (number, int(records[number]["details"]["paired_record_no"])):
                record = records[leg]
                gaps.add((record["member_path"], record["sha256"], record["source_row"]))
    return gaps


def _header_resolution_pairs(case_id):
    """Discharge only the two closed, source-bound header corrections.

    An admitted v9/v10/v11/v12/v13/v14/v15/v16/v17 interpretation of the identical physical record must have
    precisely the old mapping with that one misclassified complete column
    moved to direction for the v7/v8 defect. The v11 English unit declaration
    correction requires identical mappings and an unknown-to-known factor on
    the same mapped monetary header from v7-v10. Neither proof clears other
    financial issues, role/history conflicts, or any previous source record.
    """
    # Return source pairs only: neither per-record details nor resolved row IDs
    # cross into Python or become individual SQL parameters for large files.
    candidates = db.q_raw("""select distinct s.source_id old_source_id,n.source_id new_source_id,
                s.mapping_json old_mapping,n.mapping_json new_mapping,
                s.parser_version old_profile,n.parser_version new_profile
        from import_sources s
        join import_members m on m.case_id=s.case_id and m.source_id=s.source_id
        join import_members nm on nm.case_id=m.case_id and nm.member_path=m.member_path and nm.sha256=m.sha256
        join import_sources n on n.case_id=nm.case_id and n.source_id=nm.source_id
          and n.parser_version in ('funds-trace-table-v9','funds-trace-table-v10','funds-trace-table-v11','funds-trace-table-v12','funds-trace-table-v13','funds-trace-table-v14','funds-trace-table-v15','funds-trace-table-v16','funds-trace-table-v17')
          and n.row_kind='table_record' and n.mode=s.mode
        where s.case_id=? and s.row_kind='table_record'
          and s.parser_version in ('funds-trace-table-v7','funds-trace-table-v8',
                                   'funds-trace-table-v9','funds-trace-table-v10')""", [case_id])
    resolved, mappings = set(), {}
    aliases = {"expense_amt": table_import._norm("debit/credit indicator"),
               "income_amt": table_import._norm("credit/debit indicator")}
    for record in candidates:
        pair = (record["old_mapping"], record["new_mapping"], record["old_profile"], record["new_profile"])
        if pair not in mappings:
            old, new = (json.loads(value) for value in pair[:2])
            proof = None
            if isinstance(old, dict) and isinstance(new, dict):
                # v13 adds only this previously ignored bank-name column.
                # Its presence must not revoke an otherwise identical closed
                # direction/unit proof. Row admission, source identity and the
                # old diagnostic constraints are still checked below in SQL.
                comparable = new
                bank_column = new.get("counter_bank")
                if (record["new_profile"] in table_import.BANK_NAME_PROFILES
                        and "counter_bank" not in old and isinstance(bank_column, str)
                        and table_import._norm(bank_column) == "交易对方账号开户行"):
                    comparable = {key: value for key, value in new.items() if key != "counter_bank"}
                if record["old_profile"] in {"funds-trace-table-v7", "funds-trace-table-v8"}:
                    for field, alias in aliases.items():
                        column = old.get(field)
                        if (isinstance(column, str) and table_import._norm(column) == alias
                                and old.get("direction", column) == column):
                            expected = {key: value for key, value in old.items() if key != field}
                            expected["direction"] = column
                            if comparable == expected:
                                proof = "direction"
                                break
                if record["new_profile"] in table_import.UNIT_DECLARATION_PROFILES and old == comparable:
                    for field in ("amount", "income_amt", "expense_amt", "balance", "balance_before"):
                        column = old.get(field)
                        if not isinstance(column, str):
                            continue
                        old_base, old_factor, _ = table_import._header_parts(column, record["old_profile"])
                        new_base, new_factor, _ = table_import._header_parts(column, record["new_profile"])
                        if old_base == new_base and old_factor is None and new_factor in table_import._UNIT.values():
                            proof = "unit"
                            break
            mappings[pair] = proof
        if mappings[pair]:
            resolved.add((record["old_source_id"], record["new_source_id"], mappings[pair]))
    return resolved


def import_coverage(case_id):
    """Count each physical source occurrence once using its strongest evaluation.

    Supported v5 evaluations supersede v4 and v3; v3 evaluations supersede v2, and v2 supersedes v1, regardless
    of the order in which different versions were imported. Table archive v3
    supersedes archive v2 and v1, then ordinary table v17 through v5 evaluations.
    Within one rule version the newest assessment wins, including explicit
    removal. Unsupported evaluations take precedence and remain uncertain even
    when marked imported or removed: an older application cannot interpret
    their disposition semantics. A supported own-identity conflict or explicit
    table identity-layout conflict or financial-validation conflict remains
    a gap even across archive evaluations:
    reimporting an earlier identity or deleting a flow is not evidence that the
    competing identity was resolved. Only a future supported resolution rule
    may discharge that gap. The bounded v9/v10/v11/v12/v13/v14/v15/v16/v17 whole-direction-header correction
    discharges only its proven v7/v8 diagnostic, and v11 discharges only proven
    v7-v10 English-unit-header diagnostics. Disclosure never rewrites
    source history. This describes imported feedback sources only, not whether
    a bank supplied every transaction or amounts are settlement net.
    """
    def read_coverage(conflicts, resolution_pairs):
        conflict_values = ("values " + ",".join("(?,?,?)" for _ in conflicts) if conflicts else
                           "select null::varchar,null::varchar,null::bigint where false")
        pair_values = ("values " + ",".join("(?,?,?)" for _ in resolution_pairs) if resolution_pairs else
                       "select null::varchar,null::varchar,null::varchar where false")
        return db.q_raw(f"""
            with conflicts(member_path,sha256,source_row) as ({conflict_values}),
              resolution_pairs(old_source_id,new_source_id,proof) as ({pair_values}),
              resolved as (
                select distinct r.source_id,r.record_no
                from resolution_pairs p
                join import_records r on r.source_id=p.old_source_id and r.case_id=?
                join import_members m on m.case_id=r.case_id and m.source_id=r.source_id
                  and m.member_path=r.member_path
                join import_records nr on nr.case_id=r.case_id and nr.source_id=p.new_source_id
                  and nr.member_path=r.member_path and nr.source_row=r.source_row
                join import_members nm on nm.case_id=nr.case_id and nm.source_id=nr.source_id
                  and nm.member_path=nr.member_path and nm.sha256=m.sha256
                where r.disposition='unresolved' and r.reason_code='unknown_unit'
                  and json_extract_string(r.details_json,'$.validation_basis')=?
                  and ((p.proof='direction' and json_extract_string(r.details_json,'$.validation_issues') in (
                    'unknown_unit','unknown_unit,missing_direction'))
                    or (p.proof='unit' and json_extract_string(r.details_json,'$.validation_issues') in (
                    'unknown_unit','unknown_unit,missing_amount')))
                  and nr.disposition in ('imported','duplicate','removed')
              ), evaluations as (
              select r.disposition,s.parser_version,
                case when s.parser_version in ('funds-trace-bank-feedback-v1',
                                              'funds-trace-bank-feedback-v2',
                                              'funds-trace-bank-feedback-v3',
                                              'funds-trace-bank-feedback-v4',
                                              'funds-trace-bank-feedback-v5',
                                              'funds-trace-table-v5',
                                              'funds-trace-table-v6',
                                              'funds-trace-table-v7',
                                              'funds-trace-table-v8',
                                              'funds-trace-table-v9',
                                              'funds-trace-table-v10',
                                              'funds-trace-table-v11',
                                              'funds-trace-table-v12',
                                              'funds-trace-table-v13',
                                              'funds-trace-table-v14',
                                              'funds-trace-table-v15',
                                              'funds-trace-table-v16',
                                              'funds-trace-table-v17',
                                              'funds-trace-table-archive-v1',
                                              'funds-trace-table-archive-v2',
                                              'funds-trace-table-archive-v3')
                  then false else true end as unsupported_rule,
                max(case when s.parser_version in ('funds-trace-bank-feedback-v1',
                                                  'funds-trace-bank-feedback-v2',
                                                  'funds-trace-bank-feedback-v3','funds-trace-bank-feedback-v4','funds-trace-bank-feedback-v5')
                              and r.reason_code='own_identity_conflict'
                            or s.parser_version='funds-trace-table-archive-v3'
                              and r.reason_code='cancellation_historical_conflict'
                            or s.parser_version in ('funds-trace-table-v6','funds-trace-table-v7','funds-trace-table-v8','funds-trace-table-v9','funds-trace-table-v10','funds-trace-table-v11','funds-trace-table-v12','funds-trace-table-v13','funds-trace-table-v14','funds-trace-table-v15','funds-trace-table-v16','funds-trace-table-v17')
                              and r.disposition='unresolved'
                              and json_extract_string(r.details_json,'$.counterparty_basis') in (?,?)
                            or s.parser_version in ('funds-trace-table-v7','funds-trace-table-v8','funds-trace-table-v9','funds-trace-table-v10','funds-trace-table-v11','funds-trace-table-v12','funds-trace-table-v13','funds-trace-table-v14','funds-trace-table-v15','funds-trace-table-v16','funds-trace-table-v17') and r.disposition='unresolved'
                              and json_extract_string(r.details_json,'$.validation_basis')=?
                              and h.record_no is null
                            or c.source_row is not null
                         then 1 else 0 end) over (
                  partition by r.member_path, m.sha256, r.source_row
                ) as identity_conflict,
                row_number() over (
                  partition by r.member_path, m.sha256, r.source_row
                  order by case s.parser_version
                    when 'funds-trace-bank-feedback-v3' then 3
                    when 'funds-trace-bank-feedback-v4' then 4
                    when 'funds-trace-bank-feedback-v5' then 4.5
                    when 'funds-trace-table-v5' then 5
                    when 'funds-trace-table-v6' then 5.5
                    when 'funds-trace-table-v7' then 5.75
                    when 'funds-trace-table-v8' then 5.875
                    when 'funds-trace-table-v9' then 5.9375
                    when 'funds-trace-table-v10' then 5.96875
                    when 'funds-trace-table-v11' then 5.984375
                    when 'funds-trace-table-v12' then 5.9921875
                    when 'funds-trace-table-v13' then 5.99609375
                    when 'funds-trace-table-v14' then 5.998046875
                    when 'funds-trace-table-v15' then 5.9990234375
                    when 'funds-trace-table-v16' then 5.99951171875
                    when 'funds-trace-table-v17' then 5.999755859375
                    when 'funds-trace-table-archive-v1' then 6
                    when 'funds-trace-table-archive-v2' then 7
                    when 'funds-trace-table-archive-v3' then 8
                    when 'funds-trace-bank-feedback-v2' then 2
                    when 'funds-trace-bank-feedback-v1' then 1 else 9 end desc,
                    s.imported_at desc nulls last, s.source_id desc
                ) as assessment_rank
              from import_records r
              join import_members m on m.case_id=r.case_id and m.source_id=r.source_id
                and m.member_path=r.member_path
              join import_sources s on s.case_id=r.case_id and s.source_id=r.source_id
              left join conflicts c on c.member_path=r.member_path and c.sha256=m.sha256 and c.source_row=r.source_row
              left join resolved h on h.source_id=r.source_id and h.record_no=r.record_no
              where r.case_id=? and s.row_kind in ('archive_record','table_record')
            )
            select count(*) filter (where parser_version='funds-trace-table-archive-v3' and disposition='cancelled') table_proofs,
                   count(*) filter (where unsupported_rule or identity_conflict=1
                                          or disposition in ('review','unresolved')) unresolved_records,
                   count(*) filter (where not unsupported_rule and identity_conflict=0
                                          and disposition='non_monetary') non_monetary_records,
                   count(*) filter (where not unsupported_rule and identity_conflict=0
                                          and disposition='cancelled') cancelled_records,
                   (select count(*) from flows where case_id=?) -
                   (select count(*) from effective_flows where case_id=?) excluded_flows
            from evaluations where assessment_rank=1
        """, [value for key in conflicts for value in key] + [value for key in resolution_pairs for value in key]
              + [case_id, table_import.FINANCIAL_CONFLICT_BASIS, table_import.ROLE_CONFLICT_BASIS,
              table_import.HISTORY_CONFLICT_BASIS, table_import.FINANCIAL_CONFLICT_BASIS,
              case_id, case_id, case_id])[0]
    with db._lock:
        resolution_pairs = _header_resolution_pairs(case_id)
        row = read_coverage(set(), resolution_pairs)
        if row["table_proofs"]:
            conflicts = _table_cancellation_conflicts(case_id)
            if conflicts:
                row = read_coverage(conflicts, resolution_pairs)
    return {"scope": "source_reported_flows",
            "unresolved_records": row["unresolved_records"],
            "non_monetary_records": row["non_monetary_records"],
            "cancelled_records": row["cancelled_records"], "excluded_flows": row["excluded_flows"],
            "financial_interpretation_complete": row["unresolved_records"] == 0}
