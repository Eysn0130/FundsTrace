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


class CaseStats(ContractModel):
    funds: FundsProjection | None = None
    analysis_revision: str | None = None
    event_summary: EventSummary | None = None
    statistics_policy: Literal["typed-account-evidence-v1", "evidence-bounded-funds-v1"] | None = None
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


class PreviewFlow(ContractModel):
    trade_no: str
    trade_time: str | None
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


class RepreviewRequest(ContractModel):
    mode: Literal["A", "B"]
    mapping: dict[str, str]


class CommitRequest(RepreviewRequest):
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


class ImportRecordPage(ContractModel):
    case_id: Identifier
    source_id: str
    items: list[ImportRecord]
    total: int
    page: int
    page_size: int
