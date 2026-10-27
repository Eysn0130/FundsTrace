# -*- coding: utf-8 -*-
"""DuckDB 存储层：连接、建表、旧库迁移、线程安全的通用查询"""
import datetime
import decimal
import threading
from contextlib import contextmanager

import duckdb

from .paths import ROOT, BUNDLE_DIR, DATA_DIR

DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "fund.duckdb"

_lock = threading.RLock()
_con = None
_transaction_state = threading.local()


class TransactionRollbackError(RuntimeError):
    """A nested failure prevented the enclosing transaction from committing."""


@contextmanager
def import_transaction():
    """Keep import joins in their declared order while new rows lack statistics.

    The outer operation owns the connection through commit/rollback and restores
    its original settings afterwards, including when a query aborts the SQL
    transaction (when SET itself is no longer allowed).
    """
    with _lock:
        if getattr(_transaction_state, "depth", 0):
            # An enclosing operation owns both its transaction and query policy.
            with transaction() as c:
                yield c
            return
        c = con()
        previous = c.execute("select current_setting('disabled_optimizers')").fetchone()[0]
        disabled = [value for value in previous.split(",") if value]
        for name in ("join_order", "build_side_probe_side"):
            if name not in disabled:
                disabled.append(name)
        c.execute("set disabled_optimizers=?", [",".join(disabled)])
        try:
            with transaction() as c:
                yield c
        finally:
            c.execute("set disabled_optimizers=?", [previous])


@contextmanager
def transaction():
    """Hold the shared connection lock for one atomic, reentrant operation.

    Nested contexts join the outer transaction. A failure escaping any nested
    context marks the whole operation rollback-only, even if its caller catches
    that failure before leaving the outer context.
    """
    with _lock:
        c = con()
        state = _transaction_state
        outermost = getattr(state, "depth", 0) == 0
        if outermost:
            c.execute("begin transaction")
            state.rollback_only = False
        state.depth = getattr(state, "depth", 0) + 1
        body_failed = False
        try:
            yield c
        except BaseException:
            body_failed = True
            state.rollback_only = True
            raise
        finally:
            state.depth -= 1
            if outermost:
                try:
                    if state.rollback_only:
                        c.execute("rollback")
                        if not body_failed:
                            raise TransactionRollbackError(
                                "Transaction is rollback-only because a nested operation failed"
                            )
                    else:
                        try:
                            c.execute("commit")
                        except BaseException:
                            try:
                                c.execute("rollback")
                            except Exception:
                                pass
                            raise
                finally:
                    state.rollback_only = False

