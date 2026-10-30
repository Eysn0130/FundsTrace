"""Read-only, direct CSV links to archive-v4 cancellation evidence.

These relations never canonicalize archive paths or change a source journal.
Both effective money and coverage consume the same valid proof set.
"""
from pathlib import Path

from . import table_import


SHARED_PROFILE = "funds-trace-table-archive-v4"
ORDINARY_PROFILES = table_import.SUPPORTED_PROFILES - {table_import.XLS_PROFILE}


def ordinary_csv_member(profile, source, member):
    return (source is not None and profile in ORDINARY_PROFILES
            and isinstance(source.get("filename"), str) and bool(source["filename"])
            and Path(source["filename"]).suffix.lower() in (".csv", ".tsv", ".txt", "")
            and source.get("sheet", "") == ""
            and source.get("row_kind", table_import.ROW_KIND) == table_import.ROW_KIND
            and member["member_path"] == "table.csv" and member["role"] == "table"
            and member["encoding"] in ("utf-8-sig", "gb18030", "utf-16")
            and member["sha256"] == source.get("sha256"))


def _literal(value):
    return "'" + value.replace("'", "''") + "'"


def _profiles(values):
    return ",".join(_literal(value) for value in sorted(values))


def ordinary_sql(source="s", member="m"):
    """SQL counterpart of the established D01 text-member qualification."""
    basename = f"lower(regexp_extract({source}.filename,'[^/]+$'))"
    return f"""{source}.parser_version in ({_profiles(ORDINARY_PROFILES)})
        and {source}.filename is not null and {source}.filename<>''
        and regexp_matches({basename},'(^[^.]+$|^\\.[^.]+$|\\.$|\\.(csv|tsv|txt)$)')
        and {source}.sheet='' and {source}.row_kind='table_record'
        and {member}.member_path='table.csv' and {member}.role='table'
        and {member}.encoding in ('utf-8-sig','gb18030','utf-16')
        and {member}.sha256={source}.sha256"""


def conflict_sql(source="s", record="r"):
    """Keep A01's version domains; an old v5 HISTORY is not a sticky gap."""
    notes = f"try_cast({record}.details_json as json)"
    basis = f"json_extract_string({notes},'$.counterparty_basis')"
    archive_profiles = {f"funds-trace-table-archive-v{version}" for version in range(1, 6)}
    return f"""{record}.disposition='unresolved' and (
        {source}.parser_version in ('funds-trace-table-archive-v3','funds-trace-table-archive-v4','funds-trace-table-archive-v5','funds-trace-table-archive-v6','funds-trace-table-archive-v7','funds-trace-table-archive-v8','funds-trace-table-archive-v9')
          and {record}.reason_code='cancellation_historical_conflict'
        or
        {source}.parser_version in ({_profiles(table_import.ROLE_PROFILES)})
          and {basis} in ({_literal(table_import.ROLE_CONFLICT_BASIS)},{_literal(table_import.HISTORY_CONFLICT_BASIS)})
        or {source}.parser_version in ({_profiles(table_import.SUPPORTED_PROFILES | archive_profiles)})
          and {record}.reason_code='conflicting_field'
          and {basis}={_literal(table_import.ARCHIVE_HISTORY_CONFLICT_BASIS)}
        or {source}.parser_version in ({_profiles(table_import.FINANCIAL_PROFILES)})
          and json_extract_string({notes},'$.validation_basis')={_literal(table_import.FINANCIAL_CONFLICT_BASIS)}
    )"""


