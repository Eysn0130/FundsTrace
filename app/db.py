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
                candidate.execute("""create or replace view effective_flows as
                    with table_cancel_candidates as materialized (
                      select fs.case_id,fs.source_id,fs.source_row,fs.flow_id,
                        r.details_json source_details_json,c.details_json cancellation_details_json,
                        f.self_account,f.self_name
                      from flow_sources fs
                      join flows f on f.case_id=fs.case_id and f.id=fs.flow_id
                      join import_records r on r.case_id=fs.case_id
                        and r.source_id=fs.source_id and r.record_no=fs.source_row
                      join import_members rm on rm.case_id=r.case_id and rm.source_id=r.source_id
                        and rm.member_path=r.member_path
                      join import_records c on c.case_id=r.case_id and c.record_key=r.record_key
                        and c.member_path=r.member_path and c.source_row=r.source_row
                      join import_members cm on cm.case_id=c.case_id and cm.source_id=c.source_id
                        and cm.member_path=c.member_path and cm.sha256=rm.sha256
                      join import_sources p on p.case_id=c.case_id and p.source_id=c.source_id
                      where p.row_kind='table_record' and p.parser_version='funds-trace-table-archive-v3'
                        and c.disposition='cancelled' and c.reason_code='matched_cancellation'
                    ), table_cancelled_links as materialized (
                      select distinct case_id,source_id,source_row,flow_id from table_cancel_candidates
                      where json_extract_string(try_cast(cancellation_details_json as json),'$.cancellation_role')='original'
                        and json_extract_string(try_cast(cancellation_details_json as json),'$.cancellation_policy')='table-paired-cancellation-v1'
                        and json_extract_string(try_cast(source_details_json as json),'$.flow_sha256')=
                            json_extract_string(try_cast(cancellation_details_json as json),'$.flow_sha256')
                        and case when coalesce(self_account,'')='' then '' else sha256(self_account) end=
                            json_extract_string(try_cast(cancellation_details_json as json),'$.cancellation_own_account_sha256')
                        and case when coalesce(self_name,'')='' then '' else sha256(self_name) end=
                            json_extract_string(try_cast(cancellation_details_json as json),'$.cancellation_own_name_sha256')
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
                        and p.parser_version in ('funds-trace-bank-feedback-v3','funds-trace-bank-feedback-v4','funds-trace-bank-feedback-v5')
                        and c.disposition='cancelled' and c.reason_code='matched_cancellation'
                        and json_extract_string(try_cast(c.details_json as json),'$.cancellation_role')='original'
                        and json_extract_string(try_cast(c.details_json as json),'$.cancellation_policy')='paired-cancellation-v1'
                        and (p.parser_version<>'funds-trace-bank-feedback-v5' or
                          json_extract_string(try_cast(c.details_json as json),'$.canonical_currency')=f.currency)
                    ) and not exists (
                      select 1 from table_cancelled_flows c where c.case_id=f.case_id and c.flow_id=f.id
                    )""")
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
    """Bulk insert through DuckDB's registered pandas scan under the shared lock."""
    with _lock:
        c = con()
        cols = ",".join(columns)
        c.register("_ins_df", df[columns])
        try:
            c.execute(f"insert into {table} ({cols}) select {cols} from _ins_df")
        finally:
            try:
                c.unregister("_ins_df")
            except Exception:
                pass


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