SCHEMA = """
create sequence if not exists flow_seq;
create sequence if not exists batch_seq;
create sequence if not exists case_seq;

create table if not exists flows (
    id            bigint primary key default nextval('flow_seq'),
    case_id       integer default 1,
    batch_id      bigint,
    trade_no      varchar,
    trade_time    timestamp,
    payer_name    varchar,
    payer_account varchar,
    payer_bank    varchar,
    payee_name    varchar,
    payee_account varchar,
    payee_bank    varchar,
    amount        decimal(18,2),
    balance       decimal(18,2),
    currency      varchar default 'CNY',
    summary       varchar,
    channel       varchar,
    self_account  varchar,
    self_name     varchar,
    created_at    timestamp default current_timestamp
);

create table if not exists batches (
    id            bigint primary key default nextval('batch_seq'),
    case_id       integer default 1,
    filename      varchar,
    mode          varchar,
    rows_total    bigint,
    rows_imported bigint,
    rows_skipped  bigint,
    rows_dup      bigint,
    imported_at   timestamp default current_timestamp
);

create table if not exists cases (
    id         integer primary key default nextval('case_seq'),
    name       varchar unique,
    created_at timestamp default current_timestamp
);

create table if not exists tracked_flows (
    case_id    integer,
    flow_id    bigint,
    canvas_id  integer default 0,
    tracked_at timestamp default current_timestamp,
    primary key (case_id, flow_id, canvas_id)
);

create table if not exists main_accounts (
    case_id   integer,
    account   varchar,
    name      varchar,
    source    varchar default 'manual',
    marked_at timestamp default current_timestamp,
    primary key (case_id, account)
);

create table if not exists account_ids (
    case_id    integer,
    account    varchar,
    id_no      varchar,
    updated_at timestamp default current_timestamp,
    primary key (case_id, account)
);

create table if not exists account_photos (
    case_id    integer,
    account    varchar,
    fname      varchar,
    crop_cx    double,
    crop_cy    double,
    crop_r     double,
    updated_at timestamp default current_timestamp,
    primary key (case_id, account)
);

create table if not exists focus_nodes (
    case_id integer,
    account varchar,
    scale double default 1.6,
    primary key (case_id, account)
);

create sequence if not exists canvas_seq;

create table if not exists canvases (
    id         bigint primary key default nextval('canvas_seq'),
    case_id    integer,
    name       varchar,
    snapshot   varchar,          -- 画布快照 JSON（节点位置/线型/折点/标注位置等）
    updated_at timestamp default current_timestamp
);

create table if not exists import_sessions (
    import_id varchar primary key,
    case_id integer,
    surface varchar not null,
    filename varchar not null,
    suffix varchar not null,
    sha256 varchar not null,
    size_bytes bigint not null,
    mode varchar,
    mapping_json varchar,
    columns_json varchar,
    stage varchar default 'ready',
    done bigint default 0,
    total bigint default 0,
    request_json varchar,
    result_json varchar,
    error_code varchar,
    attempt integer default 0,
    owner_token varchar,
    source_state varchar default 'retained',
    created_at timestamp default (current_timestamp at time zone 'UTC'),
    updated_at timestamp default (current_timestamp at time zone 'UTC'),
    expires_at timestamp
);

create table if not exists import_sources (
    source_id varchar primary key,
    case_id integer not null,
    filename varchar not null,
    sha256 varchar not null,
    size_bytes bigint not null,
    mode varchar not null,
    mapping_json varchar not null,
    sheet varchar not null,
    parser_version varchar not null,
    origin varchar default 'upload',
    row_kind varchar default 'unknown',
    imported_at timestamp default (current_timestamp at time zone 'UTC')
);

create table if not exists flow_sources (
    case_id integer not null,
    source_id varchar not null,
    source_row bigint not null,
    flow_id bigint not null,
    disposition varchar not null,
    primary key (case_id, source_id, source_row)
);

create table if not exists import_members (
    case_id integer not null,
    source_id varchar not null,
    member_path varchar not null,
    role varchar not null,
    sha256 varchar not null,
    size_bytes bigint not null,
    encoding varchar not null,
    row_count bigint not null,
    primary key (source_id, member_path)
);

create table if not exists import_records (
    case_id integer not null,
    source_id varchar not null,
    record_no bigint not null,
    member_path varchar not null,
    source_row bigint not null,
    record_key varchar not null,
    disposition varchar not null,
    reason_code varchar not null,
    details_json varchar not null,
    primary key (case_id, source_id, record_no)
);

create table if not exists evidence_sources (
    case_id integer not null, source_id varchar not null, batch_id bigint, active boolean not null,
    primary key (case_id,source_id)
);
create table if not exists analysis_state (
    case_id integer primary key, revision varchar not null
);
create table if not exists event_state (
    case_id integer primary key, revision varchar not null, summary_json varchar not null
);
create table if not exists resolved_events (
    case_id integer not null, event_id varchar not null,
    payer_account varchar, payee_account varchar, currency varchar,
    amount decimal(18,2), trade_time timestamp, evidence_json varchar,
    primary key (case_id,event_id)
);
create table if not exists event_members (
    case_id integer not null, flow_id bigint not null, event_id varchar,
    status varchar not null, reason varchar not null,
    primary key (case_id,flow_id)
);
-- Disposable event-maintenance cache. Source journals remain authoritative;
-- case packages exclude these tables and rebuild them after restoration.
create table if not exists event_projection_cache_state (
    case_id integer primary key, version varchar not null, sources_json varchar not null default '{}'
);
alter table event_projection_cache_state add column if not exists sources_json varchar default '{}';
create table if not exists event_observation_cache (
    case_id integer not null, source_id varchar not null, record_no bigint not null,
    reference_key varchar not null, flow_id bigint, effective boolean not null,
    payer_account varchar, payee_account varchar, self_account varchar,
    currency varchar, amount decimal(18,2), trade_time timestamp,
    source_typed boolean not null, typed_accounts boolean not null, evidence varchar not null,
    primary key (case_id,source_id,record_no)
);
create index if not exists event_observation_cache_reference on event_observation_cache(case_id,reference_key);
create index if not exists event_observation_cache_flow on event_observation_cache(case_id,flow_id);
create table if not exists resolution_state (
    case_id integer primary key, revision varchar not null
);
create table if not exists resolution_revisions (
    case_id integer not null, revision varchar not null, summary_json varchar not null,
    decisions_json varchar not null, created_at timestamp default (current_timestamp at time zone 'UTC'),
    primary key (case_id, revision)
);
create table if not exists resolution_entities (
    case_id integer not null, entity_id varchar not null, kind varchar not null,
    account_number varchar not null, display_account varchar not null, bank varchar not null,
    details_json varchar not null, primary key (case_id, entity_id)
);
create table if not exists resolved_endpoints (
    case_id integer not null, flow_id bigint not null, role varchar not null,
    entity_id varchar not null, state varchar not null, reason varchar not null,
    primary key (case_id, flow_id, role)
);
create table if not exists resolution_subject_aliases (
    case_id integer not null, raw_account varchar not null, account varchar not null,
    primary key (case_id, raw_account)
);

create table if not exists import_file_gc (
    import_id varchar primary key,
    suffix varchar not null,
    sha256 varchar not null,
    delete_after timestamp not null
);
"""

