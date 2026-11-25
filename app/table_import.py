"""Versioned, deterministic CSV/workbook interpretation, without persistent writes.

Column names identify candidates, never a confidence score. Every logical source
record has a disposition; unsafe monetary interpretations remain source records.
The uploaded file is the original evidence, rather than a second raw-cell table.
"""
from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from decimal import Decimal, DecimalException, localcontext
import hashlib
import io
from ipaddress import ip_address
from itertools import islice
import json
from pathlib import Path
import re
import unicodedata
import zipfile

from .importer import FIELD_SYNONYMS, _row_key
from .table_mapping import ACCOUNT_CARD_FIELDS, document_number_header, identity_field
from . import table_financial, table_contracts, table_time_formats, table_source_roles, table_sign_declarations
from . import source_transaction_status, table_counterparty, table_direction_recheck, table_tiered_headers


IDENTITY_PROFILE = "funds-trace-table-v5"
ROLE_PROFILE = "funds-trace-table-v6"
FINANCIAL_PROFILE = "funds-trace-table-v7"
CONTRACT_PROFILE = "funds-trace-table-v8"
DIRECTION_PROFILE = "funds-trace-table-v9"
TIME_FORMAT_PROFILE = "funds-trace-table-v10"
UNIT_DECLARATION_PROFILE = "funds-trace-table-v11"
SOURCE_ROLE_PROFILE = "funds-trace-table-v12"
BANK_NAME_PROFILE = "funds-trace-table-v13"
QUERY_OWNER_PROFILE = "funds-trace-table-v14"
REGION_HEADER_PROFILE = "funds-trace-table-v15"
REORDERED_TIME_PROFILE = "funds-trace-table-v16"
CURRENCY_REGION_PROFILE = "funds-trace-table-v17"
XLS_PROFILE = "funds-trace-table-v18"
GUARDED_PROFILE = "funds-trace-table-v21"
GUARDED_XLS_PROFILE = "funds-trace-table-v22"
GUARDED_EVIDENCE_PROFILE = "funds-trace-table-v23"
# Internal archive CSV rule: v5 plus exactly the two header protections below.
# Keep it outside standalone and financial/identity inheritance collections.
ARCHIVE_MEMBER_PROFILE = "funds-trace-table-archive-v4"
LEGACY_EVIDENCE_PROFILE = "funds-trace-table-v19"
LEGACY_EVIDENCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v5"
EVIDENCE_PROFILE = "funds-trace-table-v20"
EVIDENCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v6"
XLS_EVIDENCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v7"
MICROSECOND_ARCHIVE_PROFILE = "funds-trace-table-archive-v8"
MICROSECOND_XLS_ARCHIVE_PROFILE = "funds-trace-table-archive-v9"
HEADER_WIDTH_ARCHIVE_PROFILE = "funds-trace-table-archive-v10"
HEADER_WIDTH_XLS_ARCHIVE_PROFILE = "funds-trace-table-archive-v11"
GUARDED_ARCHIVE_PROFILE = "funds-trace-table-archive-v12"
GUARDED_EVIDENCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v13"
GUARDED_XLS_ARCHIVE_PROFILE = "funds-trace-table-archive-v14"
PRECISE_PROFILE = "funds-trace-table-v24"
PRECISE_XLS_PROFILE = "funds-trace-table-v25"
PRECISE_EVIDENCE_PROFILE = "funds-trace-table-v26"
PRECISE_ARCHIVE_PROFILE = "funds-trace-table-archive-v15"
PRECISE_EVIDENCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v16"
PRECISE_XLS_ARCHIVE_PROFILE = "funds-trace-table-archive-v17"
REFERENCE_ROLE_PROFILE = "funds-trace-table-v27"
REFERENCE_ROLE_XLS_PROFILE = "funds-trace-table-v28"
REFERENCE_ROLE_EVIDENCE_PROFILE = "funds-trace-table-v29"
REFERENCE_ROLE_ARCHIVE_PROFILE = "funds-trace-table-archive-v18"
REFERENCE_ROLE_EVIDENCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v19"
REFERENCE_ROLE_XLS_ARCHIVE_PROFILE = "funds-trace-table-archive-v20"
OPTIONAL_REFERENCE_PROFILE = "funds-trace-table-v30"
OPTIONAL_REFERENCE_XLS_PROFILE = "funds-trace-table-v31"
OPTIONAL_REFERENCE_EVIDENCE_PROFILE = "funds-trace-table-v32"
OPTIONAL_REFERENCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v21"
OPTIONAL_REFERENCE_EVIDENCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v22"
OPTIONAL_REFERENCE_XLS_ARCHIVE_PROFILE = "funds-trace-table-archive-v23"
TIME_ROLE_PROFILE = "funds-trace-table-v33"
TIME_ROLE_XLS_PROFILE = "funds-trace-table-v34"
TIME_ROLE_EVIDENCE_PROFILE = "funds-trace-table-v35"
TIME_ROLE_ARCHIVE_PROFILE = "funds-trace-table-archive-v24"
TIME_ROLE_EVIDENCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v25"
TIME_ROLE_XLS_ARCHIVE_PROFILE = "funds-trace-table-archive-v26"
ZONED_PROFILE = "funds-trace-table-v36"
ZONED_XLS_PROFILE = "funds-trace-table-v37"
ZONED_EVIDENCE_PROFILE = "funds-trace-table-v38"
ZONED_ARCHIVE_PROFILE = "funds-trace-table-archive-v27"
ZONED_EVIDENCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v28"
ZONED_XLS_ARCHIVE_PROFILE = "funds-trace-table-archive-v29"
SOURCE_CONTRACT_PROFILE = "funds-trace-table-v39"
SOURCE_CONTRACT_XLS_PROFILE = "funds-trace-table-v40"
SOURCE_CONTRACT_EVIDENCE_PROFILE = "funds-trace-table-v41"
SOURCE_CONTRACT_ARCHIVE_PROFILE = "funds-trace-table-archive-v30"
SOURCE_CONTRACT_EVIDENCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v31"
SOURCE_CONTRACT_XLS_ARCHIVE_PROFILE = "funds-trace-table-archive-v32"
SOURCE_TEXT_PROFILE = "funds-trace-table-v42"
SOURCE_TEXT_XLS_PROFILE = "funds-trace-table-v43"
SOURCE_TEXT_EVIDENCE_PROFILE = "funds-trace-table-v44"
SOURCE_TEXT_ARCHIVE_PROFILE = "funds-trace-table-archive-v33"
SOURCE_TEXT_EVIDENCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v34"
SOURCE_TEXT_XLS_ARCHIVE_PROFILE = "funds-trace-table-archive-v35"
BANK31_VARIANT_PROFILE = "funds-trace-table-v45"
BANK31_VARIANT_XLS_PROFILE = "funds-trace-table-v46"
BANK31_VARIANT_EVIDENCE_PROFILE = "funds-trace-table-v47"
BANK31_VARIANT_ARCHIVE_PROFILE = "funds-trace-table-archive-v36"
BANK31_VARIANT_EVIDENCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v37"
BANK31_VARIANT_XLS_ARCHIVE_PROFILE = "funds-trace-table-archive-v38"
SOURCE_LEDGER_PROFILE = "funds-trace-table-v48"
SOURCE_LEDGER_XLS_PROFILE = "funds-trace-table-v49"
SOURCE_LEDGER_EVIDENCE_PROFILE = "funds-trace-table-v50"
SOURCE_LEDGER_ARCHIVE_PROFILE = "funds-trace-table-archive-v39"
SOURCE_LEDGER_EVIDENCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v40"
SOURCE_LEDGER_XLS_ARCHIVE_PROFILE = "funds-trace-table-archive-v41"
POSTING_PAIR_PROFILE = "funds-trace-table-v51"
POSTING_PAIR_XLS_PROFILE = "funds-trace-table-v52"
POSTING_PAIR_EVIDENCE_PROFILE = "funds-trace-table-v53"
POSTING_PAIR_ARCHIVE_PROFILE = "funds-trace-table-archive-v42"
POSTING_PAIR_EVIDENCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v43"
POSTING_PAIR_XLS_ARCHIVE_PROFILE = "funds-trace-table-archive-v44"
# Restore recognized auxiliary scope evidence without reinterpreting old receipts.
AUXILIARY_SCOPE_PROFILE = "funds-trace-table-v54"
AUXILIARY_SCOPE_XLS_PROFILE = "funds-trace-table-v55"
AUXILIARY_SCOPE_EVIDENCE_PROFILE = "funds-trace-table-v56"
AUXILIARY_SCOPE_ARCHIVE_PROFILE = "funds-trace-table-archive-v45"
AUXILIARY_SCOPE_EVIDENCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v46"
AUXILIARY_SCOPE_XLS_ARCHIVE_PROFILE = "funds-trace-table-archive-v47"
# Native bank observations share field roles across standalone and archive input.
# Earlier parser versions retain their original receipts and replay semantics.
NATIVE_OBSERVATION_PROFILE = "funds-trace-table-v57"
NATIVE_OBSERVATION_XLS_PROFILE = "funds-trace-table-v58"
NATIVE_OBSERVATION_EVIDENCE_PROFILE = "funds-trace-table-v59"
NATIVE_OBSERVATION_ARCHIVE_PROFILE = "funds-trace-table-archive-v48"
NATIVE_OBSERVATION_EVIDENCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v49"
NATIVE_OBSERVATION_XLS_ARCHIVE_PROFILE = "funds-trace-table-archive-v50"
UNORIENTED_PROFILE = "funds-trace-table-v60"
UNORIENTED_XLS_PROFILE = "funds-trace-table-v61"
UNORIENTED_EVIDENCE_PROFILE = "funds-trace-table-v62"
UNORIENTED_ARCHIVE_PROFILE = "funds-trace-table-archive-v51"
UNORIENTED_EVIDENCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v52"
UNORIENTED_XLS_ARCHIVE_PROFILE = "funds-trace-table-archive-v53"
# Native31 adds unoriented observations and member-local proved role bindings.
# v60-v62 / archive-v51-v53 keep their original native28-only behavior.
NATIVE31_PROFILE = "funds-trace-table-v63"
NATIVE31_XLS_PROFILE = "funds-trace-table-v64"
NATIVE31_EVIDENCE_PROFILE = "funds-trace-table-v65"
NATIVE31_ARCHIVE_PROFILE = "funds-trace-table-archive-v54"
NATIVE31_EVIDENCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v55"
NATIVE31_XLS_ARCHIVE_PROFILE = "funds-trace-table-archive-v56"
SOURCE_OBSERVATION_PROFILE = "funds-trace-table-v66"
SOURCE_OBSERVATION_XLS_PROFILE = "funds-trace-table-v67"
SOURCE_OBSERVATION_EVIDENCE_PROFILE = "funds-trace-table-v68"
SOURCE_OBSERVATION_ARCHIVE_PROFILE = "funds-trace-table-archive-v57"
SOURCE_OBSERVATION_EVIDENCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v58"
SOURCE_OBSERVATION_XLS_ARCHIVE_PROFILE = "funds-trace-table-archive-v59"
# Archive-only successors preserve malformed registered auxiliary rows without
# changing the financial interpretations or any frozen predecessor session.
AUXILIARY_ROWS_ARCHIVE_PROFILE = "funds-trace-table-archive-v60"
AUXILIARY_ROWS_EVIDENCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v61"
AUXILIARY_ROWS_XLS_ARCHIVE_PROFILE = "funds-trace-table-archive-v62"
PREVIOUS_BANK_SOURCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v63"
PREVIOUS_BANK_SOURCE_EVIDENCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v64"
PREVIOUS_BANK_SOURCE_XLS_ARCHIVE_PROFILE = "funds-trace-table-archive-v65"
BANK_SOURCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v66"
BANK_SOURCE_EVIDENCE_ARCHIVE_PROFILE = "funds-trace-table-archive-v67"
BANK_SOURCE_XLS_ARCHIVE_PROFILE = "funds-trace-table-archive-v68"
TRIAL_MEMBER_ARCHIVE_PROFILE = "funds-trace-table-archive-v69"
TRIAL_MEMBER_PROFILE_FIELD = "trial_member_parser_version"
TRANSACTION_STATUS_POLICY = source_transaction_status.POLICY
BANK_SOURCE_CSV_ARCHIVE_PROFILES = frozenset((BANK_SOURCE_ARCHIVE_PROFILE, PREVIOUS_BANK_SOURCE_ARCHIVE_PROFILE))
BANK_SOURCE_ARCHIVE_PROFILES = frozenset((
    *BANK_SOURCE_CSV_ARCHIVE_PROFILES, TRIAL_MEMBER_ARCHIVE_PROFILE, BANK_SOURCE_EVIDENCE_ARCHIVE_PROFILE, BANK_SOURCE_XLS_ARCHIVE_PROFILE,
    PREVIOUS_BANK_SOURCE_EVIDENCE_ARCHIVE_PROFILE, PREVIOUS_BANK_SOURCE_XLS_ARCHIVE_PROFILE))
AUXILIARY_ROWS_ARCHIVE_PROFILES = frozenset((AUXILIARY_ROWS_ARCHIVE_PROFILE,
    AUXILIARY_ROWS_EVIDENCE_ARCHIVE_PROFILE, AUXILIARY_ROWS_XLS_ARCHIVE_PROFILE, *BANK_SOURCE_ARCHIVE_PROFILES))
# New standalone reference interpretations must not change pinned old sessions
# or archive-member rules. These profiles inherit the existing source contracts.
VOUCHER_REFERENCE_PROFILE = "funds-trace-table-v69"
CONFIRMED_LAYOUT_POLICY = "physical-table-layout-v1"
CONFIRMED_LAYOUT_EVIDENCE_POLICY = "physical-table-layout-v2"
CONFIRMED_LAYOUT_FIELD = "confirmed_physical_layout"
CONFIRMED_NOTE_FIELD = "confirmed_field_note_scope"
WRONG_FIELD_NOTE_ROW = "field_note_wrong_header_row"
MAX_LAYOUT_XLSX_BYTES = 16 * 1024 * 1024
VOUCHER_REFERENCE_XLS_PROFILE = "funds-trace-table-v70"
VOUCHER_REFERENCE_EVIDENCE_PROFILE = "funds-trace-table-v71"
CLOSED_BANK_PROFILE = "funds-trace-table-v72"
CLOSED_BANK_XLS_PROFILE = "funds-trace-table-v73"
CLOSED_BANK_EVIDENCE_PROFILE = "funds-trace-table-v74"
PREVIOUS_BANK_SOURCE_PROFILE = "funds-trace-table-v75"
PREVIOUS_BANK_SOURCE_XLS_PROFILE = "funds-trace-table-v76"
PREVIOUS_BANK_SOURCE_EVIDENCE_PROFILE = "funds-trace-table-v77"
BANK_SOURCE_PROFILE = "funds-trace-table-v78"
BANK_SOURCE_XLS_PROFILE = "funds-trace-table-v79"
BANK_SOURCE_EVIDENCE_PROFILE = "funds-trace-table-v80"
BANK_FEEDBACK39_TRIAL_PROFILE = "funds-trace-table-v95"
BANK_SPLIT_LEDGER_TRIAL_PROFILE = "funds-trace-table-v96"
BANK_ACCOUNT_INQUIRY_TRIAL_PROFILE = "funds-trace-table-v97"
CODED_BANK_CSV_TRIAL_PROFILE = "funds-trace-table-v98"
BANK_TEMPLATE_TRIAL_PROFILE = "funds-trace-table-v99"
BANK_COMPONENT_TRIAL_PROFILE = "funds-trace-table-v101"
TRADE_CLOCK_PRECISION_TRIAL_PROFILE = "funds-trace-table-v102"
CSV_TAIL_SOURCE_PROFILE = "funds-trace-table-v103"
CSV_TAIL_REFERENCE_PROFILE = "funds-trace-table-v106"
CSV_TAIL_METADATA_PROFILE = "funds-trace-table-v107"
CSV_TAIL_COMPLETE_ROWS_PROFILE = "funds-trace-table-v108"
CSV_TAIL_COUNTERPARTY_PROFILE = "funds-trace-table-v109"
CSV_TAIL_COMPLETE_ROW_PROFILES = frozenset({CSV_TAIL_COMPLETE_ROWS_PROFILE, CSV_TAIL_COUNTERPARTY_PROFILE})
CSV_TAIL_ORDINARY_PROFILES = frozenset({CSV_TAIL_METADATA_PROFILE, *CSV_TAIL_COMPLETE_ROW_PROFILES})
CSV_TAIL_FINANCIAL_PROFILES = CSV_TAIL_ORDINARY_PROFILES | {CSV_TAIL_REFERENCE_PROFILE}
VERIFIED_REFERENCE_PROFILES = frozenset({VOUCHER_REFERENCE_PROFILE, CSV_TAIL_REFERENCE_PROFILE})
_OPTIONAL_REFERENCE_TAIL = frozenset({"", "日志号", "报告机构", "任务号", "任务流水号"})
_COUNTERPARTY_TAIL_FIELDS = frozenset({"counter_account", "counter_name", "counter_bank"})
DEPOSIT_DIRECTION_TRIAL_PROFILE = "funds-trace-table-v104"
DEPOSIT_CONFLICT_TRIAL_PROFILE = "funds-trace-table-v105"
DEPOSIT_CSV_TRIAL_PROFILES = frozenset({DEPOSIT_DIRECTION_TRIAL_PROFILE, DEPOSIT_CONFLICT_TRIAL_PROFILE})
DEPOSIT_DIRECTION_POLICY = "registered-csv-deposit-v1"
DEPOSIT_DIRECTION_HEADINGS = frozenset({"交易方向", "收付方向", "收支方向"})
_DEPOSIT_OWN_HEADINGS = frozenset({"交易账号", "交易帐号", "本方账号", "本方帐号", "本人账号", "本人帐号"})
_DEPOSIT_AMOUNT_HEADINGS = frozenset({"交易金额", "发生额", "交易发生额"})
_WITHDRAWAL_CREDIT_DECLARATION = r"取款(?:表示|为|指|计为|记为|按|代表|[=:])(?:本方)?(?:收入|入账|贷方|收款)"
_DEPOSIT_SIDE_CONFLICT = re.compile(r"存款(?:表示|为|指|计为|记为|按|代表|[=:])(?:本方)?(?:支出|出账|借方|付款)|"
                                   + _WITHDRAWAL_CREDIT_DECLARATION)
_WITHDRAWAL_CONFLICT = re.compile(_WITHDRAWAL_CREDIT_DECLARATION)
_DIRECTION_TABLE_SCOPE = ("本表", "本区", "本段")
_DEPOSIT_QUOTING = str.maketrans("", "", "\"'“”‘’「」『』")
_DEPOSIT_DIRECTION_TERMS = ("方向", "收付", "收支", "借贷", "存取", "存款", "取款")
_DEPOSIT_DESCRIPTION_TERMS = ("说明", "定义", "释义", "含义", "约定", "规则", "码表", "对应", "口径")
MAX_DEPOSIT_DIRECTION_ROWS = 256
MAX_DEPOSIT_DIRECTION_BYTES = 4 * 1024 * 1024
CORROBORATED_TRADE_CLOCK_FIELD = "source_trade_time_corroborated"
TIERED_PROFILES = frozenset({table_tiered_headers.PROFILE, table_tiered_headers.GROUPED_PROFILE})
BANK_SPLIT_LEDGER_PROFILES = frozenset((BANK_SPLIT_LEDGER_TRIAL_PROFILE, BANK_ACCOUNT_INQUIRY_TRIAL_PROFILE))
TRIAL_XLSX_PROFILES = frozenset((BANK_FEEDBACK39_TRIAL_PROFILE, table_tiered_headers.GROUPED_PROFILE, *BANK_SPLIT_LEDGER_PROFILES))
TRIAL_CSV_PROFILES = frozenset((CODED_BANK_CSV_TRIAL_PROFILE, BANK_TEMPLATE_TRIAL_PROFILE,
                                BANK_COMPONENT_TRIAL_PROFILE, TRADE_CLOCK_PRECISION_TRIAL_PROFILE))
TRIAL_TABLE_PROFILES = TRIAL_CSV_PROFILES | TRIAL_XLSX_PROFILES
CONFIRMED_LAYOUT_PROFILES = frozenset((VOUCHER_REFERENCE_PROFILE, *TRIAL_TABLE_PROFILES))
CLOSED_BANK_SUCCESSORS = {
    VOUCHER_REFERENCE_PROFILE: BANK_SOURCE_PROFILE,
    VOUCHER_REFERENCE_XLS_PROFILE: BANK_SOURCE_XLS_PROFILE,
    VOUCHER_REFERENCE_EVIDENCE_PROFILE: BANK_SOURCE_EVIDENCE_PROFILE,
}
CLOSED_BANK_PROFILES = frozenset((CLOSED_BANK_PROFILE, CLOSED_BANK_XLS_PROFILE, CLOSED_BANK_EVIDENCE_PROFILE,
                                  PREVIOUS_BANK_SOURCE_PROFILE, PREVIOUS_BANK_SOURCE_XLS_PROFILE,
                                  PREVIOUS_BANK_SOURCE_EVIDENCE_PROFILE, *CLOSED_BANK_SUCCESSORS.values()))
BANK_SOURCE_CURRENCY_PROFILES = frozenset((
    BANK_SOURCE_ARCHIVE_PROFILE, TRIAL_MEMBER_ARCHIVE_PROFILE, BANK_SOURCE_EVIDENCE_ARCHIVE_PROFILE, BANK_SOURCE_XLS_ARCHIVE_PROFILE,
    *CLOSED_BANK_SUCCESSORS.values()))
BANK_SOURCE_PROFILES = BANK_SOURCE_ARCHIVE_PROFILES | frozenset((
    PREVIOUS_BANK_SOURCE_PROFILE, PREVIOUS_BANK_SOURCE_XLS_PROFILE,
    PREVIOUS_BANK_SOURCE_EVIDENCE_PROFILE, *CLOSED_BANK_SUCCESSORS.values()))
VOUCHER_REFERENCE_PROFILES = frozenset((*CSV_TAIL_FINANCIAL_PROFILES, *CONFIRMED_LAYOUT_PROFILES, *DEPOSIT_CSV_TRIAL_PROFILES, VOUCHER_REFERENCE_XLS_PROFILE,
    VOUCHER_REFERENCE_EVIDENCE_PROFILE, table_direction_recheck.PROFILE, table_tiered_headers.PROFILE, *CLOSED_BANK_PROFILES))
SOURCE_OBSERVATION_PROFILES = frozenset((*AUXILIARY_ROWS_ARCHIVE_PROFILES, *VOUCHER_REFERENCE_PROFILES, SOURCE_OBSERVATION_PROFILE, SOURCE_OBSERVATION_XLS_PROFILE,
    SOURCE_OBSERVATION_EVIDENCE_PROFILE, SOURCE_OBSERVATION_ARCHIVE_PROFILE,
    SOURCE_OBSERVATION_EVIDENCE_ARCHIVE_PROFILE, SOURCE_OBSERVATION_XLS_ARCHIVE_PROFILE))
NATIVE31_PROFILES = frozenset((*SOURCE_OBSERVATION_PROFILES, NATIVE31_PROFILE, NATIVE31_XLS_PROFILE, NATIVE31_EVIDENCE_PROFILE,
    NATIVE31_ARCHIVE_PROFILE, NATIVE31_EVIDENCE_ARCHIVE_PROFILE, NATIVE31_XLS_ARCHIVE_PROFILE))
UNORIENTED_PROFILES = frozenset((*NATIVE31_PROFILES, UNORIENTED_PROFILE, UNORIENTED_XLS_PROFILE, UNORIENTED_EVIDENCE_PROFILE,
    UNORIENTED_ARCHIVE_PROFILE, UNORIENTED_EVIDENCE_ARCHIVE_PROFILE, UNORIENTED_XLS_ARCHIVE_PROFILE))
NATIVE_OBSERVATION_PROFILES = frozenset((*UNORIENTED_PROFILES, NATIVE_OBSERVATION_PROFILE, NATIVE_OBSERVATION_XLS_PROFILE,
    NATIVE_OBSERVATION_EVIDENCE_PROFILE, NATIVE_OBSERVATION_ARCHIVE_PROFILE,
    NATIVE_OBSERVATION_EVIDENCE_ARCHIVE_PROFILE, NATIVE_OBSERVATION_XLS_ARCHIVE_PROFILE))
AUXILIARY_SCOPE_PROFILES = frozenset((*NATIVE_OBSERVATION_PROFILES, AUXILIARY_SCOPE_PROFILE, AUXILIARY_SCOPE_XLS_PROFILE,
    AUXILIARY_SCOPE_EVIDENCE_PROFILE, AUXILIARY_SCOPE_ARCHIVE_PROFILE,
    AUXILIARY_SCOPE_EVIDENCE_ARCHIVE_PROFILE, AUXILIARY_SCOPE_XLS_ARCHIVE_PROFILE))
POSTING_PAIR_PROFILES = frozenset((*AUXILIARY_SCOPE_PROFILES, POSTING_PAIR_PROFILE, POSTING_PAIR_XLS_PROFILE, POSTING_PAIR_EVIDENCE_PROFILE,
    POSTING_PAIR_ARCHIVE_PROFILE, POSTING_PAIR_EVIDENCE_ARCHIVE_PROFILE, POSTING_PAIR_XLS_ARCHIVE_PROFILE))
SOURCE_LEDGER_PROFILES = frozenset((*POSTING_PAIR_PROFILES, SOURCE_LEDGER_PROFILE, SOURCE_LEDGER_XLS_PROFILE, SOURCE_LEDGER_EVIDENCE_PROFILE,
    SOURCE_LEDGER_ARCHIVE_PROFILE, SOURCE_LEDGER_EVIDENCE_ARCHIVE_PROFILE, SOURCE_LEDGER_XLS_ARCHIVE_PROFILE))
BANK31_VARIANT_PROFILES = frozenset((*SOURCE_LEDGER_PROFILES, BANK31_VARIANT_PROFILE, BANK31_VARIANT_XLS_PROFILE, BANK31_VARIANT_EVIDENCE_PROFILE,
    BANK31_VARIANT_ARCHIVE_PROFILE, BANK31_VARIANT_EVIDENCE_ARCHIVE_PROFILE, BANK31_VARIANT_XLS_ARCHIVE_PROFILE))
SOURCE_TEXT_PROFILES = frozenset((*BANK31_VARIANT_PROFILES, SOURCE_TEXT_PROFILE, SOURCE_TEXT_XLS_PROFILE, SOURCE_TEXT_EVIDENCE_PROFILE,
    SOURCE_TEXT_ARCHIVE_PROFILE, SOURCE_TEXT_EVIDENCE_ARCHIVE_PROFILE, SOURCE_TEXT_XLS_ARCHIVE_PROFILE))
SOURCE_CONTRACT_PROFILES = frozenset((*SOURCE_TEXT_PROFILES, SOURCE_CONTRACT_PROFILE, SOURCE_CONTRACT_XLS_PROFILE, SOURCE_CONTRACT_EVIDENCE_PROFILE,
    SOURCE_CONTRACT_ARCHIVE_PROFILE, SOURCE_CONTRACT_EVIDENCE_ARCHIVE_PROFILE, SOURCE_CONTRACT_XLS_ARCHIVE_PROFILE))
ZONED_PROFILES = frozenset((*SOURCE_CONTRACT_PROFILES, ZONED_PROFILE, ZONED_XLS_PROFILE, ZONED_EVIDENCE_PROFILE,
    ZONED_ARCHIVE_PROFILE, ZONED_EVIDENCE_ARCHIVE_PROFILE, ZONED_XLS_ARCHIVE_PROFILE, SOURCE_CONTRACT_EVIDENCE_ARCHIVE_PROFILE, SOURCE_CONTRACT_XLS_ARCHIVE_PROFILE))
TIME_ROLE_PROFILES = frozenset((*ZONED_PROFILES, TIME_ROLE_PROFILE, TIME_ROLE_XLS_PROFILE, TIME_ROLE_EVIDENCE_PROFILE,
    TIME_ROLE_ARCHIVE_PROFILE, TIME_ROLE_EVIDENCE_ARCHIVE_PROFILE, TIME_ROLE_XLS_ARCHIVE_PROFILE, ZONED_EVIDENCE_ARCHIVE_PROFILE, ZONED_XLS_ARCHIVE_PROFILE, SOURCE_CONTRACT_EVIDENCE_ARCHIVE_PROFILE, SOURCE_CONTRACT_XLS_ARCHIVE_PROFILE))
OPTIONAL_REFERENCE_PROFILES = frozenset((OPTIONAL_REFERENCE_PROFILE, OPTIONAL_REFERENCE_XLS_PROFILE,
    OPTIONAL_REFERENCE_EVIDENCE_PROFILE, OPTIONAL_REFERENCE_ARCHIVE_PROFILE,
    OPTIONAL_REFERENCE_EVIDENCE_ARCHIVE_PROFILE, OPTIONAL_REFERENCE_XLS_ARCHIVE_PROFILE, *TIME_ROLE_PROFILES))
REFERENCE_ROLE_PROFILES = frozenset((REFERENCE_ROLE_PROFILE, REFERENCE_ROLE_XLS_PROFILE,
    REFERENCE_ROLE_EVIDENCE_PROFILE, REFERENCE_ROLE_ARCHIVE_PROFILE,
    REFERENCE_ROLE_EVIDENCE_ARCHIVE_PROFILE, REFERENCE_ROLE_XLS_ARCHIVE_PROFILE, *OPTIONAL_REFERENCE_PROFILES))
PRECISE_PROFILES = frozenset((PRECISE_PROFILE, PRECISE_XLS_PROFILE, PRECISE_EVIDENCE_PROFILE,
                              PRECISE_ARCHIVE_PROFILE, PRECISE_EVIDENCE_ARCHIVE_PROFILE,
                              PRECISE_XLS_ARCHIVE_PROFILE, *REFERENCE_ROLE_PROFILES))
DECLARATION_GUARD_PROFILES = frozenset((GUARDED_PROFILE, GUARDED_XLS_PROFILE, GUARDED_EVIDENCE_PROFILE,
                                      GUARDED_ARCHIVE_PROFILE, GUARDED_EVIDENCE_ARCHIVE_PROFILE,
                                      GUARDED_XLS_ARCHIVE_PROFILE, *PRECISE_PROFILES))
HEADER_WIDTH_PROFILES = frozenset((TRIAL_MEMBER_ARCHIVE_PROFILE, BANK_SOURCE_EVIDENCE_ARCHIVE_PROFILE, BANK_SOURCE_XLS_ARCHIVE_PROFILE, PREVIOUS_BANK_SOURCE_EVIDENCE_ARCHIVE_PROFILE, PREVIOUS_BANK_SOURCE_XLS_ARCHIVE_PROFILE, AUXILIARY_ROWS_EVIDENCE_ARCHIVE_PROFILE, AUXILIARY_ROWS_XLS_ARCHIVE_PROFILE, SOURCE_OBSERVATION_EVIDENCE_ARCHIVE_PROFILE, SOURCE_OBSERVATION_XLS_ARCHIVE_PROFILE, NATIVE31_XLS_ARCHIVE_PROFILE, NATIVE31_EVIDENCE_ARCHIVE_PROFILE, UNORIENTED_EVIDENCE_ARCHIVE_PROFILE, UNORIENTED_XLS_ARCHIVE_PROFILE, NATIVE_OBSERVATION_EVIDENCE_ARCHIVE_PROFILE, NATIVE_OBSERVATION_XLS_ARCHIVE_PROFILE, AUXILIARY_SCOPE_EVIDENCE_ARCHIVE_PROFILE, AUXILIARY_SCOPE_XLS_ARCHIVE_PROFILE, POSTING_PAIR_EVIDENCE_ARCHIVE_PROFILE, POSTING_PAIR_XLS_ARCHIVE_PROFILE, SOURCE_LEDGER_EVIDENCE_ARCHIVE_PROFILE, SOURCE_LEDGER_XLS_ARCHIVE_PROFILE, BANK31_VARIANT_EVIDENCE_ARCHIVE_PROFILE, BANK31_VARIANT_XLS_ARCHIVE_PROFILE, SOURCE_TEXT_EVIDENCE_ARCHIVE_PROFILE, SOURCE_TEXT_XLS_ARCHIVE_PROFILE, HEADER_WIDTH_ARCHIVE_PROFILE, HEADER_WIDTH_XLS_ARCHIVE_PROFILE,
                                 GUARDED_EVIDENCE_ARCHIVE_PROFILE, GUARDED_XLS_ARCHIVE_PROFILE,
                                 PRECISE_EVIDENCE_ARCHIVE_PROFILE, PRECISE_XLS_ARCHIVE_PROFILE, REFERENCE_ROLE_EVIDENCE_ARCHIVE_PROFILE, REFERENCE_ROLE_XLS_ARCHIVE_PROFILE, OPTIONAL_REFERENCE_EVIDENCE_ARCHIVE_PROFILE, OPTIONAL_REFERENCE_XLS_ARCHIVE_PROFILE, TIME_ROLE_EVIDENCE_ARCHIVE_PROFILE, TIME_ROLE_XLS_ARCHIVE_PROFILE, ZONED_EVIDENCE_ARCHIVE_PROFILE, ZONED_XLS_ARCHIVE_PROFILE, SOURCE_CONTRACT_EVIDENCE_ARCHIVE_PROFILE, SOURCE_CONTRACT_XLS_ARCHIVE_PROFILE))
MICROSECOND_ARCHIVE_PROFILES = frozenset((MICROSECOND_ARCHIVE_PROFILE, MICROSECOND_XLS_ARCHIVE_PROFILE,
                                         *HEADER_WIDTH_PROFILES))
XLS_EVIDENCE_ARCHIVE_PROFILES = frozenset((BANK_SOURCE_XLS_ARCHIVE_PROFILE, PREVIOUS_BANK_SOURCE_XLS_ARCHIVE_PROFILE, AUXILIARY_ROWS_XLS_ARCHIVE_PROFILE, SOURCE_OBSERVATION_XLS_ARCHIVE_PROFILE, NATIVE31_XLS_ARCHIVE_PROFILE, UNORIENTED_XLS_ARCHIVE_PROFILE, NATIVE_OBSERVATION_XLS_ARCHIVE_PROFILE, AUXILIARY_SCOPE_XLS_ARCHIVE_PROFILE, POSTING_PAIR_XLS_ARCHIVE_PROFILE, SOURCE_LEDGER_XLS_ARCHIVE_PROFILE, BANK31_VARIANT_XLS_ARCHIVE_PROFILE, SOURCE_TEXT_XLS_ARCHIVE_PROFILE, SOURCE_CONTRACT_XLS_ARCHIVE_PROFILE, XLS_EVIDENCE_ARCHIVE_PROFILE, MICROSECOND_XLS_ARCHIVE_PROFILE,
                                         HEADER_WIDTH_XLS_ARCHIVE_PROFILE, GUARDED_XLS_ARCHIVE_PROFILE, PRECISE_XLS_ARCHIVE_PROFILE, REFERENCE_ROLE_XLS_ARCHIVE_PROFILE, OPTIONAL_REFERENCE_XLS_ARCHIVE_PROFILE, TIME_ROLE_XLS_ARCHIVE_PROFILE, ZONED_XLS_ARCHIVE_PROFILE))
