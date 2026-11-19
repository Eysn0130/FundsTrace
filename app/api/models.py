"""Explicit JSON models: identifiers and decimal amounts never use JS numbers."""
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


Identifier = Annotated[str, Field(pattern=r"^[1-9][0-9]*$")]
Amount = Annotated[str, Field(pattern=r"^-?[0-9]+\.[0-9]{2}$")]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApiErrorDetail(ContractModel):
    code: str
    message: str


class ApiError(ContractModel):
    error: ApiErrorDetail


class Case(ContractModel):
    id: Identifier
    name: str
    created_at: str | None


class CaseList(ContractModel):
    items: list[Case]


class CaseCreate(ContractModel):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, name):
        name = name.strip()
        if not name:
            raise ValueError("案件名不能为空")
        return name


class CurrencyAmount(ContractModel):
    currency: str
    amount: Amount


class ProjectedCurrencyAmount(ContractModel):
    currency: str | None
    amount: Amount | None


class FundsAmount(ProjectedCurrencyAmount):
    lower: Amount
    upper: Amount | None


class FundsCount(ContractModel):
    lower: int
    upper: int


class FundsProjection(ContractModel):
    policy: Literal["evidence-bounded-funds-v1"]
    status: Literal["exact", "bounded", "unknown"]
    observation_count: int
    confirmed_event_count: int
    unresolved_observation_count: int
    count: FundsCount
    totals: list[FundsAmount]


class ImportCoverage(ContractModel):
    scope: Literal["source_reported_flows"]
    unresolved_records: int
    non_monetary_records: int
    cancelled_records: int = 0
    excluded_flows: int = 0
    financial_interpretation_complete: bool


class ResolutionSummary(ContractModel):
    policy: Literal["typed-account-evidence-v1"]
    revision: str | None
    entities: int
    resolved_endpoints: int
    unresolved_endpoints: int
    conflicts: int
    auxiliary_records: int
    evidence_records: int
    note: str


class ResolutionEntity(ContractModel):
    entity_id: str
    kind: Literal["account", "card", "subaccount", "identifier"]
    account_number: str
    display_account: str
    display_basis: str = "account_number"
    display_card_tied: bool = False
    display_card_candidates: list[str] = Field(default_factory=list)
    display_card_order: str = "none"
    bank: str
    cards: list[str]
    parent_entity_id: str | None
    name_conflict: bool = False
    names: list[str]
    status: str


class ResolutionRegistry(ContractModel):
    case_id: Identifier
    resolution: ResolutionSummary | None
    entities: list[ResolutionEntity]


class EventSummary(ContractModel):
    policy: str
    revision: str
    confirmed_events: int
    confirmed_entries: int
    conflict_entries: int
    unknown_entries: int
    observed_entries: int
    reference_observations: int
    excluded_reference_entries: int
    unresolved_reference_observations: int
    confirmed_amount_totals: list[CurrencyAmount]
    complete_event_total: bool
    note: str


class SourceLedgerSummary(ContractModel):
    count: int
    unknown_effect_count: int
    zero_count: int
    paired_count: int


class CaseStats(ContractModel):
    source_ledger: SourceLedgerSummary | None = None
    funds: FundsProjection | None = None
    analysis_revision: str | None = None
    event_summary: EventSummary | None = None
    statistics_policy: Literal["typed-account-evidence-v1", "evidence-bounded-funds-v1", "source-observations-v1", "selected-transactions-v1"] | None = None
    resolution_revision: str | None = None
    resolution: ResolutionSummary | None = None
    case_id: Identifier
    flows: int | None
    accounts: int
    batches: int
    amount_total: Amount | None
    currency: str | None
    amount_totals: list[ProjectedCurrencyAmount]
    import_coverage: ImportCoverage | None = None


class SourceBalanceChange(ContractModel):
    status: Literal["observed", "unknown"]
    difference: Amount | None
    before: Amount | None
    after: Amount | None
    basis: str | None
    reason: str | None
    source_id: str
    previous_source_row: int | None


class SourceLedgerInfo(ContractModel):
    policy: Literal["source-ledger-observations-v1", "source-ledger-unoriented-v1", "source-ledger-unoriented-v2"]
    signed_amount: Amount
    source_direction: str
    self_account: str
    counter_account: str = ""
    counter_name: str = ""
    counter_bank: str = ""
    direction: Literal["in", "out", "unknown"]
    economic_effect: Literal["unknown", "no_movement", "paired_cancellation"]
    economic_amount: Amount | None
    reported_status: str
    balance_change: SourceBalanceChange
    source_id: str
    member_path: str
    source_row: int