# 索引须在旧库迁移（补 case_id 列）之后创建。
# 注意：不再创建二级索引——DuckDB 1.5.5 在删除带索引表的行时会触发
# "Failed to delete all rows from index" 致命错误并使整个数据库失效；
# 本系统的数据量级（数十万行内）顺序扫描完全够用，删除批次是高频操作，故移除索引。
INDEXES = """
drop index if exists idx_flows_payer;
drop index if exists idx_flows_payee;
drop index if exists idx_flows_time;
drop index if exists idx_flows_trade_no;
drop index if exists idx_flows_case;
"""


def _migrate(c):
    """旧库升级：为 flows / batches 补充 case_id 列（历史数据归入默认案件 1），
    为 main_accounts 补充 source 列；金额/余额由 DOUBLE 升级为 DECIMAL(18,2)（精确到分）；
    account_photos 补充裁剪参数列"""
    for table, col, ddl in (("flows", "case_id", "integer default 1"),
                            ("batches", "case_id", "integer default 1"),
                            ("flows", "self_account", "varchar"),
                            ("flows", "self_name", "varchar"),
                            ("main_accounts", "source", "varchar default 'manual'")):
        cols = {r[1] for r in c.execute(f"pragma table_info('{table}')").fetchall()}
        if cols and col not in cols:
            c.execute(f"alter table {table} add column {col} {ddl}")

    fcols = {r[1]: (r[2] or "") for r in c.execute("pragma table_info('flows')").fetchall()}
    if fcols:
        for col in ("amount", "balance"):
            if fcols.get(col) and "decimal" not in fcols[col].lower():
                c.execute(f"alter table flows alter column {col} type decimal(18,2)")

    pcols = {r[1] for r in c.execute("pragma table_info('account_photos')").fetchall()}
    if pcols:
        for col in ("crop_cx", "crop_cy", "crop_r"):
            if col not in pcols:
                c.execute(f"alter table account_photos add column {col} double")

    focols = {r[1]: (r[2] or "") for r in c.execute("pragma table_info('focus_nodes')").fetchall()}
    if focols and "scale" not in focols:
        c.execute("alter table focus_nodes add column scale double default 1.6")

    # tracked_flows 升级为多画布结构：同一笔流水可在不同画布分别追踪。
    # 旧表主键不含 canvas_id 且 DuckDB 不能改主键 → 重建表，历史数据归入默认画布(0)。
    tcols = c.execute("pragma table_info('tracked_flows')").fetchall()
    if tcols and "canvas_id" not in {r[1] for r in tcols}:
        c.execute("""create table tracked_flows_new (
            case_id    integer,
            flow_id    bigint,
            canvas_id  integer default 0,
            tracked_at timestamp default current_timestamp,
            primary key (case_id, flow_id, canvas_id))""")
        c.execute("insert into tracked_flows_new(case_id, flow_id, canvas_id, tracked_at) "
                  "select case_id, flow_id, 0, tracked_at from tracked_flows")
        c.execute("drop table tracked_flows")
        c.execute("alter table tracked_flows_new rename to tracked_flows")