STANDALONE_XLS_PROFILES = frozenset((BANK_SOURCE_XLS_PROFILE, PREVIOUS_BANK_SOURCE_XLS_PROFILE, CLOSED_BANK_XLS_PROFILE, VOUCHER_REFERENCE_XLS_PROFILE, SOURCE_OBSERVATION_XLS_PROFILE, NATIVE31_XLS_PROFILE, UNORIENTED_XLS_PROFILE, NATIVE_OBSERVATION_XLS_PROFILE, AUXILIARY_SCOPE_XLS_PROFILE, POSTING_PAIR_XLS_PROFILE, SOURCE_LEDGER_XLS_PROFILE, BANK31_VARIANT_XLS_PROFILE, SOURCE_TEXT_XLS_PROFILE, SOURCE_CONTRACT_XLS_PROFILE, XLS_PROFILE, GUARDED_XLS_PROFILE, PRECISE_XLS_PROFILE, REFERENCE_ROLE_XLS_PROFILE, OPTIONAL_REFERENCE_XLS_PROFILE, TIME_ROLE_XLS_PROFILE, ZONED_XLS_PROFILE))
XLS_READER_PROFILES = frozenset((*STANDALONE_XLS_PROFILES, *XLS_EVIDENCE_ARCHIVE_PROFILES))
EVENT_PROFILES = frozenset((BANK_SOURCE_EVIDENCE_PROFILE, PREVIOUS_BANK_SOURCE_EVIDENCE_PROFILE, CLOSED_BANK_EVIDENCE_PROFILE, VOUCHER_REFERENCE_EVIDENCE_PROFILE, SOURCE_OBSERVATION_EVIDENCE_PROFILE, NATIVE31_EVIDENCE_PROFILE, UNORIENTED_EVIDENCE_PROFILE, NATIVE_OBSERVATION_EVIDENCE_PROFILE, AUXILIARY_SCOPE_EVIDENCE_PROFILE, POSTING_PAIR_EVIDENCE_PROFILE, SOURCE_LEDGER_EVIDENCE_PROFILE, BANK31_VARIANT_EVIDENCE_PROFILE, SOURCE_TEXT_EVIDENCE_PROFILE, SOURCE_CONTRACT_EVIDENCE_PROFILE, ZONED_EVIDENCE_PROFILE, TIME_ROLE_EVIDENCE_PROFILE, OPTIONAL_REFERENCE_EVIDENCE_PROFILE, REFERENCE_ROLE_EVIDENCE_PROFILE, EVIDENCE_PROFILE, GUARDED_EVIDENCE_PROFILE, PRECISE_EVIDENCE_PROFILE, EVIDENCE_ARCHIVE_PROFILE, XLS_EVIDENCE_ARCHIVE_PROFILE, *MICROSECOND_ARCHIVE_PROFILES))
EVIDENCE_ARCHIVE_PROFILES = frozenset((LEGACY_EVIDENCE_ARCHIVE_PROFILE, EVIDENCE_ARCHIVE_PROFILE, XLS_EVIDENCE_ARCHIVE_PROFILE, *MICROSECOND_ARCHIVE_PROFILES))
EVIDENCE_PROFILES = frozenset((BANK_SOURCE_EVIDENCE_PROFILE, PREVIOUS_BANK_SOURCE_EVIDENCE_PROFILE, CLOSED_BANK_EVIDENCE_PROFILE, VOUCHER_REFERENCE_EVIDENCE_PROFILE, SOURCE_OBSERVATION_EVIDENCE_PROFILE, NATIVE31_EVIDENCE_PROFILE, UNORIENTED_EVIDENCE_PROFILE, NATIVE_OBSERVATION_EVIDENCE_PROFILE, AUXILIARY_SCOPE_EVIDENCE_PROFILE, POSTING_PAIR_EVIDENCE_PROFILE, SOURCE_LEDGER_EVIDENCE_PROFILE, BANK31_VARIANT_EVIDENCE_PROFILE, SOURCE_TEXT_EVIDENCE_PROFILE, SOURCE_CONTRACT_EVIDENCE_PROFILE, ZONED_EVIDENCE_PROFILE, TIME_ROLE_EVIDENCE_PROFILE, OPTIONAL_REFERENCE_EVIDENCE_PROFILE, REFERENCE_ROLE_EVIDENCE_PROFILE, LEGACY_EVIDENCE_PROFILE, *EVIDENCE_ARCHIVE_PROFILES, EVIDENCE_PROFILE, GUARDED_EVIDENCE_PROFILE, PRECISE_EVIDENCE_PROFILE))
PROFILE = VOUCHER_REFERENCE_PROFILE
CURRENCY_REGION_PROFILES = frozenset((*CSV_TAIL_FINANCIAL_PROFILES, table_tiered_headers.PROFILE, *BANK_SOURCE_CSV_ARCHIVE_PROFILES, AUXILIARY_ROWS_ARCHIVE_PROFILE, BANK_SOURCE_PROFILE, PREVIOUS_BANK_SOURCE_PROFILE, CLOSED_BANK_PROFILE, *CONFIRMED_LAYOUT_PROFILES, *DEPOSIT_CSV_TRIAL_PROFILES, table_direction_recheck.PROFILE, SOURCE_OBSERVATION_PROFILE, SOURCE_OBSERVATION_ARCHIVE_PROFILE, NATIVE31_PROFILE, NATIVE31_ARCHIVE_PROFILE, UNORIENTED_PROFILE, UNORIENTED_ARCHIVE_PROFILE, NATIVE_OBSERVATION_PROFILE, NATIVE_OBSERVATION_ARCHIVE_PROFILE, AUXILIARY_SCOPE_PROFILE, AUXILIARY_SCOPE_ARCHIVE_PROFILE, POSTING_PAIR_PROFILE, POSTING_PAIR_ARCHIVE_PROFILE, SOURCE_LEDGER_PROFILE, SOURCE_LEDGER_ARCHIVE_PROFILE, BANK31_VARIANT_PROFILE, BANK31_VARIANT_ARCHIVE_PROFILE, SOURCE_TEXT_PROFILE, SOURCE_TEXT_ARCHIVE_PROFILE, CURRENCY_REGION_PROFILE, *STANDALONE_XLS_PROFILES, GUARDED_PROFILE, PRECISE_PROFILE, REFERENCE_ROLE_PROFILE, OPTIONAL_REFERENCE_PROFILE, TIME_ROLE_PROFILE, TIME_ROLE_ARCHIVE_PROFILE, ZONED_PROFILE, ZONED_ARCHIVE_PROFILE, SOURCE_CONTRACT_PROFILE, SOURCE_CONTRACT_ARCHIVE_PROFILE,
                                     GUARDED_ARCHIVE_PROFILE, PRECISE_ARCHIVE_PROFILE, REFERENCE_ROLE_ARCHIVE_PROFILE, OPTIONAL_REFERENCE_ARCHIVE_PROFILE, *EVIDENCE_PROFILES))
CURRENCY_CODE_ALIAS_PROFILES = CURRENCY_REGION_PROFILES | {ARCHIVE_MEMBER_PROFILE}
SOURCE_CURRENCY_HEADINGS = frozenset({"交易货币", "货币码", "币种码", "币别码", "币种代码"})
OVERLAPPING_HEADER_PROFILES = CURRENCY_REGION_PROFILES | {ARCHIVE_MEMBER_PROFILE}
REORDERED_TIME_PROFILES = frozenset((REORDERED_TIME_PROFILE, *CURRENCY_REGION_PROFILES))
REGION_HEADER_PROFILES = frozenset((REGION_HEADER_PROFILE, *REORDERED_TIME_PROFILES))
QUERY_OWNER_PROFILES = frozenset((QUERY_OWNER_PROFILE, *REGION_HEADER_PROFILES))
BANK_NAME_PROFILES = frozenset((BANK_NAME_PROFILE, *QUERY_OWNER_PROFILES))
SOURCE_ROLE_PROFILES = frozenset((SOURCE_ROLE_PROFILE, *BANK_NAME_PROFILES))
UNIT_DECLARATION_PROFILES = frozenset((UNIT_DECLARATION_PROFILE, *SOURCE_ROLE_PROFILES))
TIME_FORMAT_PROFILES = frozenset((TIME_FORMAT_PROFILE, *UNIT_DECLARATION_PROFILES))
DIRECTION_PROFILES = frozenset((DIRECTION_PROFILE, *TIME_FORMAT_PROFILES))
CONTRACT_PROFILES = frozenset((CONTRACT_PROFILE, *DIRECTION_PROFILES))
FINANCIAL_PROFILES = frozenset((FINANCIAL_PROFILE, *CONTRACT_PROFILES))
ROLE_PROFILES = frozenset((ROLE_PROFILE, *FINANCIAL_PROFILES))
SUPPORTED_PROFILES = frozenset((IDENTITY_PROFILE, CSV_TAIL_SOURCE_PROFILE, *(ROLE_PROFILES - EVIDENCE_ARCHIVE_PROFILES - {GUARDED_ARCHIVE_PROFILE, PRECISE_ARCHIVE_PROFILE, REFERENCE_ROLE_ARCHIVE_PROFILE, OPTIONAL_REFERENCE_ARCHIVE_PROFILE, TIME_ROLE_ARCHIVE_PROFILE, ZONED_ARCHIVE_PROFILE, SOURCE_CONTRACT_ARCHIVE_PROFILE, SOURCE_TEXT_ARCHIVE_PROFILE, BANK31_VARIANT_ARCHIVE_PROFILE, SOURCE_LEDGER_ARCHIVE_PROFILE, POSTING_PAIR_ARCHIVE_PROFILE, AUXILIARY_SCOPE_ARCHIVE_PROFILE, NATIVE_OBSERVATION_ARCHIVE_PROFILE, UNORIENTED_ARCHIVE_PROFILE, NATIVE31_ARCHIVE_PROFILE, SOURCE_OBSERVATION_ARCHIVE_PROFILE, AUXILIARY_ROWS_ARCHIVE_PROFILE, *BANK_SOURCE_CSV_ARCHIVE_PROFILES})))
ROLE_CONFLICT_BASIS = "显式身份角色或候选字段存在矛盾"
HISTORY_CONFLICT_BASIS = "同一物理来源已有不同资金解释，保留历史事实，防止重复累计"
ARCHIVE_HISTORY_CONFLICT_BASIS = "归档v4参与的同一物理来源已有不同资金解释，保留历史事实，防止重复累计"
FINANCIAL_CONFLICT_BASIS = "显式资金字段或单位声明未通过全量校验"
FINANCIAL_VALIDATION_ISSUES = frozenset((
    "missing_time", "invalid_time", "conflicting_time", "missing_amount", "invalid_amount",
    "amount_precision", "amount_overflow", "conflicting_amount", "unknown_unit", "conflicting_unit",
    "invalid_direction", "conflicting_direction", "conflicting_income_expense", "invalid_balance",
    "currency_unit_conflict", "invalid_currency", "unknown_status", "formula_value",
    "balance_amount_conflict", "balance_direction_conflict", "invalid_cell_type",
))
ROW_KIND = "table_record"
TRANSACTION_REFERENCE_POLICY = "transaction-reference-v1"
VERIFIED_REFERENCE_POLICY = "verified-reference-columns-v1"
VERIFIED_REFERENCE_DETAIL = "source_reference_policy"
VERIFIED_REFERENCE_OCCURRENCE = "source_reference_occurrence"
VERIFIED_REFERENCE_OCCURRENCE_FORMAT = re.compile(r"([1-9]|[1-5][0-9]|6[0-4]):([1-9][0-9]{0,5}):([a-f0-9]{64}):([a-f0-9]{64})")
BANK_FEEDBACK_32_COLUMNS = frozenset("交易卡号 交易账号 查询反馈结果原因 交易户名 交易证件号码 交易时间 交易金额 交易余额 收付标志 交易对手账卡号 现金标志 对手户名 对手身份证号 对手开户银行 摘要说明 交易币种 交易网点名称 交易发生地 交易是否成功 传票号 IP地址 MAC地址 对手交易余额 交易流水号 日志号 凭证种类 凭证号 交易柜员号 备注 商户名称 商户代码 交易类型".split())
BANK_FEEDBACK_31_COLUMNS = frozenset("交易卡号 交易账号 交易方户名 交易方证件号码 交易时间 交易金额 交易余额 收付标志 交易对手账卡号 现金标志 对手户名 对手身份证号 对手开户银行 摘要说明 交易币种 交易网点名称 交易发生地 交易是否成功 传票号 IP地址 MAC地址 对手交易余额 交易流水号 日志号 凭证种类 凭证号 交易柜员号 备注 商户名称 交易类型 查询反馈结果原因".split())
FEEDBACK_31_COLUMNS = BANK_FEEDBACK_32_COLUMNS - {"查询反馈结果原因"}
# A 39-column case-investigation export used by several banks adds the account
# holder, a signed net amount and query-state columns to the 32-column feedback
# family. Only the complete label set may claim reference roles: a near-miss
# layout must stay on the conservative conflicting-field path.
BANK_FEEDBACK_39_COLUMNS = frozenset(
    "交易卡号 交易账号 主户名 开户人证件号码 交易日期 交易时间 交易净额 交易金额 交易余额 "
    "收付标志 交易对手账卡号 现金标志 对手户名 是否调取 已调取账号对手身份 对手身份证号 "
    "对手开户银行 摘要说明 交易币种 交易网点名称 交易发生地 交易是否成功 传票号 IP地址 "
    "MAC地址 对手交易余额 交易流水号 日志号 凭证种类 凭证号 交易柜员号 备注 商户名称 "
    "交易类型 查询反馈结果原因 备用字段1 备用字段2 备用字段3 重点字样".split())
_SPLIT_LEDGER_CORE = frozenset(("账户号", "对方账号", "收入", "支出", "余额", "交易日期", "交易时间", "币种"))
_CODED_BANK_CSV_ROLES = {
    "self_name": ("客户名称",), "self_account": ("账户代码",), "self_card": ("交易卡号",), "trade_no": ("交易流水",),
    "direction": (table_source_roles.CODED_BANK_DIRECTION,), "currency": ("币种名称", "币种"),
    "amount": ("交易金额",), "balance": ("联机余额",), "summary": ("摘要名称", "文字摘要", "交易摘要"),
    "counter_account": ("对方帐号",), "counter_name": ("对方客户名称",), "counter_bank": ("对方帐号开户机构名称",),
}
FEEDBACK_31_REFERENCE_PROFILE = "feedback31-independent-references-v1"
OPTIONAL_FEEDBACK_REFERENCE_PROFILE = "feedback-optional-references-v1"
# These auxiliary labels confer no amount, identity, or reference authority.
_FEEDBACK_OPTIONAL = frozenset("IP地址 MAC地址 日志号 传票号 凭证种类 交易柜员号 备注 商户名称 商户代码 交易网点名称 交易发生地 查询反馈结果原因".split())
_FEEDBACK_REQUIRED = FEEDBACK_31_COLUMNS - _FEEDBACK_OPTIONAL
BANK_FEEDBACK_31_REFERENCE_PROFILE = "bank-feedback31-reference-v1"
BANK_FEEDBACK_28_REFERENCE_PROFILE = "bank-feedback28-reference-v1"
UNIFORM_FEEDBACK_37_COLUMNS = frozenset("姓名(查询条件) 证件号码(查询条件) 查询账号 查询卡号 交易类型 借贷标志 币种 交易金额 交易余额 交易时间 交易流水号 交易对方名称 交易对方账号 交易对方卡号 交易对方证件号码 交易对手余额 交易对方账号开户行 交易摘要 交易网点名称 交易网点代码 日志号 传票号 凭证种类 凭证号 终端号 交易是否成功 交易发生地 商户名称 商户号 IP地址 MAC地址 交易柜员号 备注 本方账号 本方卡号".split()) | {"Unnamed: 35", "Unnamed: 36"}
UNIFORM_FEEDBACK_37_REFERENCE_PROFILE = "uniform-feedback37-reference-v1"
UNIFORM_FEEDBACK_35_COLUMNS = UNIFORM_FEEDBACK_37_COLUMNS - {"Unnamed: 35", "Unnamed: 36"}
UNIFORM_FEEDBACK_35_REFERENCE_PROFILE = "uniform-feedback35-reference-v1"
UNIFORM35_REFERENCE_BASIS = "uniform35_source_header"
MAX_BYTES = 256 * 1024 * 1024
MAX_RECORDS = 1_000_000
MAX_COLUMNS = 256
MAX_FIELD = 1024 * 1024
MAX_SHEETS = 256
MAX_CSV_TAIL_SOURCE_BYTES = 4 * 1024 * 1024
MAX_CSV_TAIL_SOURCE_ROWS = 256
CSV_TAIL_SOURCE_REASON = "csv_tail_source_only"
CSV_TAIL_INCOMPLETE_REASON = "csv_tail_incomplete_record"
_MAX_MONEY = Decimal("9999999999999999.99")
_CENT = Decimal("0.01")
REASONS = {
    "transaction_valid": "资金字段已通过全量校验",
    CSV_TAIL_SOURCE_REASON: "最后一条非空记录缺少末尾原格，整份仅保留来源。请核对原件或补齐报表后重新上传。",
    CSV_TAIL_INCOMPLETE_REASON: "本行缺少尚未核明用途的尾部原格，原值可查，待核后办理。",
    "unknown_table": "表格结构尚不能唯一识别，未计入资金统计",
    "missing_time": "缺少交易时间，未计入资金统计",
    "invalid_time": "交易时间格式无效或日期含义不明确，未计入资金统计",
    "unsupported_time_role": "未确定可用的交易时刻；保留来源时间及记账、受理等角色，未计入资金统计和资金图",
    "unsupported_timezone": "来源的显式时区和对应UTC瞬间已保留；尚不支持与无时区交易共同投影，未计入资金统计和资金图",
    "conflicting_time": "多个时间字段不一致，未计入资金统计",
    "missing_amount": "缺少交易金额，未计入资金统计",
    "invalid_amount": "金额格式无法精确解析，未计入资金统计",
    "amount_precision": "换算后的金额不能按分精确表示，未计入资金统计",
    "amount_overflow": "金额超出存储范围，未计入资金统计",
    "conflicting_amount": "多个金额字段换算后不一致，未计入资金统计",
    "unknown_unit": "金额单位不能唯一确定，未计入资金统计",
    "conflicting_unit": "金额单位声明互相矛盾，未计入资金统计",
    "negative_amount": "负号含义没有明确依据，未计入资金统计",
    "missing_direction": "缺少可确定的收支方向，未计入资金统计",
    "invalid_direction": "方向枚举尚未适配，未计入资金统计",
    "conflicting_direction": "方向与金额或收支分列矛盾，未计入资金统计",
    "conflicting_income_expense": "收入与支出同时非零，未计入资金统计",
    "missing_identity": "缺少必要的本方或双边身份，未计入资金统计",
    "conflicting_field": "同一字段的多个候选列不一致，未计入资金统计",
    "invalid_currency": "币种不能唯一识别，未计入资金统计",
    "invalid_balance": "余额不能精确解释，未计入资金统计",
    "balance_amount_conflict": "本笔交易前后账面余额差与金额不一致，未计入资金统计",
    "balance_direction_conflict": "本笔交易前后账面余额变化与收支方向矛盾，未计入资金统计",
    "row_width": "来源记录列数与表头不同，未计入资金统计",
    "zero_amount": "零金额记录不计入资金统计",
    "blank_record": "空白记录不计入资金统计",
    "preamble": "表头前说明记录不计入资金统计",
    "repeated_header": "重复表头不计入资金统计",
    "unknown_status": "交易状态尚未适配，未计入资金统计",
    "failed_transaction": "来源明确标记交易未成功，不计入资金统计",
    "feedback_only": "查询反馈说明不属于交易记录",
    "unsafe_account_number": "Excel数值账号可能已损失精度，未计入资金统计",
    "auxiliary_table": "辅助表仅保留来源处置元数据，不计入资金统计",
    "column_count_mismatch": "来源记录列数与表头不同，未计入资金统计",
    WRONG_FIELD_NOTE_ROW: "字段说明指向流水数据行，不能核证表头职责，保留原说明行待核",
    "currency_unit_conflict": "表头明示的金额币种与来源币种矛盾，未计入资金统计",
    "unsupported_identity_format": "Excel身份数字显示格式无法唯一解释，未计入资金统计",
    "formula_value": "关键字段为未执行的Excel公式，未计入资金统计",
    "invalid_cell_type": "关键字段为Excel错误、布尔或无法解释的日期类型，未计入资金统计",
    "region_mapping_conflict": "所选映射不能唯一适用于当前表格区域，未计入资金统计",
    "ambiguous_region_header": "当前区域同时符合交易和新表头结构，金额、时间或双方解释可能不同；保留来源，至下一明确表头前不计入资金及关系统计",
}


class TableImportError(ValueError):
    """Only safe, constant diagnostics; never interpolate source cells."""


class _RecognizedCSVWidthMismatch(TableImportError):
    """A recognized CSV layout failed its existing strict row-width check."""


def _norm(value):
    value = unicodedata.normalize("NFKC", str(value)).strip().lower()
    return re.sub(r"[\s_\-]+", "", value)


def _enum(value):
    # Header normalization is deliberately more permissive than value parsing.
    # Never delete signs or internal characters from a financial enum value.
    return unicodedata.normalize("NFKC", _text(value)).strip().lower()


def _unit_token(value):
    # Unit values are financial enums, not header names. Keep all signs and
    # punctuation; whitespace alone may separate the known English words.
    return re.sub(r"\s+", "", _enum(value))


_EXTRA = {
    "trade_no": ["transaction id", "transaction number", "reference", "reference number", "trade_no"],
    "trade_time": ["transaction date", "transaction time", "transaction datetime", "datetime", "timestamp", "posting date", "trade_time"],
    "time_part": ["交易时分秒", "交易时刻", "时分秒", "time of day"],
    "payer_name": ["payer name", "sender name", "from name", "payer_name", "付款方户名"],
    "payer_account": ["payer account", "sender account", "from account", "debit account", "payer_account"],
    "payer_bank": ["payer bank", "sender bank", "payer_bank"],
    "payee_name": ["payee name", "recipient name", "to name", "payee_name", "收款方户名"],
    "payee_account": ["payee account", "recipient account", "to account", "credit account", "payee_account"],
    "payee_bank": ["payee bank", "recipient bank", "payee_bank"],
    "self_name": ["account name", "own name", "self_name", "交易户名"],
    "self_account": ["account", "account number", "own account", "self account", "self_account", "本方帐号", "帐号"],
    "counter_name": ["counterparty name", "counterparty", "counter_name"],
    "counter_account": ["counterparty account", "counter account", "counter_account", "对方帐号", "对手帐号"],
    "self_card": ["交易卡号"],
    "counter_bank": ["counterparty bank", "counter_bank", "对手开户银行"],
    "direction": ["credit/debit", "debit/credit", "dr/cr", "credit debit indicator", "收支方向"],
    "amount": ["transaction amount", "transfer amount", "signed amount", "带符号金额", "带符号交易金额"],
    "income_amt": ["income", "credit", "credit amount", "money in", "deposit", "收入", "贷方发生金额"],
    "expense_amt": ["expense", "debit", "debit amount", "money out", "withdrawal", "支出", "借方发生金额"],
    "balance": ["closing balance", "running balance", "交易余额"],
    "currency": ["currency code", "ccy", "币别"],
    "summary": ["description", "memo", "narrative", "摘要说明"],
    "channel": ["channel", "transaction type"],
    "status": ["交易是否成功", "交易状态", "transaction status", "status"],
    "feedback_reason": ["查询反馈结果原因"],
}
_ALIASES = {}
for _field in set(FIELD_SYNONYMS) | set(_EXTRA):
    _ALIASES[_field] = {_norm(x) for x in [*FIELD_SYNONYMS.get(_field, []), *_EXTRA.get(_field, [])]}
# Cash/remittance indicators are not currency codes.
_ALIASES["currency"].discard(_norm("钞汇标志"))
_REGION_IDENTITIES = frozenset(("payer_account", "payee_account", "self_account", "self_card",
                                "counter_account", "counter_card", "payer_name", "payee_name", "self_name", "counter_name"))
_REGION_IDENTITY_HEADERS = frozenset(value for field in _REGION_IDENTITIES
                                     for value in _ALIASES.get(field, ())) | {
    "本方卡号", "交易对方卡号", "交易对方账卡号", "查询帐号", "查询卡号"}
_FINANCIAL_ALIASES = {field: {_norm(value) for value in values}
                      for field, values in table_financial.ALIASES.items()}
_FINANCIAL_MONEY_HEADERS = frozenset(value for field in table_financial.MONEY_FIELDS
                                    for value in _ALIASES[field] | _FINANCIAL_ALIASES.get(field, set()))
_UNIT = {
    "元": Decimal(1), "人民币元": Decimal(1), "yuan": Decimal(1), "cny": Decimal(1),
    "分": Decimal("0.01"), "cent": Decimal("0.01"), "cents": Decimal("0.01"),
    "角": Decimal("0.1"), "jiao": Decimal("0.1"),
    "千元": Decimal(1000), "thousandyuan": Decimal(1000),
    "万元": Decimal(10000), "tenthousandyuan": Decimal(10000),
}
_SIGNED = {_norm(x) for x in ("signed amount", "带符号金额", "带符号交易金额")}
_IN = frozenset(("c", "cr", "crdt", "credit", "in", "income", "收", "入", "进", "贷", "收入", "转入", "收款", "贷方", "贷记", "收方", "存入", "入账", "进账", "存现"))
_OUT = frozenset(("d", "dr", "dbit", "debit", "out", "expense", "支", "出", "付", "借", "支出", "转出", "付款", "借方", "借记", "付方", "取现", "取款", "出账", "支取", "提现"))
_CURRENCY = {"人民币": "CNY", "人民币元": "CNY", "RMB": "CNY", "美元": "USD", "美金": "USD", "欧元": "EUR", "港币": "HKD", "港元": "HKD", "日元": "JPY", "英镑": "GBP", "澳元": "AUD", "加元": "CAD", "新加坡元": "SGD", "新西兰元": "NZD", "瑞士法郎": "CHF"}
_ISO_CURRENCIES = frozenset({"CNY", "USD", "EUR", "HKD", "JPY", "GBP", "AUD", "CAD", "SGD", "NZD", "CHF", "KRW", "THB", "MYR", "MOP", "TWD", "RUB", "INR", "BRL", "ZAR", "AED", "SAR", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "TRY", "IDR", "PHP", "VND", "MXN", "ILS"})
_CONTRACT_ALIASES = {field: {_norm(value) for value in values}
                     for field, values in table_contracts.ALIASES.items()}
_DIRECTION_HEADERS = frozenset(_norm(value) for value in (
    "debit/credit indicator", "credit/debit indicator", "cr/dr",
    "借贷标识", "收支标识", "收付标识"))
_BANK_TEMPLATE_DIRECTION_HEADERS = frozenset(("进出标志", "进出标识"))
_CLOCK_HEADERS = frozenset(_norm(value) for value in (
    "交易时间", "业务时间", "记账时间", "转账时间", "发生时间", "时间",
    "time", "transaction time", "trade time"))
_AFTER_HEADERS = {_norm(value) for value in table_contracts.AFTER_HEADERS}
_BEFORE_HEADERS = {_norm(value) for value in table_contracts.BEFORE_HEADERS}
_CONTRACT_MONEY_HEADERS = frozenset(value for field in (*table_financial.MONEY_FIELDS, *table_contracts.MONEY_FIELDS)
                                    for value in _CONTRACT_ALIASES.get(field, ()))
FINANCIAL_MAPPING_FIELDS = frozenset((*_ALIASES, *_FINANCIAL_ALIASES))
MAPPING_FIELDS = FINANCIAL_MAPPING_FIELDS | frozenset(_CONTRACT_ALIASES) | {"counter_card"}
REASONS[table_tiered_headers.PARENT_REASON] = "两层表头的父标题行"
REASONS[table_sign_declarations.LEG_OUTSIDE] = "原件金额符号说明只适用于本方腿，此行是对方腿"
REASONS[table_sign_declarations.LEG_UNPROVED] = "本行的腿别、币种或符号作用范围未能核对"
REASON_CODES = frozenset(REASONS)
DETAILS_FIELDS = frozenset({"sheet", "source_row", "financial_effect", "reason", "validation_issues", "validation_basis", "currency_basis", "counterparty_basis", "sign_convention", "flow_sha256", "source_account_column", "reported_status", "source_role", source_transaction_status.DETAIL_FIELD})
REFERENCE_DETAILS_FIELDS = frozenset({"trade_no_basis"})
FIELD_EVIDENCE_DETAILS = frozenset({"source_field_evidence", "source_field_evidence_limited"})
TIME_EVIDENCE_DETAILS = frozenset({"source_time_evidence", "source_time_evidence_limited"})
FLOW_FIELDS = ("trade_no", "trade_time", "payer_name", "payer_account", "payer_bank", "payee_name", "payee_account", "payee_bank", "amount", "balance", "currency", "summary", "channel")
STORED_FLOW_FIELDS = (*FLOW_FIELDS, "trade_time_utc", "trade_time_original", "trade_time_offset")


def mapping_fields(profile):
    """A new identity role must not become an ignored old-version override."""
    return MAPPING_FIELDS if profile in SOURCE_ROLE_PROFILES else MAPPING_FIELDS - {"counter_card"}


def is_bank_feedback39(headers):
    return len(headers) == len(BANK_FEEDBACK_39_COLUMNS) and frozenset(headers) == BANK_FEEDBACK_39_COLUMNS


def is_bank_split_ledger(headers, candidates):
    return (all(headers.count(name) == 1 for name in _SPLIT_LEDGER_CORE)
            and not any(candidates.get(field) for field in ("self_account", "self_card", "payer_account", "payee_account")))


def record_key(path, member_sha, source_row):
    """Stable identity for one physical table occurrence under this parser."""
    return hashlib.sha256(json.dumps([IDENTITY_PROFILE, path, member_sha, source_row], separators=(",", ":")).encode()).hexdigest()


def flow_digest(row):
    """Hash the established deduplication facts; never include mutable source IDs."""
    values = list(_row_key(row))
    values[8] = format(values[8], ".2f")
    values[9] = None if values[9] is None else format(values[9], ".2f")
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def verified_voucher_binding(member_digest, canonical_digest, column, size, voucher_sha256):
    descriptor = f"{column}:{size}:{voucher_sha256}"
    material = json.dumps([member_digest, canonical_digest, descriptor], separators=(",", ":"))
    return descriptor + ":" + hashlib.sha256(material.encode()).hexdigest()


def verified_voucher_occurrence(member_digest, canonical_digest, column, voucher):
    raw = voucher.encode("utf-8")
    return verified_voucher_binding(member_digest, canonical_digest, column, len(raw), hashlib.sha256(raw).hexdigest())


@dataclass(frozen=True, slots=True)
class _WorkbookValue:
    value: object
    number_format: str = "General"
    formula: bool = False
    date_only: bool = False
    invalid_type: bool = False


class _XlsxError(str):
    """Retain the original text without treating a typed error as a header."""


def _raw(value):
    return value.value if isinstance(value, _WorkbookValue) else value


def _text(value):
    if type(value) is str:
        return value.strip()
    value = _raw(value)
    if value is None:
        return ""
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat(sep=" ") if isinstance(value, dt.datetime) else value.isoformat()
    return str(value).strip()


def _cell_present(value):
    return (isinstance(value, _WorkbookValue) and (value.formula or value.invalid_type)) or bool(_text(value))


def _uniform35_reference_source(sheets, plan):
    if len(sheets) != 1 or len(plan["documents"]) != 1:
        return False
    document = plan["documents"][0]
    if document["header_rows"] != [1] or any(
        reason in plan["stats"]["reason_counts"] for reason in ("ambiguous_region_header", "unknown_table")
    ):
        return False
    headers = sheets[0]["rows"][0]
    width = len(UNIFORM_FEEDBACK_35_COLUMNS)
    return (len(headers) in (width, width + 2)
            and all(type(cell) is str for cell in headers[:width])
            and frozenset(headers[:width]) == UNIFORM_FEEDBACK_35_COLUMNS
            and all(cell is None or type(cell) is str and cell == ""
                    for row in sheets[0]["rows"] for cell in row[width:]))


def _header_value(value, profile):
    # Cached formula results and invalid typed cells cannot declare a schema.
    return "" if profile in XLS_READER_PROFILES and isinstance(value, _WorkbookValue) and (value.formula or value.invalid_type) else value


def _evidence_value(value, profile):
    # v7 adds evidence to the XLS reader without granting its formula cache
    # authority over identities, event references or posting-order declarations.
    if profile in XLS_EVIDENCE_ARCHIVE_PROFILES and isinstance(value, _WorkbookValue) and (value.formula or value.invalid_type):
        return None
    return _raw(value)


def _header_parts(value, profile=IDENTITY_PROFILE):
    value = unicodedata.normalize("NFKC", _text(_header_value(value, profile)))
    if profile in FINANCIAL_PROFILES:
        normalized = _norm(value)
        money_headers = _FINANCIAL_MONEY_HEADERS | (_CONTRACT_MONEY_HEADERS if profile in CONTRACT_PROFILES else frozenset())
        if normalized in _ALIASES["direction"] or profile in DIRECTION_PROFILES and normalized in _DIRECTION_HEADERS:
            return normalized, Decimal(1), "元/默认基础单位"
        if profile in UNIT_DECLARATION_PROFILES:
            # A complete monetary field may declare an existing unit in either
            # language. Pair delimiters before interpreting the declaration;
            # the historical fallback otherwise accepts mixed brackets.
            paired = re.fullmatch(r"(.*?)\s*(?:\(([^()\[\]]+)\)|\[([^()\[\]]+)\])\s*", value)
            if paired and _norm(paired[1]) in money_headers:
                declared = re.sub(r"^(?:单位\s*[:：]?\s*|(?i:unit)\s*[:：]\s*)", "", (paired[2] or paired[3]).strip())
                token = _unit_token(declared)
                return _norm(paired[1]), _UNIT.get(token), token
            malformed = re.fullmatch(r"(.*?)\s*([\(\[].*)", value)
            if malformed and _norm(malformed[1]) in money_headers:
                return _norm(malformed[1]), None, _unit_token(malformed[2])
        # Only complete monetary names may declare a unit. A slash in a
        # direction such as debit/credit must keep its original meaning.
        declaration = re.fullmatch(r"(.*?)\s*[\(\[]\s*(?:单位\s*[:：]?\s*)?([^()\[\]]+)\s*[\)\]]\s*", value)
        if not declaration:
            declaration = re.fullmatch(r"(.*?)\s*/\s*(?:单位\s*[:：]?\s*)?([^/]+)\s*", value)
        if declaration and _norm(declaration[1]) in money_headers:
            declared = declaration[2]
            if profile in UNIT_DECLARATION_PROFILES:
                declared = re.sub(r"^(?i:unit)\s*[:：]\s*", "", declared.strip())
            token = _unit_token(declared)
            return _norm(declaration[1]), _UNIT.get(token), token
        if profile in UNIT_DECLARATION_PROFILES:
            malformed_slash = re.fullmatch(r"([^/]+)/(.*)", value)
            if malformed_slash and _norm(malformed_slash[1]) in money_headers:
                return _norm(malformed_slash[1]), None, _unit_token(malformed_slash[2])
        normalized = _unit_token(value)
        for unit in sorted(_UNIT, key=len, reverse=True):
            if not normalized.endswith(unit):
                continue
            base = _norm(normalized[:-len(unit)])
            if base in money_headers:
                return base, _UNIT[unit], unit
        # A recognizable monetary declaration with an invalid unit must not
        # fall back to historical header normalization that deletes its signs.
        legacy = _header_parts(value, IDENTITY_PROFILE)
        if legacy[2] != "元/默认基础单位" and legacy[0] in money_headers:
            return legacy[0], None, normalized
        return legacy
    match = re.fullmatch(r"(.*?)\s*[（(\[]\s*(?:单位\s*[:：]?\s*)?([^()（）\[\]]+)\s*[）)\]]\s*", value)
    if match:
        base, unit = _norm(match[1]), _norm(match[2])
        # Parentheses which are not monetary units must not erase semantics.
        if unit in _UNIT or base in _ALIASES["amount"] | _ALIASES["balance"] | _ALIASES["income_amt"] | _ALIASES["expense_amt"]:
            return base, _UNIT.get(unit), unit
    # Explicit unit suffix, such as amount_cents and 交易金额万元.
    normalized = _norm(value)
    for unit in sorted(_UNIT, key=len, reverse=True):
        if normalized.endswith(unit) and normalized[:-len(unit)] in (_ALIASES["amount"] | _ALIASES["balance"] | _ALIASES["income_amt"] | _ALIASES["expense_amt"]):
            return normalized[:-len(unit)], _UNIT[unit], unit
    return normalized, Decimal(1), "元/默认基础单位"