class PreviewFlow(ContractModel):
    trade_no: str
    trade_time: str | None
    trade_time_wall: str | None = None
    trade_time_utc: str | None = None
    trade_time_original: str | None = None
    trade_time_offset: str | None = None
    trade_time_basis: Literal["utc", "unqualified"] = "unqualified"
    payer_name: str
    payer_account: str
    payer_bank: str
    payee_name: str
    payee_account: str
    payee_bank: str
    amount: Amount
    balance: Amount | None
    currency: str
    summary: str
    channel: str
    self_account: str
    self_name: str
    source_member: str | None = None
    source_row: int | None = None
    source_record_no: int | None = None


class Flow(PreviewFlow):
    source_ledger: SourceLedgerInfo | None = None
    amount: Amount | None
    observation_ids: list[Identifier] = Field(default_factory=list)
    observation_count: int = 1
    granularity: Literal["confirmed_event", "unresolved_observation", "observation"] = "observation"
    event_id: str | None = None
    event_state: str = "unknown"
    analysis_revision: str | None = None
    payer_display_account: str = ""
    payee_display_account: str = ""
    self_display_account: str = ""
    raw_payer_account: str = ""
    raw_payee_account: str = ""
    raw_self_account: str = ""
    payer_card: str = ""
    payee_card: str = ""
    self_card: str = ""
    payer_source_account: str = ""
    payee_source_account: str = ""
    self_source_account: str = ""
    payer_card_state: str = ""
    payee_card_state: str = ""
    self_card_state: str = ""
    id: Identifier
    case_id: Identifier
    batch_id: Identifier | None


class FlowPage(ContractModel):
    funds: FundsProjection | None = None
    analysis_revision: str | None = None
    case_id: Identifier
    items: list[Flow]
    total: int
    page: int
    page_size: int


class AccountIdentity(ContractModel):
    account: str
    name: str


class AccountCandidate(AccountIdentity):
    count: int


class BadExample(ContractModel):
    row: str
    reason: str
    source_row: int | None = None


class PreviewStats(BaseModel):
    model_config = ConfigDict(extra="allow")
    total: int
    ok: int
    skipped: int
    bad_examples: list[BadExample] = Field(default_factory=list)


class FeedbackMember(ContractModel):
    member_path: str
    role: str
    sha256: str
    size_bytes: int
    encoding: str
    row_count: int


class FeedbackExample(ContractModel):
    member_path: str
    source_row: int
    reason_code: str
    details: dict[str, str] = Field(default_factory=dict)


class FeedbackSummary(ContractModel):
    profile: str
    record_total: int
    transaction_total: int
    accepted: int
    review: int
    non_monetary: int = 0
    unresolved: int = 0
    cancelled: int = 0
    excluded_flows: int = 0
    non_transaction: int
    auxiliary: int
    reason_counts: dict[str, int]
    currency_totals: dict[str, Amount]
    members: list[FeedbackMember]
    review_examples: list[FeedbackExample]
    unresolved_examples: list[FeedbackExample] = Field(default_factory=list)


class TableSummary(ContractModel):
    header_plan_sha256: str | None = None
    unsupported_attachments: int = 0
    non_business_attachments: int = 0
    profile: str
    record_total: int
    accepted: int
    cancelled: int = 0
    excluded_flows: int = 0
    effective_added: int | None = None
    unresolved: int
    non_monetary: int
    non_transaction: int
    auxiliary: int
    reason_counts: dict[str, int]
    currency_totals: dict[str, Amount]
    members: list[FeedbackMember]
    unresolved_examples: list[FeedbackExample] = Field(default_factory=list)
    documents: list[dict[str, Any]] = Field(default_factory=list)
    mapping_evidence: list[dict[str, str]] = Field(default_factory=list)


class ImportPreview(ContractModel):
    resolution: ResolutionSummary | None = None
    case_id: Identifier
    import_id: str
    filename: str
    mode: Literal["A", "B"]
    columns: list[str]
    mapping: dict[str, str]
    stats: PreviewStats
    preview: list[PreviewFlow]
    self_accounts_in_file: list[AccountCandidate]
    feedback: FeedbackSummary | None = None
    table_summary: TableSummary | None = None
    automatic: bool = False
    reference_policy_available: bool = False
    reference_policy: Literal["transaction-reference-v1"] | None = None


class RepreviewRequest(ContractModel):
    mode: Literal["A", "B"]
    mapping: dict[str, str]
    # Explicit caller choice for an uncataloged table layout: keep the source
    # transaction id and the voucher as separate facts instead of rejecting
    # every row whose voucher differs. Never inferred from cell values.
    reference_policy: Literal["transaction-reference-v1"] | None = None


