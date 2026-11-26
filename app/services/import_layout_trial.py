"""Read-only interpretation of small, explicitly selected physical regions."""
from collections import defaultdict
import csv
from decimal import localcontext
import zipfile

from app import import_originals, table_import
from app.table_mapping import ACCOUNT_CARD_FIELDS, document_number_header
from .cases import ServiceError, preview_flow


_MAX_FILE_BYTES = 4 * 1024 * 1024
_MAX_SOURCE_ROWS = 256
_MAX_TRIAL_ROWS = 64


def check_scope(session, members, regions, source_rows):
    if not members or any(region.member_path not in members for region in regions):
        raise ServiceError(422, "outside_layout_review", "试算仅限本次预览已登记的原件表格")
    if session["size_bytes"] > _MAX_FILE_BYTES or source_rows > _MAX_SOURCE_ROWS:
        raise ServiceError(422, "layout_trial_limit", "原件超出小表试算范围")
    counts = 0
    covered = defaultdict(set)
    for region in regions:
        start, last = region.header_row, region.last_row
        member = members[region.member_path]["scope"]
        if not start < last <= member["source_rows"] or any(start < n <= last for n in member["header_rows"]):
            raise ServiceError(422, "outside_layout_review", "试算区域超出原件或跨越已登记表头")
        rows = set(range(start, last + 1))
        if covered[region.member_path] & rows:
            raise ServiceError(422, "outside_layout_review", "试算表区不得重复或重叠")
        counts += last - start
        if counts > _MAX_TRIAL_ROWS:
            raise ServiceError(422, "layout_trial_limit", "所选记录数超出小表试算范围")
        covered[region.member_path].update(rows)
    return covered, members, regions


def evaluate(source, suffix, profile, selection, reference_policy, *, evidence_layout=False, reject_opening_clock=False):
    covered, members, regions = selection
    if suffix == ".xlsx":
        try:
            with zipfile.ZipFile(source) as archive:
                if sum(member.file_size for member in archive.infolist()) > table_import.MAX_LAYOUT_XLSX_BYTES:
                    raise ServiceError(422, "layout_trial_limit", "工作簿展开大小超出小表试算范围")
        except zipfile.BadZipFile:
            raise table_import.TableImportError("工作簿无法读取") from None
    tiers_enabled = profile in table_import.TIERED_PROFILES
    grouped = {}
    if suffix == ".xlsx":
        raw = table_import._bytes(source)
        sheets = table_import._excel(raw, suffix, mark_errors=True)
        if profile == table_import.table_tiered_headers.GROUPED_PROFILE:
            grouped = table_import._grouped_tiers(raw, sheets)
    else:
        [entry] = members.values()
        document, scope = entry["document"], entry["scope"]
        try:
            raw_rows = import_originals._csv_rows(source, entry["encoding"], document,
                set(range(1, scope["source_rows"] + 2)), profile, for_layout=True)
        except (ValueError, OSError, csv.Error):
            raise table_import.TableImportError("原始分隔结构无法核对") from None
        sheets = [{"path": scope["member_path"], "name": scope["sheet"], "rows": list(raw_rows.values())}]
    if sum(len(sheet["rows"]) for sheet in sheets) > _MAX_SOURCE_ROWS:
        raise ServiceError(422, "layout_trial_limit", "原件记录数超出小表试算范围")
    by_path = {sheet["path"]: sheet for sheet in sheets}
    for path, entry in members.items():
        member = entry["scope"]
        sheet = by_path.get(path)
        if not sheet or sheet["name"] != member["sheet"] or len(sheet["rows"]) != member["source_rows"]:
            raise ServiceError(409, "layout_review_changed", "原件表格位置与已登记范围不一致")
    tiers = {sheet["path"]: table_import._potential_tiers(sheet["rows"]) for sheet in sheets} if tiers_enabled else {}
    for path, proofs in grouped.items():
        tiers[path][0].update(proofs)
    output = []
    with localcontext() as context:
        if profile in table_import.CONTRACT_PROFILES:
            context.prec = max(context.prec, 50)
        signs = table_import._source_sign_contracts(sheets, profile, tiers)
        for region in regions:
            path = region.member_path
            rows = by_path[path]["rows"]
            tier, _ = tiers.get(path, ({}, {}))
            proof = tier.get(region.header_row - 1)
            if proof:
                covered[path].add(region.header_row - 1)
            sign_contract = signs.get(path)
            if sign_contract and not sign_contract.get("error"):
                initial_header, _ = table_import._initial_region(rows, profile, tier)
                if initial_header != region.header_row - 1:
                    sign_contract = {"error": "conflicting_direction"}
            readings = _region(rows, region, profile, reference_policy, sign_contract, proof,
                               evidence_layout=evidence_layout, reject_opening_clock=reject_opening_clock)
            output.append({"member_path": path, "header_row": region.header_row, "rows": readings})
    uncovered = sum(number not in covered[sheet["path"]] and any(table_import._cell_present(cell) for cell in row)
                    for sheet in sheets for number, row in enumerate(sheet["rows"], 1))
    return {"regions": output, "uncovered_present_rows": uncovered}