def _declared_time_header(value, profile):
    if profile not in TIME_FORMAT_PROFILES:
        return None
    declaration = table_time_formats.header_parts(_text(value), precise=profile in PRECISE_PROFILES)
    if declaration is None:
        return None
    base, spec = declaration
    base = _norm(base)
    clocks = _ALIASES["time_part"] | _FINANCIAL_ALIASES.get("time_part", set())
    dates = _ALIASES["trade_time"] | _FINANCIAL_ALIASES.get("trade_time", set())
    if spec in {"HH:MM:SS", "HHMMSS", "HHMMSSFFFFFF"}:
        return (base, spec) if base in clocks | _CLOCK_HEADERS else None
    return (base, spec) if base in dates else None


def _unsupported_time_declaration(headers, profile):
    """An unrecognized clock annotation cannot silently become a missing clock."""
    if profile not in DECLARATION_GUARD_PROFILES:
        return False
    aliases = (_ALIASES["trade_time"] | _ALIASES["time_part"] | _CLOCK_HEADERS
               | _FINANCIAL_ALIASES["trade_time"] | _FINANCIAL_ALIASES.get("time_part", set()))
    query_owner = (profile in QUERY_OWNER_PROFILES and table_source_roles.classify(
        [_text(_header_value(header, profile)) for header in headers], query_conditions=True)
        in {"transaction_query_only", "transaction_explicit"})
    for header in headers:
        raw = unicodedata.normalize("NFKC", _text(_header_value(header, profile)))
        if query_owner and table_source_roles.query_condition(raw):
            continue
        match = re.fullmatch(r"([^()\[\]]+?)\s*(?:\(([^()\[\]]+)\)|\[([^()\[\]]+)\])", raw)
        if match and _norm(match[1]) in aliases and _declared_time_header(raw, profile) is None:
            return True
    return False


def _columns(headers, profile=IDENTITY_PROFILE):
    counts = {}
    labels = []
    for index, raw in enumerate(headers):
        label = _text(_header_value(raw, profile)) or f"未命名列{index + 1}"
        counts[label] = counts.get(label, 0) + 1
        labels.append(label)
    return [f"{label} [列{i + 1}]" if counts[label] > 1 else label for i, label in enumerate(labels)]


def _source_time_role(base):
    if base in {_norm(value) for value in ("记账时间", "记账日期", "posting date", "posting time")}:
        return "posting"
    if base in {_norm(value) for value in ("受理时间", "受理日期")}:
        return "receipt"
    if base in {_norm(value) for value in ("结算时间", "结算日期")}:
        return "settlement"
    if base in {_norm(value) for value in ("交易日期时间", "交易时间", "交易日期", "交易发生日期", "交易时分秒", "交易时刻",
            "转账时间", "转账日期", "transaction date", "transaction time", "transaction datetime", "trade time", "trade_time", "tradedate", "transdate")}:
        return "transaction"
    return "unspecified"


_TIME_CORROBORATION_HEADERS = frozenset(_norm(value) for value in ("date", "datetime", "timestamp", "time", "time of day"))


def _template_direction_column(headers, candidates, profile):
    if (candidates.get("direction") or candidates.get("income_amt") or candidates.get("expense_amt")
            or not all(len(candidates.get(field, ())) == 1 for field in ("trade_time", "amount", "currency"))
            or _shape(candidates, profile) != "B"):
        return None
    matches = [index for index, header in enumerate(headers)
               if _norm(header) in _BANK_TEMPLATE_DIRECTION_HEADERS]
    return matches[0] if len(matches) == 1 else None


def _template_balance_column(headers, candidates, profile):
    times = candidates.get("trade_time", ())
    if (candidates.get("balance") or len(candidates.get("currency", ())) != 1
            or not any(item.get("time_role") == "transaction" for item in times)):
        return None
    mode = _shape(candidates, profile)
    if mode != "B":
        # An undecided split table may receive the field at preview. Its
        # ownership still needs the existing whole-region layout proof.
        if (mode is not None or len(times) != 1
                or any(candidates.get(field) for field in ("amount", "direction", "payer_account", "payee_account"))
                or not all(len(candidates.get(field, ())) == 1
                           for field in ("counter_account", "income_amt", "expense_amt"))):
            return None
    names = [_norm(header) for header in headers]
    if names.count("账面余额") == names.count("可用余额") == 1:
        return names.index("账面余额")
    return None


def _candidates(headers, profile=IDENTITY_PROFILE):
    headers = [_header_value(value, profile) for value in headers]
    found = {}
    query_owner = (profile in QUERY_OWNER_PROFILES and table_source_roles.classify(
        [_text(header) for header in headers], query_conditions=True)
        in {"transaction_query_only", "transaction_explicit"})
    for index, header in enumerate(headers):
        if query_owner and table_source_roles.query_condition(_text(header)):
            continue
        base, factor, unit = _header_parts(header, profile)
        declared_time = _declared_time_header(header, profile)
        if declared_time:
            base = declared_time[0]
        fields = [field for field, synonyms in _ALIASES.items() if base in synonyms]
        basis = "exact_header_alias"
        if profile in FINANCIAL_PROFILES:
            extra = [field for field, synonyms in _FINANCIAL_ALIASES.items() if base in synonyms]
            if not fields and extra:
                fields, basis = extra, "explicit_financial_header"
            elif fields and _header_parts(header) != (base, factor, unit):
                basis = "explicit_financial_header"
        if profile in CONTRACT_PROFILES:
            for field, aliases in _CONTRACT_ALIASES.items():
                if base in aliases and field not in fields:
                    fields.append(field)
                    basis = "explicit_source_contract"
        if profile in DIRECTION_PROFILES and _norm(_text(header)) in _DIRECTION_HEADERS:
            fields, basis = ["direction"], "explicit_financial_header"
        if profile in SOURCE_ROLE_PROFILES:
            # An account and its card are different identifiers, not competing
            # values of one field. Generic query conditions confer no role.
            identity_roles = {"本方卡号": "self_card", "交易卡号": "self_card",
                              "交易对方卡号": "counter_card", "交易对方账卡号": "counter_account"}
            if base in identity_roles:
                fields, basis = [identity_roles[base]], "explicit_identity_role"
        if query_owner and _norm(header) in {"查询帐号", "查询卡号"}:
            fields = ["self_account" if _norm(header) == "查询帐号" else "self_card"]
            basis = "uniform_query_owner"
        if profile in BANK_NAME_PROFILES and _norm(header) == "交易对方账号开户行":
            fields, basis = ["counter_bank"], "explicit_identity_role"
        if profile in CURRENCY_CODE_ALIAS_PROFILES and base in SOURCE_CURRENCY_HEADINGS:
            fields, basis = ["currency"], "exact_header_alias"
        if declared_time:
            basis = "explicit_time_format"
        if not fields and profile in ROLE_PROFILES and (field := identity_field(base)):
            fields = [field]
            basis = "role_qualified_header"
        for field in fields:
            found.setdefault(field, []).append({"index": index, "factor": factor, "unit": unit, "signed": base in _SIGNED, "unit_currency": "CNY" if unit in {"人民币元", "cny"} else "", "basis": basis, **({"same_entry_after": field == "balance" and base in _AFTER_HEADERS,
                                  "same_entry_before": field == "balance_before" and base in _BEFORE_HEADERS}
                                 if profile in CONTRACT_PROFILES else {}),
                                 **({"time_format": declared_time[1], "precise_time": profile in PRECISE_PROFILES} if declared_time else {}),
                                 **({"time_role": _source_time_role(base), "time_corroborates": profile in ZONED_PROFILES and base in _TIME_CORROBORATION_HEADERS, "zoned_time": profile in ZONED_PROFILES, "time_note_indices": tuple(i for i, value in enumerate(headers)
                                      if _text(value) in {"备注", "说明"})} if profile in TIME_ROLE_PROFILES and field in {"trade_time", "time_part"} else {}),
                                 **({"identity_kind": "card" if field == "self_card" else "account",
                                     "contract_owner": True}
                                    if query_owner and field in {"self_account", "self_card"} else {})})
    if profile in PRECISE_PROFILES:
        owner_indices = tuple(index for index, header in enumerate(headers)
                              if _norm(_text(header)) in {"本方账号", "交易账号", "本方卡号", "交易卡号"})
        if owner_indices and any(found.get(field) for field in ("direction", "income_amt", "expense_amt")):
            for index, header in enumerate(headers):
                if _norm(_text(header)) == "账户开户名称":
                    found.setdefault("self_name", []).append({"index": index, "factor": Decimal(1),
                        "unit": "元/默认基础单位", "signed": False, "unit_currency": "",
                        "basis": "explicit_identity_role", "owner_name_anchor_indices": owner_indices})
    if profile in {BANK_TEMPLATE_TRIAL_PROFILE, BANK_COMPONENT_TRIAL_PROFILE}:
        for field, index in (("direction", _template_direction_column(headers, found, profile)),
                             ("balance", _template_balance_column(headers, found, profile))):
            if index is not None:
                found[field] = [{"index": index, "factor": Decimal(1), "unit": "元/默认基础单位", "signed": False,
                                 "unit_currency": "", "basis": "explicit_financial_header"}]
    return found


def _shape(candidates, profile=IDENTITY_PROFILE):
    money = any(candidates.get(k) for k in ("amount", "income_amt", "expense_amt"))
    if not candidates.get("trade_time") or not money:
        return None
    if (candidates.get("payer_account") or candidates.get("payer_name")) and (candidates.get("payee_account") or candidates.get("payee_name")):
        return "A"
    if candidates.get("self_account") or profile in SOURCE_ROLE_PROFILES and candidates.get("self_card"):
        return "B"
    return None


def _locate_header(raw, profile=IDENTITY_PROFILE):
    time_names = tuple(_ALIASES["trade_time"] | _FINANCIAL_ALIASES.get("trade_time", set())
                       | _CONTRACT_ALIASES.get("trade_time", set()) | _CLOCK_HEADERS) if type(profile) is str else ()
    for index, row in enumerate(raw):
        # One ordinary cell cannot supply both time and money candidates.
        # Keep historical profiles and unproved/custom types on the old path.
        if (type(profile) is str and profile in HEADER_WIDTH_PROFILES
                and type(row) in (list, tuple) and len(row) < 2
                and (not row or type(row[0]) is str)):
            continue
        # Unit and time-format declarations only remove a name's suffix.
        # A time candidate therefore needs an alias prefix; keep false
        # positives and custom objects on the complete candidate path.
        if (time_names and type(row) in (list, tuple) and all(type(cell) is str for cell in row)
                and not any(_norm(cell).startswith(time_names) for cell in row)):
            continue
        candidates = _candidates(row, profile)
        if candidates.get("trade_time") and any(candidates.get(k) for k in ("amount", "income_amt", "expense_amt")):
            return index, candidates, _shape(candidates, profile)
    return None, {}, None


def _bytes(source):
    try:
        if isinstance(source, (bytes, bytearray)):
            raw = bytes(source)
        elif hasattr(source, "read"):
            source.seek(0)
            raw = source.read(MAX_BYTES + 1)
        else:
            with Path(source).open("rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
    except (OSError, ValueError, TypeError):
        raise TableImportError("无法读取表格文件") from None
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise TableImportError("表格文件超出安全处理大小")
    return raw


def _strict_source_csv(text, profile):
    """Use complete quoted records and recognized column structure as evidence."""
    from . import feedback_import, table_archive
    auxiliary = set(table_archive._AUXILIARY) | set(table_archive._SOURCE_AUXILIARY)
    auxiliary |= {frozenset(names) for names in feedback_import.SCHEMAS.values()}
    choice, priority, ambiguous = None, -1, False
    damaged_recognized = False
    absent_seen = False
    for delimiter in (",", "\t", ";", "|"):
        # Absent separators produce identical quoted records and structure.
        # Keep the first one, including its format choice and safety errors.
        if delimiter not in text:
            if absent_seen:
                continue
            absent_seen = True
        try:
            feedback_import._validate_csv_quotes(text, delimiter)
            rows = _collect_rows(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True))
        except (csv.Error, feedback_import.FeedbackImportError):
            continue
        if not rows:
            continue
        header, _, mode = _locate_header(rows, profile)
        known_auxiliary = frozenset(cell.strip() for cell in rows[0]) in auxiliary and len(rows[0]) == len(set(rows[0]))
        auxiliary_rows = (profile in AUXILIARY_ROWS_ARCHIVE_PROFILES and known_auxiliary
                          and table_archive._auxiliary_role([cell.strip() for cell in rows[0]], profile)[0]
                          in {"freeze", "manifest"})
        recognized = header is not None or known_auxiliary
        start = header if header is not None else 0
        width = len(rows[start])
        # An auxiliary label cannot hide a later financial region. The whole
        # member has already passed quote, field and record safety validation.
        valid = not (auxiliary_rows and header is not None)
        for row in rows[start + 1:]:
            if not row or not any(cell.strip() for cell in row):
                continue
            # A same-width header cannot change this structural check. The
            # parser independently interprets every region after CSV selection.
            if len(row) != width and not known_auxiliary:
                regional, _, _ = _locate_header([row], profile)
                if regional is not None:
                    width = len(row)
            if len(row) != width and not auxiliary_rows:
                valid = False
                break
        if recognized and not valid:
            damaged_recognized = True
        if valid:
            candidate_priority = 2 if recognized else int(max(map(len, rows)) > 1)
            if candidate_priority > priority:
                choice, priority, ambiguous = (rows, delimiter), candidate_priority, False
            elif candidate_priority == priority and not ambiguous and rows != choice[0]:
                ambiguous = True
        # Keep one exact representative, rather than every delimiter matrix
        # plus a second full tuple-of-tuples index. Later candidates still run
        # the complete structural and safety checks before selection finishes.
        rows = None
    if priority < 2 and damaged_recognized:
        raise _RecognizedCSVWidthMismatch("CSV已识别表头与数据列数不一致，文件未接纳")
    if ambiguous:
        raise TableImportError("CSV分隔符存在歧义，无法安全识别")
    if choice is None:
        raise TableImportError("CSV引号或记录结构损坏，文件未接纳")
    return choice


def _csv_text(raw):
    if b"\x00" in raw[:4096] and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise TableImportError("CSV编码无法安全识别")
    encodings = ("utf-16",) if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "gb18030")
    text, encoding = None, None
    for candidate in encodings:
        try:
            text, encoding = raw.decode(candidate, errors="strict"), candidate
            break
        except UnicodeError:
            continue
    if text is None or "\x00" in text:
        raise TableImportError("CSV编码无法安全识别")
    csv.field_size_limit(max(csv.field_size_limit(), MAX_FIELD))
    return text, encoding


def _csv(raw, suffix, profile=IDENTITY_PROFILE):
    text, encoding = _csv_text(raw)
    if profile in SOURCE_CONTRACT_PROFILES:
        rows, delimiter = _strict_source_csv(text, profile)
        return [{"path": "table.csv", "name": "", "rows": rows, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw), "encoding": encoding, "delimiter": delimiter}]
    choices = []
    for delimiter in ("\t", ",", ";", "|"):
        sample = []
        try:
            with io.StringIO(text, newline="") as stream:
                reader = csv.reader(stream, delimiter=delimiter, strict=True)
                for index, row in enumerate(reader):
                    if index >= MAX_RECORDS:
                        raise TableImportError("表格记录数或列数超出安全处理范围")
                    sample.append(row)
                    if index >= 99:
                        break
        except csv.Error:
            continue
        header, candidates, mode = _locate_header(sample, profile)
        if header is not None:
            choices.append((delimiter, header, candidates, mode))
    if len(choices) > 1:
        # No order-based tie breaking when two separators yield valid schemas.
        raise TableImportError("CSV分隔符存在歧义，无法安全识别")
    if choices:
        delimiter = choices[0][0]
    else:
        # An unknown schema still gets a complete disposition, not a guessed map.
        try:
            delimiter = csv.Sniffer().sniff(text[:65536], delimiters=",\t;|").delimiter
        except csv.Error:
            delimiter = "\t" if suffix == ".tsv" else ","
    try:
        with io.StringIO(text, newline="") as stream:
            rows = _collect_rows(csv.reader(stream, delimiter=delimiter, strict=True))
    except csv.Error:
        raise TableImportError("CSV引号或记录结构损坏，文件未接纳") from None
    return [{"path": "table.csv", "name": "", "rows": rows, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw), "encoding": encoding, "delimiter": delimiter}]


class _DecodedCSV:
    """One member's authoritative decode, consumed by its same-profile parser."""
    def __init__(self, raw, suffix, profile):
        if type(raw) is not bytes or suffix not in {".csv", ".tsv", ".txt"} or profile not in SOURCE_CONTRACT_PROFILES:
            raise TableImportError("临时CSV解码仅用于来源合同成员")
        self._raw, self._suffix, self._profile = raw, suffix, profile
        self._sheets = _csv(raw, suffix, profile)
        sheet = self._sheets[0]
        self.header = sheet["rows"][0] if sheet["rows"] else []
        self.encoding, self.delimiter = sheet["encoding"], sheet["delimiter"]

    def take(self, raw, suffix, profile):
        if (self._sheets is None or self._raw != raw
                or self._suffix != suffix or self._profile != profile):
            raise TableImportError("临时CSV解码与来源或规则不匹配")
        sheets = self._sheets
        self._raw = self._sheets = self.header = None
        return sheets


def _check_row(row, count):
    if count > MAX_RECORDS or len(row) > MAX_COLUMNS:
        raise TableImportError("表格记录数或列数超出安全处理范围")
    if any(len(_text(value)) > MAX_FIELD for value in row):
        raise TableImportError("表格字段超出安全处理大小")


def _collect_rows(iterator, *, previous_count=0):
    rows = []
    for row in iterator:
        row = list(row)
        _check_row(row, previous_count + len(rows) + 1)
        rows.append(row)
    return rows


def _csv_tail_sheet(raw):
    """Recognize a bounded terminal omission without padding or moving physical cells."""
    from . import feedback_import

    if len(raw) > MAX_CSV_TAIL_SOURCE_BYTES:
        return None
    text, encoding = _csv_text(raw)
    base = VOUCHER_REFERENCE_PROFILE
    registered = []
    for delimiter in (",", "\t", ";", "|"):
        if delimiter not in text:
            continue
        try:
            feedback_import._validate_csv_quotes(text, delimiter)
            reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
            rows = _collect_rows(islice(reader, MAX_CSV_TAIL_SOURCE_ROWS + 1))
        except (csv.Error, feedback_import.FeedbackImportError):
            continue
        header, candidates, mode = _locate_header(rows[:1], base)
        if header == 0:
            registered.append((rows, candidates, delimiter, mode))
    if len(registered) != 1:
        return None
    [(rows, candidates, delimiter, mode)] = registered
    if len(rows) > MAX_CSV_TAIL_SOURCE_ROWS:
        return None
    headings = rows[0]
    data = [row for row in rows[1:] if any(cell.strip() for cell in row)]
    if len(data) < 2:
        return None
    last_width = len(data[-1])
    omitted_roles = {field for field, columns in candidates.items()
                     if any(candidate["index"] >= last_width for candidate in columns)}
    balances = candidates.get("balance", ())
    # Retain a short final row whose missing columns are the explicitly
    # identified counterparty suffix. Earlier rows still own all their cells.
    counterparty_tail = (
        mode == "B" and omitted_roles == _COUNTERPARTY_TAIL_FIELDS
        and all(len(candidates[field]) == 1 for field in omitted_roles)
        and {candidates[field][0]["index"] for field in omitted_roles} == set(range(last_width, len(headings)))
        and identity_field(_norm(headings[candidates["counter_account"][0]["index"]])) == "counter_account"
        and len(balances) == 1 and balances[0]["index"] == last_width - 1
        and _money(_value(data[-1], balances[0]), balances[0])[0] is not None
    )
    if (not 0 < last_width < len(headings) or any(len(row) != len(headings) for row in data[:-1])
            or not counterparty_tail and (
                any(value != "" for row in data[:-1] for value in row[last_width:])
                or omitted_roles - {"summary"}
                or any(document_number_header(_norm(label)) or identity_field(_norm(label)) for label in headings[last_width:])
            )
            or _locate_header(data, base)[0] is not None):
        return None
    amounts, clocks = candidates.get("amount", ()), candidates.get("trade_time", ())
    income, expense = candidates.get("income_amt", ()), candidates.get("expense_amt", ())
    split_amounts = not amounts and len(income) == len(expense) == 1
    if not clocks or not (split_amounts or len(amounts) == 1 and not (income or expense)):
        return None
    # Split columns on a short record are retained as source cells only.
    # Their conflicting signs or excess precision may be the facts to inspect.
    if any(not any(_time(_value(row, clock)) for clock in clocks)
           or amounts and _money(_value(row, amounts[0]), amounts[0])[0] is None
           or _overlapping_header(row, candidates, base) for row in data):
        return None

    return {"path": "table.csv", "name": "", "rows": rows, "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw), "encoding": encoding, "delimiter": delimiter, "present_tail_width": last_width,
            "counterparty_tail": counterparty_tail, "source_only": bool(split_amounts or omitted_roles and not counterparty_tail)}


def _tail_reference_supported(sheet):
    """Limit financial replay to the existing, explicit two-number interpretation."""
    headings = sheet["rows"][0]
    present = sheet["present_tail_width"]
    positions = explicit_reference_columns(headings, VOUCHER_REFERENCE_PROFILE)
    if (sheet["source_only"] or positions is None or max(positions) > present
            or any(_norm(label) not in _OPTIONAL_REFERENCE_TAIL for label in headings[present:])
            or _withdrawal_conflicts_by_row(sheet)):
        return False
    primary, voucher = (position - 1 for position in positions)
    for row in sheet["rows"][1:]:
        if (len(row) > max(primary, voucher) and (left := _text(row[primary])) and (right := _text(row[voucher]))
                and left != right):
            return True
    return False


def _tail_observation_metadata(label):
    # Existing bank feedback uses network and transaction-location fields as
    # observations, never as account, amount, clock, direction or status.
    return label == "交易发生地" or re.fullmatch(r"(?:交易)?(?:ip|mac)(?:地址)?", label) is not None


def _tail_boundary_kind(value):
    if value.isascii() and value.isdecimal():
        return "digits"
    if re.fullmatch(r"[0-9a-fA-F]{2}(?:(?::|-)[0-9a-fA-F]{2}){5}", value):
        return "mac"
    try:
        ip_address(value)
    except ValueError:
        return "text"
    return "ip"


def _tail_ordinary_profile(sheet):
    """Admit only intact records unless the absent fields are proved optional."""
    headers = sheet["rows"][0]
    present = sheet["present_tail_width"]
    suffix = [_norm(label) for label in headers[present:] if _norm(label)]
    if sheet["source_only"] or _withdrawal_conflicts_by_row(sheet):
        return None
    rows = [row for row in sheet["rows"][1:] if any(cell.strip() for cell in row)]
    # An empty voucher column supplies no competing transaction identifier.
    if ((positions := explicit_reference_columns(headers, VOUCHER_REFERENCE_PROFILE))
            and any(row[positions[1] - 1] != "" for row in rows)):
        return None
    if sheet["counterparty_tail"]:
        return CSV_TAIL_COUNTERPARTY_PROFILE
    # A short row needs an observable boundary with no contrary shape, e.g.
    # a network address shifted into a column of numeric account identifiers.
    final = rows[-1][present - 1].strip()
    earlier = {_tail_boundary_kind(value) for row in rows[:-1] if (value := row[present - 1].strip())}
    if not final or not earlier or len(earlier) == 1 and _tail_boundary_kind(final) not in earlier:
        return None
    if suffix and all(map(_tail_observation_metadata, suffix)):
        return CSV_TAIL_METADATA_PROFILE
    return CSV_TAIL_COMPLETE_ROWS_PROFILE


def _csv_tail_source_plan(sheet):
    """Retain unpadded source records under the original, read-only profile."""
    rows, path, digest = sheet["rows"], sheet["path"], sheet["sha256"]
    columns = _columns(rows[0], CSV_TAIL_SOURCE_PROFILE)
    document = {"member_path": path, "sheet": "", "header_row": 1, "header_rows": [1], "columns": columns,
                "mapping": {}, "mode": "B", "source_rows": len(rows), "row_count": len(rows) - 1}
    records = []
    for position, row in enumerate(rows[1:], 2):
        details = {"sheet": "", "source_row": str(position), "financial_effect": "not_a_flow"}
        disposition, reason = "non_transaction", "blank_record"
        if any(cell.strip() for cell in row):
            disposition, reason = "unresolved", CSV_TAIL_SOURCE_REASON
            details.update(financial_effect="undetermined", validation_issues=reason)
        records.append({"record_no": position - 1, "member_path": path, "source_row": position,
                        "record_key": record_key(path, digest, position), "disposition": disposition,
                        "reason_code": reason, "details": details})
    member = {"member_path": path, "role": "table", "sha256": digest, "size_bytes": sheet["size"], "encoding": sheet["encoding"],
              "row_count": len(records)}
    plan = {"profile": CSV_TAIL_SOURCE_PROFILE, "mode": "B", "mapping": {}, "columns": columns, "sheet": "",
            "rows": [], "records": records, "documents": [document], "mapping_evidence": [], "members": [member]}
    refresh_stats(plan)
    return plan


def xlsx_member_digest(workbook_digest, index, title):
    return hashlib.sha256(json.dumps([workbook_digest, index, title], ensure_ascii=False).encode()).hexdigest()


def archive_workbook_path(member_path):
    worksheet = re.fullmatch(r"(.+\.(?:xls|xlsx))/worksheets/[0-9]{4}", member_path, re.IGNORECASE)
    return worksheet[1] if worksheet else None


def _excel(raw, suffix, *, mark_errors=False):
    if suffix == ".xls":
        from .table_xls import read_workbook
        return read_workbook(raw)
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            info = archive.infolist()
            if len(info) > 2048 or sum(member.file_size for member in info) > 300 * 1024 * 1024:
                raise TableImportError("工作簿展开大小超出安全处理范围")
        from openpyxl import load_workbook
        from openpyxl.styles.numbers import is_datetime
        workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=False, keep_links=False)
        sheets = []
        try:
            digest = hashlib.sha256(raw).hexdigest()
            count = 0
            if len(workbook.worksheets) > MAX_SHEETS:
                raise TableImportError("工作表数量超出安全处理范围")
            for index, sheet in enumerate(workbook.worksheets, start=1):
                # Read the actual XML stream; producer dimensions may understate
                # rows/columns and must not authorize silent source truncation.
                sheet.reset_dimensions()
                direction_error_cells = set()

                def values():
                    for row_no, cells in enumerate(sheet.iter_rows(values_only=False), 1):
                        row = []
                        for column_index, cell in enumerate(cells):
                            value = cell.value
                            cell_type = getattr(cell, "data_type", "")
                            formula = cell_type == "f"
                            if cell_type == "e" and table_direction_recheck.could_be_literal(value):
                                # Preserve historical cell values while withholding
                                # declaration authority from typed Excel errors.
                                direction_error_cells.add((row_no, column_index))
                            if mark_errors and cell_type == "e" and isinstance(value, str):
                                value = _XlsxError(value)
                            if formula or isinstance(value, (int, float, dt.date, dt.time)):
                                fmt = getattr(cell, "number_format", "General")
                                date_only = (isinstance(value, dt.date) and not isinstance(value, dt.datetime)) or (isinstance(value, dt.datetime) and value.time() == dt.time() and is_datetime(fmt) == "date")
                                value = _WorkbookValue(value, fmt, formula, date_only)
                            row.append(value)
                        yield row

                rows = _collect_rows(values(), previous_count=count)
                width = max((len(row) for row in rows), default=0)
                # XLSX omits trailing empty cells; unlike malformed CSV widths,
                # missing XML cells are a defined empty worksheet coordinate.
                for row in rows:
                    row.extend([None] * (width - len(row)))
                count += len(rows)
                path = f"worksheets/{index:04d}"
                member_sha = xlsx_member_digest(digest, index, sheet.title)
                sheets.append({"path": path, "name": sheet.title, "rows": rows, "sha256": member_sha, "size": 0, "encoding": "xlsx", "delimiter": "",
                               "direction_error_cells": direction_error_cells})
        finally:
            workbook.close()
        return sheets
    except TableImportError:
        raise
    except Exception:
        raise TableImportError("Excel工作簿损坏、加密或格式不受支持") from None


def _money(value, candidate):
    raw = _text(value)
    if not raw or raw.lower() in {"null", "none", "nan", "--", "-"}:
        return None, None
    if candidate["factor"] is None:
        return None, "unknown_unit"
    parentheses = raw.startswith("(") and raw.endswith(")")
    if parentheses:
        raw = raw[1:-1].strip()
        if raw.startswith(("+", "-")):
            return None, "invalid_amount"
    # Currency decoration may surround the entire number; embedded symbols are
    # not characters which can safely be deleted from a numeric token.
    if raw.startswith(("￥", "¥")):
        raw = raw[1:].strip()
    elif raw.endswith(("￥", "¥")):
        raw = raw[:-1].strip()
    elif len(raw) > 1 and raw[0] in "+-" and raw[1] in "￥¥":
        raw = raw[0] + raw[2:].strip()
    if "," in raw or "，" in raw:
        raw = raw.replace("，", ",")
        if not re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", raw):
            return None, "invalid_amount"
        raw = raw.replace(",", "")
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", raw):
        return None, "invalid_amount"
    if len(raw) > 256:
        return None, "invalid_amount"
    exponent = re.search(r"[eE]([+-]?\d+)\Z", raw)
    if exponent and abs(int(exponent[1])) > 1000:
        # Bound the exponent before arithmetic: Decimal context underflow must
        # never turn a positive source value into a zero-movement decision.
        return None, "amount_precision" if int(exponent[1]) < 0 else "amount_overflow"
    try:
        with localcontext() as context:
            context.prec = max(50, len(raw) + 12)
            amount = Decimal(raw) * candidate["factor"]
            if parentheses:
                amount = -amount
            if not amount.is_finite() or abs(amount) > _MAX_MONEY:
                return None, "amount_overflow"
            if amount != amount.quantize(_CENT):
                return None, "amount_precision"
            return amount.quantize(_CENT), None
    except (DecimalException, OverflowError, ValueError):
        return None, "invalid_amount"


def _time(value):
    value = _raw(value)
    if isinstance(value, dt.datetime):
        return value if value.tzinfo is None else None
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time())
    raw = _text(value)
    raw = raw.replace("年", "-").replace("月", "-").replace("日", "")
    # Year-first dates have an unambiguous day/month convention. No inference.
    match = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T]?(\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?)?", raw)
    if match:
        parts = match.groups()
        try:
            return dt.datetime(int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3] or 0), int(parts[4] or 0), int(parts[5] or 0), int((parts[6] or "").ljust(6, "0") or 0))
        except ValueError:
            return None
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%d %H%M%S", "%Y%m%d"):
        if re.fullmatch(r"\d{8}|\d{14}|\d{8} \d{6}", raw):
            try:
                return dt.datetime.strptime(raw, fmt)
            except ValueError:
                continue
    return None