class CommitRequest(RepreviewRequest):
    material_group_id: str | None = Field(default=None,pattern=r'^[0-9a-f]{32}$')
    self_accounts: list[str] = Field(default_factory=list)
    mark_accounts: list[str] | None = None

    @field_validator("self_accounts", "mark_accounts")
    @classmethod
    def account_values(cls, values):
        if values is not None and any(not value.strip() or any(c in value for c in ",，;；\t\r\n ") for value in values):
            raise ValueError("账号不得为空或包含分隔符")
        return values


class CommitStarted(ContractModel):
    case_id: Identifier
    import_id: str
    started: bool


class ImportResult(ContractModel):
    resolution: ResolutionSummary | None = None
    total: int
    ok: int
    skipped: int
    rows_dup: int
    batch_id: Identifier
    bad_examples: list[BadExample]
    self_accounts: list[AccountIdentity]
    self_accounts_count: int
    main_matched: list[str]
    accounts_in_batch: int
    main_detected: list[AccountIdentity]
    source_id: str | None = None
    feedback: FeedbackSummary | None = None
    table_summary: TableSummary | None = None


class ImportMemberResult(ContractModel):
    member_path: str
    dispositions: dict[str, int]


class ImportJob(ContractModel):
    case_id: Identifier
    import_id: str
    stage: Literal["parsing", "importing", "done", "error"]
    done: int
    total: int
    message: str
    result: ImportResult | None
    error: str | None
    error_code: str | None = None
    recoverable: bool = False
    source_available: bool = False
    withdrawn: bool = False
    member_results: list[ImportMemberResult] = Field(default_factory=list)


class ImportSession(ContractModel):
    case_id: Identifier
    import_id: str
    surface: Literal["v1", "legacy"]
    filename: str
    mode: Literal["A", "B"]
    mapping: dict[str, str]
    stage: Literal["ready", "parsing", "importing", "done", "error"]
    source_state: Literal["retained", "cleaned"]
    created_at: str
    expires_at: str | None
    error_code: str | None
    attempt: int
    source_available: bool
    recoverable: bool
    request: CommitRequest | None
    result: ImportResult | None


class ImportSessionList(ContractModel):
    case_id: Identifier
    items: list[ImportSession]


class ImportSourceMetadata(ContractModel):
    case_id: Identifier
    source_id: str
    filename: str
    sha256: str
    size_bytes: int
    registered_at: str
    material_name: str | None
    source_available: bool
    active_batch_id: Identifier | None
    withdrawn: bool


class WithdrawnImport(ContractModel):
    case_id: Identifier
    source_id: str
    batch_id: Identifier


class CleanupRequest(ContractModel):
    dry_run: bool = True


class CleanupResult(ContractModel):
    dry_run: bool
    eligible: int
    deleted: int
    missing: int
    failed: int
    protected: int
    queued: int


class FlowSource(ContractModel):
    observation_flow_id: Identifier | None = None
    observation_amount: Amount | None = None
    observation_balance: Amount | None = None
    observation_currency: str | None = None
    observation_account: str | None = None
    source_id: str
    filename: str
    sha256: str
    size_bytes: int
    mode: Literal["A", "B"]
    mapping: dict[str, str]
    sheet: str
    row_kind: Literal["csv_record", "worksheet_row", "archive_record", "table_record", "unknown"]
    source_row: int
    parser_version: str
    disposition: Literal["imported", "duplicate"]
    origin: Literal["upload", "package"]
    imported_at: str
    source_available: bool
    member_path: str | None = None
    member_row: int | None = None
    record_details: dict[str, str] = Field(default_factory=dict)


class FlowSourceList(ContractModel):
    case_id: Identifier
    flow_id: Identifier
    items: list[FlowSource]
    total: int
    page: int
    page_size: int


class ImportRecord(ContractModel):
    record_no: int
    member_path: str
    source_row: int
    record_key: str
    disposition: Literal["imported", "duplicate", "review", "unresolved", "non_monetary", "cancelled", "non_transaction", "auxiliary", "removed"]
    reason_code: str
    details: dict[str, str]
    flow_id: Identifier | None
    preview: PreviewFlow | None = None


class ImportRecordPage(ContractModel):
    case_id: Identifier
    source_id: str
    items: list[ImportRecord]
    total: int
    page: int
    page_size: int


class ImportOriginalCell(ContractModel):
    column: int
    header: str
    value: str
    fields: list[str]
    kind: Literal['text', 'number', 'date', 'boolean', 'error', 'formula'] = 'text'


class ImportReviewRecord(ImportRecord):
    original: list[ImportOriginalCell] | None = None


class ImportReviewIssue(ContractModel):
    index: int
    record_no: int
    source_row: int
    reason_code: str