def ctes(*, scoped=False):
    """CTEs for WITH RECURSIVE; scoped=True consumes one case-id parameter.

    Start with v4 proof members, then join only their direct physical positions.
    Recursive invalidation follows witness links within that proof source.
    Materialize the cancellation lookup before its witness lookup so an
    uncommitted journal cannot be joined to itself on member identity alone.
    """
    scope = "and p.case_id=?" if scoped else ""
    return f"""
    a02_roots as materialized (
      select o.case_id,o.source_id proof_source_id,o.record_no original_no,o.member_path,m.sha256,
        o.source_row original_row,try_cast(o.details_json as json) original_details,
        try_cast(json_extract_string(try_cast(o.details_json as json),'$.paired_record_no') as bigint) cancellation_no,
        try_cast(json_extract_string(try_cast(o.details_json as json),'$.cancellation_witness_record_no') as bigint) witness_no
      from import_sources p
      join import_records o on o.case_id=p.case_id and o.source_id=p.source_id
      join import_members m on m.case_id=o.case_id and m.source_id=o.source_id and m.member_path=o.member_path
      where p.row_kind='table_record' and p.parser_version in ('{SHARED_PROFILE}','funds-trace-table-archive-v5','funds-trace-table-archive-v6','funds-trace-table-archive-v7','funds-trace-table-archive-v8','funds-trace-table-archive-v9') {scope}
        and o.disposition='cancelled' and o.reason_code='matched_cancellation'
        and json_extract_string(try_cast(o.details_json as json),'$.cancellation_policy')='table-paired-cancellation-v1'
        and json_extract_string(try_cast(o.details_json as json),'$.cancellation_role')='original'
    ), a02_cancellation_candidates as materialized (
      select o.*,c.source_row cancellation_row,
        c.disposition cancellation_disposition,c.reason_code cancellation_reason,
        try_cast(c.details_json as json) cancellation_details
      from import_records c
      join a02_roots o on c.case_id=o.case_id and c.source_id=o.proof_source_id
        and c.member_path=o.member_path and c.record_no=o.cancellation_no
    ), a02_pair_candidates as materialized (
      select o.*,w.source_row witness_row,try_cast(w.details_json as json) witness_details
      from import_records w
      join a02_cancellation_candidates o on w.case_id=o.case_id and w.source_id=o.proof_source_id
        and w.member_path=o.member_path and w.record_no=o.witness_no
    ), a02_pairs as materialized (
      select * from a02_pair_candidates
      where cancellation_disposition='cancelled' and cancellation_reason='matched_cancellation'
        and json_extract_string(cancellation_details,'$.cancellation_policy')='table-paired-cancellation-v1'
        and json_extract_string(cancellation_details,'$.cancellation_role')='cancellation'
        and try_cast(json_extract_string(cancellation_details,'$.paired_record_no') as bigint)=original_no
        and try_cast(json_extract_string(cancellation_details,'$.cancellation_witness_record_no') as bigint)=witness_no
    ), a02_ordinary as materialized (
      select s.case_id,s.source_id,m.member_path,m.sha256
      from import_sources s
      join import_members m on m.case_id=s.case_id and m.source_id=s.source_id
      where {ordinary_sql()}
        and exists (select 1 from a02_pairs p where p.case_id=s.case_id and p.sha256=m.sha256)
    ), a02_positions as materialized (
      select case_id,proof_source_id,original_no,cancellation_no,witness_no,member_path,sha256,
        original_row source_row,'original' position_role,
        json_extract_string(original_details,'$.flow_sha256') expected_flow_sha,
        json_extract_string(original_details,'$.cancellation_own_account_sha256') expected_account_sha,
        json_extract_string(original_details,'$.cancellation_own_name_sha256') expected_name_sha,
        json_extract_string(original_details,'$.cancellation_quality') expected_quality from a02_pairs
      union all
      select case_id,proof_source_id,original_no,cancellation_no,witness_no,member_path,sha256,
        cancellation_row,'cancellation',null::varchar,null::varchar,null::varchar,null::varchar from a02_pairs
      union all
      select case_id,proof_source_id,original_no,cancellation_no,witness_no,member_path,sha256,
        witness_row,'witness',json_extract_string(witness_details,'$.flow_sha256'),
        json_extract_string(witness_details,'$.cancellation_own_account_sha256'),
        json_extract_string(witness_details,'$.cancellation_own_name_sha256'),
        json_extract_string(witness_details,'$.cancellation_quality') from a02_pairs
    ), a02_history_records as materialized (
      select p.*,r.source_id history_source_id,r.record_no history_no
      from a02_positions p
      join import_members m on m.case_id=p.case_id and m.member_path=p.member_path and m.sha256=p.sha256
      join import_records r on r.case_id=m.case_id and r.source_id=m.source_id
        and r.member_path=m.member_path and r.source_row=p.source_row
      union all
      select p.*,r.source_id,r.record_no
      from a02_positions p
      join a02_ordinary m on m.case_id=p.case_id and m.sha256=p.sha256
      join import_records r on r.case_id=m.case_id and r.source_id=m.source_id
        and r.member_path=m.member_path and r.source_row=p.source_row
    ), a02_history_details as materialized (
      select r.* from import_records r
      join (select distinct case_id,history_source_id,history_no from a02_history_records) wanted
        on r.case_id=wanted.case_id and r.source_id=wanted.history_source_id and r.record_no=wanted.history_no
    ), a02_history_source_facts as materialized (
      select r.case_id,r.source_id,r.record_no,r.disposition,
        ({conflict_sql()}) validation_conflict,
        json_extract_string(try_cast(r.details_json as json),'$.flow_sha256') flow_sha
      from a02_history_details r
      join import_sources s on s.case_id=r.case_id and s.source_id=r.source_id
    ), a02_history_links as materialized (
      select r.*,fs.flow_id linked_id
      from flow_sources fs right join a02_history_source_facts r
        on fs.case_id=r.case_id and fs.source_id=r.source_id and fs.source_row=r.record_no
    ), a02_history_facts as materialized (
      select r.*,f.id actual_id,
        case when coalesce(f.self_account,'')='' then '' else sha256(f.self_account) end account_sha,
        case when coalesce(f.self_name,'')='' then '' else sha256(f.self_name) end name_sha
      from flows f right join a02_history_links r on f.case_id=r.case_id and f.id=r.linked_id
    ), a02_bad_roots as (
      select distinct case_id,proof_source_id,original_no,cancellation_no
      from a02_positions p
      where position_role='original' or position_role='witness'
        and expected_quality='accepted'
      group by case_id,proof_source_id,original_no,cancellation_no
      having bool_or(not coalesce(regexp_full_match(expected_flow_sha,'[0-9a-f]{{64}}'),false)
          or not coalesce(regexp_full_match(expected_account_sha,'[0-9a-f]{{64}}'),false)
          or expected_name_sha is null)
      union
      select distinct r.case_id,r.proof_source_id,r.original_no,r.cancellation_no
      from a02_history_records r
      join a02_history_facts h on h.case_id=r.case_id and h.source_id=r.history_source_id and h.record_no=r.history_no
      where h.validation_conflict
        or h.disposition in ('imported','duplicate') and h.actual_id is null
        or (h.actual_id is not null or h.linked_id is not null) and (
          r.position_role='cancellation'
          or r.position_role='witness' and coalesce(r.expected_quality,'')<>'accepted'
          or coalesce(h.flow_sha,'')<>coalesce(r.expected_flow_sha,'')
          or not coalesce(regexp_full_match(r.expected_flow_sha,'[0-9a-f]{{64}}'),false)
          or h.account_sha<>coalesce(r.expected_account_sha,'!')
          or h.name_sha<>coalesce(r.expected_name_sha,'!')
        )
    ), a02_invalid(case_id,proof_source_id,original_no,cancellation_no) as (
      select * from a02_bad_roots
      union
      select p.case_id,p.proof_source_id,p.original_no,p.cancellation_no
      from a02_pairs p join a02_invalid i on i.case_id=p.case_id
        and i.proof_source_id=p.proof_source_id and i.cancellation_no=p.witness_no
    ), a02_valid as materialized (
      select p.* from a02_pairs p where not exists (
        select 1 from a02_invalid i where i.case_id=p.case_id
          and i.proof_source_id=p.proof_source_id and i.original_no=p.original_no)
    ), a02_shared_positions as materialized (
      select p.case_id,m.source_id,m.member_path,p.original_row,p.original_details
      from a02_valid p
      join a02_ordinary m on m.case_id=p.case_id and m.sha256=p.sha256
    ), a02_shared_records as materialized (
      select r.case_id,r.source_id,r.record_no,r.disposition,r.details_json,p.original_details
      from import_records r
      join a02_shared_positions p on r.case_id=p.case_id and r.source_id=p.source_id
        and r.member_path=p.member_path and r.source_row=p.original_row
    ), a02_shared_source_links as materialized (
      select fs.case_id,fs.source_id,fs.source_row,fs.flow_id,r.disposition,r.details_json,r.original_details
      from flow_sources fs
      join a02_shared_records r on fs.case_id=r.case_id and fs.source_id=r.source_id and fs.source_row=r.record_no
    ), a02_shared_candidates as materialized (
      select r.*,f.self_account,f.self_name
      from flows f
      join a02_shared_source_links r on f.case_id=r.case_id and f.id=r.flow_id
    ), a02_shared_links as materialized (
      select distinct case_id,source_id,source_row,flow_id from a02_shared_candidates
      where disposition in ('imported','duplicate')
        and json_extract_string(try_cast(details_json as json),'$.flow_sha256')=json_extract_string(original_details,'$.flow_sha256')
        and case when coalesce(self_account,'')='' then '' else sha256(self_account) end
            =json_extract_string(original_details,'$.cancellation_own_account_sha256')
        and case when coalesce(self_name,'')='' then '' else sha256(self_name) end
            =json_extract_string(original_details,'$.cancellation_own_name_sha256')
    ), a02_resolved_negative as materialized (
      select distinct r.case_id,r.source_id,r.record_no
      from a02_valid p
      join a02_ordinary m on m.case_id=p.case_id and m.sha256=p.sha256
      join import_records r on r.case_id=m.case_id and r.source_id=m.source_id
        and r.member_path=m.member_path and r.source_row=p.cancellation_row
      where r.disposition='unresolved' and r.reason_code='negative_amount'
        and json_extract_string(try_cast(r.details_json as json),'$.validation_issues')='negative_amount'
    )"""