def con():
    global _con, _ckpt_started
    with _lock:
        if _con is None:
            candidate = duckdb.connect(str(DB_PATH))
            try:
                candidate.execute("begin transaction")
                for stmt in SCHEMA.strip().split(";"):
                    if stmt.strip():
                        candidate.execute(stmt)
                _migrate(candidate)
                # Keep original facts and source links immutable. A versioned
                # paired-cancellation journal excludes the same physical
                # original occurrence from every effective-money reader,
                # including a later reimport under an older parser.
                # Narrow physical links before parsing JSON so metadata hashes
                # cannot become join-build keys over the complete journal.
                # Materialize each lookup before joining the next table: new
                # rows in this transaction can have zero cardinality estimates,
                # otherwise the optimizer joins every flow by case first.
                from . import table_cancellation_links
                candidate.execute(f"""create or replace view source_effective_flows as
                    with recursive {table_cancellation_links.ctes()},
                    table_cancel_proofs as materialized (
                      select c.case_id,c.record_key,c.member_path,c.source_row,c.details_json,cm.sha256
                      from import_records c
                      join import_sources p on p.case_id=c.case_id and p.source_id=c.source_id
                      join import_members cm on cm.case_id=c.case_id and cm.source_id=c.source_id
                        and cm.member_path=c.member_path
                      where p.row_kind='table_record' and p.parser_version in ('funds-trace-table-archive-v3','funds-trace-table-archive-v4','funds-trace-table-archive-v5','funds-trace-table-archive-v6')
                        and c.disposition='cancelled' and c.reason_code='matched_cancellation'
                    ), table_cancel_positions as materialized (
                      select c.*,rm.source_id
                      from table_cancel_proofs c
                      join import_members rm on rm.case_id=c.case_id and rm.member_path=c.member_path and rm.sha256=c.sha256
                    ), table_cancel_records as materialized (
                      select r.case_id,r.source_id,r.record_no,r.details_json source_details_json,
                        c.details_json cancellation_details_json
                      from import_records r
                      join table_cancel_positions c on r.case_id=c.case_id and r.source_id=c.source_id
                        and r.record_key=c.record_key and r.member_path=c.member_path and r.source_row=c.source_row
                    ), table_cancel_source_links as materialized (
                      select r.*,fs.source_row,fs.flow_id
                      from flow_sources fs
                      join table_cancel_records r on fs.case_id=r.case_id and fs.source_id=r.source_id and fs.source_row=r.record_no
                    ), table_cancel_candidates as materialized (
                      select r.case_id,r.source_id,r.source_row,r.flow_id,r.source_details_json,
                        r.cancellation_details_json,f.self_account,f.self_name
                      from flows f
                      join table_cancel_source_links r on f.case_id=r.case_id and f.id=r.flow_id
                    ), table_same_path_cancelled_links as materialized (
                      select distinct case_id,source_id,source_row,flow_id from table_cancel_candidates
                      where json_extract_string(try_cast(cancellation_details_json as json),'$.cancellation_role')='original'
                        and json_extract_string(try_cast(cancellation_details_json as json),'$.cancellation_policy')='table-paired-cancellation-v1'
                        and json_extract_string(try_cast(source_details_json as json),'$.flow_sha256')=
                            json_extract_string(try_cast(cancellation_details_json as json),'$.flow_sha256')
                        and case when coalesce(self_account,'')='' then '' else sha256(self_account) end=
                            json_extract_string(try_cast(cancellation_details_json as json),'$.cancellation_own_account_sha256')
                        and case when coalesce(self_name,'')='' then '' else sha256(self_name) end=
                            json_extract_string(try_cast(cancellation_details_json as json),'$.cancellation_own_name_sha256')
                    ), table_cancelled_links as materialized (
                      select * from table_same_path_cancelled_links
                      union select * from a02_shared_links
                    ), table_cancelled_flows as materialized (
                      select fs.case_id,fs.flow_id from flow_sources fs
                      left join table_cancelled_links c on c.case_id=fs.case_id and c.source_id=fs.source_id
                        and c.source_row=fs.source_row and c.flow_id=fs.flow_id
                      group by fs.case_id,fs.flow_id having count(*)=count(c.flow_id)
                    )
                    select f.* from flows f where not exists (
                      select 1 from flow_sources fs
                      join import_records r on r.case_id=fs.case_id
                        and r.source_id=fs.source_id and r.record_no=fs.source_row
                      join import_records c on c.case_id=r.case_id and c.record_key=r.record_key
                      join import_sources p on p.case_id=c.case_id and p.source_id=c.source_id
                      where fs.case_id=f.case_id and fs.flow_id=f.id
                        and p.row_kind='archive_record'
                        and p.parser_version in ('funds-trace-bank-feedback-v3','funds-trace-bank-feedback-v4','funds-trace-bank-feedback-v5','funds-trace-bank-feedback-v6')
                        and c.disposition='cancelled' and c.reason_code='matched_cancellation'
                        and json_extract_string(try_cast(c.details_json as json),'$.cancellation_role')='original'
                        and json_extract_string(try_cast(c.details_json as json),'$.cancellation_policy')='paired-cancellation-v1'
                        and (p.parser_version not in ('funds-trace-bank-feedback-v5','funds-trace-bank-feedback-v6') or
                          json_extract_string(try_cast(c.details_json as json),'$.canonical_currency')=f.currency)
                    ) and not exists (
                      select 1 from table_cancelled_flows c where c.case_id=f.case_id and c.flow_id=f.id
                    )""")
                candidate.execute("""create or replace view effective_flows as
                    select f.* exclude(payer_account,payee_account,self_account,payer_bank,payee_bank),
                      coalesce(pe.entity_id,f.payer_account) payer_account,
                      coalesce(qe.entity_id,f.payee_account) payee_account,
                      coalesce(se.entity_id,f.self_account) self_account,
                      coalesce(nullif(p.bank,''),f.payer_bank) payer_bank,
                      coalesce(nullif(q.bank,''),f.payee_bank) payee_bank,
                      f.payer_account raw_payer_account,f.payee_account raw_payee_account,f.self_account raw_self_account,
                      coalesce(p.display_account,f.payer_account) payer_display_account,
                      coalesce(q.display_account,f.payee_account) payee_display_account,
                      coalesce(s.display_account,f.self_account) self_display_account,
                      case when pe.entity_id is not null or qe.entity_id is not null then 'typed-account-evidence-v1' else null end resolution_policy
                    from source_effective_flows f
                    left join resolved_endpoints pe on pe.case_id=f.case_id and pe.flow_id=f.id and pe.role='payer'
                    left join resolved_endpoints qe on qe.case_id=f.case_id and qe.flow_id=f.id and qe.role='payee'
                    left join resolved_endpoints se on se.case_id=f.case_id and se.flow_id=f.id and se.role='self'
                    left join resolution_entities p on p.case_id=f.case_id and p.entity_id=pe.entity_id
                    left join resolution_entities q on q.case_id=f.case_id and q.entity_id=qe.entity_id
                    left join resolution_entities s on s.case_id=f.case_id and s.entity_id=se.entity_id""")
                candidate.execute("""create or replace view effective_account_subjects as
                    with aliases as (
                      select distinct f.case_id,f.payer_account raw_account,e.entity_id
                        from flows f join resolved_endpoints e on e.case_id=f.case_id and e.flow_id=f.id and e.role='payer'
                      union select distinct f.case_id,f.payee_account,e.entity_id
                        from flows f join resolved_endpoints e on e.case_id=f.case_id and e.flow_id=f.id and e.role='payee'
                      union select distinct f.case_id,f.self_account,e.entity_id
                        from flows f join resolved_endpoints e on e.case_id=f.case_id and e.flow_id=f.id and e.role='self'
                      union select case_id,raw_account,account from resolution_subject_aliases
                    ), unique_aliases as (
                      select case_id,raw_account,min(entity_id) entity_id from aliases
                        where coalesce(raw_account,'')<>'' and coalesce(entity_id,'')<>''
                        group by case_id,raw_account having count(distinct entity_id)=1
                    ), declarations as (
                      select case_id,account from main_accounts union select case_id,account from account_ids
                      union select case_id,account from account_photos
                    ) select case_id,raw_account,entity_id account from unique_aliases
                      union select d.case_id,d.account,d.account from declarations d
                        where not exists(select 1 from unique_aliases a where a.case_id=d.case_id and a.raw_account=d.account)""")
                candidate.execute("""create or replace view effective_account_ids as
                    with projected as (
                      select i.*,coalesce(s.account,i.account) projected_account from account_ids i
                      left join effective_account_subjects s on s.case_id=i.case_id and s.raw_account=i.account
                    ) select case_id,projected_account account,min(id_no) id_no,max(updated_at) updated_at,
                        min(account) raw_account from projected group by case_id,projected_account
                      having count(distinct id_no)=1""")
                candidate.execute("""create or replace view effective_account_photos as
                    with projected as (
                      select p.*,coalesce(s.account,p.account) projected_account from account_photos p
                      left join effective_account_subjects s on s.case_id=p.case_id and s.raw_account=p.account
                    ) select case_id,projected_account account,min(fname) fname,min(crop_cx) crop_cx,
                        min(crop_cy) crop_cy,min(crop_r) crop_r,max(updated_at) updated_at,min(account) raw_account
                      from projected group by case_id,projected_account
                      having count(distinct (fname,crop_cx,crop_cy,crop_r))=1""")
                candidate.execute("""create or replace view effective_main_account_declarations as
                    select m.case_id,coalesce(s.account,m.account) account,m.name,m.source,m.marked_at,m.account raw_account
                    from main_accounts m left join effective_account_subjects s on s.case_id=m.case_id and s.raw_account=m.account""")
                candidate.execute("""create or replace view effective_main_accounts as
                    with ranked as (
                      select *,row_number() over(partition by case_id,account order by
                        source='manual' desc,marked_at desc,raw_account) choice from effective_main_account_declarations
                    ) select case_id,account,name,source,marked_at,raw_account from ranked where choice=1""")
                for stmt in INDEXES.strip().split(";"):
                    if stmt.strip():
                        candidate.execute(stmt)
                candidate.execute("insert or ignore into cases(name) values ('默认案件')")
                candidate.execute("commit")
                # DuckDB 1.5.6 can fail WAL replay for startup ALTER statements.
                # Finish the schema checkpoint before accepting runtime writes;
                # import transactions then recover against the persisted catalog.
                candidate.execute("checkpoint")
            except BaseException:
                candidate.close()
                raise
            _con = candidate
            _start_ckpt()
        return _con