class ImportReviewPage(ContractModel):
    case_id: Identifier
    source_id: str
    member_path: str
    view: Literal['all', 'review', 'accepted']
    counts: dict[str, int]
    items: list[ImportReviewRecord]
    total: int
    page: int
    page_size: int
    issue: ImportReviewIssue | None = None
    original_error: str | None = None


class ImportReportIntegrity(ContractModel):
    status: Literal['registered', 'unavailable', 'match', 'mismatch']
    object_name: str
    expected_sha256: str
    observed_sha256: str | None
    checked_at: str | None
    size_bytes: int


class ImportReportScope(ContractModel):
    filename: str
    member_path: str
    member_role: str
    source_sha256: str
    source_size_bytes: int
    member_sha256: str
    member_size_bytes: int | None
    member_digest_kind: Literal['bytes', 'worksheet_locator']
    parser_version: str
    imported_at: str
    material_name: str | None = None
    material_origin_name: str | None = None
    source_record_total: int | None = None


class ImportReportQuality(ContractModel):
    source_records: int
    dispositions: dict[str, int]
    linked_observations: int
    selected_transactions: int
    pending_observations: int
    unattributed_transactions: int


class ImportReportDirectional(ContractModel):
    in_count: int
    out_count: int
    in_amount: Amount | None
    out_amount: Amount | None
    net_amount: Amount | None


class ImportReportSummary(ImportReportDirectional):
    transaction_count: int
    amount: Amount | None


class ImportReportAccount(ImportReportDirectional):
    account: str
    account_number: str
    account_numbers: list[str]
    names: list[str]
    cards: list[str]
    currency: str


class ImportReportAccounts(ContractModel):
    items: list[ImportReportAccount]
    total: int
    page: int
    page_size: int


class ImportReportOverview(ContractModel):
    name_count: int
    account_count: int
    number_count: int
    unnamed_account_count: int
    selected_account: ImportReportAccount | None = None


class ImportReportTimelinePage(ContractModel):
    total: int
    page: int
    page_size: int


class ImportReportPeer(ContractModel):
    account: str
    account_number: str
    names: list[str]
    count: int
    amount: Amount | None


class ImportReportPeriod(ContractModel):
    first_time: str | None
    last_time: str | None
    time_basis: Literal['utc', 'unqualified', 'mixed']


class ImportReportTimeBucket(ImportReportDirectional):
    start: str
    end: str
    time_basis: Literal['utc', 'unqualified']
    has_records: bool = True
    transaction_count: int | None = None


class ImportReportTrendScope(ContractModel):
    mode: Literal['all','selected','except']
    accounts: list[str]
    search: str = ''
    account_count: int
    available_account_count: int
    undated_transactions: int
    summary: ImportReportDirectional
    periods: list[ImportReportPeriod]


class ImportReportAccountChoice(ContractModel):
    account: str
    account_number: str
    account_numbers: list[str]
    names: list[str]
    cards: list[str]
    currency: str


class ImportReportAccountOptions(ContractModel):
    case_id: Identifier
    source_id: str
    member_path: str
    analysis_revision: str
    items: list[ImportReportAccountChoice]
    total: int
    page: int
    page_size: int


class ImportReportRelation(ImportReportDirectional, ImportReportPeriod):
    account: str
    counterparty: str
    account_number: str
    counterparty_number: str
    names: list[str]
    currency: str


class ImportReportRelations(ContractModel):
    items: list[ImportReportRelation]
    total: int
    page: int
    page_size: int


class ImportReportMethodParagraph(ContractModel):
    title: str
    text: str


class ImportReport(ContractModel):
    case_id: Identifier
    source_id: str
    member_path: str
    analysis_revision: str
    policy: Literal['import-member-funds-report-v1']
    generated_at: str
    scope: ImportReportScope
    quality: ImportReportQuality
    perspective: Literal['own_ledger', 'transaction_endpoints']
    currency: str | None
    currencies: list[str]
    account: str | None
    accounts: ImportReportAccounts
    overview: ImportReportOverview | None = None
    peer_account: str | None = None
    peer_selected_account: ImportReportAccount | None = None
    summary: ImportReportSummary
    top_in: list[ImportReportPeer]
    top_out: list[ImportReportPeer]
    timeline: list[ImportReportTimeBucket]
    timeline_grain: str
    timeline_mode: Literal['auto','day','month','year'] = 'auto'
    timeline_pagination: ImportReportTimelinePage | None = None
    timeline_scope: ImportReportTrendScope | None = None
    periods: list[ImportReportPeriod]
    relations: ImportReportRelations
    integrity: ImportReportIntegrity
    notes: list[str]
    method_paragraphs: list[ImportReportMethodParagraph] = Field(default_factory=list)


class ImportReportRecords(ImportRecordPage):
    analysis_revision: str