def _date_only(value):
    if isinstance(value, _WorkbookValue) and value.date_only:
        return True
    value = _raw(value)
    if isinstance(value, dt.datetime):
        return False
    if isinstance(value, dt.date):
        return True
    raw = _text(value).replace("年", "-").replace("月", "-").replace("日", "")
    return bool(re.fullmatch(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{8}", raw))


def _value(row, candidate):
    if "source_component" in candidate:
        return candidate["source_component"]
    anchors = candidate.get("owner_name_anchor_indices")
    if anchors is not None and not any(index < len(row) and _text(row[index]) for index in anchors):
        return ""
    return row[candidate["index"]] if candidate["index"] < len(row) else ""


def _field(row, candidates, name, issues, *, normalize=None):
    values = []
    for candidate in candidates.get(name, []):
        value = _value(row, candidate)
        if name in {"payer_account", "payee_account", "self_account", "self_card", "counter_account", "counter_card"} and isinstance(value, _WorkbookValue):
            raw = value.value
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                try:
                    integer = int(raw)
                    if raw != integer or integer < 0 or len(str(integer)) > 15:
                        issues.append("unsafe_account_number")
                    elif value.number_format not in {"General", "0", "@"}:
                        if re.fullmatch(r"0+", value.number_format):
                            value = str(integer).zfill(len(value.number_format))
                        else:
                            issues.append("unsupported_identity_format")
                except (ValueError, OverflowError):
                    issues.append("unsafe_account_number")
        values.append(_text(value))
    nonempty = [value for value in values if value]
    compare = [normalize(value) if normalize else value for value in nonempty]
    if len(set(compare)) > 1:
        issues.append("conflicting_field")
    return nonempty[0] if nonempty else ""


_CARD_HEADERS = frozenset(_norm(value) for value in (
    "本方卡号", "交易卡号", "卡号", "交易对方卡号", "对方卡号", "对手卡号",
    "付款人卡号", "付款卡号", "转出卡号", "收款人卡号", "收款卡号", "转入卡号"))


def _identity_pair(row, candidates, field, issues):
    """Validate each identifier kind; new contracts retain typed overrides."""
    accounts, cards = [], []
    items = list(candidates.get(field, ()))
    if field in {"self_account", "counter_account"}:
        card_items = candidates.get(field.replace("account", "card"), ())
    else:
        card_items = ()
    for item in items:
        (cards if item.get("identity_kind") == "card" or
         _norm(item.get("source_name", "")) in _CARD_HEADERS else accounts).append(item)
    for item in card_items:
        (accounts if item.get("identity_kind") == "account" else cards).append(item)
    return (_field(row, {field: accounts}, field, issues),
            _field(row, {field: cards}, field, issues))


def _identity_proof_candidates(candidates, own_side):
    manual_fields = {field for field in ACCOUNT_CARD_FIELDS & candidates.keys()
                     if any(item.get("mapping_evidence_basis") == "manual_override" for item in candidates[field])}
    if not manual_fields:
        return candidates, ()
    filtered = candidates.copy()
    for field in manual_fields:
        filtered[field] = [item for item in candidates[field] if item.get("mapping_evidence_basis") != "manual_override"]
    fallback_roles = {field.partition("_")[0] for field in manual_fields}
    other_side = {"payer": "payee", "payee": "payer"}.get(own_side)
    # Observation interpretation can copy identifiers between the generic
    # ledger roles and the explicit parties. Apply the same scope to fallbacks.
    if own_side and {"self", own_side} & fallback_roles:
        fallback_roles.update(("self", own_side))
    if other_side and "counter" in fallback_roles:
        fallback_roles.add(other_side)
    return filtered, fallback_roles


def _amount_field(row, candidates, name, issues):
    found = []
    for candidate in candidates.get(name, []):
        amount, error = _money(_value(row, candidate), candidate)
        if error:
            issues.append("invalid_balance" if name in {"balance", "balance_before"} else error)
        if amount is not None:
            found.append(amount)
    if len(set(found)) > 1:
        issues.append("invalid_balance" if name in {"balance", "balance_before"} else "conflicting_amount")
    return found[0] if found else None


def _reference_field(row, candidates, issues):
    """Separate the source transaction ID from its independent voucher."""
    references = candidates.get("trade_no", [])
    primary = [item for item in references if item.get("reference_role") == "source_transaction_id"]
    vouchers = [item for item in references if item.get("reference_role") == "independent_voucher_reference"]
    closed_missing = any(item.get("reference_profile") in {BANK_FEEDBACK_28_REFERENCE_PROFILE, OPTIONAL_FEEDBACK_REFERENCE_PROFILE, FEEDBACK_31_REFERENCE_PROFILE, BANK_FEEDBACK_31_REFERENCE_PROFILE,
                                                          UNIFORM_FEEDBACK_37_REFERENCE_PROFILE,
                                                          UNIFORM_FEEDBACK_35_REFERENCE_PROFILE} for item in references)
    blank_indices = {index for item in references for index in item.get("reference_blank_indices", ())}
    has_extension = any(_cell_present(_value(row, {"index": index})) for index in blank_indices)
    if has_extension:
        issues.append("conflicting_field")
    def value(items):
        if closed_missing:
            # Interpret an explicit missing marker in its own reference role;
            # never edit the retained source cell or suppress its other role.
            items = [item for item in items if (text := _text(_value(row, item)))
                     and text.casefold() not in {"null", "none", "nan", "n/a"}
                     and any(character.isalnum() for character in text)]
        return _field(row, {"trade_no": items}, "trade_no", issues)
    transaction, voucher = value(primary), value(vouchers)
    if any(item.get("reference_profile") in {BANK_FEEDBACK_28_REFERENCE_PROFILE, VERIFIED_REFERENCE_POLICY}
           for item in references):
        # A voucher is independently traceable even when the primary is absent.
        return transaction, "source_transaction_id" if transaction else "none", has_extension
    # Keep the already accepted v5 value when the actual transaction ID is blank.
    reference, basis = (transaction, "source_transaction_id") if transaction else (
        (voucher, "legacy_voucher_fallback") if voucher else ("", "none"))
    return reference, basis, has_extension


def _financial_candidates(row, candidates):
    """Apply only explicitly scoped row units; never carry one row into another."""
    resolved, issues = dict(candidates), []
    for field in (*table_financial.MONEY_FIELDS, *table_contracts.MONEY_FIELDS):
        unit_columns = candidates.get(field + "_unit", [])
        if not unit_columns:
            continue
        tokens = [_unit_token(_value(row, item)) for item in unit_columns
                  if _text(_value(row, item))]
        factors = {_UNIT.get(token) for token in tokens}
        items = candidates.get(field, [])
        if field == "amount" and tokens and not items and any(candidates.get(key) for key in ("income_amt", "expense_amt")):
            # A generic amount unit does not declare which split column it
            # describes. Income, expense and balance have their own scopes.
            issues.append("unknown_unit")
        resolved[field] = []
        for item in items:
            explicit = item["unit"] != "元/默认基础单位"
            current = dict(item)
            if _text(_value(row, item)):
                if None in factors:
                    issues.append("unknown_unit")
                elif len(factors) > 1:
                    issues.append("conflicting_unit")
                elif not factors:
                    if not explicit:
                        issues.append("unknown_unit")
                else:
                    factor = next(iter(factors))
                    if explicit and item["factor"] != factor:
                        issues.append("conflicting_unit")
                    elif not explicit:
                        current.update(factor=factor, unit=tokens[0])
                    if any(token in {"人民币元", "cny"} for token in tokens):
                        current["unit_currency"] = "CNY"
            resolved[field].append(current)
    return resolved, issues


_TRADE_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_TRADE_SECOND_CLOCK = re.compile(r"[0-9]{1,2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?\Z")
_TRADE_MINUTE_DATETIME = re.compile(r"[0-9]{4}([-/])[0-9]{1,2}\1[0-9]{1,2}[ T][0-9]{1,2}:[0-9]{2}\Z")
_TRADE_DATED_SECONDS = re.compile(r"(?:[ T][0-9]{1,2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?"
                                  r"(?:Z|[+-][0-9]{2}:[0-9]{2})?|^[0-9]{14}|^[0-9]{8} [0-9]{6}|^[0-9]{6})\Z")


def _redundant_trade_clock_columns(candidates):
    items = (*candidates.get("trade_time", ()), *candidates.get("time_part", ()))
    if any(item.get("time_role") != "transaction" or item.get("time_format") for item in items):
        return None
    columns = {item["index"]: item for item in items}
    days = [item for item in columns.values() if item["source_name"] == "交易日期"]
    clocks = [item for item in columns.values() if item["source_name"] == "交易时间"]
    if len(columns) == 3 and len(days) == 1 and len(clocks) == 2:
        return days[0], clocks


def _corroborate_minute_clock(row, candidates, issues):
    columns = _redundant_trade_clock_columns(candidates)
    if columns is None:
        return None
    day_column, clock_columns = columns
    date_text = _text(_value(row, day_column))
    readings = [_text(_value(row, item)) for item in clock_columns]
    precise = next((value for value in readings if _TRADE_SECOND_CLOCK.fullmatch(value)), None)
    coarse = next((value for value in readings if _TRADE_MINUTE_DATETIME.fullmatch(value)), None)
    if precise is None:
        # A date or minute reading by itself has no explicit seconds. Other
        # seconds-bearing forms still undergo the ordinary role/time checks.
        if any(_TRADE_DATED_SECONDS.search(value) for value in readings):
            return None
        issues.append("invalid_time" if any(readings) else "missing_time")
        return []
    if coarse is None:
        # In particular, an explicitly recorded :00 remains a seconds
        # observation and must pass the ordinary exact time comparison.
        return None
    day = _time(date_text) if _TRADE_DATE.fullmatch(date_text) else None
    fine_clock = table_financial.clock(precise)
    coarse_time = _time(coarse)
    if day is None or fine_clock is None or coarse_time is None:
        issues.append("invalid_time")
        return []
    precise_time = dt.datetime.combine(day.date(), fine_clock)
    if precise_time.replace(second=0, microsecond=0) != coarse_time:
        issues.append("conflicting_time")
        return []
    return [precise_time]


def validate_corroborated_trade_clock(value, raw):
    """Recompute a v102 timestamp from the retained time-role entries."""
    try:
        columns, cells = {}, {}
        for item in json.loads(raw)["values"]:
            position = item["column_index"] - 1
            if (not 0 <= position < MAX_COLUMNS or item["basis"] != "source_wall_clock"
                    or item["role"] != "transaction"
                    or position in cells and (cells[position] != item["raw"]
                                                   or columns[position]["source_name"] != item["column"])):
                return False
            columns[position] = {"index": position, "source_name": item["column"], "time_role": item["role"]}
            cells[position] = item["raw"]
        issues = []
        # The row reader uses length as a physical column bound.
        row = [cells.get(position, "") for position in range(max(cells, default=-1) + 1)]
        result = _corroborate_minute_clock(row, {"trade_time": list(columns.values())}, issues)
        return bool(result) and not issues and value == result[0].isoformat(sep=" ")
    except (TypeError, ValueError, KeyError):
        return False


def _financial_times(row, candidates, issues, *, corroboration_notes=None):
    """Normalize every date/clock candidate before comparing semantic values."""
    if corroboration_notes is not None:
        corroborated = _corroborate_minute_clock(row, candidates, issues)
        if corroborated is not None:
            if corroborated:
                corroboration_notes[CORROBORATED_TRADE_CLOCK_FIELD] = corroborated[0].isoformat(sep=" ")
            return corroborated
    clocks, dates = [], []
    present = False
    for item in candidates.get("time_part", []):
        source = _value(row, item)
        raw = _text(source)
        if raw:
            present = True
            if item.get("time_format") and not table_time_formats.validate(
                    _raw(source), item["time_format"], date_only=_date_only(source), precise=item.get("precise_time", False)):
                issues.append("invalid_time")
                continue
            value = (table_time_formats.precise_clock(raw) if item.get("precise_time") and item.get("time_format") == "HHMMSSFFFFFF"
                     else table_financial.clock(raw))
            if value is None:
                issues.append("invalid_time")
            else:
                clocks.append(value)
    for item in candidates.get("trade_time", []):
        value = _value(row, item)
        raw = _text(value)
        if not raw:
            continue
        present = True
        if item.get("time_format") and not table_time_formats.validate(
                _raw(value), item["time_format"], date_only=_date_only(value), precise=item.get("precise_time", False)):
            issues.append("invalid_time")
            continue
        clock = table_financial.clock(raw)
        if clock is not None:
            clocks.append(clock)
        else:
            dates.append((value, item.get("time_format") in {"YYYY-MM-DD", "YYYY/MM/DD", "YYYYMMDD"}))
    if len(set(clocks)) > 1:
        issues.append("conflicting_time")
    times, kinds, instants = [], [], []
    for value, declared_date in dates:
        zoned = table_time_formats.source_time_value(_text(value))
        parsed = (_time(zoned["wall_clock"]) if any(item.get("zoned_time") for item in candidates.get("trade_time", []))
                  and zoned["basis"] == "explicit_offset" else _time(value))
        if parsed is None:
            issues.append("invalid_time")
            continue
        only_date = _date_only(value) or declared_date
        if clocks:
            if only_date:
                parsed = dt.datetime.combine(parsed.date(), clocks[0])
            elif parsed.time() != clocks[0]:
                issues.append("conflicting_time")
        times.append(parsed)
        kinds.append(only_date and not clocks)
        instants.append(zoned["utc"] if any(item.get("zoned_time") for item in candidates.get("trade_time", []))
                        and zoned["basis"] == "explicit_offset" else None)
    if not times:
        issues.append("invalid_time" if present else "missing_time")
    elif any(instants):
        if not all(instants) or len(set(instants)) > 1:
            issues.append("conflicting_time")
    elif len(set(times)) > 1:
        detailed = {value for value, date_only in zip(times, kinds) if not date_only}
        if len(detailed) == 1 and all(value.date() == next(iter(detailed)).date() for value in times):
            times = list(detailed)
        else:
            issues.append("conflicting_time")
    return times


def _zoned_fields(row, time_candidates, financial):
    zoned = [(item, table_time_formats.source_time_value(_text(_value(row, item))))
             for item in time_candidates.get("trade_time", []) if item.get("zoned_time") and _text(_value(row, item))] if financial else []
    explicit = [value for _, value in zoned if value["basis"] == "explicit_offset"]
    if explicit:
        if len(explicit) != len(zoned) or len({value["utc"] for value in explicit}) > 1:
            return {}, "conflicting_time"
        value = explicit[0]
        original = _text(_value(row, zoned[0][0]))
        return {"trade_time_utc": dt.datetime.fromisoformat(value["utc"].replace("Z", "+00:00")).replace(tzinfo=None),
                "trade_time_original": original, "trade_time_offset": value["offset"]}, None
    return {}, None


def _interpret(row, candidates, mode, *, reference_policy=None, financial=False, source_contract=None, source_roles=False,
               identity_values=None, retain_endpoint_role=False, sign_contract=None, source_ledger=False,
               corroborate_trade_clock=False, bind_named_ledger_parties=False, transaction_status_policy=None):
    issues, notes = [], {}
    if sign_contract:
        if sign_contract.get("error"):
            issues.append(sign_contract["error"])
        else:
            notes[table_sign_declarations.DETAIL_FIELD] = sign_contract["proof"]
            candidates = {field: [dict(item) for item in items] for field, items in candidates.items()}
            for item in candidates.get("amount", []):
                declared_factor = Decimal(sign_contract["factor"])
                if item["unit"] != "元/默认基础单位" and item["factor"] != declared_factor:
                    issues.append("conflicting_unit")
                elif item["unit"] == "元/默认基础单位":
                    item.update(factor=declared_factor, unit="来源声明单位")
    if financial:
        candidates, financial_issues = _financial_candidates(row, candidates)
        issues.extend(financial_issues)
    critical_fields = set(candidates) - {"summary", "channel", "feedback_reason"}
    if any(isinstance(value := _value(row, item), _WorkbookValue) and value.formula for field in critical_fields for item in candidates[field]):
        return None, "unresolved", "formula_value", {"financial_effect": "undetermined", "validation_issues": "formula_value"}
    if any((isinstance(value := _value(row, item), _WorkbookValue) and value.invalid_type)
           or (isinstance(value, _XlsxError) and any("region_parent_row" in claim
               for items in candidates.values() for claim in items))
           for field in critical_fields for item in candidates[field]):
        return None, "unresolved", "invalid_cell_type", {"financial_effect": "undetermined", "validation_issues": "invalid_cell_type"}
    if (candidates.get("feedback_reason")
            and any(_text(_value(row, item)) for item in candidates["feedback_reason"])
            and not any(_text(_value(row, item)) for field in ("trade_time", "amount", "income_amt", "expense_amt", "direction") for item in candidates.get(field, []))):
        return None, "non_transaction", "feedback_only", {"financial_effect": "not_a_flow"}
    status_candidates = candidates.get("status", [])
    if transaction_status_policy and status_candidates:
        exact = [item for item in status_candidates if item["source_name"] == source_transaction_status.COLUMN]
        if exact:
            notes[source_transaction_status.DETAIL_FIELD] = transaction_status_policy
            if any(source_transaction_status.explicitly_failed(_raw(_value(row, item))) for item in exact):
                return None, "non_transaction", "failed_transaction", {
                    "financial_effect": "not_a_flow", "reported_status": "explicit_failure",
                    source_transaction_status.DETAIL_FIELD: transaction_status_policy,
                }
            notes["reported_status"] = "unknown"
        # A mapped summary or feedback phrase is not an original status field.
        # Keep the historical semantics of other recognized status headings.
        status_candidates = [item for item in status_candidates if item not in exact
                             and _norm(item["source_name"]) in _ALIASES["status"]]
    statuses = [_text(_value(row, item)) for item in status_candidates]
    if statuses:
        status_values = {_enum(value) for value in statuses}
        code_basis = all(item.get("source_name") == "交易是否成功"
                         and item.get("numeric_status_policy", "historical") != "unknown"
                         for item in status_candidates)
        positive_statuses = {"成功", "交易成功", "success", "succeeded"} | ({"01", "1"} if code_basis else set())
        ledger_status = all(item.get("source_posting_status") for item in status_candidates)
        ledger_fields = all(any(_text(_value(row, item)) for item in candidates.get(field, []))
                            for field in ("trade_time", "amount", "balance", "direction", "trade_no"))
        ledger_owner = any(_text(_value(row, item)) for field in ("self_account", "self_card") for item in candidates.get(field, []))
        if ledger_status and ledger_fields and ledger_owner and all(not value or re.fullmatch(r"[0-9]+", value) for value in status_values):
            notes["reported_status"] = "unknown"
        elif not code_basis and status_values <= {"01", "1"} and all(
                item.get("numeric_status_policy") == "unknown" for item in status_candidates):
            # Preserve exact source observations, without inventing a platform
            # codebook or promoting the literal state to reported success.
            notes["reported_status"] = "unknown"
        elif not status_values <= positive_statuses:
            if status_values and status_values <= {"失败", "交易失败", "failed", "failure", "不成功"}:
                return None, "non_transaction", "failed_transaction", {"financial_effect": "not_a_flow", "reported_status": "explicit_failure"}
            issues.append("unknown_status")
        else:
            notes["reported_status"] = "source_reported_success"
    if financial:
        time_candidates = candidates
        corroboration_notes = notes if (corroborate_trade_clock and
                                        _redundant_trade_clock_columns(candidates)) else None
        if any("time_role" in item for field in ("trade_time", "time_part") for item in candidates.get(field, [])):
            time_candidates = {field: list(items) for field, items in candidates.items()}
            tier_transaction = any(item.get("region_source_role") == "transaction_time"
                                   for item in candidates.get("trade_time", ()))
            explicit_transaction = any(item.get("time_role") == "transaction" and _text(_value(row, item))
                and (_time(_value(row, item)) is not None and not _date_only(_value(row, item))
                     or table_time_formats.source_time_value(_text(_value(row, item)))["basis"] == "explicit_offset")
                for item in candidates.get("trade_time", []))
            transaction, posting, explicit_zone = False, False, False
            for field in ("trade_time", "time_part"):
                selected = []
                for item in candidates.get(field, []):
                    value = _text(_value(row, item))
                    role = item.get("time_role")
                    if role in {"posting", "receipt", "settlement"} or tier_transaction and role != "transaction":
                        posting |= bool(value)
                        continue
                    if explicit_transaction and item.get("time_role") == "unspecified" and not item.get("time_corroborates"):
                        continue
                    transaction |= bool(value)
                    if table_time_formats.source_time_value(value)["basis"] == "explicit_offset":
                        explicit_zone = True
                        if not item.get("zoned_time"):
                            continue
                    selected.append(item)
                time_candidates[field] = selected
            if posting and not transaction:
                issues.append("unsupported_time_role")
            if explicit_zone and not any(item.get("zoned_time") for item in time_candidates.get("trade_time", [])):
                issues.append("unsupported_timezone")
            time_issues = []
            times = _financial_times(row, time_candidates, time_issues, corroboration_notes=corroboration_notes)
            issues.extend(issue for issue in time_issues if issue != "missing_time" or not (posting and not transaction or explicit_zone))
        else:
            times = _financial_times(row, time_candidates, issues, corroboration_notes=corroboration_notes)
    else:
        times, time_kinds = [], []
        time_parts = [_text(_value(row, candidate)) for candidate in candidates.get("time_part", []) if _text(_value(row, candidate))]
        # A dedicated date and ordinary 'time' column is a supported split format.
        time_candidates = candidates.get("trade_time", [])
        date_values, clock_values = [], []
        for candidate in time_candidates:
            value = _value(row, candidate)
            raw = _text(value)
            if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?", raw):
                clock_values.append(raw)
            elif raw:
                date_values.append(value)
        time_parts += clock_values
        if len(set(time_parts)) > 1:
            issues.append("conflicting_time")
        for value in date_values:
            parsed = _time(value)
            if parsed is None:
                issues.append("invalid_time")
            elif time_parts:
                clock = _time("2000-01-01 " + time_parts[0])
                if clock is None:
                    issues.append("invalid_time")
                elif _date_only(value):
                    parsed = dt.datetime.combine(parsed.date(), clock.time())
                elif parsed.time() != clock.time():
                    issues.append("conflicting_time")
            if parsed is not None:
                times.append(parsed)
                time_kinds.append(_date_only(value) and not time_parts)
        if not times:
            issues.append("invalid_time" if date_values or time_parts else "missing_time")
        elif len(set(times)) > 1:
            # A date-only candidate may corroborate a full timestamp's calendar day.
            detailed = {value for value, date_only in zip(times, time_kinds) if not date_only}
            if len(detailed) == 1 and all(value.date() == next(iter(detailed)).date() for value in times):
                times = list(detailed)
            else:
                issues.append("conflicting_time")
    amount = _amount_field(row, candidates, "amount", issues)
    income = _amount_field(row, candidates, "income_amt", issues)
    expense = _amount_field(row, candidates, "expense_amt", issues)
    balance = _amount_field(row, candidates, "balance", issues)
    before = _amount_field(row, candidates, "balance_before", issues) if source_contract else None
    if (income is not None and income < 0) or (expense is not None and expense < 0):
        issues.append("negative_amount")
    split_direction, split_amount = None, None
    if income not in (None, 0) and expense not in (None, 0):
        issues.append("conflicting_income_expense")
    elif income not in (None, 0):
        split_direction, split_amount = "in", income
    elif expense not in (None, 0):
        split_direction, split_amount = "out", expense
    elif income is not None or expense is not None:
        split_amount = Decimal("0.00")
    direction_values = []
    for candidate in candidates.get("direction", []):
        raw = _value(row, candidate)
        if candidate.get("coded_bank_csv"):
            marker = _text(raw)
            if declared := table_source_roles.CODED_BANK_DIRECTIONS.get(marker):
                direction_values.append(declared)
            else:
                issues.append("invalid_direction" if marker else "missing_direction")
            continue
        value = _enum(raw)
        if declared_direction := candidate.get("rechecked_direction"):
            direction_values.append(declared_direction)
        elif value in _IN:
            direction_values.append("in")
        elif value in _OUT:
            direction_values.append("out")
        elif value:
            issues.append("invalid_direction")
    if len(set(direction_values)) > 1:
        issues.append("conflicting_direction")
    direction = direction_values[0] if direction_values else None
    explicit_direction = direction
    coded_amount = any(candidate.get("coded_bank_csv") for candidate in candidates.get("amount", ()))
    signed = any(candidate["signed"] and _money(_value(row, candidate), candidate)[0] is not None for candidate in candidates.get("amount", []))
    sign_direction = "in" if amount is not None and amount > 0 else "out" if amount is not None and amount < 0 else None
    if sign_contract and not sign_contract.get("error") and sign_direction:
        declared_direction = sign_contract["positive" if amount > 0 else "negative"]
        if signed and declared_direction != sign_direction:
            issues.append("conflicting_direction")
        sign_direction = declared_direction
        signed = True
    if amount is not None and amount < 0 and (not signed or mode != "B"):
        issues.append("negative_amount")
    if signed and sign_direction:
        if direction and direction != sign_direction:
            issues.append("conflicting_direction")
            if coded_amount and amount < 0:
                notes["reason"] = "原借贷标记与负金额矛盾。保留原符号、摘要和联机余额，原交易与反向事项仍须核查。"
        direction = direction or sign_direction
        notes["sign_convention"] = ("来源闭式声明：" + ("正收入、负支出" if sign_contract["positive"] == "in" else "正支出、负收入")
                                    if sign_contract and not sign_contract.get("error") else
                                    "来源借贷标记与原金额符号交叉校验" if coded_amount else "表头明示带符号金额：正收入、负支出")
    if split_direction:
        if direction and direction != split_direction:
            issues.append("conflicting_direction")
        direction = direction or split_direction
    if amount is not None and split_amount is not None and abs(amount) != split_amount:
        issues.append("conflicting_amount")
    amount = amount if amount is not None else split_amount
    if amount is None:
        issues.append("missing_amount")
    if mode == "B" and not direction and amount != 0 and not source_contract:
        issues.append("missing_direction")
    if mode is None:
        issues.append("unknown_table")
    fields, identities = {}, {}
    has_reference_extension = False
    for field in ("trade_no", "payer_name", "payer_account", "payer_bank", "payee_name", "payee_account", "payee_bank", "self_name", "self_account", "counter_name", "counter_account", "counter_bank"):
        if field == "trade_no" and reference_policy == TRANSACTION_REFERENCE_POLICY:
            fields[field], notes["trade_no_basis"], has_reference_extension = _reference_field(row, candidates, issues)
        elif source_roles and field in {"payer_account", "payee_account", "self_account", "counter_account"}:
            identities[field] = _identity_pair(row, candidates, field, issues)
            fields[field] = identities[field][0] or identities[field][1]
        else:
            fields[field] = _field(row, candidates, field, issues)
    coded_account = any(item.get("coded_bank_csv") for item in candidates.get("self_account", ()))
    if coded_account and not identities["self_account"][0]:
        issues.append("missing_identity")
    for field in ("summary", "channel"):
        source_summary = [item for item in candidates.get(field, []) if item.get("source_text_role") == "summary"]
        if field == "summary" and source_summary:
            _field(row, {field: source_summary}, field, issues)
            value = _raw(_value(row, source_summary[0]))
            fields[field] = value if isinstance(value, str) else _text(value)
        else:
            fragments = list(dict.fromkeys(_text(_value(row, item)) for item in candidates.get(field, []) if _text(_value(row, item))))
            fields[field] = "；".join(fragments)
    if not coded_account and not fields["self_account"] and candidates.get("self_card") and candidates.get("self_account"):
        # This fallback is bounded to the explicit source pair, not arbitrary cards.
        account_columns = candidates["self_account"]
        if len(account_columns) == 1 and account_columns[0].get("source_name") == "交易账号":
            fields["self_account"] = _field(row, candidates, "self_card", issues)
            notes["source_account_column"] = "交易卡号"
    card_fallbacks = set()
    if source_roles:
        # Validate cards even when an explicit account is present. Only a blank
        # account may use its independently named card; neither overwrites the
        # other and same-role duplicate candidates must still agree.
        for account, card in (("self_account", "self_card"), ("counter_account", "counter_card")):
            account_value, card_value = identities[account]
            if not account_value and card_value and not (coded_account and account == "self_account"):
                fields[account] = card_value
                card_fallbacks.add(account)
                if account == "self_account":
                    notes["source_account_column"] = next(item["source_name"] for item in (
                        *candidates.get(card, ()), *(item for item in candidates.get(account, ())
                                                   if item.get("identity_kind") == "card" or
                                                   _norm(item["source_name"]) in _CARD_HEADERS))
                        if _text(_value(row, item)))
    if fields["self_account"] and "source_account_column" not in notes:
        owner_accounts = [item for item in candidates.get("self_account", ())
                          if item.get("contract_owner") and item.get("identity_kind") == "account"
                          and _text(_value(row, item))]
        if owner_accounts:
            notes["source_account_column"] = owner_accounts[0]["source_name"]
    if fields["self_account"] and "source_account_column" not in notes:
        notes["source_account_column"] = "mapped_self_account"
    for field in ("payer_account", "payee_account", "self_account", "self_card", "counter_account", "counter_card"):
        for candidate in candidates.get(field, []):
            value = _raw(_value(row, candidate))
            if isinstance(value, (int, float)) and not isinstance(value, bool) and len(str(abs(int(value)))) > 15:
                issues.append("unsafe_account_number")
    if mode == "A" and not ((fields["payer_account"] or fields["payer_name"]) and (fields["payee_account"] or fields["payee_name"])):
        issues.append("missing_identity")
    elif not fields["self_account"] and (mode == "B" or any(
            item.get("contract_owner") for field in ("self_account", "self_card")
            for item in candidates.get(field, ()))):
        # A uniform source's owner is a required source fact in either layout.
        # Switching to party mode cannot turn a reference into the missing owner.
        issues.append("missing_identity")
    if source_contract:
        # All semantic candidates remain active; the contract was compiled once
        # for the entire region, not selected from each row's balance fit.
        after = _amount_field(row, {"balance": [item for item in candidates.get("balance", [])
                                              if item.get("same_entry_after")]}, "balance", issues)
        paired_before = before if any(item.get("same_entry_before") and _text(_value(row, item))
                                      for item in candidates.get("balance_before", [])) else None
        party_direction = None
        if mode == "A" and fields["self_account"] and "self_account" not in card_fallbacks:
            own = fields["self_account"]
            payer_account = identities["payer_account"][0] if source_roles else fields["payer_account"]
            payee_account = identities["payee_account"][0] if source_roles else fields["payee_account"]
            if own == payer_account and own != payee_account:
                party_direction = "out"
            elif own == payee_account and own != payer_account:
                party_direction = "in"
        direction, contract_issues, contract_notes = table_contracts.resolve_direction(
            source_contract, mode="B" if fields["self_account"] else mode, amount=amount, direction=explicit_direction,
            signed_direction=sign_direction if signed else None, split_direction=split_direction, party_direction=party_direction,
            before=paired_before, after=after)
        issues.extend(contract_issues)
        notes.update(contract_notes)
        if mode == "B" and not direction and amount != 0:
            issues.append("missing_direction")
    own_bank = ""
    if source_roles and direction:
        bind_parties = bind_named_ledger_parties and mode == "B"
        # A card can identify its account ledger without being equal to the
        # bank account number. A known direction attaches an explicitly named
        # party account to the same role; it never derives direction from the
        # equality or difference of two identifier kinds.
        own_side, counter_side = ("payee", "payer") if direction == "in" else ("payer", "payee")
        for account, side in (("self_account", own_side), ("counter_account", counter_side)):
            field = side + "_account"
            own_pair, party_pair = identities[account], identities[field]
            if not any(own_pair) and not (bind_parties and account == "counter_account"):
                continue
            account_conflict = bool(own_pair[0] and party_pair[0] and own_pair[0] != party_pair[0])
            own_direction_conflict = account_conflict and account == "self_account" and mode == "A"
            if own_direction_conflict:
                issues.append("conflicting_direction")
            if (account_conflict and not own_direction_conflict
                    or own_pair[1] and party_pair[1] and own_pair[1] != party_pair[1]):
                # A new same-kind conflict must not be hidden by a missing
                # identity, while earlier financial errors retain priority.
                position = next((index for index, issue in enumerate(issues)
                                 if issue in {"missing_identity", "conflicting_direction"}), len(issues))
                issues.insert(position, "conflicting_field")
            combined = tuple(own or party for own, party in zip(own_pair, party_pair))
            identities[account] = identities[field] = combined
            fields[account] = combined[0] or combined[1]
            if any(party_pair) and not own_direction_conflict:
                fields[field] = fields[account]
            if combined[0]:
                card_fallbacks.discard(account)
                if account == "self_account" and not own_pair[0]:
                    notes["source_account_column"] = next(item["source_name"] for item in candidates[field]
                        if _norm(item["source_name"]) not in _CARD_HEADERS and _text(_value(row, item)))
        if bind_parties:
            # A named payer/payee can supply the corresponding ledger fields
            # only after the original direction is known. Keep genuine conflicts.
            for field, explicit in (("self_name", own_side + "_name"),
                                    ("counter_name", counter_side + "_name"),
                                    ("counter_bank", counter_side + "_bank")):
                if fields[field] and fields[explicit] and fields[field] != fields[explicit]:
                    issues.append("conflicting_field")
                fields[field] = fields[field] or fields[explicit]
            own_bank = fields[own_side + "_bank"]
    if mode == "A" and fields["self_account"] and direction and "self_account" not in card_fallbacks:
        expected = fields["payee_account"] if direction == "in" else fields["payer_account"]
        if fields["self_account"] != expected:
            issues.append("conflicting_direction")
    currencies = []
    currency_candidates = candidates.get("currency", [])
    coded_currencies = {item["source_name"]: _text(_value(row, item))
                        for item in currency_candidates if item.get("coded_bank_csv")}
    has_reported_currency = any(coded_currencies.values())
    if coded_currencies:
        if coded_currencies == {"币种": "10", "币种名称": "人民币"}:
            currencies.append("CNY")
        else:
            issues.append("invalid_currency")
    for candidate in currency_candidates:
        if candidate.get("coded_bank_csv"):
            continue
        value = _text(_value(row, candidate))
        if not value:
            continue
        has_reported_currency = True
        currency = _CURRENCY.get(value, value.upper())
        if currency not in _ISO_CURRENCIES:
            issues.append("invalid_currency")
        else:
            currencies.append(currency)
    if len(set(currencies)) > 1:
        issues.append("invalid_currency")
    required_currency = corroborate_trade_clock or any(
        item.get("required_source_currency") for item in candidates.get("currency", []))
    source_currency_only = any(item.get("source_currency_only") for item in candidates.get("currency", []))
    currency_for_movements = not currencies and not has_reference_extension and any(
        item.get("source_currency_for_movements") for item in candidates.get("currency", []))
    if not currencies and (required_currency or amount is not None and amount != 0
            and (source_currency_only or currency_for_movements and "negative_amount" not in issues)):
        issues.append("invalid_currency")
    currency = (currencies[0] if currencies else "" if has_reported_currency or required_currency
                or source_currency_only or currency_for_movements else "CNY")
    unit_currencies = {candidate.get("unit_currency") for name in (*table_financial.MONEY_FIELDS, *(table_contracts.MONEY_FIELDS if source_contract else ())) for candidate in candidates.get(name, []) if candidate.get("unit_currency") and _text(_value(row, candidate))}
    if unit_currencies and unit_currencies != {currency}:
        issues.append("currency_unit_conflict")
    # Keep the conflict and unit checks above on their established inputs.
    # The retained source row has no currency when a conflicting extension
    # prevents admission and no valid currency was supplied.
    if has_reference_extension and not currencies and any(
            item.get("preserve_currency_on_extension_conflict") for item in candidates.get("currency", [])):
        currency = ""
    if not currencies and has_reported_currency:
        notes["currency_basis"] = "来源已报告币种，现有依据无法核实，按原始记录保留"
    elif not currencies and not required_currency:
        notes["currency_basis"] = ("来源币种未明确，按原始记录保留" if not currency
                                   else "沿用普通表格历史默认币种CNY；来源未提供币种")
    if mode == "B":
        own = (fields["self_name"], fields["self_account"], own_bank)
        counter = (fields["counter_name"], fields["counter_account"], fields["counter_bank"])
        payer, payee = (counter, own) if direction == "in" else (own, counter)
        fields.update(payer_name=payer[0], payer_account=payer[1], payer_bank=payer[2], payee_name=payee[0], payee_account=payee[1], payee_bank=payee[2])
        if source_roles:
            own_side, counter_side = ("payee", "payer") if direction == "in" else ("payer", "payee")
            identities[own_side + "_account"] = identities["self_account"]
            identities[counter_side + "_account"] = identities["counter_account"]
        if not fields["counter_account"]:
            notes["counterparty_basis"] = "来源未提供对手账号；保留为空，不以姓名代造账号"
    if source_roles and identity_values is not None:
        identity_values.update(identities)
    zoned_fields, zone_error = _zoned_fields(row, time_candidates if financial else candidates, financial)
    if source_ledger and zone_error:
        issues.insert(0, zone_error)
    issues = list(dict.fromkeys(issues))
    if source_ledger:
        from . import source_ledger as ledger
        raw_direction = next((_text(_value(row, item)) for item in candidates.get("direction", [])
                              if _text(_value(row, item))), "")
        if raw_direction in table_source_roles.CODED_BANK_DIRECTIONS and any(
                item.get("coded_bank_csv") for item in candidates.get("direction", ())):
            # The literal coded cell remains in positional source evidence.
            raw_direction = raw_direction[0]
        if (mode == "B" and amount == 0 and not direction and not raw_direction
                and any(item.get("allow_unoriented") for item in candidates.get("direction", []))):
            issues.append("missing_direction")
        snapshot = ledger.capture(fields, amount=amount, balance=balance, currency=currency,
            source_currency=currencies[0] if currencies else "", stamp=times[0] if times else None,
            direction=explicit_direction, raw_direction=raw_direction, card=_field(row, candidates, "self_card", []),
            issues=issues, signed=signed, mode=mode, reported_status=notes.get("reported_status"), before=before, zoned=zoned_fields,
            allow_unoriented=any(item.get("allow_unoriented") for item in candidates.get("direction", [])),
            allow_source_observation=any(item.get("allow_source_observation") for item in candidates.get("direction", [])),
            original_direction=next((str(_value(row, item)) for item in candidates.get("direction", [])
                                     if _value(row, item) is not None), ""))
        if snapshot:
            notes[ledger.FIELD] = snapshot
    if issues:
        return None, "unresolved", issues[0], {**notes, "validation_issues": ",".join(issues), "financial_effect": "undetermined"}
    if amount == 0:
        return None, "non_monetary", "zero_amount", {**notes, "financial_effect": "no_movement"}
    result = {key: fields[key] for key in ("trade_no", "payer_name", "payer_account", "payer_bank", "payee_name", "payee_account", "payee_bank", "self_name", "self_account", "summary", "channel")}
    result.update(trade_time=times[0], amount=abs(amount), balance=balance, currency=currency)
    if zone_error:
        return None, "unresolved", zone_error, {**notes, "financial_effect": "undetermined", "validation_issues": zone_error}
    result.update(zoned_fields)
    if retain_endpoint_role:
        notes["_resolved_own_side"] = "payee" if direction == "in" else "payer" if direction == "out" else ""
    return result, "accepted", "transaction_valid", {**notes, "financial_effect": "source_reported", "flow_sha256": flow_digest(result)}


def _role_conflict(outcome):
    notes = dict(outcome[3])
    issues = list(filter(None, notes.get("validation_issues", "").split(",")))
    primary = outcome[2] if issues and set(issues) - {"missing_identity", "conflicting_direction"} else "conflicting_field"
    notes.pop("flow_sha256", None)
    notes.pop("source_ledger_evidence", None)
    notes.update(financial_effect="undetermined", counterparty_basis=ROLE_CONFLICT_BASIS,
                 validation_issues=",".join(dict.fromkeys([primary, *issues, "conflicting_field"])))
    return None, "unresolved", primary, notes


def _direction_declarations(headers, header_row, error_cells, enabled):
    if not enabled:
        return []
    return [
        (index, heading, table_direction_recheck.directions(heading)
         if (header_row, index) not in error_cells else {})
        for index, heading in enumerate(headers) if table_direction_recheck.is_heading(heading)
    ]


def _literal_direction_code(row, index, source_row, error_cells):
    value = row[index]
    return value if type(value) is str and (source_row, index) not in error_cells else None


def _direct_direction_conflict(row, headers, candidates, declarations, *, own_side, source_row, error_cells):
    opposite = {"payer": "in", "payee": "out"}.get(own_side)
    if opposite is None:
        return ()
    for index, heading, codebook in declarations:
        code = _literal_direction_code(row, index, source_row, error_cells)
        if codebook.get(code) == opposite:
            excerpts = [table_direction_recheck.column_excerpt(index, heading, code)]
            for item in candidates.get("direction", ()):
                original_index = item["index"]
                original_code = _literal_direction_code(row, original_index, source_row, error_cells)
                if _enum(original_code) in _IN | _OUT:
                    excerpts.append(table_direction_recheck.column_excerpt(
                        original_index, headers[original_index], original_code))
                    break
            return excerpts
    return ()


def _recheck_direction(row, headers, candidates, outcome, declarations, *, header_row,
                       source_row, error_cells, interpreter, options):
    """Check contradictory declarations and retry a sole missing direction."""
    result, disposition, reason, details = outcome
    if result is not None:
        excerpts = _direct_direction_conflict(
            row, headers, candidates, declarations, own_side=details.get("_resolved_own_side"),
            source_row=source_row, error_cells=error_cells)
        if not excerpts:
            return outcome, ()
        pending = dict(details)
        for key in ("flow_sha256", "source_ledger_evidence", "_resolved_own_side"):
            pending.pop(key, None)
        pending.update(financial_effect="undetermined", direction_basis="undetermined",
                       reason="同一表头区域的收付代码声明与本行已识别方向矛盾，未计入资金统计",
                       validation_issues="conflicting_direction")
        pending[table_direction_recheck.DETAIL_FIELD] = table_direction_recheck.serialize(
            "conflicting_declaration", header_row, excerpts)
        return (None, "unresolved", "conflicting_direction", pending), excerpts
    if disposition != "unresolved":
        return outcome, ()
    issues = details.get("validation_issues", "").split(",")
    if not {"missing_direction", "invalid_direction"}.intersection(issues):
        return outcome, ()
    index, heading, codebook = declarations[0]
    code = _literal_direction_code(row, index, source_row, error_cells)
    excerpts = [table_direction_recheck.column_excerpt(index, heading, code)]
    direction = codebook.get(code)
    if len(declarations) > 1 or candidates.get("direction"):
        other_index = declarations[1][0] if len(declarations) > 1 else candidates["direction"][0]["index"]
        other = _literal_direction_code(row, other_index, source_row, error_cells)
        excerpts.append(table_direction_recheck.column_excerpt(other_index, headers[other_index], other))
        status, review_reason = "conflicting_declaration", "conflicting_direction"
    elif not codebook:
        status, review_reason = "unsupported_declaration", "invalid_direction"
    elif code is None:
        status = "non_literal_code"
        original = row[index]
        review_reason = "formula_value" if isinstance(original, _WorkbookValue) and original.formula else "invalid_direction"
    elif direction is None:
        status, review_reason = "undeclared_code", "invalid_direction"
    elif issues != ["missing_direction"]:
        status, review_reason = "validation_failed", reason
    else:
        rechecked = {**candidates, "direction": [{"index": index, "source_name": heading, "rechecked_direction": direction}]}
        retried_result, retried_disposition, retried_reason, retried_details = interpreter(row, rechecked, "B", **options)
        if retried_disposition == "accepted" and retried_result is not None:
            retried_details[table_direction_recheck.DETAIL_FIELD] = table_direction_recheck.serialize(
                "verified", header_row, excerpts, direction)
            return (retried_result, "accepted", retried_reason, retried_details), excerpts
        status = "validation_failed"
        review_reason = retried_reason if retried_disposition == "unresolved" else reason
        issues.extend(retried_details.get("validation_issues", "").split(","))
    if reason in {"missing_direction", "invalid_direction"}:
        reason = review_reason
    details = {**details,
               "validation_issues": ",".join(dict.fromkeys(filter(None, [reason, review_reason, *issues]))),
               table_direction_recheck.DETAIL_FIELD: table_direction_recheck.serialize(status, header_row, excerpts)}
    return (None, "unresolved", reason, details), excerpts


def _interpret_candidates(row, candidates, mode, *, reference_policy=None, financial=False, source_contract=None,
                          source_roles=False, retain_endpoint_role=False, sign_contract=None, source_ledger=False,
                          corroborate_trade_clock=False, bind_named_ledger_parties=False, transaction_status_policy=None):
    """Resolve explicit role layouts, never rank them by row acceptance rate.

    Financial validation stays in the established interpreter. Only missing
    identities can be completed from another explicitly directed layout.
    """
    selected_identities = {}
    selected = _interpret(row, candidates, mode, reference_policy=reference_policy, financial=financial, source_contract=source_contract, source_roles=source_roles, identity_values=selected_identities, retain_endpoint_role=retain_endpoint_role, sign_contract=sign_contract, source_ledger=source_ledger,
                          corroborate_trade_clock=corroborate_trade_clock, bind_named_ledger_parties=bind_named_ledger_parties,
                          transaction_status_policy=transaction_status_policy)
    party_fields = tuple(side + "_" + field for side in ("payer", "payee") for field in ("name", "account", "bank"))
    if selected[1] == "unresolved":
        identity_issues = []
        for field in (*party_fields, "self_name", "self_account", "self_card", "counter_name", "counter_account", "counter_card", "counter_bank"):
            if len(candidates.get(field, [])) > 1:
                if source_roles and field in {"payer_account", "payee_account", "self_account", "counter_account"}:
                    _identity_pair(row, candidates, field, identity_issues)
                else:
                    _field(row, candidates, field, identity_issues)
        if "conflicting_field" in identity_issues:
            return _role_conflict(selected)
    has_owner = candidates.get("self_account") or source_roles and candidates.get("self_card")
    has_counter = any(candidates.get("counter_" + field) for field in ("name", "account", "bank")) or source_roles and candidates.get("counter_card")
    if (not has_owner or not has_counter and not (source_roles and candidates.get("self_card"))
            or not any(candidates.get(field) for field in party_fields)
            or selected[1] in {"non_transaction", "non_monetary"}):
        return selected
    peer_identities = selected_identities if mode == "B" else {}
    peer = selected if mode == "B" else _interpret(row, candidates, "B", reference_policy=reference_policy, financial=financial, source_contract=source_contract, source_roles=source_roles, identity_values=peer_identities, retain_endpoint_role=retain_endpoint_role, sign_contract=sign_contract, source_ledger=source_ledger,
                          corroborate_trade_clock=corroborate_trade_clock, bind_named_ledger_parties=bind_named_ledger_parties,
                          transaction_status_policy=transaction_status_policy)
    if peer[0] is not None:
        merged = dict(peer[0])
        for field in party_fields:
            if source_roles and field in peer_identities:
                explicit = _identity_pair(row, candidates, field, [])
                projected = peer_identities[field]
                if any(left and right and left != right for left, right in zip(explicit, projected)):
                    return _role_conflict(selected)
                merged[field] = projected[0] or explicit[0] or projected[1] or explicit[1]
                continue
            value = _field(row, candidates, field, [])
            if value and merged[field] and value != merged[field]:
                return _role_conflict(selected)
            if value:
                merged[field] = value
        # A missing source identity is the only repair permitted here. In
        # particular, signed negatives rejected in A do not become B successes.
        issues = set(filter(None, selected[3].get("validation_issues", "").split(",")))
        if selected[0] is not None or selected[1] == "unresolved" and issues and issues <= {"missing_identity", "conflicting_direction"}:
            return merged, "accepted", "transaction_valid", {**peer[3], "flow_sha256": flow_digest(merged)}
        return selected
    if selected[0] is None or mode != "A":
        return selected
    # A already names both parties. Test every possible orientation of sparse
    # B evidence; if more than one remains, keep the explicit A facts. Do not
    # infer a missing direction for a B-only ledger.
    own = {"name": selected[0]["self_name"], **({"account": selected[0]["self_account"]} if not source_roles else {})}
    counter = {field: _field(row, candidates, "counter_" + field, []) for field in (
        ("name", "bank") if source_roles else ("name", "account", "bank"))}
    directions = set()
    for item in candidates.get("direction", []):
        value = _enum(_value(row, item))
        if value in _IN | _OUT:
            directions.add("in" if value in _IN else "out")
    for field, direction in (("income_amt", "in"), ("expense_amt", "out")):
        amount = _amount_field(row, candidates, field, [])
        if amount is not None and amount > 0:
            directions.add(direction)
    for item in candidates.get("amount", []):
        if item["signed"]:
            amount, error = _money(_value(row, item), item)
            if not error and amount:
                directions.add("in" if amount > 0 else "out")
    for direction in directions or {"in", "out"}:
        own_side, counter_side = ("payee", "payer") if direction == "in" else ("payer", "payee")
        if source_roles:
            pairs = ((selected_identities["self_account"], selected_identities[own_side + "_account"]),
                     (selected_identities["counter_account"], selected_identities[counter_side + "_account"]))
            if (all(not left or not right or left == right for expected, actual in pairs
                    for left, right in zip(expected, actual))
                    and all(not value or not selected[0][side + "_" + field] or value == selected[0][side + "_" + field]
                            for side, values in ((own_side, own), (counter_side, counter))
                            for field, value in values.items() if field != "account")):
                return selected
            continue
        if all(not value or not selected[0][side + "_" + field] or value == selected[0][side + "_" + field]
               for side, fields in ((own_side, own), (counter_side, counter)) for field, value in fields.items()):
            return selected
    return _role_conflict(selected)


def _tiered_candidates(candidates, proof, profile):
    """Bind roles to the paired cells, without assigning generic leaves alone."""
    grouped = proof["version"] == 2
    if grouped:
        proved = {column - 1 for column, *_ in proof["columns"]}
        covered = {column for left, _, right, _ in proof["ranges"] for column in range(left - 1, right)}
        resolved = {}
        for field, items in candidates.items():
            outside = [item for item in items if item["index"] not in proved]
            if any(item["index"] in covered for item in outside):
                return None
            if outside:
                resolved[field] = outside
    else:
        resolved = {field: list(items) for field, items in candidates.items()}
    for item in resolved.get("time_part", ()):
        # Explicit transaction clocks corroborate the paired date. Generic
        # clock aliases still have no authority to supply transaction time.
        declaration = _declared_time_header(item["source_name"], profile)
        base = declaration[0] if declaration else _norm(item["source_name"])
        if base in {"交易时间时分秒", "transactionclock"}:
            item["time_role"] = "transaction"
    for index, field, role, unit, factor, _parent, leaf in table_tiered_headers.candidate_columns(proof):
        for other_field, items in resolved.items():
            for item in list(items):
                if item["index"] != index:
                    continue
                if (other_field != field and not (other_field == "self_account"
                        and field.endswith("_account") and table_tiered_headers.generic_account(leaf))):
                    return None
                items.remove(item)
        item = {"index": index, "factor": Decimal(factor), "unit": unit or "元/默认基础单位",
                "unit_currency": "CNY" if unit == "人民币元" else "", "signed": False,
                "basis": "verified_region_header", "region_parent_row": proof["parent_row"],
                "region_source_role": role}
        if grouped:
            item.update(source_name=leaf, grouped_header=True)
            if field in {"self_account", "counter_account"} and _norm(leaf) in {"本方账卡", "本方账卡号", "对方账卡", "对方账卡号"}:
                item["mixed_identifier"] = True
            if field == "currency":
                item["required_source_currency"] = True
        if field == "trade_time":
            item.update(time_role="transaction" if role == "transaction_time" else "posting",
                        zoned_time=profile in ZONED_PROFILES, time_corroborates=False)
        elif grouped and field == "time_part":
            item.update(time_role="transaction", time_corroborates=False)
        resolved.setdefault(field, []).append(item)
    return {field: sorted(items, key=lambda item: item["index"]) for field, items in resolved.items() if items}


def _coded_bank_csv_columns(headers, candidates):
    for field, names in _CODED_BANK_CSV_ROLES.items():
        options = candidates.setdefault(field, [])
        for name in names:
            index = headers.index(name)
            item = next((item for item in options if item["index"] == index), None)
            if item is None:
                item = {"index": index, "factor": Decimal(1), "unit": "元/默认基础单位", "signed": False,
                        "unit_currency": "", "basis": "explicit_source_contract"}
                options.append(item)
            item["coded_bank_csv"] = True
            if field == "amount":
                item["signed"] = True
            elif field == "currency":
                item["required_source_currency"] = True


def _region(headers, mode, mapping, *, strict, reference_policy=None, voucher_reference=False,
            profile=IDENTITY_PROFILE, tiered_proof=None, manual_mapping_evidence=False):
    """Resolve labels against this region's positions, never an earlier region."""
    if tiered_proof is not None:
        headers = ["" if isinstance(header, _XlsxError) else header for header in headers]
    candidates = _candidates(headers, profile)
    header_names = tuple(_text(_header_value(header, profile)) for header in headers)
    signature = frozenset(header_names)
    coded_bank_csv = profile == CODED_BANK_CSV_TRIAL_PROFILE and table_source_roles.is_coded_bank_csv(header_names)
    if coded_bank_csv:
        _coded_bank_csv_columns(header_names, candidates)
    automatic_uniform37 = (profile in CLOSED_BANK_PROFILES and len(headers) == len(UNIFORM_FEEDBACK_37_COLUMNS)
                           and signature == UNIFORM_FEEDBACK_37_COLUMNS)
    feedback39_reference_family = profile in VOUCHER_REFERENCE_PROFILES and is_bank_feedback39(header_names)
    if profile in TRIAL_XLSX_PROFILES and feedback39_reference_family:
        candidates["self_name"] = [{"index": header_names.index("主户名"), "factor": Decimal(1),
                                    "unit": "元/默认基础单位", "signed": False, "unit_currency": "",
                                    "basis": "explicit_identity_role", "literal_holder_name": True}]
    if profile in BANK_SPLIT_LEDGER_PROFILES and is_bank_split_ledger(header_names, candidates):
        candidates["self_account"] = [{"index": header_names.index("账户号"), "factor": Decimal(1),
                                       "unit": "元/默认基础单位", "signed": False, "unit_currency": "",
                                       "basis": "explicit_identity_role", "identity_kind": "account"}]
        for item in candidates["trade_time"]:
            if (name := header_names[item["index"]]) in {"交易日期", "交易时间"}:
                item["compact_ledger_column"] = name
        for item in candidates["currency"]:
            if header_names[item["index"]] == "币种":
                item["required_source_currency"] = True
    reference_names = {_norm(_text(headers[item["index"]])) for item in candidates.get("trade_no", [])}
    voucher_names = frozenset((_norm("凭证号"), _norm("凭证编号")))
    separable_references = (profile in VOUCHER_REFERENCE_PROFILES
        and _norm("交易流水号") in reference_names and bool(reference_names & voucher_names))
    # Complete source schema, including identity/direction/status columns.
    # Repeated role columns remain validators. Additional unknown columns must
    # be empty on every admitted row; their names confer no reference authority.
    contract_names = tuple(_header_parts(header, profile)[0] for header in headers)
    contract_signature = frozenset(contract_names)
    from .feedback_import import COLUMNS as native_columns
    bank31_names = {_norm(name) for name in BANK_FEEDBACK_31_COLUMNS}
    # The feedback-result label remains a source-family marker, even when its
    # cells are empty; an unknown replacement cannot authorize blank statuses.
    bank31_variant = (profile in BANK31_VARIANT_PROFILES
        and {_norm(name) for name in (BANK_FEEDBACK_31_COLUMNS - _FEEDBACK_OPTIONAL) | {"查询反馈结果原因"}} <= contract_signature)
    bank31_blank_indices = tuple(index for index, name in enumerate(contract_names) if name not in bank31_names)
    if bank31_variant and bank31_blank_indices:
        from . import event_resolution
        # Extra semantic declarations keep their existing parser path. Only
        # unrecognized, empty extension cells can extend this source family.
        semantic_indices = {item["index"] for items in candidates.values() for item in items}
        event_names = set(event_resolution.HEADERS.values()) | event_resolution.POST_BALANCE_HEADERS | {"记账顺序号"}
        bank31_variant = not any(index in semantic_indices or header_names[index] in event_names
                                 for index in bank31_blank_indices)
    closed_bank31 = (profile in SOURCE_CONTRACT_PROFILES and contract_signature == bank31_names or bank31_variant)
    explicit_reference_roles = (voucher_reference is True and separable_references and not automatic_uniform37
                                and not (profile in BANK_SOURCE_PROFILES and closed_bank31))
    if closed_bank31:
        for index, name in enumerate(contract_names):
            if name == _norm("交易方户名") and index not in {item["index"] for item in candidates.get("self_name", [])}:
                candidates.setdefault("self_name", []).append({"index": index, "factor": Decimal(1),
                    "unit": "元/默认基础单位", "signed": False, "basis": "explicit_identity_role"})
    native_typed_family = profile in TIME_ROLE_PROFILES and contract_signature == {_norm(name) for name in native_columns}
    closed_native28 = (profile in NATIVE_OBSERVATION_PROFILES and len(headers) == len(native_columns)
                       and contract_signature == {_norm(name) for name in native_columns})
    closed_unoriented31 = (profile in NATIVE31_PROFILES and len(headers) == len(bank31_names)
                           and contract_signature == bank31_names)
    if closed_native28 or closed_unoriented31:
        for item in candidates.get("direction", []):
            item["allow_unoriented"] = profile in UNORIENTED_PROFILES
            item["allow_source_observation"] = profile in SOURCE_OBSERVATION_PROFILES
    if closed_native28:
        for item in candidates.get("currency", []):
            item["required_source_currency"] = True
    if profile in BANK_SOURCE_PROFILES and closed_bank31:
        for item in candidates.get("currency", []):
            item["source_currency_for_movements"] = True
            if profile in BANK_SOURCE_CURRENCY_PROFILES:
                item["preserve_currency_on_extension_conflict"] = True
    elif ((profile in CLOSED_BANK_PROFILES or profile in AUXILIARY_ROWS_ARCHIVE_PROFILES)
            and len(headers) == len(BANK_FEEDBACK_31_COLUMNS) and signature == BANK_FEEDBACK_31_COLUMNS):
        for item in candidates.get("currency", []):
            item["source_currency_only"] = True
    if native_typed_family:
        type_indices = tuple(index for index, name in enumerate(contract_names) if name == _norm("凭证种类"))
        for item in candidates.get("trade_no", []):
            item["native_voucher_type_indices"] = type_indices
    known_feedback = {_norm(name) for name in FEEDBACK_31_COLUMNS | _FEEDBACK_OPTIONAL}
    optional_references = (profile in OPTIONAL_REFERENCE_PROFILES
        and {_norm(name) for name in _FEEDBACK_REQUIRED} <= contract_signature)
    feedback_references = (closed_native28 or closed_bank31 or optional_references or profile in REFERENCE_ROLE_PROFILES and signature == FEEDBACK_31_COLUMNS)
    source_role = table_source_roles.classify(
        [_text(_header_value(header, profile)) for header in headers], query_conditions=profile in QUERY_OWNER_PROFILES
    ) if profile in SOURCE_ROLE_PROFILES else None
    query_owner = profile in QUERY_OWNER_PROFILES and source_role in {
        "transaction_explicit", "transaction_query_only"}
    labels = _columns(headers, profile)
    for items in candidates.values():
        for candidate in items:
            candidate["source_name"] = _text(headers[candidate["index"]])
    if (closed_bank31 or closed_native28) and profile in SOURCE_TEXT_PROFILES:
        for item in candidates.get("summary", []):
            item["source_text_role"] = "summary" if contract_names[item["index"]] == _norm("摘要说明") else "remark"
    if profile in NATIVE_OBSERVATION_PROFILES and (closed_bank31 or closed_native28):
        for index, name in enumerate(contract_names):
            role = {_norm("传票号"): "source_slip_reference", _norm("日志号"): "source_log_reference"}.get(name)
            if role and index not in {item["index"] for item in candidates.get("trade_no", [])}:
                candidates.setdefault("trade_no", []).append({"index": index, "source_name": _text(headers[index]),
                    "factor": Decimal(1), "unit": "元/默认基础单位", "signed": False, "reference_role": role})
    error = None
    proved_columns = {}
    if tiered_proof is not None:
        tiered = (_tiered_candidates(candidates, tiered_proof, profile)
                  if tiered_proof["status"] == "verified" else None)
        has_transaction_time = tiered and any(item.get("time_role") != "posting" for item in tiered.get("trade_time", ()))
        if not has_transaction_time or not _shape(tiered, profile):
            tiered_proof["status"] = "ambiguous"
            return {}, labels, None, {}, "ambiguous_region_header", None
        candidates = tiered
        proved_columns = {index: field for index, field, *_ in table_tiered_headers.candidate_columns(tiered_proof)}
        if tiered_proof["version"] == 2:
            proved_columns.update({column - 1: role for column, _, _, role, _ in tiered_proof["columns"]
                                   if role == "available_balance"})
    recognized = {}
    if manual_mapping_evidence and mapping:
        recognized = {field: (items[0]["index"], _mapping_basis(items[0]))
                      for field, items in candidates.items() if len(items) == 1}
    pinned_mapping = {}
    for field, column in (mapping or {}).items():
        if field not in (mapping_fields(profile) if profile in CONTRACT_PROFILES else FINANCIAL_MAPPING_FIELDS if profile == FINANCIAL_PROFILE else _ALIASES) or not isinstance(column, str):
            raise TableImportError("表格映射字段无效")
        matches = [index for index, label in enumerate(labels) if label == column]
        if len(matches) != 1:
            if strict:
                raise TableImportError("表格映射列不存在或不唯一")
            error = "region_mapping_conflict"
            continue
        index = matches[0]
        if proved_columns.get(index, field) != field or field in proved_columns.values() and proved_columns.get(index) != field:
            if strict:
                raise TableImportError("映射与原表两层表头的职责冲突")
            error = "region_mapping_conflict"
            continue
        if index in proved_columns:
            pinned_mapping[field] = column
            continue
        if query_owner and table_source_roles.query_condition(_text(headers[index])):
            if strict:
                raise TableImportError("查询条件注记不是交易事实字段")
            error = "region_mapping_conflict"
            continue
        identity = next((item for items in candidates.values() for item in items
                         if item["index"] == index and item.get("contract_owner")), None)
        if query_owner and (
                field in {"self_account", "self_card"} and identity is None
                or identity is not None and (field not in {"self_account", "self_card", "payer_account", "payee_account"}
                                             or field == "self_card" and identity["identity_kind"] != "card")):
            if strict:
                raise TableImportError("统一交易明细的本方映射必须保留本方账号、卡号职责")
            error = "region_mapping_conflict"
            continue
        pinned_mapping[field] = column
        # Repreview repeats the source holder column when another field changes.
        if any(item["index"] == index and (item.get("literal_holder_name") or item.get("coded_bank_csv"))
               for item in candidates.get(field, ())):
            continue
        if coded_bank_csv and field in {"self_account", "self_card", "counter_account", "trade_no"}:
            if strict:
                raise TableImportError("已证本方、对方或流水列不支持改指其他物理列")
            error = "region_mapping_conflict"
            continue
        base, factor, unit = _header_parts(headers[index], profile)
        explicit = {"index": index, "factor": factor, "unit": unit, "signed": base in _SIGNED, "source_name": _text(headers[index]), "unit_currency": "CNY" if unit in {"人民币元", "cny"} else ""}
        if profile in TIME_ROLE_PROFILES and field in {"trade_time", "time_part"}:
            explicit["time_role"] = _source_time_role(base)
            explicit["time_corroborates"] = profile in ZONED_PROFILES and base in _TIME_CORROBORATION_HEADERS
            explicit["zoned_time"] = profile in ZONED_PROFILES
        if query_owner and identity:
            explicit.update(identity_kind=identity["identity_kind"], contract_owner=True)
        declaration = _declared_time_header(headers[index], profile)
        if declaration and field in {"trade_time", "time_part"}:
            explicit["time_format"] = declaration[1]
            explicit["precise_time"] = profile in PRECISE_PROFILES
        if profile in FINANCIAL_PROFILES and field in {
                *table_financial.MONEY_FIELDS, "trade_time", "time_part", "direction",
                "currency", "status", "amount_unit", "income_amt_unit", "expense_amt_unit", "balance_unit"}:
            explicit["basis"] = "explicit_financial_header"
        if profile in CONTRACT_PROFILES:
            explicit.update(same_entry_after=field == "balance" and base in _AFTER_HEADERS,
                            same_entry_before=field == "balance_before" and base in _BEFORE_HEADERS)
            if field in {"balance_before", "balance_before_unit"}:
                explicit["basis"] = "explicit_source_contract"
        # An override still cannot suppress other monetary alias candidates.
        if (query_owner and field in {"self_account", "self_card"}) or field in ({"amount", "income_amt", "expense_amt", "balance"} |
                     ({"balance_before", "balance_before_unit"} if profile in CONTRACT_PROFILES else set()) |
                     ({"trade_time", "time_part", "direction", "currency", "status", "amount_unit", "income_amt_unit", "expense_amt_unit", "balance_unit"} if profile in FINANCIAL_PROFILES else set())):
            if index not in {item["index"] for item in candidates.get(field, [])}:
                candidates.setdefault(field, []).append(explicit)
        elif (feedback_references or native_typed_family or feedback39_reference_family or explicit_reference_roles
              or separable_references and not query_owner) and field == "trade_no":
            reference_name = contract_names[index] if optional_references or native_typed_family else explicit["source_name"]
            if reference_name != "交易流水号":
                raise TableImportError("来源合同的主交易号映射不能替换为独立凭证")
            # Repreview's derived primary mapping cannot erase voucher or
            # additional same-role validators from the source contract.
        else:
            candidates[field] = [explicit]
    if manual_mapping_evidence:
        for field, column in pinned_mapping.items():
            for item in candidates.get(field, ()):
                if labels[item["index"]] == column:
                    automatic_index, automatic_basis = recognized.get(field, (None, None))
                    item["mapping_evidence_basis"] = (
                        automatic_basis if item["index"] == automatic_index else "manual_override"
                    )
    actual_mode = mode or ("B" if coded_bank_csv else _shape(candidates, profile))
    if not actual_mode and source_role == "transaction_query_only":
        actual_mode = "B"
    current_mapping = {field: labels[items[0]["index"]] for field, items in candidates.items() if len(items) == 1}
    if profile in FINANCIAL_PROFILES:
        # Multiple candidates remain active validators, while the explicit
        # choice must survive repreview/commit so custom columns are not lost.
        current_mapping.update(pinned_mapping)
    active_reference_policy = None
    header_names = tuple(_text(_header_value(header, profile)) for header in headers)
    signature = frozenset(header_names)
    # The physical XLSX reader pads every row to the worksheet's actual width.
    # This observed form has exactly the 35 business labels followed by two
    # empty coordinates; every transaction must also keep both cells empty.
    uniform35_padding = (profile in EVIDENCE_PROFILES and len(header_names) == 37
                         and frozenset(header_names[:35]) == UNIFORM_FEEDBACK_35_COLUMNS
                         and not any(header_names[35:]))
    closed_reference_family = (feedback39_reference_family
                               or len(headers) == len(BANK_FEEDBACK_32_COLUMNS) and signature == BANK_FEEDBACK_32_COLUMNS
                               or profile in EVIDENCE_PROFILES and len(headers) == len(BANK_FEEDBACK_31_COLUMNS)
                               and signature == BANK_FEEDBACK_31_COLUMNS
                               or profile in EVIDENCE_PROFILES and len(headers) == len(UNIFORM_FEEDBACK_37_COLUMNS)
                               and signature == UNIFORM_FEEDBACK_37_COLUMNS
                               or profile in EVIDENCE_PROFILES and len(headers) == len(UNIFORM_FEEDBACK_35_COLUMNS)
                               and signature == UNIFORM_FEEDBACK_35_COLUMNS
                               or uniform35_padding)
    if explicit_reference_roles:
        # The caller asked for this layout explicitly (repreview), so role the
        # references apart without inferring anything from cell values. The
        # choice is recorded per row as trade_no_basis.
        active_reference_policy = TRANSACTION_REFERENCE_POLICY
        for item in candidates.get("trade_no", []):
            name = _norm(_text(item.get("source_name", "")))
            item["reference_role"] = ("independent_voucher_reference" if name in voucher_names
                                      else "source_slip_reference" if name == _norm("传票号")
                                      else "source_log_reference" if name == _norm("日志号")
                                      else "source_transaction_id")
        current_mapping["trade_no"] = labels[next(item["index"] for item in candidates["trade_no"]
                                                  if item.get("reference_role") == "source_transaction_id")]
    elif (coded_bank_csv or automatic_uniform37 or feedback39_reference_family or feedback_references or (reference_policy == TRANSACTION_REFERENCE_POLICY
            and closed_reference_family)
            or source_role in {"transaction_explicit", "transaction_query_only"} and not (mapping and "trade_no" in mapping)):
        active_reference_policy = TRANSACTION_REFERENCE_POLICY
        for item in candidates.get("trade_no", []):
            if item.get("reference_role") in {"source_slip_reference", "source_log_reference"}:
                continue
            source_name = contract_names[item["index"]] if closed_native28 or optional_references or bank31_variant else item["source_name"]
            item["reference_role"] = ("source_transaction_id" if source_name == ("交易流水" if coded_bank_csv else "交易流水号")
                                      else "independent_voucher_reference")
            if closed_native28:
                item["reference_profile"] = BANK_FEEDBACK_28_REFERENCE_PROFILE
            elif closed_bank31:
                item["reference_profile"] = BANK_FEEDBACK_31_REFERENCE_PROFILE
                if bank31_variant:
                    item["reference_blank_indices"] = bank31_blank_indices
            elif optional_references:
                item["reference_profile"] = OPTIONAL_FEEDBACK_REFERENCE_PROFILE
                item["reference_blank_indices"] = tuple(index for index, name in enumerate(contract_names)
                                                       if name not in known_feedback)
            elif feedback_references:
                item["reference_profile"] = FEEDBACK_31_REFERENCE_PROFILE
            elif closed_bank31 or profile in EVIDENCE_PROFILES and signature == BANK_FEEDBACK_31_COLUMNS:
                item["reference_profile"] = BANK_FEEDBACK_31_REFERENCE_PROFILE
            elif automatic_uniform37 or profile in EVIDENCE_PROFILES and signature == UNIFORM_FEEDBACK_37_COLUMNS:
                item["reference_profile"] = UNIFORM_FEEDBACK_37_REFERENCE_PROFILE
                item["reference_blank_indices"] = tuple(index for index, header in enumerate(headers)
                                                       if _text(_header_value(header, profile)) in {"Unnamed: 35", "Unnamed: 36"})
            elif profile in EVIDENCE_PROFILES and signature == UNIFORM_FEEDBACK_35_COLUMNS or uniform35_padding:
                item["reference_profile"] = UNIFORM_FEEDBACK_35_REFERENCE_PROFILE
                if uniform35_padding:
                    item["reference_blank_indices"] = (35, 36)
        current_mapping["trade_no"] = labels[next(item["index"] for item in candidates["trade_no"]
                                                  if item["reference_role"] == "source_transaction_id")]
        if profile in NATIVE_OBSERVATION_PROFILES and (closed_bank31 or closed_native28):
            candidates["voucher_type"] = [{"index": index, "source_name": _text(headers[index]),
                "factor": Decimal(1), "unit": "元/默认基础单位", "signed": False,
                "reference_role": "independent_voucher_type"}
                for index, name in enumerate(contract_names) if name == _norm("凭证种类")]
    if closed_bank31 or closed_native28:
        for item in candidates.get("status", []):
            item["source_posting_status"] = True
    if profile in PRECISE_PROFILES:
        # A structural schema match is not a verified platform codebook.
        # Keep numeric source observations without asserting reported success.
        for item in candidates.get("status", []):
            item["numeric_status_policy"] = "unknown"
    if _unsupported_time_declaration(headers, profile):
        error = "invalid_time"
    return candidates, labels, actual_mode, current_mapping, error, active_reference_policy


def _later_header(row, candidates, profile=IDENTITY_PROFILE):
    # Keep the fast path for ordinary dated financial rows. A complete new
    # header may put a date-named extra column at the previous time coordinate;
    # v16 checks that structure only when the old monetary fields cannot parse.
    has_old_time = any(_time(_value(row, item)) is not None for item in candidates.get("trade_time", []))
    if has_old_time:
        if profile not in REORDERED_TIME_PROFILES or any(
                _money(_value(row, item), item)[0] is not None
                for field in ("amount", "income_amt", "expense_amt")
                for item in candidates.get(field, [])):
            return False
    monetary_fields = ("amount", "income_amt", "expense_amt", "balance")
    has_old_money = any(_money(_value(row, item), item)[0] is not None
                        for field in monetary_fields for item in candidates.get(field, []))
    if has_old_money and profile not in REGION_HEADER_PROFILES:
        return False
    time_aliases = _ALIASES["trade_time"] | (_FINANCIAL_ALIASES["trade_time"] if profile in FINANCIAL_PROFILES else set())
    if not any(_norm(value) in time_aliases or _declared_time_header(value, profile) for item in row
               if (value := _text(item)) and not value[0].isdigit() and len(value) <= 128):
        return False
    found = _candidates(row, profile)
    money_fields = ("amount", "income_amt", "expense_amt")
    if not found.get("trade_time") or not any(found.get(key) for key in money_fields):
        return False
    if _shape(found, profile):
        return True
    # A complete new schema can move the amount column and put a numeric
    # unrelated header at its old coordinate. It must take precedence over
    # that old coordinate's monetary value. Partial label-like data still
    # cannot revoke a region when its current monetary cell is populated.
    if has_old_time or has_old_money:
        return False
    # A partial schema can revoke the old interpretation only when its actual
    # time and money coordinates contain those header labels. Words such as
    # "date" in an account and "amount" in a memo are not a regional boundary,
    # including when both the real time and amount cells are empty.
    old_time = {item["index"] for item in candidates.get("trade_time", [])}
    old_money = {item["index"] for key in money_fields for item in candidates.get(key, [])}
    new_time = {item["index"] for item in found["trade_time"]}
    new_money = {item["index"] for key in money_fields for item in found.get(key, [])}
    return bool(old_time & new_time and old_money & new_money)


def _overlapping_header(row, candidates, profile):
    """Opted-in profiles protect schemas reusing party coordinates.

    Header words wholly in extra cells remain ordinary transaction data. Account
    text itself is legal; neither the date/amount fast path nor schema priority
    proves which interpretation was intended when the core coordinates overlap.
    """
    if profile not in OVERLAPPING_HEADER_PROFILES:
        return False
    old_positions = {item["index"] for field in _REGION_IDENTITIES for item in candidates.get(field, [])}
    if not any(_norm(row[index]) in _REGION_IDENTITY_HEADERS
               or identity_field(_norm(row[index])) in _REGION_IDENTITIES
               for index in old_positions if index < len(row)):
        return False
    if not any(_time(_value(row, item)) is not None for item in candidates.get("trade_time", [])):
        return False
    if not any(_money(_value(row, item), item)[0] is not None
               for field in ("amount", "income_amt", "expense_amt")
               for item in candidates.get(field, [])):
        return False
    found = _candidates(row, profile)
    new_positions = {item["index"] for field in _REGION_IDENTITIES for item in found.get(field, [])}
    return bool(old_positions & new_positions and _shape(found, profile))


def _remember_layout(layouts, candidates):
    # Retain only actual competing financial coordinates, once per layout.
    # This is not a search over arbitrary columns or combinations of mappings.
    fields = ("trade_time", "time_part", "amount", "income_amt", "expense_amt",
              "amount_unit", "income_amt_unit", "expense_amt_unit")
    layout = {field: candidates[field] for field in fields if field in candidates}
    key = tuple((field, tuple((item["index"], item.get("factor"), item.get("unit"),
                              item.get("time_format")) for item in items))
                for field, items in layout.items())
    layouts.setdefault(key, layout)


def _unmistakable_header(row, profile, layouts):
    if not _shape(_candidates(row, profile), profile):
        return False
    for layout in layouts.values():
        resolved, _ = _financial_candidates(row, layout)
        if (_financial_times(row, resolved, [])
                and any(_money(_value(row, item), item)[0] is not None
                        for field in ("amount", "income_amt", "expense_amt")
                        for item in resolved.get(field, []))):
            return False
    return True


def _mapping_basis(item):
    return item.get("reference_role", item.get("source_text_role", item.get("basis", "exact_header_alias")))


def _region_evidence(headers, candidates, labels, path, header_row):
    return [{"field": "voucher_reference" if item.get("reference_role") == "independent_voucher_reference" else item.get("source_text_role", field),
             "column": labels[item["index"]], "column_index": item["index"] + 1,
             "unit": item["unit"], "unit_factor": str(item["factor"]) if item["factor"] is not None else "",
             "candidate_count": (sum(other.get("reference_role") == item["reference_role"] for other in items) if "reference_role" in item else
                                 sum(other.get("source_text_role") == item["source_text_role"] for other in items) if "source_text_role" in item else len(items)),
             "basis": item.get("mapping_evidence_basis") or _mapping_basis(item), "member_path": path,
             "header_row": header_row,
             **({"time_format": item["time_format"]} if "time_format" in item else {}),
             **({"parent_row": item["region_parent_row"], "source_role": item["region_source_role"]}
                if "region_parent_row" in item else {})}
            for field, items in candidates.items() for item in items]


def _row_field_evidence(headers, row, candidates, disposition, direction_excerpts=(), *,
                        combined_column=None, split_conflict=False):
    """Bounded source excerpts for role checking; the retained original is complete."""
    evidence = [{"field": "direction", "role": "direction", **entry} for entry in direction_excerpts
                if entry["column"] is not None and entry["raw"] is not None]
    limited = len(evidence) != len(direction_excerpts)
    if combined_column is not None:
        raw = _raw(row[combined_column])
        if isinstance(raw, str) and raw:
            entry = {"field": "counterparty_text", "role": "counterparty_text", "column": _text(headers[combined_column]),
                     "column_index": combined_column + 1, "raw": raw}
            if len(json.dumps([*evidence, entry], ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 2048:
                evidence.append(entry)
            else:
                limited = True
    for field, items in candidates.items():
        coded_evidence = field in {"direction", "amount", "balance", "summary", "currency"} and any(
            item.get("coded_bank_csv") for item in items)
        if (not coded_evidence and not (split_conflict and field in {"income_amt", "expense_amt"})
                and field not in {"trade_no", "voucher_type", "time_role_declaration"}
                and not any(item.get("source_text_role") for item in items)
                and not (disposition == "unresolved" and len(items) > 1)):
            continue
        for item in items:
            index = item["index"]
            value = _value(row, item)
            raw = value if isinstance(value, str) else _text(value)
            entry = {"field": item.get("source_text_role", field), "role": item.get("reference_role", item.get("source_text_role", item.get("identity_kind", field))),
                     "column": _text(headers[index]), "column_index": index + 1, "raw": raw}
            proposed = json.dumps([*evidence, entry], ensure_ascii=False, separators=(",", ":"))
            if len(proposed.encode("utf-8")) > 2048:
                limited = True
                continue
            evidence.append(entry)
    return {**({"source_field_evidence": json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))} if evidence else {}),
            **({"source_field_evidence_limited": "true"} if limited else {})}


def _typed_native_references(row, candidates, reference_policy):
    references = candidates.get("trade_no", [])
    if any(item.get("reference_profile") == BANK_FEEDBACK_28_REFERENCE_PROFILE for item in references):
        return candidates, reference_policy
    type_indices = {index for item in references for index in item.get("native_voucher_type_indices", ())}
    kinds = {_text(_value(row, {"index": index})) for index in type_indices} - {""}
    primary = [item for item in references if _norm(item.get("source_name", "")) == _norm("交易流水号")]
    voucher = [item for item in references if _norm(item.get("source_name", "")) == _norm("凭证号")]
    positive = lambda value: value and value.casefold() not in {"null", "none", "nan", "n/a"} and any(char.isalnum() for char in value)
    if (len(kinds) != 1 or not positive(next(iter(kinds), ""))
            or not any(positive(_text(_value(row, item))) for item in primary)
            or not any(positive(_text(_value(row, item))) for item in voucher)):
        return candidates, reference_policy
    selected = dict(candidates)
    selected["trade_no"] = [{**item, "reference_role": "source_transaction_id" if item in primary else "independent_voucher_reference"}
                            for item in references]
    selected["voucher_type"] = [{"index": index, "reference_role": "independent_voucher_type"} for index in sorted(type_indices)]
    return selected, TRANSACTION_REFERENCE_POLICY


def _row_time_declaration(row, candidates):
    # A closed, verbatim source claim is a reason to withhold this row's
    # transaction-time projection. It is not a verified platform codebook and
    # never changes another row, sheet, institution or case.
    statement = "本文件交易时间表示受理时间，不是交易完成时间或记账时间。"
    affected = [item for item in candidates.get("trade_time", [])
                if "time_role" in item and _norm(item.get("source_name", "")) == _norm("交易时间")]
    note_indices = {index for item in affected for index in item.get("time_note_indices", ())
                    if _text(_value(row, {"index": index})) == statement}
    if not note_indices:
        return candidates
    selected = dict(candidates)
    selected["trade_time"] = [{**item, "time_role": "receipt"} if item in affected else item
                              for item in candidates.get("trade_time", [])]
    selected["time_role_declaration"] = [{"index": index, "reference_role": "row_time_role_declaration"} for index in sorted(note_indices)]
    return selected


def _auxiliary_header(rows, profile, tiers):
    header_no = next((index for index, row in enumerate(rows) if any(_cell_present(value) for value in row)), None)
    if header_no is None:
        return None
    names = [_text(_header_value(value, profile)) for value in rows[header_no]]
    inquiry = profile == BANK_ACCOUNT_INQUIRY_TRIAL_PROFILE and table_source_roles.is_account_inquiry(names)
    if inquiry and _locate_header(rows[header_no:header_no + 1], profile)[0] is not None:
        return None
    role = "account_inquiry" if inquiry else table_source_roles.classify(names)
    if profile in EVIDENCE_PROFILES:
        from . import evidence_resolution
        role = role or evidence_resolution.classify_auxiliary(names, native_observations=profile in NATIVE_OBSERVATION_PROFILES)
    if role not in {"account", "client", "subaccount", "account_inquiry"}:
        return None
    # A later transaction region must go through the ordinary complete parser.
    if any(parent > header_no + 1 for parent in tiers) or _locate_header(rows[header_no + 1:], profile)[0] is not None:
        return None
    return header_no, role


def _append_auxiliary_sheet(plan, sheet, *, details_pool=None, tiers=(), confirmed_field_description=False,
                            wrong_field_notes=None):
    rows = sheet["rows"]
    header = (0, "field_description") if confirmed_field_description else _auxiliary_header(rows, plan["profile"], tiers)
    if header is None:
        return False
    header_no, role = header
    inquiry = role == "account_inquiry"
    if inquiry:
        role = "account"
    headers = rows[header_no]
    width = len(headers)
    meaningful_width = max(index + 1 for index, value in enumerate(headers) if _text(value))
    contract = table_contracts.compile_contract({})
    labels = _columns(headers, plan["profile"])
    plan["documents"].append({"member_path": sheet["path"], "sheet": sheet["name"],
                              "header_row": header_no + 1, "header_rows": [header_no + 1],
                              "columns": labels, "mapping": {}, "mode": "", "source_role": role,
                              "source_rows": len(rows), "row_count": len(rows) - 1})
    if inquiry:
        plan["documents"][-1]["intake_role"] = "account"
    for index, row in enumerate(rows, start=1):
        if index == header_no + 1:
            continue
        valid = len(row) == width and not any(_cell_present(value) for value in row[meaningful_width:])
        reason = "auxiliary_table" if valid else "column_count_mismatch"
        wrong_scope = wrong_field_notes.get(index) if wrong_field_notes else None
        if wrong_scope is not None:
            valid, reason = False, WRONG_FIELD_NOTE_ROW
        if valid and plan["profile"] in XLS_EVIDENCE_ARCHIVE_PROFILES:
            if any(isinstance(value, _WorkbookValue) and value.formula for value in row[:meaningful_width]):
                valid, reason = False, "formula_value"
            elif any(isinstance(value, _WorkbookValue) and value.invalid_type for value in row[:meaningful_width]):
                valid, reason = False, "invalid_cell_type"
        details = {"sheet": sheet["name"], "source_row": str(index), "source_role": role,
                   "financial_effect": "not_a_flow" if valid else "undetermined",
                   "reason": REASONS[reason], **contract.details()}
        if reason in {"formula_value", "invalid_cell_type"}:
            details["validation_issues"] = reason
        if wrong_scope is not None:
            details["validation_issues"] = reason
            details[CONFIRMED_NOTE_FIELD] = json.dumps(wrong_scope, sort_keys=True, separators=(",", ":"))
        if valid and plan["profile"] in EVIDENCE_PROFILES:
            value = evidence_resolution.auxiliary_evidence([_text(_header_value(h, plan["profile"])) for h in headers],
                                                         [_evidence_value(v, plan["profile"]) for v in row], role,
                                                         native_observations=plan["profile"] in NATIVE_OBSERVATION_PROFILES)
            if value:
                details[evidence_resolution.DETAIL_FIELD] = value
        plan["records"].append({"record_no": len(plan["records"]) + 1, "member_path": sheet["path"],
                                "source_row": index, "record_key": record_key(sheet["path"], sheet["sha256"], index),
                                "disposition": "auxiliary" if valid else "unresolved", "reason_code": reason,
                                "details": details_pool.pack(details) if details_pool is not None else details})
    plan["members"].append({"member_path": sheet["path"], "role": "auxiliary", "sha256": sheet["sha256"],
                            "size_bytes": sheet["size"], "encoding": sheet["encoding"], "row_count": len(rows) - 1})
    return True


def _literal_tier_cell(value):
    if isinstance(value, _XlsxError) or isinstance(value, _WorkbookValue) and (value.formula or value.invalid_type):
        return None
    return _raw(value)


def _potential_tiers(rows):
    tiers, orphans = {}, {}
    for index, row in enumerate(rows, 1):
        following = rows[index] if index < len(rows) else None
        if proof := table_tiered_headers.inspect(row, following, index, _literal_tier_cell):
            tiers[index] = proof
        if proof := table_tiered_headers.leaf_proof(row, index, _literal_tier_cell):
            orphans[index] = proof
    return tiers, orphans


def _grouped_tiers(raw, sheets):
    requested = {}
    for position, sheet in enumerate(sheets, 1):
        rows = sheet["rows"]
        parents = {row for row in range(1, len(rows))
                   if table_tiered_headers.grouped_parent(rows[row - 1], _literal_tier_cell)}
        if parents:
            requested[position] = parents
    try:
        geometry = table_tiered_headers.xlsx_merge_ranges(raw, requested)
        grouped = {}
        for position, parents in requested.items():
            sheet = sheets[position - 1]
            rows = sheet["rows"]
            proofs = {parent: proof for parent in sorted(parents) if (proof := table_tiered_headers.inspect_grouped(
                rows[parent - 1], rows[parent], parent, _literal_tier_cell, geometry[position][parent]
            )) is not None}
            if proofs:
                grouped[sheet["path"]] = proofs
        return grouped
    except ValueError:
        raise TableImportError("无法核对工作表分组表头和物理合并位置") from None


def _initial_region(rows, profile, tiers):
    header_no, _, _ = _locate_header(rows, profile)
    parent = next(iter(tiers), None)
    if parent is not None and (header_no is None or parent - 1 <= header_no):
        return parent, dict(tiers[parent])
    return header_no, None


def _tier_overlap(rows, proof_indices, candidates):
    # Date and money in the active coordinates keep an ordinary transaction
    # from becoming a header just because its auxiliary values look like one.
    identity_columns = [item for field in _REGION_IDENTITIES for item in candidates.get(field, ())]
    dated_identity = False
    for row in rows:
        financial, _ = _financial_candidates(row, candidates)
        dates = {field: [item for item in financial.get(field, ())
                         if _literal_tier_cell(_value(row, item)) is not None]
                 for field in ("trade_time", "time_part")}
        if not _financial_times(row, dates, []):
            continue
        if any(_literal_tier_cell(_value(row, item)) is not None and _money(_value(row, item), item)[0] is not None
               for field in ("amount", "income_amt", "expense_amt") for item in financial.get(field, ())):
            return "data"
        dated_identity |= any(item["index"] not in proof_indices and _text(_literal_tier_cell(_value(row, item)))
                              for item in identity_columns)
    if dated_identity:
        return "competing" if proof_indices.intersection(
            item["index"] for items in candidates.values() for item in items) else "data"
    return "header"


def _tier_boundary(rows, index, tiers, candidates):
    proof = tiers.get(index)
    if proof is None:
        return None
    overlap = _tier_overlap(rows[index - 1:index + 1], {claim[0] - 1 for claim in proof["columns"]}, candidates)
    if overlap == "data":
        return None
    return {**proof, "status": "ambiguous"} if overlap == "competing" else dict(proof)


def _orphan_tier_boundary(row, proof, candidates):
    if proof is None:
        return None
    return proof if _tier_overlap((row,), {claim[0] - 1 for claim in proof["columns"]}, candidates) != "data" else None


def _financial_regions(sheets, profile, tiers_by_sheet, *, include_unshaped=False, include_ambiguous=False):
    """Use the interpreter's region guards on the already decoded source."""
    for sheet in sheets:
        rows = sheet["rows"]
        tiers, orphans = tiers_by_sheet.get(sheet["path"], ({}, {}))
        if (tiers or orphans) and _auxiliary_header(rows, profile, tiers) is not None:
            continue
        header_no, proof = _initial_region(rows, profile, tiers)
        before_header = len(rows) if header_no is None else header_no - (proof is not None)
        if any(index <= before_header for index in orphans):
            yield None, (), {}, True
        if header_no is None:
            continue
        candidates, *_ = _region(rows[header_no], None, None, strict=True, profile=profile, tiered_proof=proof)
        if include_unshaped or _shape(candidates, profile):
            yield header_no, rows[header_no], candidates, proof is not None
        elif proof is not None:
            yield header_no, (), {}, True
        ambiguous_layouts = {}
        next_body = header_no + 1
        for index in range(next_body, len(rows)):
            if index < next_body:
                continue
            row = rows[index]
            if not any(_cell_present(value) for value in row):
                continue
            if proof := tiers and _tier_boundary(rows, index + 1, tiers, candidates):
                ambiguous_layouts.clear()
                headers = rows[index + 1]
                next_body = index + 2
                candidates, *_ = _region(headers, None, None, strict=False, profile=profile, tiered_proof=proof)
                yield index + 1, headers if candidates else (), candidates, True
            elif orphans and _orphan_tier_boundary(row, orphans.get(index + 1), candidates):
                _remember_layout(ambiguous_layouts, candidates)
                yield index, (), {}, True
            elif _overlapping_header(row, candidates, profile):
                _remember_layout(ambiguous_layouts, candidates)
                _remember_layout(ambiguous_layouts, _candidates(row, profile))
                if include_ambiguous:
                    yield index, (), {}, False
            elif ((ambiguous_layouts and _unmistakable_header(row, profile, ambiguous_layouts))
                  or not ambiguous_layouts and _later_header(row, candidates, profile)):
                ambiguous_layouts.clear()
                candidates, *_ = _region(row, None, None, strict=False, profile=profile)
                if include_unshaped or _shape(candidates, profile):
                    yield index, row, candidates, False
            elif ambiguous_layouts:
                found = _candidates(row, profile)
                if _shape(found, profile):
                    _remember_layout(ambiguous_layouts, found)


def _declared_table_layouts(sheets, profile, tiers_by_sheet, *, trial_xlsx=False, template_csv=False):
    from .event_resolution import HEADERS

    check_events = profile not in EVIDENCE_PROFILES and profile not in STANDALONE_XLS_PROFILES
    check_bank = profile in CLOSED_BANK_SUCCESSORS
    check_tiers = any(tiers or orphans for tiers, orphans in tiers_by_sheet.values())
    events = bank = direction_recheck = tiered = False
    trial_profile = None
    csv_regions = 0
    redundant_clock = False
    inspect_unshaped = trial_xlsx or template_csv
    for _, headers, candidates, boundary in _financial_regions(
        sheets, profile, tiers_by_sheet, include_unshaped=inspect_unshaped, include_ambiguous=template_csv
    ):
        tiered |= boundary
        if trial_xlsx and trial_profile != BANK_SPLIT_LEDGER_TRIAL_PROFILE:
            headings = tuple(_text(_header_value(header, profile)) for header in headers)
            if is_bank_split_ledger(headings, candidates):
                trial_profile = BANK_SPLIT_LEDGER_TRIAL_PROFILE
            elif trial_profile is None and is_bank_feedback39(headings):
                trial_profile = BANK_FEEDBACK39_TRIAL_PROFILE
        if template_csv:
            csv_regions += 1
            if csv_regions == 1 and _shape(candidates, profile):
                redundant_clock = _redundant_trade_clock_columns(candidates) is not None
            if (_shape(candidates, profile) == "B" and candidates.get("direction") and candidates.get("amount")
                    and len(candidates.get("currency", ())) == 1 and table_counterparty.source_column(headers) is not None):
                trial_profile = BANK_COMPONENT_TRIAL_PROFILE
            elif trial_profile is None and (_template_direction_column(headers, candidates, profile) is not None
                                            or _template_balance_column(headers, candidates, profile) is not None):
                trial_profile = BANK_TEMPLATE_TRIAL_PROFILE
        if inspect_unshaped and headers and not _shape(candidates, profile):
            continue
        if check_events and not events:
            names = [_text(value) for value in headers]
            events = all(names.count(name) == 1 for name in HEADERS.values())
        if check_bank and not bank:
            names = frozenset(_text(_header_value(header, profile)) for header in headers)
            bank = ((len(headers) == len(names) and names in (BANK_FEEDBACK_31_COLUMNS, UNIFORM_FEEDBACK_37_COLUMNS))
                    or any(item.get("reference_profile") == BANK_FEEDBACK_31_REFERENCE_PROFILE
                           for item in candidates.get("trade_no", [])))
        if profile == VOUCHER_REFERENCE_PROFILE and not direction_recheck and _shape(candidates, profile) == "B":
            direction_recheck = any(table_direction_recheck.is_heading(header) for header in headers)
        if (events or not check_events) and (bank or not check_bank) and (tiered or not check_tiers):
            break
    if template_csv and trial_profile is None and csv_regions == 1 and redundant_clock:
        trial_profile = TRADE_CLOCK_PRECISION_TRIAL_PROFILE
    return events, bank, direction_recheck, tiered, trial_profile


def _needs_sign_layouts(sheets):
    if type(sheets) is not list:
        return True
    for sheet in sheets:
        if (type(sheet) is not dict or type(sheet.get("path")) is not str
                or type(sheet.get("rows")) is not list):
            return True
        for row in sheet["rows"]:
            if type(row) is not list:
                return True
            for cell in row:
                if type(cell) is _WorkbookValue:
                    cell = _raw(cell)
                if (type(cell) is not str and cell is not None) or table_sign_declarations.looks_like(cell):
                    return True
    return False


def _source_sign_contracts(sheets, profile, tiers_by_sheet=None):
    if profile not in PRECISE_PROFILES:
        return {}
    if type(profile) is str and not _needs_sign_layouts(sheets):
        return {}
    layouts = []
    for sheet in sheets:
        rows = sheet["rows"]
        tiers, orphans = (tiers_by_sheet or {}).get(sheet["path"], ({}, {}))
        header_no, proof = _initial_region(rows, profile, tiers)
        headers = rows[header_no] if header_no is not None else []
        candidates = (_region(headers, None, None, strict=True, profile=profile, tiered_proof=proof)[0]
                      if proof else _candidates(headers, profile))
        amount = candidates.get("amount", [])
        single_region = header_no is not None and not any(
            tiers and _tier_boundary(rows, index + 1, tiers, candidates)
            or orphans and _orphan_tier_boundary(row, orphans.get(index + 1), candidates)
            or _later_header(row, candidates, profile) or _overlapping_header(row, candidates, profile)
            for index, row in enumerate(rows[header_no + 1:], header_no + 1))
        single_region = single_region and not any(index < header_no + 1 - (proof is not None) for index in orphans)
        layouts.append({"member": sheet["path"], "rows": [[_raw(cell) for cell in row] for row in rows],
                        "invalid_cells": {(r + 1, c + 1) for r, row in enumerate(rows) for c, cell in enumerate(row)
                                          if isinstance(cell, _WorkbookValue) and (cell.formula or cell.invalid_type)
                                          or profile == table_tiered_headers.PROFILE and isinstance(cell, _XlsxError)},
                        "header_row": header_no + 1 if header_no is not None else 0,
                        "note_indices": {index for index, h in enumerate(headers)
                                         if _text(_header_value(h, profile)) in table_sign_declarations.NOTE_HEADERS},
                        "single_region": single_region,
                        "amount_header": _header_parts(headers[amount[0]["index"]], profile)[0] if len(amount) == 1 else ""})
        if profile == BANK_COMPONENT_TRIAL_PROFILE and _shape(candidates, profile) == "B":
            legs = [index for index, value in enumerate(headers)
                    if _norm(_text(_header_value(value, profile))) == table_sign_declarations.LEG_HEADER]
            currencies = candidates.get("currency", ())
            if len(legs) == len(currencies) == 1 and any(
                    item.get("time_role") == "transaction" for item in candidates.get("trade_time", ())):
                layouts[-1].update(leg_column=legs[0], currency_column=currencies[0]["index"],
                                   leg_note_indices={index for index, value in enumerate(headers)
                                                     if _text(value) in table_sign_declarations.LEG_NOTE_HEADERS})
    if profile == BANK_COMPONENT_TRIAL_PROFILE:
        return table_sign_declarations.compile_sources(layouts, scoped_legs=True)
    if profile == BANK_TEMPLATE_TRIAL_PROFILE:
        return table_sign_declarations.compile_sources(layouts, guard_scope_limits=True)
    return table_sign_declarations.compile_sources(layouts)


def _deposit_direction_explanation_columns(headings):
    return {index for index, label in enumerate(map(_norm, headings))
            if any(term in label for term in _DEPOSIT_DIRECTION_TERMS)
            and any(term in label for term in _DEPOSIT_DESCRIPTION_TERMS)}


def _withdrawal_conflicts_by_row(sheet):
    """Locate explicit withdrawal reversals without assuming their financial meaning."""
    rows = sheet["rows"]
    if len(rows) > MAX_DEPOSIT_DIRECTION_ROWS or sheet["size"] > MAX_DEPOSIT_DIRECTION_BYTES:
        return {}
    declarations = {}
    for index, row in enumerate(rows):
        for column, cell in enumerate(row):
            if not isinstance(cell, str) or "取款" not in cell:
                continue
            text = _norm(cell).translate(_DEPOSIT_QUOTING)
            if _WITHDRAWAL_CONFLICT.search(text):
                declarations.setdefault(index, []).append((column, text))
    if not declarations:
        return {}

    profile = VOUCHER_REFERENCE_PROFILE
    regions = list(_financial_regions([sheet], profile, {}, include_unshaped=True, include_ambiguous=True))
    if not regions or _shape(regions[0][2], profile) != "B":
        return {}
    conflicts = {}
    for (header, headings, candidates, _), end in zip(
            regions, [item[0] for item in regions[1:]] + [len(rows)], strict=True):
        directions = candidates.get("direction", ())
        if _shape(candidates, profile) != "B" or len(directions) != 1:
            continue
        [direction] = directions
        column = direction["index"]
        if _norm(headings[column]) not in DEPOSIT_DIRECTION_HEADINGS:
            continue
        notes = _deposit_direction_explanation_columns(headings)
        statement, local = None, {}
        for index in range(header + 1, end):
            for cited_column, text in declarations.get(index, ()):
                coordinates = index + 1, cited_column + 1
                if text.startswith(_DIRECTION_TABLE_SCOPE):
                    statement = statement or coordinates
                elif text.startswith("本行") or cited_column in notes and not text.startswith(
                        ("另表", "另一表", "其他表", "其它表", "上表", "下表", "非本表")):
                    local.setdefault(index, coordinates)
        if statement is None and not local:
            continue
        for index in range(header + 1, end):
            if len(rows[index]) == len(headings) and _text(rows[index][column]) == "取款":
                if citation := local.get(index, statement):
                    conflicts[index + 1] = (header + 1, column, *citation)
    return conflicts


def deposit_direction_column(headings, rows):
    """Locate a single explicit, closed deposit/withdrawal role on an original CSV table."""
    profile = VOUCHER_REFERENCE_PROFILE
    if not 2 <= len(rows) < MAX_DEPOSIT_DIRECTION_ROWS or any(len(row) != len(headings) for row in rows):
        return None
    candidates = _candidates(headings, profile)
    if (_shape(candidates, profile) != "B" or any(candidates.get(field) for field in (
            "payer_account", "payee_account", "income_amt", "expense_amt"))):
        return None
    owners, directions, amounts = (candidates.get(field, ()) for field in ("self_account", "direction", "amount"))
    if not len(owners) == len(directions) == len(amounts) == 1:
        return None
    [owner], [direction], [amount] = owners, directions, amounts
    own, column, money = (item["index"] for item in (owner, direction, amount))
    if (len({own, column, money}) != 3 or _norm(headings[own]) not in _DEPOSIT_OWN_HEADINGS
            or _norm(headings[column]) not in DEPOSIT_DIRECTION_HEADINGS
            or _header_parts(headings[money], profile)[0] not in _DEPOSIT_AMOUNT_HEADINGS or amount["signed"]
            or any(table_direction_recheck.is_heading(value) for value in headings)):
        return None
    texts = {item["index"] for role in ("summary", "channel") for item in candidates.get(role, ())}
    texts.update(i for i, heading in enumerate(headings) if _norm(heading) in {"备注", "说明", "金额说明", "收付说明"})
    explanations = _deposit_direction_explanation_columns(headings)
    role_columns = {own, column, money}
    sides, accounts = set(), set()
    for row in rows:
        account, marker = _text(row[own]), _text(row[column])
        value, issue = _money(row[money], amount)
        if (not account.isascii() or not account.isdecimal() or not account.strip("0")
                or marker not in {"存款", "取款"} or value is None or issue or value <= 0):
            return None
        if any(_text(row[index]) for index in explanations):
            return None
        if any(any(term in _text(row[index]) for term in ("手续费", "费用", "服务费", "冲正", "撤销"))
               for index in texts):
            return None
        if any(_DEPOSIT_SIDE_CONFLICT.search(_norm(text).translate(_DEPOSIT_QUOTING))
               for index, cell in enumerate(row) if index not in role_columns and (text := _text(cell))):
            return None
        sides.add(marker)
        accounts.add(account)
    sheet = {"path": "table.csv", "rows": [headings, *rows]}
    if (len(accounts) != 1 or sides != {"存款", "取款"} or _locate_header(rows, profile)[0] is not None
            or _source_sign_contracts([sheet], profile).get("table.csv")):
        return None
    return column


def _confirmed_csv_sheet(raw, layout):
    from . import feedback_import, import_originals

    encoding = layout["encoding"]
    try:
        text = raw.decode(encoding, errors="strict")
        delimiter = import_originals._layout_csv_delimiter(io.StringIO(text, newline=""))
        feedback_import._validate_csv_quotes(text, delimiter)
        rows = _collect_rows(import_originals._csv_records(io.StringIO(text, newline=""), delimiter))
    except (ValueError, csv.Error, feedback_import.FeedbackImportError):
        raise TableImportError("无法用已确认的范围复读 CSV 原件") from None
    return {"path": "table.csv", "name": "", "rows": rows, "sha256": layout["sha256"],
            "size": len(raw), "encoding": encoding, "delimiter": delimiter}


def _confirmed_mapping(headers, region, profile):
    positions = region["mapping"]
    if len(set(positions.values())) != len(positions) or any(
        not 1 <= n <= len(headers) or type(headers[n - 1]) is not str or not headers[n - 1].strip()
        for n in positions.values()
    ):
        raise TableImportError("正式映射的物理列为空、越界或重复")
    known = _candidates(headers, profile)
    for field, column in positions.items():
        heading = headers[column - 1]
        normalized = _norm(heading)
        registered = {item["index"] for item in known.get(field, ())}
        roles = {name for name, items in known.items() if any(item["index"] == column - 1 for item in items)}
        if (registered and column - 1 not in registered or roles and field not in roles
                or table_source_roles.query_condition(_text(headers[column - 1]))
                or field in ACCOUNT_CARD_FIELDS and (document_number_header(normalized) or normalized.startswith("查询"))
                or field == "trade_time" and (normalized.startswith(("记账", "入账")) or _time(heading)
                                              or table_time_formats.source_time_value(heading)["offset"] is not None)
                or field in {"amount", "income_amt", "expense_amt"} and _money(heading, {"factor": Decimal(1)})[0] is not None):
            raise TableImportError("已确认映射与原列的已知职责冲突")
    labels = _columns(headers, profile)
    return region["mode"], {field: labels[column - 1] for field, column in positions.items()}


def opening_clock_role_conflict(field, normalized_heading):
    """An explicit account-opening clock cannot timestamp a transaction."""
    return field in {"trade_time", "time_part"} and normalized_heading in {
        "开户日期", "开户时间", "账号开户日期", "账号开户时间", "账户开户日期", "账户开户时间",
    }


def explicit_reference_columns(headers, profile):
    """The sole transaction-number and voucher columns in role order, if declared."""
    candidates = _candidates(headers, profile).get("trade_no", ())
    if len(candidates) != 2:
        return None
    positions = {_norm(_text(headers[item["index"]])): item["index"] + 1 for item in candidates}
    primary = positions.get("交易流水号")
    voucher = positions.get("凭证号") or positions.get("凭证编号")
    return (primary, voucher) if primary is not None and voucher is not None else None


AVAILABLE_BALANCE_SOURCE_HEADERS = frozenset({"可用余额", "交易后可用余额"})


def explicit_converted_amount_currency(heading):
    """Return the named target, an empty target for explicit conversion, or None."""
    name = _norm(heading)
    if name in {"换算金额", "交易换算金额"}:
        return ""
    targets = {"人民币": "CNY", "cny": "CNY", "rmb": "CNY", "美元": "USD", "usd": "USD"}
    if name.endswith("换算金额"):
        return targets.get(name.removesuffix("换算金额"))
    if name.startswith("换算") and name.endswith("金额"):
        return targets.get(name.removeprefix("换算").removesuffix("金额"))
    amount, marker, target = name.rpartition("折")
    if marker and amount in (_ALIASES["amount"] | _FINANCIAL_ALIASES["amount"]):
        return targets.get(target)
    return None


def is_explicit_converted_amount(heading):
    """Recognize a conversion amount even when its target currency is unproved."""
    if explicit_converted_amount_currency(heading) is not None:
        return True
    name = _norm(heading)
    field = re.sub(r"\([^()]*\)$", "", name)
    if ("是否" in name or re.search(r"(?:未|不|非)(?:换算|折算|折合|等值)", name)
            or field.endswith(("单位", "币种", "币别", "汇率", "比率", "比例", "标志", "标识", "标记",
                               "代码", "编码", "名称", "说明", "备注", "方式", "类型", "日期", "时间",
                               "currency", "rate", "unit", "flag", "method", "note", "code", "name", "type"))):
        return False
    if (any(noun in name for noun in ("金额", "发生额", "amount"))
            and any(conversion in name for conversion in ("换算", "折算", "折合", "等值", "converted", "equivalent"))):
        return True
    target_and_unit = re.fullmatch(r"(?:换算|折(?:算|合)?|等值)([^()]+)\(([^()]+)\)", name)
    if target_and_unit is None:
        return False
    target, unit = target_and_unit.groups()
    return unit in _UNIT and (target.upper() in _ISO_CURRENCIES or target in _CURRENCY or target.upper() in _CURRENCY)


def directional_money_source_role(heading):
    """Distinguish explicitly directional currency amounts from the posted amount."""
    name = _norm(heading)
    if "货币" not in name or not name.endswith("金额"):
        return None
    incoming, outgoing = "转入" in name, "转出" in name
    if incoming == outgoing:
        return None
    return "transfer_in_amount" if incoming else "transfer_out_amount"


def _mapping_headers(columns, profile):
    headers = [label.removesuffix(f" [列{index + 1}]") for index, label in enumerate(columns)]
    return headers if _columns(headers, profile) == columns else columns


def mapping_uses_nonposting_amount(columns, mapping, profile):
    headers = _mapping_headers(columns, profile)
    for field, label in mapping.items():
        if field in {"amount", "income_amt", "expense_amt", "balance", "currency"}:
            heading = headers[columns.index(label)]
            if is_explicit_converted_amount(heading) or directional_money_source_role(heading):
                return True
    return False


def mapping_contradicts_source_roles(columns, mapping, profile):
    """Check new manual mappings against explicit roles of the registered headers."""
    headers = _mapping_headers(columns, profile)
    available_or_peer_balances = AVAILABLE_BALANCE_SOURCE_HEADERS | {
        "对手余额", "对方余额", "对手交易余额", "交易对手余额", "交易对方余额"}
    explicit_balances = {"账面余额", "联机余额"} | available_or_peer_balances
    amount_fields = {"amount", "income_amt", "expense_amt"}
    known = _candidates(headers, profile)
    for field, label in mapping.items():
        index = columns.index(label)
        heading = _norm(headers[index])
        if opening_clock_role_conflict(field, heading):
            return True
        roles = {name for name, items in known.items() if any(item["index"] == index for item in items)}
        available = _header_parts(headers[index], profile)[0] in AVAILABLE_BALANCE_SOURCE_HEADERS
        if field in amount_fields and (
                available or heading in explicit_balances or roles & {"balance", "balance_before"}):
            return True
        if field == "balance" and (available or heading in available_or_peer_balances or roles & amount_fields):
            return True
        if field == "counter_name" and (roles & ACCOUNT_CARD_FIELDS or "direction" in roles):
            return True
        if field in {"counter_account", "counter_card"} and heading.endswith(("姓名", "名称", "户名", "name")) and (
                roles & {"self_name", "counter_name", "payer_name", "payee_name"}):
            return True
    return False


def _account_use_code_columns(headings, profile):
    """Identify all unassigned account-like columns that the note must resolve."""
    registered = _candidates(headings, profile)
    if registered.get("self_account"):
        return ()
    known = {item["index"] + 1 for items in registered.values() for item in items}
    account_terms = ("账号", "帐号", "账户", "帐户", "卡号", "账卡", "account", "acct", "card")
    return tuple(column for column, heading in enumerate(headings, 1) if type(heading) is str
                 and column not in known and not table_source_roles.query_condition(heading)
                 and not document_number_header(_norm(heading))
                 and any(word in _header_parts(heading, profile)[0] for word in account_terms))


def _confirmed_field_notes(sheet, headings, full_spans, *, narrative_notes=False):
    rows = sheet["rows"]
    note_format = tuple(rows[0]) if rows else ()
    if full_spans and (note_format == table_source_roles.SCOPED_FIELD_NOTE_HEADER
                       or narrative_notes and note_format == table_source_roles.NARRATIVE_FIELD_NOTE_HEADER):
        locate = (table_source_roles.scoped_field_note if note_format == table_source_roles.SCOPED_FIELD_NOTE_HEADER
                  else table_source_roles.narrative_field_note)
        active = [row for row in rows[1:] if any(_cell_present(cell) for cell in row)]
        return bool(active) and all(any(locate(row, name, head, last, cells)
                                        for (name, head), (last, cells) in full_spans.items()) for row in active)
    if (sheet["name"] != "字段说明" or not rows or len(rows[0]) != 2 or rows[0][0] != "字段"
            or type(rows[0][1]) is not str or "字段说明" not in rows[0][1]):
        return False
    found = False
    for row in rows[1:]:
        if not any(_cell_present(cell) for cell in row):
            continue
        address = _field_note_address(row)
        if address is None:
            return False
        name, row_number, index, heading = address
        values = headings.get((name, row_number), ())
        if not 0 < index <= len(values) or _text(values[index - 1]) != heading:
            return False
        found = True
    return found


def _field_note_address(row):
    if len(row) != 2 or any(type(cell) is not str or not cell.strip() for cell in row):
        return None
    ref = re.fullmatch(r"(.+)!([A-Z]{1,3})([1-9][0-9]*) (.+)", row[0])
    if not ref:
        return None
    name, letters, row_number, heading = ref.groups()
    index = 0
    for char in letters:
        index = index * 26 + ord(char) - 64
    return name, row_number, index, heading


def _scoped_field_note_rows(sheet, headings, full_spans, member_by_name):
    """Separate genuine header references from known data-row misreferences."""
    wrong, found = {}, False
    for number, row in enumerate(sheet["rows"][1:], 2):
        if not any(_cell_present(cell) for cell in row):
            continue
        address = _field_note_address(row)
        if address is None:
            return None
        name, row_number, column, heading = address
        if (name, row_number) in headings:
            header = headings[name, row_number]
            if not 0 < column <= len(header) or _text(header[column - 1]) != heading:
                return None
            found = True
            continue
        if len(row_number) > len(str(MAX_RECORDS)):
            return None
        target_row = int(row_number)
        scope = next((head for (sheet_name, head), (end, header) in full_spans.items()
                      if sheet_name == name and head < target_row <= end and column <= len(header)
                      and _text(header[column - 1]) == heading), None)
        if scope is None:
            return None
        wrong[number] = {"policy": CONFIRMED_LAYOUT_EVIDENCE_POLICY, "member_path": member_by_name[name],
                         "header_row": scope, "target_row": target_row, "column": column}
    return wrong if found else None


def _confirmed_sheets(raw, suffix, layout, profile):
    evidence_layout = layout["policy"] == CONFIRMED_LAYOUT_EVIDENCE_POLICY
    if ((layout["policy"] != CONFIRMED_LAYOUT_POLICY and not evidence_layout)
            or layout["sha256"] != hashlib.sha256(raw).hexdigest()):
        raise TableImportError("已确认表区不属于此原件或解释版本")
    if suffix == ".csv":
        sheets = [_confirmed_csv_sheet(raw, layout)]
    else:
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if sum(member.file_size for member in archive.infolist()) > MAX_LAYOUT_XLSX_BYTES:
                    raise TableImportError("工作簿展开大小超出小表正式映射范围")
        except zipfile.BadZipFile:
            raise TableImportError("工作簿无法读回") from None
        sheets = _excel(raw, suffix, mark_errors=True)
    declared = {}
    for region in layout["regions"]:
        declared.setdefault(region["member_path"], []).append(region)
    if set(declared) - {sheet["path"] for sheet in sheets}:
        raise TableImportError("已确认工作表不在原件中")
    mapped, headings, full_spans = {}, {}, {}
    note_policy = layout.get("field_notes_policy")
    coded_notes = note_policy == table_source_roles.CODED_FIELD_NOTE_POLICY
    narrative_notes = note_policy == table_source_roles.NARRATIVE_FIELD_NOTE_POLICY
    scoped_notes = coded_notes or narrative_notes or note_policy == table_source_roles.SCOPED_FIELD_NOTE_POLICY
    if evidence_layout and not scoped_notes:
        raise TableImportError("证据受理布局缺少字段说明规则")
    if coded_notes and (not evidence_layout or suffix != ".xlsx" or len(sheets) != 2
                        or len(layout["regions"]) != 1 or layout["regions"][0]["mode"] != "B"
                        or layout["regions"][0]["mapping"].keys() != {"self_account"}):
        raise TableImportError("用途码说明仅适用于单一交易表区及其独立说明")
    # Check new selections here. Frozen layouts can still be read under their
    # recorded interpretation; the service checks them before a current commit.
    checking_new_layout = "plan_sha256" not in layout
    for sheet in sheets:
        if sheet["path"] not in declared:
            continue
        rows = sheet["rows"]
        regions = sorted(declared[sheet["path"]], key=lambda region: region["header_row"])
        if regions[-1]["last_row"] != len(rows) or any(
            left["last_row"] + 1 != right["header_row"] for left, right in zip(regions, regions[1:])
        ):
            raise TableImportError("正式映射必须连续覆盖每个选定工作表的全部数据区")
        for row in rows[:regions[0]["header_row"] - 1]:
            values = [cell for cell in row if _cell_present(cell)]
            if not values:
                continue
            if len(values) != 1 or type(values[0]) is not str:
                raise TableImportError("正式映射之前的非空原行须为可辨认的单格说明")
            caption = values[0]
            if not (any(mark in caption.split("：", 1)[0] for mark in ("字段口径", "字段说明", "流水", "分表", "明细"))
                    or scoped_notes and table_source_roles.inline_field_caption(caption)):
                raise TableImportError("正式映射之前的非空原行须为可辨认的单格说明")
        sections = {}
        for region in regions:
            header = region["header_row"]
            if not 1 <= header < region["last_row"] <= len(rows) or header in sections:
                raise TableImportError("已确认物理范围与原件不一致")
            header_cells = rows[header - 1]
            headings[sheet["name"], str(header)] = header_cells
            if scoped_notes:
                full_spans[sheet["name"], header] = region["last_row"], header_cells
            mode, mapping = _confirmed_mapping(header_cells, region, profile)
            if checking_new_layout:
                columns = _columns(header_cells, profile)
                if (mapping_uses_nonposting_amount(columns, mapping, profile)
                        or scoped_notes and mapping_contradicts_source_roles(columns, mapping, profile)):
                    raise TableImportError("原表头已明确该列的其他职责，不能采用这项物理映射")
            sections[header] = mode, mapping
        normal, _, _ = _locate_header(rows, profile)
        if normal is not None and normal + 1 not in sections:
            raise TableImportError("已确认表头与规则已识别表区冲突")
        mapped[sheet["path"]] = sections
    wrong_notes = {}
    member_by_name = {sheet["name"]: sheet["path"] for sheet in sheets}
    for sheet in sheets:
        if sheet["path"] in mapped:
            continue
        if coded_notes:
            [((name, header), (last, cells))] = full_spans.items()
            proof = table_source_roles.verify_account_use_codes(
                dict(enumerate(sheet["rows"], 1)), sheet=name, header_row=header, last_row=last,
                headings=cells, columns=_account_use_code_columns(cells, profile),
            ) if sheet["name"] == table_source_roles.CODED_FIELD_NOTE_SHEET else None
            if proof is None or proof.column != layout["regions"][0]["mapping"]["self_account"]:
                raise TableImportError("用途码说明未核定该交易表区的本方原列")
            continue
        if (evidence_layout and sheet["name"] == "字段说明" and sheet["rows"]
                and tuple(sheet["rows"][0]) == ("字段", "字段说明")):
            qualified = _scoped_field_note_rows(sheet, headings, full_spans, member_by_name)
            if qualified is not None:
                wrong_notes[sheet["path"]] = qualified
                continue
        if not _confirmed_field_notes(sheet, headings, full_spans, narrative_notes=narrative_notes):
            raise TableImportError("原件有未覆盖的工作表，须独立核对")
    return sheets, mapped, wrong_notes


def validate_confirmed_layout_detail(value, header, source_row):
    layout = json.loads(value)
    if (type(layout) is not dict or set(layout) != {"policy", "header_row", "last_row", "mode", "mapping"}
            or layout["policy"] not in {CONFIRMED_LAYOUT_POLICY, CONFIRMED_LAYOUT_EVIDENCE_POLICY}
            or layout["mode"] not in {"A", "B"}
            or type(layout["header_row"]) is not int or layout["header_row"] != header
            or type(layout["last_row"]) is not int or not header < source_row <= layout["last_row"]
            or not isinstance(layout["mapping"], dict) or not layout["mapping"]
            or not layout["mapping"].keys() <= mapping_fields(VOUCHER_REFERENCE_PROFILE)
            or any(type(index) is not int or not 1 <= index <= MAX_COLUMNS for index in layout["mapping"].values())
            or len(set(layout["mapping"].values())) != len(layout["mapping"])):
        raise ValueError("invalid confirmed physical source layout")
    return layout


def validate_confirmed_note_scope(value):
    scope = json.loads(value)
    if (type(scope) is not dict or set(scope) != {"policy", "member_path", "header_row", "target_row", "column"}
            or scope["policy"] != CONFIRMED_LAYOUT_EVIDENCE_POLICY
            or not isinstance(scope["member_path"], str) or not re.fullmatch(r"worksheets/[0-9]{4}", scope["member_path"])
            or any(type(scope[key]) is not int for key in ("header_row", "target_row", "column"))
            or not 1 <= scope["header_row"] < scope["target_row"] <= MAX_RECORDS
            or not 1 <= scope["column"] <= MAX_COLUMNS):
        raise ValueError("invalid confirmed field-note scope")
    return scope


def retained_split_conflict(disposition, reason, details):
    # Missing amount and direction are the dependent effects of the two
    # incompatible positive legs. Any independent validation issue blocks.
    return (disposition == "unresolved" and reason == "conflicting_income_expense"
            and details.get("amount_model") == "split_magnitude"
            and details.get("validation_issues") == "conflicting_income_expense,missing_amount,missing_direction"
            and details.get("financial_effect") == "undetermined"
            and "source_ledger_evidence" not in details)


def confirmed_plan_admissible(plan, policy):
    if not plan["rows"]:
        return False
    if not plan["stats"]["unresolved"]:
        return True
    return policy == CONFIRMED_LAYOUT_EVIDENCE_POLICY and all(
        retained_split_conflict(record["disposition"], record["reason_code"], record["details"])
        or record["reason_code"] == WRONG_FIELD_NOTE_ROW and CONFIRMED_NOTE_FIELD in record["details"]
        for record in plan["records"] if record["disposition"] == "unresolved")


def confirmed_plan_sha256(plan):
    rows = [dict(row) for row in plan["rows"]]
    records = [{**record, "details": dict(record["details"])} for record in plan["records"]]
    signature = json.dumps([rows, records], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(signature.encode()).hexdigest()


def parse(source, *, suffix, mode=None, mapping=None, reference_policy=None, voucher_reference=False,
          profile=None, _details_pool=None,
          _defer_occurrence_binding=False, _retain_csv_format=False, _auto_event_profile=False, _decoded_csv=None,
          _manual_mapping_evidence=False, _confirmed_layout=None, _trial_xlsx=False, _trial_csv=False,
          _trial_archive_member=False, _verified_reference=None, transaction_status_policy=None):
    if profile is None:
        profile = TIME_ROLE_XLS_PROFILE if suffix.lower() == ".xls" else PROFILE
    # New exact cross-field arithmetic is independent of ambient Decimal
    # precision. Historical parsers keep their original behavior and receipts.
    if profile in CONTRACT_PROFILES:
        with localcontext() as context:
            context.prec = max(context.prec, 50)
            return _parse(source, suffix=suffix, mode=mode, mapping=mapping,
                          reference_policy=reference_policy, voucher_reference=voucher_reference,
                          profile=profile, details_pool=_details_pool,
                          defer_occurrence_binding=_defer_occurrence_binding, retain_csv_format=_retain_csv_format,
                          auto_event_profile=_auto_event_profile, decoded_csv=_decoded_csv,
                          manual_mapping_evidence=_manual_mapping_evidence, confirmed_layout=_confirmed_layout,
                          trial_xlsx=_trial_xlsx, trial_csv=_trial_csv, trial_archive_member=_trial_archive_member,
                          verified_reference=_verified_reference, transaction_status_policy=transaction_status_policy)
    return _parse(source, suffix=suffix, mode=mode, mapping=mapping,
                  reference_policy=reference_policy, voucher_reference=voucher_reference,
                  profile=profile, details_pool=_details_pool,
                  defer_occurrence_binding=_defer_occurrence_binding, retain_csv_format=_retain_csv_format,
                  auto_event_profile=_auto_event_profile, decoded_csv=_decoded_csv,
                  manual_mapping_evidence=_manual_mapping_evidence, confirmed_layout=_confirmed_layout,
                  trial_xlsx=_trial_xlsx, trial_csv=_trial_csv, trial_archive_member=_trial_archive_member,
                  verified_reference=_verified_reference, transaction_status_policy=transaction_status_policy)


def _region_row_headers(headers, profile):
    """Immutable header-only inputs for rows in one active table region."""
    names = tuple(_text(_header_value(header, profile)) for header in headers) if profile in EVIDENCE_PROFILES or profile in SOURCE_CONTRACT_PROFILES else ()
    feedback_names = None
    if profile in EVIDENCE_ARCHIVE_PROFILES:
        from . import feedback_import
        if len(headers) == len(feedback_import.COLUMNS) and frozenset(names) == frozenset(feedback_import.COLUMNS):
            # The closed adapter historically reads raw headers, without the
            # XLS schema filter used by identity and event evidence.
            feedback_names = tuple(_text(header) for header in headers)
    return names, feedback_names


def _compact_ledger_clock(row, columns):
    date_index = columns["交易日期"]["index"]
    clock_column = columns["交易时间"]
    clock_index = clock_column["index"]
    day, clock = row[date_index], row[clock_index]
    if day in (None, "") or clock in (None, ""):
        return row, "missing_time"
    if (type(day) is not str or type(clock) is not str or not re.fullmatch(r"[0-9]{8}", day)
            or not re.fullmatch(r"[0-9]{8} [0-9]{2}:[0-9]{2}:[0-9]{2}", clock)):
        return row, "invalid_time"
    try:
        date = dt.datetime.strptime(day, "%Y%m%d").date()
        timestamp = dt.datetime.strptime(clock, "%Y%m%d %H:%M:%S")
    except ValueError:
        return row, "invalid_time"
    if date != timestamp.date():
        return row, "conflicting_time"
    if clock_column["time_role"] != "transaction":
        return row, "unsupported_time_role"
    interpreted = list(row)
    interpreted[date_index] = date
    interpreted[clock_index] = timestamp
    return interpreted, None


def _trial_source_profile(sheets, candidates, tiers_by_sheet, trial_csv):
    if trial_csv and sheets[0]["rows"] and table_source_roles.is_coded_bank_csv(
        tuple(_text(cell) for cell in sheets[0]["rows"][0])
    ):
        return CODED_BANK_CSV_TRIAL_PROFILE
    if candidates == BANK_SPLIT_LEDGER_TRIAL_PROFILE and any(
        (header := _auxiliary_header(sheet["rows"], BANK_ACCOUNT_INQUIRY_TRIAL_PROFILE,
                                     tiers_by_sheet[sheet["path"]][0])) is not None
        and header[1] == "account_inquiry" for sheet in sheets
    ):
        return BANK_ACCOUNT_INQUIRY_TRIAL_PROFILE
    return candidates


def _interpret_table_row(row, candidates, mode, profile, reference_policy, source_contract, sign_contract,
                         *, check_direction=False, source_row=None, combined_column=None, transaction_status_policy=None):
    interpreter = _interpret_candidates if profile in ROLE_PROFILES else _interpret
    row_candidates, row_reference_policy = _typed_native_references(row, candidates, reference_policy)
    row_candidates = _row_time_declaration(row, row_candidates)
    if profile == DEPOSIT_DIRECTION_TRIAL_PROFILE:
        direction = row_candidates["direction"][0]
        if _text(_value(row, direction)) == "存款":
            row_candidates = {**row_candidates, "direction": [{**direction, "rechecked_direction": "in"}]}
    if (profile in TRIAL_XLSX_PROFILES and (names := row_candidates.get("self_name"))
            and names[0].get("literal_holder_name") and type(row[names[0]["index"]]) is not str):
        row_candidates = {**row_candidates, "self_name": []}
    if profile == BANK_COMPONENT_TRIAL_PROFILE and sign_contract and sign_contract.get("leg"):
        sign_contract = table_sign_declarations.contract_for_row(sign_contract, row, source_row or 0)
    if mode == "B" and combined_column is not None:
        components = table_counterparty.components(_literal_tier_cell(row[combined_column]))
        if components:
            row_candidates = dict(row_candidates)
            for field, value in components.items():
                row_candidates[field] = [*row_candidates.get(field, ()),
                                         {"index": combined_column, "source_component": value}]
    options = {"reference_policy": row_reference_policy, "financial": profile in FINANCIAL_PROFILES,
               "source_contract": source_contract, "source_roles": profile in SOURCE_ROLE_PROFILES,
               "retain_endpoint_role": check_direction or profile in EVIDENCE_PROFILES,
               "sign_contract": sign_contract, "source_ledger": profile in SOURCE_LEDGER_PROFILES,
               "corroborate_trade_clock": profile == TRADE_CLOCK_PRECISION_TRIAL_PROFILE,
               "bind_named_ledger_parties": profile in CSV_TAIL_ORDINARY_PROFILES,
               "transaction_status_policy": transaction_status_policy}
    if profile == table_tiered_headers.GROUPED_PROFILE and (group_clock := next(
            (item for item in row_candidates.get("time_part", ()) if item.get("grouped_header")), None)):
        group_day = next(item for item in row_candidates["trade_time"] if item.get("grouped_header"))
        day, clock = _value(row, group_day), _value(row, group_clock)
        if not (_text(day) and _text(clock)):
            reason = "missing_time"
        elif not _date_only(day) or table_financial.clock(_text(clock)) is None:
            reason = "invalid_time"
        else:
            reason = None
        if reason:
            outcome = (None, "unresolved", reason, {"financial_effect": "undetermined", "validation_issues": reason})
            return outcome, row_candidates, row_reference_policy, interpreter, options
    if profile in BANK_SPLIT_LEDGER_PROFILES:
        compact = {item["compact_ledger_column"]: item for item in row_candidates.get("trade_time", ())
                   if "compact_ledger_column" in item}
        if compact:
            row, reason = _compact_ledger_clock(row, compact)
            if reason:
                outcome = (None, "unresolved", reason, {"financial_effect": "undetermined", "validation_issues": reason})
                return outcome, row_candidates, row_reference_policy, interpreter, options
    return interpreter(row, row_candidates, mode, **options), row_candidates, row_reference_policy, interpreter, options


def invalid_layout_fact(row, candidates):
    fields = ACCOUNT_CARD_FIELDS | {"trade_time", "time_part", "amount", "income_amt", "expense_amt",
                                  "direction", "currency", "balance", "balance_before"}
    for field in fields:
        for item in candidates.get(field, ()):
            cell = row[item["index"]]
            raw = _raw(cell)
            if (isinstance(cell, _XlsxError) or isinstance(raw, bool)
                    or field in ACCOUNT_CARD_FIELDS and isinstance(raw, (dt.date, dt.time))):
                return True
    return False


def _parse(source, *, suffix, mode=None, mapping=None, reference_policy=None, voucher_reference=False,
           profile=PROFILE, details_pool=None,
           defer_occurrence_binding=False, retain_csv_format=False, auto_event_profile=False, decoded_csv=None,
           manual_mapping_evidence=False, confirmed_layout=None, trial_xlsx=False, trial_csv=False,
           trial_archive_member=False, verified_reference=None, transaction_status_policy=None):
    """Interpret every source record and return a write-free versioned plan."""
    suffix = suffix.lower()
    archive_member = ((profile in {ARCHIVE_MEMBER_PROFILE, GUARDED_ARCHIVE_PROFILE, PRECISE_ARCHIVE_PROFILE, REFERENCE_ROLE_ARCHIVE_PROFILE, OPTIONAL_REFERENCE_ARCHIVE_PROFILE, TIME_ROLE_ARCHIVE_PROFILE, ZONED_ARCHIVE_PROFILE, SOURCE_CONTRACT_ARCHIVE_PROFILE, SOURCE_TEXT_ARCHIVE_PROFILE, BANK31_VARIANT_ARCHIVE_PROFILE, SOURCE_LEDGER_ARCHIVE_PROFILE, POSTING_PAIR_ARCHIVE_PROFILE, AUXILIARY_SCOPE_ARCHIVE_PROFILE, NATIVE_OBSERVATION_ARCHIVE_PROFILE, UNORIENTED_ARCHIVE_PROFILE, NATIVE31_ARCHIVE_PROFILE, SOURCE_OBSERVATION_ARCHIVE_PROFILE, AUXILIARY_ROWS_ARCHIVE_PROFILE, *BANK_SOURCE_CSV_ARCHIVE_PROFILES} and suffix == ".csv"
                       or profile in EVIDENCE_ARCHIVE_PROFILES and suffix in {".csv", ".txt", ".tsv", ".xlsx"}
                       or profile in XLS_EVIDENCE_ARCHIVE_PROFILES and suffix == ".xls")
                      and reference_policy == TRANSACTION_REFERENCE_POLICY)
    if profile not in SUPPORTED_PROFILES and not archive_member:
        raise TableImportError("不支持的表格解析规则")
    if transaction_status_policy is not None and (transaction_status_policy != TRANSACTION_STATUS_POLICY
            or profile not in PRECISE_PROFILES | {CSV_TAIL_SOURCE_PROFILE}):
        raise TableImportError("不支持的交易状态解释规则")
    if details_pool is not None and profile not in EVIDENCE_ARCHIVE_PROFILES:
        raise TableImportError("临时审计存储仅用于新反馈包规则")
    if (type(defer_occurrence_binding) is not bool
            or defer_occurrence_binding and not (archive_member and profile in EVIDENCE_ARCHIVE_PROFILES)):
        raise TableImportError("延后来源绑定仅用于完整证据反馈包的成员解析")
    if type(retain_csv_format) is not bool or retain_csv_format and suffix != ".csv":
        raise TableImportError("临时CSV格式仅用于CSV成员复读")
    if suffix not in {".csv", ".tsv", ".txt", ".xlsx", ".xls"}:
        raise TableImportError("表格文件格式不受支持")
    pinned_tiered = profile in TIERED_PROFILES
    if pinned_tiered and suffix != ".xlsx":
        raise TableImportError("分层表头规则仅适用于单文件XLSX")
    if profile in TRIAL_XLSX_PROFILES and suffix != ".xlsx":
        raise TableImportError("试用工作簿来源规则仅适用于单文件XLSX")
    if profile in TRIAL_CSV_PROFILES and suffix != ".csv":
        raise TableImportError("试用银行流水规则仅适用于单文件CSV")
    if profile in STANDALONE_XLS_PROFILES and suffix != ".xls" or suffix == ".xls" and profile not in XLS_READER_PROFILES:
        raise TableImportError("XLS仅支持单文件v18或证据归档v7读取规则；历史规则保持原解释")
    if mode not in (None, "A", "B") or (mapping is not None and not isinstance(mapping, dict)):
        raise TableImportError("表格映射参数无效")
    if reference_policy is not None and reference_policy != TRANSACTION_REFERENCE_POLICY:
        raise TableImportError("表格引用策略无效")
    if type(voucher_reference) is not bool or voucher_reference and profile not in VOUCHER_REFERENCE_PROFILES:
        raise TableImportError("凭证独立引用选择仅适用于新版单文件表格规则，请重新上传预览")
    if reference_policy is not None and mapping and "trade_no" in mapping:
        raise TableImportError("表格引用策略不能与交易号映射同时使用")
    if (type(auto_event_profile) is not bool or auto_event_profile and
            (profile not in {*CLOSED_BANK_SUCCESSORS, CURRENCY_REGION_PROFILE, GUARDED_PROFILE, PRECISE_PROFILE, REFERENCE_ROLE_PROFILE, OPTIONAL_REFERENCE_PROFILE, TIME_ROLE_PROFILE, ZONED_PROFILE, SOURCE_CONTRACT_PROFILE, SOURCE_TEXT_PROFILE, BANK31_VARIANT_PROFILE, SOURCE_LEDGER_PROFILE, POSTING_PAIR_PROFILE, AUXILIARY_SCOPE_PROFILE, NATIVE_OBSERVATION_PROFILE, UNORIENTED_PROFILE, NATIVE31_PROFILE, SOURCE_OBSERVATION_PROFILE}
             or suffix not in ({".xls"} if profile == VOUCHER_REFERENCE_XLS_PROFILE else {".csv", ".tsv", ".txt", ".xlsx"})
             or mode is not None or mapping is not None or reference_policy is not None)):
        raise TableImportError("来源规则自动识别仅用于单文件表格的首次自动预览")
    if trial_xlsx and not (auto_event_profile and profile == VOUCHER_REFERENCE_PROFILE and suffix == ".xlsx"):
        raise TableImportError("试用工作簿来源规则仅在隔离工作簿首次预览选择")
    if trial_csv and not (auto_event_profile and profile == VOUCHER_REFERENCE_PROFILE and suffix == ".csv"):
        raise TableImportError("试用银行流水规则仅在隔离CSV首次预览选择")
    if trial_archive_member and not (archive_member and profile == BANK_SOURCE_EVIDENCE_ARCHIVE_PROFILE
                                     and suffix in {".xlsx", ".csv"}):
        raise TableImportError("试用叶成员规则仅由受支持的容器选择")
    if profile in DEPOSIT_CSV_TRIAL_PROFILES and (
            suffix != ".csv" or mode not in (None, "B") or mapping is not None or voucher_reference
            or reference_policy is not None or confirmed_layout is not None or decoded_csv is not None
            or manual_mapping_evidence or retain_csv_format):
        raise TableImportError("存取款字面规则仅能重放同一原件的已登记方向列")
    trial_xlsx |= trial_archive_member and suffix == ".xlsx"
    trial_csv |= trial_archive_member and suffix == ".csv"
    # Validate the ordinary layout before trusting the specialised tail profile.
    selection_profile = VOUCHER_REFERENCE_PROFILE if trial_archive_member or profile in CSV_TAIL_FINANCIAL_PROFILES else profile
    scan_tiers = pinned_tiered or ((auto_event_profile or trial_archive_member)
                                  and selection_profile == VOUCHER_REFERENCE_PROFILE and suffix == ".xlsx")
    raw = _bytes(source)
    if profile == CSV_TAIL_SOURCE_PROFILE:
        if (suffix != ".csv" or mode not in (None, "B") or mapping not in (None, {}) or reference_policy is not None
                or voucher_reference or confirmed_layout is not None or decoded_csv is not None or retain_csv_format):
            raise TableImportError("末行缺格的CSV仅可按原登记范围保留来源")
        if (sheet := _csv_tail_sheet(raw)) is None:
            raise TableImportError("CSV原件与已登记的末行缺格范围不符")
        return _csv_tail_source_plan(sheet)
    if profile in CSV_TAIL_FINANCIAL_PROFILES and (
            suffix != ".csv" or mode is not None or mapping is not None or confirmed_layout is not None
            or decoded_csv is not None or manual_mapping_evidence or retain_csv_format
            or voucher_reference != (verified_reference is not None)
            or profile in CSV_TAIL_ORDINARY_PROFILES and voucher_reference):
        raise TableImportError("末行缺格仅能按已登记原表和原列复算")
    if verified_reference is not None and not (
        profile in VERIFIED_REFERENCE_PROFILES and suffix in {".csv", ".xlsx"} and voucher_reference
        and mode is None and mapping is None and confirmed_layout is None and not auto_event_profile
        and verified_reference["policy"] == VERIFIED_REFERENCE_POLICY
        and verified_reference["sha256"] == hashlib.sha256(raw).hexdigest()
    ):
        raise TableImportError("编号用途证书不属于当前原件或解析范围")
    confirmed_sections, confirmed_wrong_notes = {}, {}
    if confirmed_layout is not None:
        if suffix not in {".csv", ".xlsx"} or profile not in CONFIRMED_LAYOUT_PROFILES or auto_event_profile or decoded_csv is not None:
            raise TableImportError("已确认物理表区仅用于此版本独立 CSV 和 XLSX")
        sheets, confirmed_sections, confirmed_wrong_notes = _confirmed_sheets(raw, suffix, confirmed_layout, profile)
        manual_mapping_evidence = True
    elif profile in CSV_TAIL_FINANCIAL_PROFILES:
        sheet = _csv_tail_sheet(raw)
        if sheet is None or (profile == CSV_TAIL_REFERENCE_PROFILE and not _tail_reference_supported(sheet)
                             or profile in CSV_TAIL_ORDINARY_PROFILES and _tail_ordinary_profile(sheet) != profile):
            raise TableImportError("CSV原件与已登记的末行缺格核证范围不符")
        sheets = [sheet]
    elif decoded_csv is not None:
        if type(decoded_csv) is not _DecodedCSV:
            raise TableImportError("临时CSV解码与来源或规则不匹配")
        sheets = decoded_csv.take(raw, suffix, profile)
    else:
        mark_errors = scan_tiers or profile in TRIAL_XLSX_PROFILES
        try:
            sheets = _excel(raw, suffix, mark_errors=mark_errors) if suffix in {".xlsx", ".xls"} else _csv(raw, suffix, profile)
        except _RecognizedCSVWidthMismatch:
            if not trial_csv or trial_archive_member or (sheet := _csv_tail_sheet(raw)) is None:
                raise
            if _tail_reference_supported(sheet):
                profile = CSV_TAIL_REFERENCE_PROFILE
            elif chosen := _tail_ordinary_profile(sheet):
                profile = chosen
            else:
                return _csv_tail_source_plan(sheet)
            sheets = [sheet]
    if verified_reference is not None and not (
        len(sheets) == 1 and sheets[0]["path"] == verified_reference["member_path"]
        and len(sheets[0]["rows"]) == verified_reference["last_row"]
    ):
        raise TableImportError("编号用途证书与原件表区不一致")
    tiers_by_sheet = {sheet["path"]: _potential_tiers(sheet["rows"]) for sheet in sheets} if scan_tiers else {}
    grouped = (_grouped_tiers(raw, sheets) if profile == table_tiered_headers.GROUPED_PROFILE
               or trial_xlsx and not trial_archive_member else {})
    for path, proofs in grouped.items():
        tiers, orphans = tiers_by_sheet[path]
        tiers.update(proofs)
        tiers_by_sheet[path] = dict(sorted(tiers.items())), orphans
    template_csv = (trial_csv or profile in CSV_TAIL_ORDINARY_PROFILES) and not trial_archive_member
    declares_events, closed_bank, direction_recheck, found_tier, trial_profile = (
        _declared_table_layouts(sheets, selection_profile, tiers_by_sheet, trial_xlsx=trial_xlsx, template_csv=template_csv)
        if auto_event_profile or trial_archive_member or profile in CSV_TAIL_ORDINARY_PROFILES
        else (False, False, False, False, None))
    trial_profile = _trial_source_profile(sheets, trial_profile, tiers_by_sheet, trial_csv or profile in CSV_TAIL_ORDINARY_PROFILES)
    deposit_column = None
    if profile == DEPOSIT_DIRECTION_TRIAL_PROFILE or (
            template_csv and profile == VOUCHER_REFERENCE_PROFILE and trial_profile is None
            and not (declares_events or closed_bank or direction_recheck or found_tier)):
        if len(sheets) == 1 and sheets[0]["path"] == "table.csv" and len(raw) <= MAX_DEPOSIT_DIRECTION_BYTES:
            raw_rows = sheets[0]["rows"]
            if raw_rows:
                deposit_column = deposit_direction_column(raw_rows[0], raw_rows[1:])
        if deposit_column is not None:
            trial_profile = DEPOSIT_DIRECTION_TRIAL_PROFILE
        elif profile == DEPOSIT_DIRECTION_TRIAL_PROFILE:
            raise TableImportError("存取款原表与已登记的方向作用域不符")
    if profile in CSV_TAIL_FINANCIAL_PROFILES:
        if declares_events or closed_bank or direction_recheck or found_tier or trial_profile is not None:
            if trial_csv:
                return _csv_tail_source_plan(sheets[0])
            raise TableImportError("末行缺格资料超出原表核证范围")
        template_csv = False
    if trial_archive_member:
        if trial_profile and not (declares_events or closed_bank or direction_recheck or found_tier):
            profile, reference_policy = trial_profile, None
        elif suffix == ".xlsx":
            # Untyped members retain the legacy workbook cell representation.
            for sheet in sheets:
                for row in sheet["rows"]:
                    for index, cell in enumerate(row):
                        if isinstance(cell, _XlsxError):
                            row[index] = str(cell)
        declares_events = closed_bank = direction_recheck = found_tier = False
    if found_tier and (declares_events or closed_bank):
        raise TableImportError("双层表头或缺上层表头暂不支持与事件声明或登记版式混用，请分开上传")
    if declares_events:
        profile = (VOUCHER_REFERENCE_EVIDENCE_PROFILE if profile == VOUCHER_REFERENCE_PROFILE else
                   SOURCE_OBSERVATION_EVIDENCE_PROFILE if profile == SOURCE_OBSERVATION_PROFILE else
                   NATIVE31_EVIDENCE_PROFILE if profile == NATIVE31_PROFILE else
                   UNORIENTED_EVIDENCE_PROFILE if profile == UNORIENTED_PROFILE else
                   NATIVE_OBSERVATION_EVIDENCE_PROFILE if profile == NATIVE_OBSERVATION_PROFILE else
                   AUXILIARY_SCOPE_EVIDENCE_PROFILE if profile == AUXILIARY_SCOPE_PROFILE else
                   POSTING_PAIR_EVIDENCE_PROFILE if profile == POSTING_PAIR_PROFILE else
                   SOURCE_LEDGER_EVIDENCE_PROFILE if profile == SOURCE_LEDGER_PROFILE else
                   BANK31_VARIANT_EVIDENCE_PROFILE if profile == BANK31_VARIANT_PROFILE else
                   SOURCE_TEXT_EVIDENCE_PROFILE if profile == SOURCE_TEXT_PROFILE else
                   SOURCE_CONTRACT_EVIDENCE_PROFILE if profile == SOURCE_CONTRACT_PROFILE else
                   ZONED_EVIDENCE_PROFILE if profile == ZONED_PROFILE else
                   TIME_ROLE_EVIDENCE_PROFILE if profile == TIME_ROLE_PROFILE else
                   OPTIONAL_REFERENCE_EVIDENCE_PROFILE if profile == OPTIONAL_REFERENCE_PROFILE else
                   REFERENCE_ROLE_EVIDENCE_PROFILE if profile == REFERENCE_ROLE_PROFILE else
                   PRECISE_EVIDENCE_PROFILE if profile == PRECISE_PROFILE else
                   GUARDED_EVIDENCE_PROFILE if profile == GUARDED_PROFILE else EVIDENCE_PROFILE)
    if closed_bank:
        profile = CLOSED_BANK_SUCCESSORS[profile]
    if profile == VOUCHER_REFERENCE_PROFILE:
        if found_tier:
            profile = table_tiered_headers.GROUPED_PROFILE if grouped else table_tiered_headers.PROFILE
        elif direction_recheck:
            profile = table_direction_recheck.PROFILE
        elif trial_profile is not None:
            profile = trial_profile
    use_tiers = profile in TIERED_PROFILES
    if not use_tiers:
        tiers_by_sheet = {}
    sign_contracts = _source_sign_contracts(sheets, profile, tiers_by_sheet)
    if (template_csv and profile in {VOUCHER_REFERENCE_PROFILE, BANK_TEMPLATE_TRIAL_PROFILE}
            and any("proof" in contract for contract in sign_contracts.values())
            and any(table_sign_declarations.limits_scope(cell) for row in sheets[0]["rows"] for cell in row)):
        profile = BANK_COMPONENT_TRIAL_PROFILE
        sign_contracts = _source_sign_contracts(sheets, profile)
    withdrawal_conflicts = {}
    if profile == DEPOSIT_CONFLICT_TRIAL_PROFILE or template_csv and profile == VOUCHER_REFERENCE_PROFILE:
        withdrawal_conflicts = _withdrawal_conflicts_by_row(sheets[0])
        if withdrawal_conflicts:
            profile = DEPOSIT_CONFLICT_TRIAL_PROFILE
        elif profile == DEPOSIT_CONFLICT_TRIAL_PROFILE:
            raise TableImportError("方向冲突原表与已登记的作用域不符")
    plan = {"profile": profile, "mode": "B", "mapping": {}, "columns": [], "sheet": "workbook" if suffix in {".xlsx", ".xls"} else "", "rows": [], "records": [], "members": [], "documents": [], "mapping_evidence": []}
    if retain_csv_format:
        # The envelope's cancellation pass may replay this already validated
        # CSV with the exact selected format, without materializing cells again.
        # This operation-local hint never enters documents or persisted notes.
        plan["_csv_format"] = {key: sheets[0][key] for key in ("encoding", "delimiter")}
    recheck_enabled = profile in ({table_direction_recheck.PROFILE} | TIERED_PROFILES) and mode is None and mapping is None
    recorded_tier = False
    selected = False
    for sheet in sheets:
        raw_rows = sheet["rows"]
        sections = confirmed_sections.get(sheet["path"], {})
        if confirmed_layout is not None and not sections:
            _append_auxiliary_sheet(plan, sheet, confirmed_field_description=True,
                                    wrong_field_notes=confirmed_wrong_notes.get(sheet["path"]))
            continue
        tiers, orphans = tiers_by_sheet.get(sheet["path"], ({}, {}))
        if profile in SOURCE_ROLE_PROFILES and _append_auxiliary_sheet(plan, sheet, details_pool=details_pool, tiers=tiers):
            if profile in EVIDENCE_PROFILES:
                raw_rows.clear()
            continue
        header_no, initial_tier = _initial_region(raw_rows, profile, tiers)
        region_mode, region_mapping = mode, mapping
        if sections:
            first = min(sections)
            if sign_contracts.get(sheet["path"]) and (len(sections) != 1 or header_no != first - 1):
                raise TableImportError("金额单位或符号声明不能跨表区应用")
            header_no = first - 1
            region_mode, region_mapping = sections[first]
        headers = raw_rows[header_no] if header_no is not None else []
        candidates, labels, actual_mode, current_mapping, region_error, active_reference_policy = _region(
            headers, region_mode, region_mapping, strict=True, reference_policy=reference_policy,
            voucher_reference=voucher_reference, profile=profile, tiered_proof=initial_tier,
            manual_mapping_evidence=manual_mapping_evidence)
        if verified_reference is not None:
            if (header_no != 0 or verified_reference["header_row"] != 1
                    or active_reference_policy != TRANSACTION_REFERENCE_POLICY
                    or explicit_reference_columns(headers, profile) != tuple(verified_reference["columns"])):
                raise TableImportError("编号用途证书与原件列职责不一致")
            for item in candidates["trade_no"]:
                item["reference_profile"] = VERIFIED_REFERENCE_POLICY
        verified_empty_rows = set(verified_reference.get("fallback_rows", ())) if verified_reference else set()
        source_contract = table_contracts.compile_contract(candidates) if profile in CONTRACT_PROFILES else None
        header_names, feedback_header_names = _region_row_headers(headers, profile)
        combined_column = table_counterparty.source_column(header_names) if profile == BANK_COMPONENT_TRIAL_PROFILE else None
        direction_error_cells = sheet.get("direction_error_cells", ())
        declarations = _direction_declarations(headers, None if header_no is None else header_no + 1,
                                               direction_error_cells, recheck_enabled)
        if not selected and header_no is not None:
            plan.update(mode=actual_mode or "B", mapping=current_mapping, columns=labels)
            selected = True
        plan["mapping_evidence"].extend(_region_evidence(headers, candidates, labels, sheet["path"], header_no + 1 if header_no is not None else None))
        document = {"member_path": sheet["path"], "sheet": sheet["name"], "header_row": header_no + 1 if header_no is not None else None, "header_rows": [], "columns": labels, "mapping": current_mapping, "mode": actual_mode or "", "source_rows": len(raw_rows), "row_count": len(raw_rows) - (1 if header_no is not None else 0)}
        if use_tiers:
            document["tier_parent_rows"] = []
        if any(item.get("reference_profile") == BANK_FEEDBACK_28_REFERENCE_PROFILE for item in candidates.get("trade_no", [])):
            document.update(reference_profile=BANK_FEEDBACK_28_REFERENCE_PROFILE,
                            source_text_profile="bank-feedback28-summary-remark-v1")
        if profile in SOURCE_CONTRACT_PROFILES and (frozenset(header_names) == BANK_FEEDBACK_31_COLUMNS
                or profile in BANK31_VARIANT_PROFILES and any(item.get("reference_profile") == BANK_FEEDBACK_31_REFERENCE_PROFILE
                                                            for item in candidates.get("trade_no", []))):
            document["reference_profile"] = BANK_FEEDBACK_31_REFERENCE_PROFILE
            if profile in SOURCE_TEXT_PROFILES:
                document["source_text_profile"] = "bank-feedback31-summary-remark-v1"
            if profile in BANK31_VARIANT_PROFILES:
                document["source_shape_profile"] = "bank-feedback31-optional-metadata-v1"
        if source_contract:
            document.update(source_contract=table_contracts.VERSION, contract_sha256=source_contract.digest,
                            amount_model=source_contract.amount_model, candidate_scope="all_region_candidates")
        plan["documents"].append(document)
        member_count = 0
        ambiguous_layouts = {}
        pending_leaf = None
        tier_binding = None
        for index, row in enumerate(raw_rows, start=1):
            if profile in EVIDENCE_PROFILES or profile in {SOURCE_OBSERVATION_ARCHIVE_PROFILE, AUXILIARY_ROWS_ARCHIVE_PROFILE, *BANK_SOURCE_CSV_ARCHIVE_PROFILES}:
                # A source row is consumed once. Keep the active header object,
                # but release processed cells as the immutable plan is built.
                raw_rows[index - 1] = None
            if header_no is not None and index == header_no + 1:
                document["header_rows"].append(index)
                if use_tiers:
                    ambiguous_layouts.clear()
                    declarations = _direction_declarations(headers, index, direction_error_cells, recheck_enabled)
                    if initial_tier and initial_tier["status"] == "verified":
                        tier_binding = table_tiered_headers.binding(sheet["sha256"], initial_tier)
                continue
            number = len(plan["records"]) + 1
            key = record_key(sheet["path"], sheet["sha256"], index)
            details = {"sheet": sheet["name"], "source_row": str(index), "financial_effect": "not_a_flow"}
            result, own_side = None, ""
            observation = None
            if verified_reference is not None:
                details[VERIFIED_REFERENCE_DETAIL] = VERIFIED_REFERENCE_POLICY
            tier_boundary = (_tier_boundary(raw_rows, index, tiers, candidates)
                             if tiers and header_no is not None and index > header_no + 1 and index != pending_leaf else None)
            orphan = (_orphan_tier_boundary(row, orphans.get(index),
                                           candidates if header_no is None or index > header_no + 1 else {})
                      if orphans and index != pending_leaf else None)
            if initial_tier and index == initial_tier["parent_row"]:
                document["tier_parent_rows"].append(index)
                disposition, reason = "non_transaction", table_tiered_headers.PARENT_REASON
                details[table_tiered_headers.PROOF_FIELD] = table_tiered_headers.encode(initial_tier)
                details[table_tiered_headers.BINDING_FIELD] = table_tiered_headers.binding(sheet["sha256"], initial_tier)
                recorded_tier = True
            elif index == pending_leaf:
                pending_leaf = None
                document["header_rows"].append(index)
                disposition, reason = "non_transaction", "repeated_header"
                if tier_binding is not None:
                    details[table_tiered_headers.BINDING_FIELD] = tier_binding
            elif tier_boundary is not None:
                document["tier_parent_rows"].append(index)
                pending_leaf = index + 1
                headers = raw_rows[index]
                candidates, labels, actual_mode, current_mapping, region_error, active_reference_policy = _region(
                    headers, mode, mapping, strict=False, reference_policy=reference_policy,
                    voucher_reference=voucher_reference, profile=profile, tiered_proof=tier_boundary,
                    manual_mapping_evidence=manual_mapping_evidence)
                source_contract = table_contracts.compile_contract(candidates)
                header_names, feedback_header_names = _region_row_headers(headers, profile)
                declarations = _direction_declarations(headers, pending_leaf, direction_error_cells, recheck_enabled)
                ambiguous_layouts.clear()
                plan["mapping_evidence"].extend(_region_evidence(headers, candidates, labels, sheet["path"], pending_leaf))
                tier_binding = (table_tiered_headers.binding(sheet["sha256"], tier_boundary)
                                if tier_boundary["status"] == "verified" else None)
                disposition, reason = "non_transaction", table_tiered_headers.PARENT_REASON
                details[table_tiered_headers.PROOF_FIELD] = table_tiered_headers.encode(tier_boundary)
                details[table_tiered_headers.BINDING_FIELD] = table_tiered_headers.binding(sheet["sha256"], tier_boundary)
                recorded_tier = True
            elif orphan is not None:
                disposition, reason = "unresolved", "ambiguous_region_header"
                details.update(financial_effect="undetermined", validation_issues=reason)
                details[table_tiered_headers.LEAF_PROOF_FIELD] = table_tiered_headers.encode(orphan)
                details[table_tiered_headers.BINDING_FIELD] = table_tiered_headers.binding(sheet["sha256"], orphan)
                document["header_rows"].append(index)
                _remember_layout(ambiguous_layouts, candidates)
                declarations, tier_binding = (), None
                recorded_tier = True
            elif not any(_cell_present(value) for value in row):
                disposition, reason = "non_transaction", "blank_record"
            elif header_no is not None and index < header_no + 1:
                if ambiguous_layouts:
                    disposition, reason = "unresolved", "ambiguous_region_header"
                    details.update(financial_effect="undetermined", validation_issues=reason)
                else:
                    disposition, reason = "auxiliary", "preamble"
            elif header_no is None:
                disposition, reason = "unresolved", "ambiguous_region_header" if ambiguous_layouts else "unknown_table"
                details["financial_effect"] = "undetermined"
                if ambiguous_layouts:
                    details["validation_issues"] = reason
            elif index not in sections and _overlapping_header(row, candidates, profile):
                _remember_layout(ambiguous_layouts, candidates)
                _remember_layout(ambiguous_layouts, _candidates(row, profile))
                declarations, tier_binding = (), None
                disposition, reason = "unresolved", "ambiguous_region_header"
                details.update(financial_effect="undetermined", validation_issues=reason)
            elif (index in sections or (ambiguous_layouts and _unmistakable_header(row, profile, ambiguous_layouts))
                  or not ambiguous_layouts and _later_header(row, candidates, profile)):
                if index in sections and len(row) == len(headers):
                    outcome, *_ = _interpret_table_row(
                        row, candidates, actual_mode, profile, active_reference_policy, source_contract,
                        sign_contracts.get(sheet["path"]), source_row=index, combined_column=combined_column,
                        transaction_status_policy=transaction_status_policy,
                    )
                    if outcome[0] is not None:
                        raise TableImportError("实际流水原行不能作为下一表区表头")
                ambiguous_layouts.clear()
                headers = row
                region_mode, region_mapping = sections.get(index, (mode, mapping))
                candidates, labels, actual_mode, current_mapping, region_error, active_reference_policy = _region(
                    headers, region_mode, region_mapping, strict=False, reference_policy=reference_policy,
                    voucher_reference=voucher_reference, profile=profile,
                    manual_mapping_evidence=manual_mapping_evidence)
                source_contract = table_contracts.compile_contract(candidates) if profile in CONTRACT_PROFILES else None
                header_names, feedback_header_names = _region_row_headers(headers, profile)
                combined_column = table_counterparty.source_column(header_names) if profile == BANK_COMPONENT_TRIAL_PROFILE else None
                declarations = _direction_declarations(headers, index, direction_error_cells, recheck_enabled)
                tier_binding = None
                document["header_rows"].append(index)
                plan["mapping_evidence"].extend(_region_evidence(headers, candidates, labels, sheet["path"], index))
                disposition, reason = "non_transaction", "repeated_header"
            elif ambiguous_layouts:
                found = _candidates(row, profile)
                if _shape(found, profile):
                    _remember_layout(ambiguous_layouts, found)
                disposition, reason = "unresolved", "ambiguous_region_header"
                details.update(financial_effect="undetermined", validation_issues=reason)
            elif profile in CSV_TAIL_COMPLETE_ROW_PROFILES and len(row) == sheet["present_tail_width"]:
                disposition, reason = "unresolved", CSV_TAIL_INCOMPLETE_REASON
                details.update(financial_effect="undetermined", validation_issues=reason,
                               reason=f"本行缺少第 {len(row) + 1} 至 {len(headers)} 列原格。请核对原表。")
            elif len(row) != len(headers) and not (
                    profile in CSV_TAIL_FINANCIAL_PROFILES and len(row) == sheet["present_tail_width"]):
                disposition, reason = "unresolved", "row_width"
                details["financial_effect"] = "undetermined"
            elif region_error:
                disposition, reason = "unresolved", region_error
                details.update(financial_effect="undetermined", validation_issues=region_error)
            elif sections and invalid_layout_fact(row, candidates):
                disposition, reason = "unresolved", "invalid_cell_type"
                details["financial_effect"] = "undetermined"
            else:
                if tier_binding is not None:
                    details[table_tiered_headers.BINDING_FIELD] = tier_binding
                check_direction_declaration = bool(declarations) and actual_mode == "B"
                outcome, row_candidates, row_reference_policy, interpreter, options = _interpret_table_row(
                    row, candidates, actual_mode, profile, active_reference_policy, source_contract,
                    sign_contracts.get(sheet["path"]), check_direction=check_direction_declaration,
                    source_row=index, combined_column=combined_column, transaction_status_policy=transaction_status_policy,
                )
                direction_excerpts = ()
                if check_direction_declaration:
                    outcome, direction_excerpts = _recheck_direction(
                        row, headers, row_candidates, outcome, declarations, header_row=document["header_rows"][-1],
                        source_row=index, error_cells=direction_error_cells, interpreter=interpreter, options=options)
                result, disposition, reason, parsed_details = outcome
                if result is not None and (conflict := withdrawal_conflicts.get(index)):
                    header, column, note_row, note_column = conflict
                    if header != document["header_rows"][-1] or actual_mode != "B":
                        raise TableImportError("方向冲突原行与当前表区不符")
                    result, disposition, reason = None, "unresolved", "conflicting_direction"
                    for field in ("flow_sha256", "source_ledger_evidence", "_resolved_own_side"):
                        parsed_details.pop(field, None)
                    parsed_details.update(
                        direction_basis="undetermined", financial_effect="undetermined", validation_issues=reason,
                        reason=f"原第 {note_row} 行第 {note_column} 列的方向说明与本行第 {column + 1} 列取款的默认方向相反。"
                               "请核对该表区的方向释义。")
                    direction_excerpts = (table_direction_recheck.column_excerpt(column, _text(headers[column]), "取款"),)
                deposit_excerpt = (table_direction_recheck.column_excerpt(
                    deposit_column, _text(headers[deposit_column]), _text(row[deposit_column]))
                    if profile == DEPOSIT_DIRECTION_TRIAL_PROFILE else None)
                if use_tiers and table_direction_recheck.DETAIL_FIELD in parsed_details:
                    details[table_tiered_headers.DIRECTION_BINDING_FIELD] = table_tiered_headers.binding(
                        sheet["sha256"], [index, parsed_details[table_direction_recheck.DETAIL_FIELD]])
                if (profile in VOUCHER_REFERENCE_PROFILES and row_reference_policy is None
                        and reason == "conflicting_field" and not plan.get("_reference_choice_conflict")):
                    # This only offers a preview choice. An unrelated identity
                    # or bank conflict must not stop ordinary automatic intake.
                    references = row_candidates.get("trade_no", [])
                    voucher_names = {_norm("凭证号"), _norm("凭证编号")}
                    primary_values = {_text(_value(row, item)) for item in references
                                      if _norm(item.get("source_name", "")) not in voucher_names} - {""}
                    voucher_values = {_text(_value(row, item)) for item in references
                                      if _norm(item.get("source_name", "")) in voucher_names} - {""}
                    if len(primary_values) == len(voucher_values) == 1 and primary_values != voucher_values:
                        plan["_reference_choice_conflict"] = True
                if profile in EVIDENCE_ARCHIVE_PROFILES:
                    if (profile not in NATIVE_OBSERVATION_PROFILES
                            and feedback_header_names is not None and "time_role_declaration" not in row_candidates
                            and not (profile in XLS_EVIDENCE_ARCHIVE_PROFILES and reason in {"formula_value", "invalid_cell_type"})):
                        result, disposition, reason, parsed_details = _feedback_member_outcome(
                            headers, row, (result, disposition, reason, parsed_details), profile,
                            header_names=feedback_header_names, transaction_status_policy=transaction_status_policy)
                own_side = parsed_details.pop("_resolved_own_side", "")
                if profile in SOURCE_LEDGER_PROFILES and "source_ledger_evidence" in parsed_details:
                    observation = json.loads(parsed_details["source_ledger_evidence"])
                    observation["header_scope"] = document["header_rows"][-1]
                    if deposit_excerpt and deposit_excerpt["raw"] == "存款":
                        observation["direction_literal"] = {"policy": DEPOSIT_DIRECTION_POLICY,
                            "column": deposit_excerpt["column"], "column_index": deposit_excerpt["column_index"]}
                    if result is not None:
                        if direction_excerpts:
                            observation["direction_heading"] = direction_excerpts[0]["column"]
                        for field in ("trade_time_utc", "trade_time_original", "trade_time_offset"):
                            value = result.get(field)
                            observation["row"][field] = value.isoformat(sep=" ") if isinstance(value, dt.datetime) else value
                    parsed_details["source_ledger_evidence"] = json.dumps(observation, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                details.update(parsed_details)
                if profile in TIME_ROLE_PROFILES:
                    values, limited = [], False
                    for field in ("trade_time", "time_part"):
                        for item in row_candidates.get(field, []):
                            value = _text(_value(row, item))
                            if not value:
                                continue
                            entry = {"role": item.get("time_role", "unspecified"), "column": _text(headers[item["index"]]),
                                     "column_index": item["index"] + 1, "raw": value,
                                     **table_time_formats.source_time_value(value)}
                            if len(json.dumps([*values, entry], ensure_ascii=False).encode()) > 1800:
                                limited = True
                            else:
                                values.append(entry)
                    details["source_time_evidence"] = json.dumps({"policy": "source-time-roles-v1",
                        "ordinary_time_role": ("transaction" if any(value["role"] == "transaction" for value in values) else "unspecified") if result is not None else None, "values": values},
                        ensure_ascii=False, separators=(",", ":"))
                    if limited:
                        details["source_time_evidence_limited"] = "true"
                    if reason == "invalid_time" and any(value["role"] != "posting" and value["basis"] == "explicit_offset" for value in values):
                        reason = "unsupported_timezone"
                        details["validation_issues"] = ",".join([reason, *[issue for issue in details.get("validation_issues", "").split(",") if issue and issue != "invalid_time"]])
                if profile in REFERENCE_ROLE_PROFILES:
                    separate_amounts = (reason == "conflicting_income_expense" and (
                        profile == table_tiered_headers.GROUPED_PROFILE
                        or confirmed_layout and confirmed_layout["policy"] == CONFIRMED_LAYOUT_EVIDENCE_POLICY))
                    originals = (deposit_excerpt,) if deposit_excerpt else direction_excerpts
                    details.update(_row_field_evidence(headers, row, row_candidates, disposition, originals,
                                                       combined_column=combined_column, split_conflict=separate_amounts))
                if source_contract:
                    details.update(source_contract.details())
                if (profile in FINANCIAL_PROFILES and disposition == "unresolved"
                        and FINANCIAL_VALIDATION_ISSUES.intersection(parsed_details.get("validation_issues", "").split(","))
                        and any(item.get("basis") in {"explicit_financial_header", "explicit_source_contract", "explicit_time_format"} for items in candidates.values() for item in items)):
                    details["validation_basis"] = FINANCIAL_CONFLICT_BASIS
            if source_contract and reason in {"ambiguous_region_header", CSV_TAIL_INCOMPLETE_REASON}:
                details.update(source_contract.details())
            details.setdefault("reason", REASONS[reason])
            if source_contract and reason == "missing_direction":
                details["reason"] = ("缺少明确方向及同一笔交易的完整前后账面余额；普通相邻余额未证明连续性，"
                                     "不能补方向。该笔未计入资金统计和节点关系，收支及关联分析可能不完整")
            if profile in SOURCE_ROLE_PROFILES and reason == "missing_identity":
                details["reason"] = (("统一交易明细的本方账号、卡号及查询帐号、查询卡号均缺失。"
                                      if profile in QUERY_OWNER_PROFILES and any(
                                          item.get("contract_owner") for items in candidates.values()
                                          for item in items)
                                      else "缺少银行返回的本方账号或卡号；查询条件不证明本笔交易主体。")
                                     + "该笔未计入资金统计和节点关系，收支及关联分析可能不完整")
            identity_result = result
            unknown_direction = observation is not None and observation["direction"] == "unknown"
            if identity_result is None and observation is not None and observation["kind"] == "ledger":
                identity_result = observation["row"]
                own_side = "" if unknown_direction else "payee" if observation["direction"] == "in" else "payer"
                if unknown_direction:
                    identity_result = {**identity_result, **{"counter_" + key: value for key, value in observation.get("counterparty", {}).items()}}
                    details["reason"] = "来源未提供收付方向；保留本方、原金额和来源记账，不推定收支或对手。零额无资金变动，非零资金影响未确定。"
            neutral_observation = profile in SOURCE_OBSERVATION_PROFILES and unknown_direction
            if identity_result is not None and (profile in EVIDENCE_PROFILES or neutral_observation):
                from . import evidence_resolution
                include_counter = neutral_observation
                if feedback_header_names is not None:
                    source_cells = {name: _text(_evidence_value(value, profile)) for name, value in zip(header_names, row)}
                    if profile in BANK_SOURCE_ARCHIVE_PROFILES:
                        # Use the same resolved role as the admitted or retained ledger row.
                        feedback_direction = "in" if own_side == "payee" else "out" if own_side == "payer" else "unknown"
                    else:
                        feedback_direction = ("unknown" if own_side == "" and unknown_direction
                                              else "in" if source_cells["收付标志"] in {"进", "入"} else "out")
                    details[evidence_resolution.DETAIL_FIELD] = evidence_resolution.feedback_evidence(
                        source_cells, identity_result, ("", "", feedback_direction), include_counter=include_counter)
                else:
                    proof_columns, fallback_roles = (_identity_proof_candidates(candidates, own_side)
                                                     if manual_mapping_evidence else (candidates, ()))
                    identity_evidence = evidence_resolution.transaction_evidence(
                        header_names, [_evidence_value(v, profile) for v in row], proof_columns, identity_result, _identity_pair,
                        own_side=own_side, include_counter=include_counter, excluded_account_fallback=fallback_roles)
                    if identity_evidence is not None:
                        details[evidence_resolution.DETAIL_FIELD] = identity_evidence
            if (profile in EVENT_PROFILES and header_no is not None and len(row) == len(headers)
                    and disposition in {"accepted", "unresolved", "non_monetary"}
                    and reason not in {"preamble", "blank_record", "repeated_header", "ambiguous_region_header"}):
                from . import event_resolution
                details.update(event_resolution.parse_fields(
                    header_names,
                    [None if isinstance(value, _WorkbookValue) and (value.formula or value.invalid_type) else _raw(value) for value in row],
                    accepted=result is not None, posting_scope=document["header_rows"][-1]))
            if (profile in POSTING_PAIR_PROFILES and len(row) == len(headers)
                    and disposition in {"accepted", "unresolved", "non_monetary"}
                    and reason not in {"preamble", "blank_record", "repeated_header", "ambiguous_region_header"}):
                from . import bank_posting_pairs
                pair_contract = bank_posting_pairs.capture(header_names, [_text(_evidence_value(v, profile)) for v in row])
                if pair_contract is not None:
                    details[bank_posting_pairs.FIELD] = pair_contract
            # The envelope owns one bounded decoded cache for all members.
            # Pack each completed note now: waiting for a large member to
            # return retains its whole decoded journal beside financial rows.
            if result is not None and index in verified_empty_rows:
                voucher_column = verified_reference["columns"][1]
                voucher = _text(_value(row, {"index": voucher_column - 1}))
                if result["trade_no"] or not voucher:
                    raise TableImportError("独立凭证与空主号证明不一致")
                details[VERIFIED_REFERENCE_OCCURRENCE] = verified_voucher_occurrence(
                    sheet["sha256"], flow_digest(result), voucher_column, voucher)
            record = {"record_no": number, "member_path": sheet["path"], "source_row": index, "record_key": key, "disposition": disposition, "reason_code": reason,
                      "details": details_pool.pack(details) if details_pool is not None else details}
            plan["records"].append(record)
            if (result is not None and profile in SOURCE_CONTRACT_PROFILES
                    and suffix in {".csv", ".tsv", ".txt"}
                    and frozenset(header_names) == BANK_FEEDBACK_31_COLUMNS
                    and parsed_details.get("trade_no_basis") == "source_transaction_id"
                    and result.get("trade_no") and result.get("self_account") and result.get("balance") is not None):
                # One complete source member may report a posting twice with
                # only its branch label changed. Preserve both physical rows.
                observed = [(name, _raw(_evidence_value(cell, profile))) for name, cell in zip(header_names, row)
                            if name != "交易网点名称"]
                result["_source_posting_repeat_key"] = hashlib.sha256(json.dumps(observed, ensure_ascii=False,
                    separators=(",", ":"), default=str).encode()).hexdigest()
            if result is not None:
                result.update(_source_row=number, _source_sheet=plan["sheet"], _source_row_kind=ROW_KIND, _record_key=key)
                plan["rows"].append(result)
            member_count += 1
        plan["members"].append({"member_path": sheet["path"], "role": "table", "sha256": sheet["sha256"], "size_bytes": sheet["size"], "encoding": sheet["encoding"], "row_count": member_count})
    if use_tiers and not recorded_tier:
        raise TableImportError("分层规则无法重放已登记的表头边界")
    if not defer_occurrence_binding and profile in EVIDENCE_PROFILES | SOURCE_OBSERVATION_PROFILES:
        from . import evidence_resolution
        if profile in EVIDENCE_PROFILES:
            evidence_resolution.bind_occurrences(plan)
        else:
            evidence_resolution.bind_families(plan)
    refresh_stats(plan)
    if verified_reference is not None and (
        len(plan["documents"]) != 1 or plan["documents"][0]["header_rows"] != [1]
        or "ambiguous_region_header" in plan["stats"]["reason_counts"]
    ):
        raise TableImportError("编号用途证书无法覆盖全部表区")
    if (trial_xlsx and not trial_archive_member and profile == VOUCHER_REFERENCE_PROFILE
            and _uniform35_reference_source(sheets, plan)):
        plan["_reference_policy_basis"] = UNIFORM35_REFERENCE_BASIS
    if confirmed_layout is not None:
        policy = confirmed_layout["policy"]
        if not confirmed_plan_admissible(plan, policy) or any(
            doc["header_rows"] != sorted(confirmed_sections[doc["member_path"]])
            for doc in plan["documents"] if doc["member_path"] in confirmed_sections
        ):
            raise TableImportError("整张原件仍有未决、没有流水或表区与确认不一致")
        sources = {}
        for region in confirmed_layout["regions"]:
            value = json.dumps({"policy": policy, **{key: region[key] for key in
                                ("header_row", "last_row", "mode", "mapping")}}, ensure_ascii=False,
                               sort_keys=True, separators=(",", ":"))
            for row in range(region["header_row"] + 1, region["last_row"] + 1):
                sources[region["member_path"], row] = value
        for record in plan["records"]:
            if (basis := sources.get((record["member_path"], record["source_row"]))) is not None:
                record["details"][CONFIRMED_LAYOUT_FIELD] = basis
        plan["_confirmed_layout_sha256"] = confirmed_plan_sha256(plan)
        expected = confirmed_layout.get("plan_sha256")
        if expected is not None and expected != plan["_confirmed_layout_sha256"]:
            raise TableImportError("正式重放结果与已确认预览不一致")
    return plan


def refresh_stats(plan):
    """Recount the shared plan after a transaction-bound history check."""
    counts = {key: 0 for key in ("accepted", "unresolved", "non_monetary", "non_transaction", "auxiliary")}
    reasons, examples, totals = {}, [], {}
    recheck_counts = {}
    for record in plan["records"]:
        counts[record["disposition"]] += 1
        reasons[record["reason_code"]] = reasons.get(record["reason_code"], 0) + 1
        if record["disposition"] == "unresolved" and len(examples) < 3:
            examples.append({"row": "来源记录", "source_row": record["source_row"], "member_path": record["member_path"], "reason": record["details"].get("reason") or REASONS[record["reason_code"]]})
        if plan["profile"] in ({table_direction_recheck.PROFILE} | TIERED_PROFILES) and table_direction_recheck.DETAIL_FIELD in record["details"]:
            review = recheck_counts.setdefault(record["member_path"], {"verified": 0, "pending": 0})
            review["verified" if record["disposition"] == "accepted" else "pending"] += 1
    for document in plan.get("documents", ()):
        if document["member_path"] in recheck_counts:
            document["direction_recheck"] = recheck_counts[document["member_path"]]
    for row in plan["rows"]:
        totals[row["currency"]] = totals.get(row["currency"], Decimal(0)) + row["amount"]
    plan["stats"] = {"total": len(plan["records"]), "ok": len(plan["rows"]), "skipped": len(plan["records"]) - len(plan["rows"]), "bad_examples": examples, **counts, "reason_counts": reasons, "currency_totals": {key: format(value, ".2f") for key, value in sorted(totals.items())}}
    return plan


def _feedback_member_outcome(headers, cells, generic, profile, *, header_names=None, transaction_status_policy=None):
    """The closed 28-column family keeps its admission/facts in mixed folders.

    The common table journal records this versioned adapter. It does not broaden
    the bank reader's currency/status/time contract or recompute its summaries.
    """
    from . import feedback_import
    if header_names is None:
        header_names = (_text(header) for header in headers)
    source = {header: _text(value) for header, value in zip(header_names, cells)}
    row, disposition, reason, legacy_details, _context = feedback_import._transaction(
        source, feedback_import.MICROSECOND_EVIDENCE_PROFILE if profile in MICROSECOND_ARCHIVE_PROFILES
        else feedback_import.EVIDENCE_PROFILE, transaction_status_policy=transaction_status_policy)
    notes = dict(generic[3])
    notes.pop("flow_sha256", None)
    notes.pop("validation_issues", None)
    notes.pop("validation_basis", None)
    if profile in MICROSECOND_ARCHIVE_PROFILES:
        # The closed adapter owns the reference even when generic inference
        # could not interpret this row. Cancellation package verification must
        # bind to the actual source transaction ID, never a voucher fallback.
        notes["trade_no_basis"] = "source_transaction_id" if source["交易流水号"] else "none"
    if row is not None:
        notes.update(financial_effect="source_reported", flow_sha256=flow_digest(row))
        return row, "accepted", "transaction_valid", notes
    if disposition == "unresolved":
        mapped = ("negative_amount" if reason.startswith("negative_") else "invalid_balance" if reason in {"balance_precision", "balance_overflow"}
                  else generic[2] if reason == "missing_core" and generic[1] == "unresolved" else "missing_identity" if reason == "missing_core" else reason)
        if mapped not in REASONS:
            mapped = "unknown_table"
        notes.update(financial_effect="undetermined", validation_issues=mapped)
        # Preserve other independently detected faults, so cancellation cannot
        # treat an invalid negative as a proven two-leg reversal.
        other_issues = [value for value in generic[3].get("validation_issues", "").split(",") if value and value != mapped]
        if other_issues:
            notes["validation_issues"] = ",".join((mapped, *other_issues))
        return None, "unresolved", mapped, notes
    notes["financial_effect"] = "no_movement" if disposition == "non_monetary" else "not_a_flow"
    return None, disposition, reason, notes