def _region(rows, region, profile, reference_policy, sign_contract, tiered_proof, *,
            evidence_layout=False, reject_opening_clock=False):
    headers = rows[region.header_row - 1]
    labels = table_import._columns(headers, profile)
    indices = list(region.mapping.values())
    if len(set(indices)) != len(indices) or any(index > len(labels) for index in indices):
        raise table_import.TableImportError("试算列越界或重复")
    if any(field in ACCOUNT_CARD_FIELDS and document_number_header(table_import._norm(table_import._text(headers[index - 1])))
           for field, index in region.mapping.items()):
        raise ServiceError(422, "invalid_layout_mapping", "证件号码列不能映射为交易账号或卡号")
    if reject_opening_clock and any(
        table_import.opening_clock_role_conflict(field, table_import._norm(table_import._text(headers[index - 1])))
        for field, index in region.mapping.items()
    ):
        raise ServiceError(422, "invalid_layout_mapping", "开户日期或时间不能映射为交易时钟")
    mapping = {field: labels[index - 1] for field, index in region.mapping.items()}
    candidates, _, mode, _, region_error, active_reference = table_import._region(
        headers, region.mode, mapping, strict=True, reference_policy=reference_policy,
        voucher_reference=reference_policy == table_import.TRANSACTION_REFERENCE_POLICY,
        profile=profile, tiered_proof=tiered_proof, manual_mapping_evidence=True,
    )
    if sign_contract and not sign_contract.get("error"):
        authoritative = {item["index"] for item in table_import._candidates(headers, profile).get("amount", ())}
        selected = {item["index"] for item in candidates.get("amount", ())}
        if len(authoritative) != 1 or authoritative != selected:
            sign_contract = {"error": "conflicting_direction"}
    contract = table_import.table_contracts.compile_contract(candidates) if profile in table_import.CONTRACT_PROFILES else None
    combined = (table_import.table_counterparty.source_column(headers)
                if profile == table_import.BANK_COMPONENT_TRIAL_PROFILE else None)
    result = []
    for number in range(region.header_row + 1, region.last_row + 1):
        row = rows[number - 1]
        preview = None
        details = {}
        if not any(table_import._cell_present(cell) for cell in row):
            disposition, reason = "non_transaction", "blank_record"
        elif len(row) != len(headers):
            disposition, reason = "unresolved", "row_width"
        elif region_error:
            disposition, reason = "unresolved", region_error
        elif table_import.invalid_layout_fact(row, candidates):
            disposition, reason = "unresolved", "invalid_cell_type"
        else:
            (parsed, disposition, reason, details), *_ = table_import._interpret_table_row(
                row, candidates, mode, profile, active_reference, contract, sign_contract,
                source_row=number, combined_column=combined,
            )
            if parsed is not None:
                preview = preview_flow(parsed)
                preview.update(source_member=region.member_path, source_row=number)
        record = {"source_row": number, "disposition": disposition, "reason": reason, "preview": preview}
        if evidence_layout and table_import.retained_split_conflict(disposition, reason, details):
            record["retained_split_conflict"] = True
        result.append(record)
    return result