_ckpt_started = False
_busy = False   # 导入等大批量写操作进行中（checkpoint 避让，防锁竞争卡导入）


def set_busy(v):
    global _busy
    _busy = bool(v)


def insert_dataframe(table, df, columns):
    """Bulk scan local data without transaction-long registered-view ownership."""
    with _lock:
        c = con()
        cols = ",".join(columns)
        # A registered view dropped inside this transaction keeps its pandas
        # dependency in catalog undo until commit, accumulating every chunk.
        # The replacement scan uses this local frame without a catalog entry.
        # Catalog objects resolve first, so reject any colliding table/view.
        if c.execute("select 1 from information_schema.tables where lower(table_name)=? limit 1",
                     ["_funds_trace_insert_frame"]).fetchone():
            raise ValueError("Bulk scan name is already in use")
        _funds_trace_insert_frame = df[columns]
        c.execute(f"insert into {table} ({cols}) select {cols} from _funds_trace_insert_frame")


def _start_ckpt():
    """周期性 checkpoint：把 WAL 合并进主文件并清空 WAL。
    大批量导入期间（_busy）避让，导入完成后再尝试合并。
    周期与重试不构成异常断电下的数据损失上限或损坏恢复保证。"""
    global _ckpt_started
    if _ckpt_started:
        return
    _ckpt_started = True

    def loop():
        import time
        while True:
            time.sleep(60)
            try:
                if _busy:
                    continue
                with _lock:
                    if _con is not None and not getattr(_transaction_state, "depth", 0):
                        _con.execute("checkpoint")
            except Exception:
                pass  # checkpoint 失败不影响服务，下个周期重试

    threading.Thread(target=loop, daemon=True).start()


def clean(v):
    """把 DuckDB 返回值转换为 JSON 安全类型"""
    if isinstance(v, dict):
        return {k: clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    if isinstance(v, datetime.datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, datetime.date):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, decimal.Decimal):
        from .money import money_string
        return money_string(v)
    return v


def q(sql, params=None):
    """查询，返回 list[dict]"""
    return clean(q_raw(sql, params))


def q_raw(sql, params=None):
    """Query without lossy JSON conversion; monetary contracts retain Decimal."""
    with _lock:
        cur = con().execute(sql, params or [])
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
    return [dict(zip(cols, row)) for row in rows]


def x(sql, params=None):
    """执行写语句"""
    with _lock:
        con().execute(sql, params or [])


def executemany(sql, seq):
    with _lock:
        con().executemany(sql, seq)
