"""Read-only, member-scoped statistics over current evidence-bounded funds.

The source journal selects observations; it never substitutes a batch's added
count for a file cohort. Financial decisions stay in funds_projection. All
sections share one locked case revision, and only bounded aggregates leave SQL.
"""
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from html import escape
import json

from app import analysis_snapshot, db, funds_projection, table_cancellation_links, transaction_time
from . import imports
from .cases import ServiceError

POLICY = 'import-member-funds-report-v1'


def _scope(case_id, source_id, member_path, expected_revision=None):
    cid, source = imports._journal_source(case_id, source_id)
    members = db.q_raw('select * from import_members where case_id=? and source_id=? and member_path=?',
                       [cid, source_id, member_path])
    if not members:
        raise imports._missing()
    revision = analysis_snapshot.revision(cid, expected_revision)
    if not db.q_raw('select 1 from import_sources p where p.case_id=? and p.source_id=? and '
                    + table_cancellation_links.active_support_sql('p'), [cid, source_id]):
        raise ServiceError(409, 'source_withdrawn', '该文件的导入操作已撤回，请重新选择文件')
    return cid, source, members[0], revision


def _integrity(cid, source, member):
    return {'status': 'registered' if imports.source_is_available(cid, source['source_id']) else 'unavailable',
            'object_name': source['filename'], 'expected_sha256': source['sha256'],
            'observed_sha256': None, 'checked_at': None, 'size_bytes': source['size_bytes']}


def verify(case_id, source_id, member_path, expected_revision):
    """Explicitly reread the registered outer original, without reparsing it."""
    with db._lock:
        cid, source, member, _ = _scope(case_id, source_id, member_path, expected_revision)
        result = _integrity(cid, source, member)
        result['checked_at'] = datetime.now(timezone.utc).isoformat()
        try:
            session = imports._source_session(cid, source_id)
            imports._source(session)
            result.update(status='match', observed_sha256=source['sha256'])
        except ServiceError as error:
            if error.code not in {'source_missing', 'source_changed', 'import_not_found', 'unsafe_upload_storage'}:
                raise
            result['status'] = 'mismatch' if error.code == 'source_changed' else 'unavailable'
        return result


def _selection(cid, source_id, member_path):
    # Bind record/flow pairs before joining projected observations. Otherwise
    # the planner can join a member's records to every flow in the case first.
    links = '''select r.case_id,r.source_id,r.record_no,r.source_row,json_extract_string(r.details_json,'$.resolution_evidence') resolution_evidence,fs.flow_id
        from import_records r join flow_sources fs
        on fs.case_id=r.case_id and fs.source_id=r.source_id and fs.source_row=r.record_no
        where r.case_id=? and r.source_id=? and r.member_path=? and r.disposition in ('imported','duplicate')'''
    source = '''select f.* from effective_flows f join (
        select distinct case_id,flow_id from _report_member_links
        ) k on k.case_id=f.case_id and k.flow_id=f.id'''
    # A late conflict withdraws a former event proof, not the source amounts.
    # cte() keeps those selected observations separate and unresolved.
    prefix = 'with _report_member_links as materialized (' + links + '), ' + funds_projection.cte(source).removeprefix('with ')
    # Bind the implied ledger scope as constants before expanding its payload;
    # dynamic record-key joins alone can still scan other members' notes.
    prefix += ''', _report_support as materialized (
        select o.* exclude(trade_time,trade_time_utc),r.record_no,r.source_row,
            case when l.ledger_evidence is not null then try_cast(json_extract_string(l.ledger_evidence,'$.row.trade_time') as timestamp) else o.trade_time end trade_time,
            case when l.ledger_evidence is not null then try_cast(json_extract_string(l.ledger_evidence,'$.row.trade_time_utc') as timestamp) else o.trade_time_utc end trade_time_utc,
            json_extract_string(l.ledger_evidence,'$.direction') source_direction,
            json_extract_string(l.ledger_evidence,'$.row.self_account') own_number,
            json_extract_string(l.ledger_evidence,'$.row.self_name') own_name,
            json_extract_string(l.ledger_evidence,'$.own_card') own_card,
            coalesce(json_extract_string(l.ledger_evidence,'$.row.payer_account'),
                nullif(json_extract_string(r.resolution_evidence,'$.endpoints.payer.account'),''),
                json_extract_string(r.resolution_evidence,'$.endpoints.payer.card'),o.raw_payer_account,'') raw_payer_number,
            coalesce(json_extract_string(l.ledger_evidence,'$.row.payee_account'),
                nullif(json_extract_string(r.resolution_evidence,'$.endpoints.payee.account'),''),
                json_extract_string(r.resolution_evidence,'$.endpoints.payee.card'),o.raw_payee_account,'') raw_payee_number,
            coalesce(json_extract_string(l.ledger_evidence,'$.row.payer_name'),
                json_extract_string(r.resolution_evidence,'$.endpoints.payer.name'),o.payer_name,'') raw_payer_name,
            coalesce(json_extract_string(l.ledger_evidence,'$.row.payee_name'),
                json_extract_string(r.resolution_evidence,'$.endpoints.payee.name'),o.payee_name,'') raw_payee_name,
            case when o.fact_event_id is null then 'flow:'||cast(o.id as varchar) else 'event:'||o.fact_event_id end report_fact
        from selected_observations o join _report_member_links r on r.case_id=o.case_id and r.flow_id=o.id
        left join (select * from source_ledger_records where case_id=? and source_id=? and member_path=?) l
            on l.case_id=r.case_id and l.source_id=r.source_id and l.record_no=r.record_no
    ), _report_own as (
        select *,case source_direction when 'in' then payee_account else payer_account end account,
            case source_direction when 'in' then payer_account else payee_account end counterparty,
            own_number account_number,coalesce(own_name,'') account_name,
            case source_direction when 'in' then raw_payer_number else raw_payee_number end counterparty_number,
            case source_direction when 'in' then raw_payer_name else raw_payee_name end counterparty_name,
            source_direction direction
        from _report_support where source_direction in ('in','out') and nullif(own_number,'') is not null
    ), _report_endpoint as (
        select *,payer_account account,payee_account counterparty,raw_payer_number account_number,
            raw_payer_name account_name,raw_payee_number counterparty_number,raw_payee_name counterparty_name,'out' direction
        from _report_support where not exists(select 1 from _report_own)
        union all
        select *,payee_account account,payer_account counterparty,raw_payee_number account_number,
            raw_payee_name account_name,raw_payer_number counterparty_number,raw_payer_name counterparty_name,'in' direction
        from _report_support where not exists(select 1 from _report_own)
    ), _report_candidates as (select * from _report_own union all select * from _report_endpoint),
    _report_account_labels as (
        select account,coalesce(fact_currency,'') currency,min(account_number) account_number,
            list_slice(list_sort(list(distinct account_number)),1,20) account_numbers,
            list_slice(list_sort(list(distinct account_name) filter(where account_name<>'')),1,20) "names",
            list_slice(list_sort(list(distinct own_card) filter(where own_card<>'' and own_card<>account_number and own_number=account_number)),1,20) cards
        from _report_candidates group by account,coalesce(fact_currency,'')
    ),
    _report_sides as materialized (
        select * exclude(amount,currency),fact_amount amount,coalesce(fact_currency,'') currency,
            coalesce(trade_time_utc,trade_time) report_time,
            case when trade_time_utc is not null then 'utc' else 'unqualified' end time_basis
        from _report_candidates
        qualify row_number() over(partition by account,direction,report_fact order by id,record_no)=1
    )'''
    return prefix, [cid, source_id, member_path, cid, source_id, member_path]


def _amount(value):
    return format(Decimal(value) if isinstance(value,str) else value, '.2f') if value is not None else None


def _clock(value, basis):
    return str(value) + ('Z' if basis == 'utc' else '') if value is not None else None


def _directional(row):
    for direction in ('in', 'out'):
        row[direction + '_count'] = int(row[direction + '_count'] or 0)
        row[direction + '_amount'] = _amount(row[direction + '_amount'])
    with localcontext() as context:
        context.prec = 50
        row['net_amount'] = _amount(Decimal(row['in_amount'])-Decimal(row['out_amount'])) if row['in_amount'] is not None and row['out_amount'] is not None else None
    return row


_SIDES_AGG = '''count(*) filter(where direction='in') in_count,
    count(*) filter(where direction='out') out_count,
    case when count(*) filter(where direction='in' and amount is null)=0
        then coalesce(sum(amount) filter(where direction='in'),0) end in_amount,
    case when count(*) filter(where direction='out' and amount is null)=0
        then coalesce(sum(amount) filter(where direction='out'),0) end out_amount'''


def _trend_selection(mode, accounts):
    accounts = sorted(set(accounts or []))
    if mode not in {'all','selected','except'} or len(accounts)>200 or any(not isinstance(a,str) or not 0<len(a)<=4096 for a in accounts) or mode=='all' and accounts:
        raise ServiceError(422,'report_timeline_scope_invalid','时间变化账户范围无效')
    return accounts


def report(case_id, source_id, member_path, currency=None, account=None, page=1, page_size=20, expected_revision=None, accounts_page=None, relations_page=None,
           timeline_grain='auto',timeline_page=1,peer_account=None,timeline_scope='all',timeline_accounts=None,timeline_search=''):
    timeline_accounts = _trend_selection(timeline_scope,timeline_accounts)
    accounts_page = accounts_page if accounts_page is not None else page
    relations_page = relations_page if relations_page is not None else page
    with db._lock:
        cid, source, member, revision = _scope(case_id, source_id, member_path, expected_revision)
        prefix, params = _selection(cid, source_id, member_path)
        aggregate_sql = prefix + f""", _report_options as (
            select ?::varchar requested_currency,?::varchar requested_account,?::integer page_size,?::bigint account_offset,?::bigint relation_offset,
                ?::varchar requested_timeline_grain,?::bigint requested_timeline_page,?::varchar requested_peer_account,
                ?::varchar requested_timeline_scope,?::varchar[] requested_timeline_accounts,?::varchar requested_timeline_search
        ), _report_runtime as (
            select *,coalesce(requested_currency,(select case when bool_or(currency='CNY') then 'CNY' else min(currency) end from _report_sides)) chosen_currency
            from _report_options
        ), _report_filtered as materialized (
            select s.* from _report_sides s cross join _report_runtime p
            where s.currency=p.chosen_currency and (p.requested_account is null or s.account=p.requested_account)
        ), _report_overview_candidates as materialized (
            select s.* from _report_candidates s cross join _report_runtime p
            where coalesce(s.fact_currency,'')=p.chosen_currency and (p.requested_account is null or s.account=p.requested_account)
        ), _report_overview as (
            select count(distinct account_name) filter(where nullif(account_name,'') is not null) name_count,
                count(distinct account) account_count,
                (select count(*) from (select account_number raw_identifier from _report_overview_candidates where nullif(account_number,'') is not null
                    union select own_card from _report_overview_candidates where nullif(own_card,'') is not null and own_number=account_number)) number_count,
                (select count(*) from (select account from _report_overview_candidates group by account
                    having count(*) filter(where nullif(account_name,'') is not null)=0)) unnamed_account_count
            from _report_overview_candidates
        ), _report_peer_filtered as materialized (
            select s.* from _report_sides s cross join _report_runtime p
            where s.currency=p.chosen_currency and (p.requested_peer_account is null or s.account=p.requested_peer_account)
        ), _report_summary as (
            select {_SIDES_AGG},count(distinct report_fact) transaction_count from _report_filtered
        ), _report_financial as (
            select case when count(*)=count(amount) then coalesce(sum(amount),0) end amount from (
                select report_fact,min(amount) amount from _report_filtered group by report_fact)
        ), _report_accounts as materialized (
            select s.account,s.currency,{_SIDES_AGG} from _report_sides s cross join _report_runtime p
            where s.currency=p.chosen_currency group by s.account,s.currency
        ), _report_relations as materialized (
            select account,counterparty,min(account_number) account_number,min(counterparty_number) counterparty_number,
                list_slice(list_sort(list(distinct counterparty_name) filter(where counterparty_name<>'')),1,20) \"names\",
                currency,{_SIDES_AGG},
                case when count(distinct time_basis)=1 then min(report_time) end first_time,
                case when count(distinct time_basis)=1 then max(report_time) end last_time,
                case when count(distinct time_basis)=1 then min(time_basis) else 'mixed' end time_basis
            from _report_filtered group by account,counterparty,currency
        ), _report_peers as (
            select direction,counterparty account,min(counterparty_number) account_number,
                list_slice(list_sort(list(distinct counterparty_name) filter(where counterparty_name<>'')),1,20) \"names\",
                count(*) count,sum(amount) amount from (
                    select * from _report_peer_filtered where counterparty<>''
                    qualify row_number() over(partition by direction,counterparty,report_fact order by account)=1)
            group by direction,counterparty having count(amount)=count(*)
        ), _report_periods as materialized (
            select time_basis,min(report_time) first_time,max(report_time) last_time,
                epoch(max(report_time))-epoch(min(report_time)) span_seconds from _report_filtered
            where report_time is not null group by time_basis
        ), _report_trend_filtered as materialized (
            select s.* from _report_filtered s cross join _report_runtime p where (p.requested_timeline_scope='all'
                or (p.requested_timeline_scope='selected' and list_contains(p.requested_timeline_accounts,s.account))
                or (p.requested_timeline_scope='except' and not list_contains(p.requested_timeline_accounts,s.account)))
                and (p.requested_timeline_search='' or exists(select 1 from _report_candidates c
                    where c.account=s.account and coalesce(c.fact_currency,'')=s.currency
                    and (contains(lower(c.account_name),lower(p.requested_timeline_search))
                        or contains(lower(c.account_number),lower(p.requested_timeline_search))
                        or (c.own_number=c.account_number and contains(lower(c.own_card),lower(p.requested_timeline_search))))))
        ), _report_trend_summary as (
            select {_SIDES_AGG} from _report_trend_filtered where report_time is not null
        ), _report_trend_periods as (
            select time_basis,min(report_time) first_time,max(report_time) last_time,
                epoch(max(report_time))-epoch(min(report_time)) span_seconds from _report_trend_filtered
            where report_time is not null group by time_basis
        ), _report_grain as (
            select case when p.requested_timeline_grain<>'auto' then p.requested_timeline_grain
                when coalesce((select max(span_seconds) from _report_trend_periods),0)<=2678400 then 'day'
                when (select max(span_seconds) from _report_trend_periods)<=63244800 then 'month' else 'year' end grain
            from _report_runtime p
        ), _report_time_points as (
            select s.*,date_trunc(g.grain,report_time) bucket_start
            from _report_trend_filtered s cross join _report_grain g where report_time is not null
        ), _report_timeline as materialized (
            select time_basis,bucket_start \"start\",{_SIDES_AGG},count(distinct report_fact) transaction_count
            from _report_time_points group by time_basis,bucket_start
        ), _report_windows as (
            select *,coalesce(sum(n) over(order by time_basis rows between unbounded preceding and 1 preceding),0)::bigint base from (
                select time_basis,date_trunc(g.grain,first_time) first_bucket,
                    date_diff(g.grain,date_trunc(g.grain,first_time),date_trunc(g.grain,last_time))+1 n
                from _report_trend_periods cross join _report_grain g)
        ), _report_timeline_page as (
            select coalesce(sum(n),0)::bigint total,least((select requested_timeline_page from _report_runtime),greatest(1,(coalesce(sum(n),0)+119)//120))::bigint page,
                120 page_size from _report_windows
        ), _report_calendar as (
            select w.time_basis,w.first_bucket+((p.page-1)*120+i-w.base)*case g.grain
                when 'day' then interval '1 day' when 'month' then interval '1 month' else interval '1 year' end \"start\",
                w.first_bucket+((p.page-1)*120+i-w.base+1)*case g.grain
                when 'day' then interval '1 day' when 'month' then interval '1 month' else interval '1 year' end \"end\"
            from _report_windows w cross join _report_timeline_page p cross join _report_grain g cross join range(120) t(i)
            where (p.page-1)*120+i>=w.base and (p.page-1)*120+i<w.base+w.n
        ), _report_meta as (
            select (select exists(select 1 from _report_own)) own,
                (select list_sort(list(distinct currency)) from _report_sides) currencies,
                (select chosen_currency from _report_runtime) currency,
                (select exists(select 1 from _report_sides where account=(select requested_account from _report_runtime))) account_exists,
                (select exists(select 1 from _report_sides where account=(select requested_peer_account from _report_runtime)
                    and currency=(select chosen_currency from _report_runtime))) peer_account_exists,
                (select count(*) from _report_accounts) account_count,
                (select count(*) from _report_relations) relation_count,
                (select grain from _report_grain) grain,
                (select count(distinct account) from _report_filtered) trend_available,
                (select count(distinct account) from _report_trend_filtered) trend_accounts,
                (select count(distinct report_fact) from _report_trend_filtered where report_time is null) undated_transactions,
                (select count(*) from unnest((select requested_timeline_accounts from _report_runtime)) t(a)
                    where not exists(select 1 from _report_filtered s where s.account=t.a)) invalid_trend_accounts,
                count(*) selected_transactions,coalesce(sum(observation_count),0) linked_observations,
                count(*) filter(where fact_status='unresolved') pending_observations,
                count(*) filter(where not exists(select 1 from _report_sides s where s.report_fact=
                    case when f.fact_event_id is null then 'flow:'||cast(f.id as varchar) else 'event:'||f.fact_event_id end)) unattributed_transactions
            from selected_facts f
        )
        select 'meta' section,to_json(r) payload from _report_meta r
        union all select 'overview',to_json(r) from _report_overview r
        union all select 'timeline_page',to_json(r) from _report_timeline_page r
        union all select 'trend_summary',to_json(r) from (select * exclude(in_amount,out_amount),cast(in_amount as varchar) in_amount,cast(out_amount as varchar) out_amount from _report_trend_summary) r
        union all select 'trend_period',to_json(r) from (select * exclude(span_seconds) from _report_trend_periods order by time_basis) r
        union all select 'summary',to_json(r) from (
            select s.* exclude(in_amount,out_amount),cast(in_amount as varchar) in_amount,cast(out_amount as varchar) out_amount,
                cast(f.amount as varchar) amount from _report_summary s cross join _report_financial f) r
        union all select 'account',to_json(r) from (
            select a.* exclude(in_amount,out_amount),cast(in_amount as varchar) in_amount,cast(out_amount as varchar) out_amount,
                l.account_number,l.account_numbers,l.names,l.cards from _report_accounts a join _report_account_labels l using(account,currency)
            order by account limit (select page_size from _report_runtime) offset (select account_offset from _report_runtime)) r
        union all select 'selected_account',to_json(r) from (
            select a.* exclude(in_amount,out_amount),cast(in_amount as varchar) in_amount,cast(out_amount as varchar) out_amount,
                l.account_number,l.account_numbers,l.names,l.cards from _report_accounts a join _report_account_labels l using(account,currency)
                cross join _report_runtime p where a.account=p.requested_account or a.account=p.requested_peer_account) r
        union all select 'relation',to_json(r) from (
            select * exclude(in_amount,out_amount),cast(in_amount as varchar) in_amount,cast(out_amount as varchar) out_amount from _report_relations
            order by account,counterparty limit (select page_size from _report_runtime) offset (select relation_offset from _report_runtime)) r
        union all select 'top_'||r.direction,to_json(r) from (
            select * exclude(amount),cast(amount as varchar) amount from _report_peers
            qualify row_number() over(partition by direction order by _report_peers.amount desc,account)<=10
            order by direction,_report_peers.amount desc,account) r
        union all select 'period',to_json(r) from (select * exclude(span_seconds) from _report_periods order by time_basis) r
        union all select 'timeline',to_json(r) from (
            select c.*,t.start is not null has_records,coalesce(t.in_count,0) in_count,coalesce(t.out_count,0) out_count,
                coalesce(t.transaction_count,0) transaction_count,
                cast(t.in_amount as varchar) in_amount,cast(t.out_amount as varchar) out_amount
            from _report_calendar c left join _report_timeline t on c.time_basis=t.time_basis and c.start=t.start
            order by c.time_basis,c.start) r"""
        groups = {}
        for row in db.q_raw(aggregate_sql,[*params,currency,account,page_size,(accounts_page-1)*page_size,(relations_page-1)*page_size,timeline_grain,timeline_page,peer_account,timeline_scope,timeline_accounts,timeline_search.strip()]):
            groups.setdefault(row['section'],[]).append(json.loads(row['payload']))
        meta = groups['meta'][0]
        if meta['invalid_trend_accounts']:
            raise ServiceError(404,'report_timeline_account_missing','当前资料和报告范围没有所选时间变化账户')
        perspective = 'own_ledger' if meta['own'] else 'transaction_endpoints'
        currencies = meta['currencies'] or []
        if currency is not None and currency not in currencies:
            raise ServiceError(422,'report_currency_missing','该文件没有所选币种的可统计资金')
        if account is not None and not meta['account_exists']:
            raise ServiceError(404,'report_account_missing','该文件没有所选账户的可统计资金')
        if peer_account is not None and not meta['peer_account_exists']:
            raise ServiceError(404,'report_peer_account_missing','当前文件和币种没有所选本方账户的可统计资金')
        currency = meta['currency']
        summary = _directional(groups['summary'][0])
        accounts = [_directional({**row,'names':row['names'] or [],'cards':row['cards'] or []}) for row in groups.get('account',[])]
        selected_accounts={row['account']:_directional({**row,'names':row['names'] or [],'cards':row['cards'] or []}) for row in groups.get('selected_account',[])}
        overview={**groups['overview'][0],'selected_account':selected_accounts.get(account)}
        account_count,relation_total = meta['account_count'],meta['relation_count']
        tops = {direction:[{k:v for k,v in row.items() if k!='direction'} for row in groups.get('top_'+direction,[])] for direction in ('in','out')}
        for peers in tops.values():
            for row in peers:
                row['names']=row['names'] or []
        relations = groups.get('relation',[])
        for row in relations:
            _directional(row)
            row.update(names=row['names'] or [],first_time=_clock(row['first_time'],row['time_basis']),last_time=_clock(row['last_time'],row['time_basis']))
        timeline,periods = groups.get('timeline',[]),groups.get('period',[])
        for row in timeline:
            _directional(row)
            row.update(start=_clock(row['start'],row['time_basis']),end=_clock(row['end'],row['time_basis']))
        dispositions={r['disposition']:r['n'] for r in db.q_raw("""select disposition,count(*) n from import_records
            where case_id=? and source_id=? and member_path=? group by disposition""",[cid,source_id,member_path])}
        facts={key:meta[key] for key in ('selected_transactions','linked_observations','pending_observations','unattributed_transactions')}
        integrity = _integrity(cid,source,member)
        from . import materials
        material=materials.source(cid,source_id)
        source_counts=db.q_raw("""select sum(row_count) n,count(*) members from import_members
            where case_id=? and source_id=? and role<>'attachment'""",[cid,source_id])[0]
        source_record_total=source_counts['n'] if source_counts['members'] else None
        worksheet = member['encoding'] in {'xlsx','xls'}
        result = {'case_id':str(cid),'source_id':source_id,'member_path':member_path,'analysis_revision':revision,
            'policy':POLICY,'generated_at':datetime.now(timezone.utc).isoformat(),
            'scope':{'filename':source['filename'],'member_path':member_path,'member_role':member['role'],
                'source_sha256':source['sha256'],'source_size_bytes':source['size_bytes'],
                'member_sha256':member['sha256'],'member_size_bytes':None if worksheet else member['size_bytes'],
                'member_digest_kind':'worksheet_locator' if worksheet else 'bytes',
                'material_name':material['material_name'],'material_origin_name':material['origin_name'],
                'source_record_total':source_record_total,
                'parser_version':source['parser_version'],'imported_at':str(source['imported_at'])},
            'quality':{'source_records':sum(dispositions.values()),'dispositions':dispositions,**{k:int(v) for k,v in facts.items()}},
            'perspective':perspective,'currency':currency,'currencies':currencies,'account':account,
            'overview':overview,'peer_account':peer_account,'peer_selected_account':selected_accounts.get(peer_account),
            'summary':summary,'accounts':{'items':accounts,'total':account_count,'page':accounts_page,'page_size':page_size},
            'top_in':tops['in'],'top_out':tops['out'],'timeline':timeline,
            'timeline_grain':{'day':'日','month':'月','year':'年'}[meta['grain']],
            'timeline_mode':timeline_grain,'timeline_pagination':groups['timeline_page'][0],
            'timeline_scope':{'mode':timeline_scope,'accounts':timeline_accounts,'search':timeline_search.strip(),'account_count':meta['trend_accounts'],
                'available_account_count':meta['trend_available'],'undated_transactions':meta['undated_transactions'],
                'summary':_directional(groups['trend_summary'][0]),
                'periods':[{k:_clock(v,r['time_basis']) if k in {'first_time','last_time'} else v for k,v in r.items()} for r in groups.get('trend_period',[])]},
            'periods':[{k:_clock(v,r['time_basis']) if k in {'first_time','last_time'} else v for k,v in r.items() if k!='seconds'} for r in periods],
            'relations':{'items':relations,'total':relation_total,'page':relations_page,'page_size':page_size},'integrity':integrity,
            'notes':['本报告仅统计所选成员关联的当前有效流水；重复来源关联同笔不重复计数，已确认同笔按现有资金投影合并，未决观察保留各自记录。',
                '进出以所列主体为视角；多主体内部转账可在两侧出现，进出相加不代表独立总资金。收支差额不等于余额、收益或损失。',
                '账户名称、账号与卡号来自本成员来源支持；同名不合并主体，卡号不另行复制账户金额。每账户最多列示20个来源别名，完整原值可回查来源记录。',
                '显式时区按UTC瞬间分桶；未注明时区保留来源壁钟，不跨时间依据推定顺序。SHA-256只核对留存字节与登记指纹，不证明业务真实性。',
                '本方角色明确时采用来源本方账簿视角；否则列交易端点，端点收支不证明取得其完整账簿。',
                '概览中的登记户名为来源非空户名文本去重，不是已核实自然人或主体数；来源卡/账号标识按非空文本去重，同名不合并账户。',
                '主要往来默认列示当前文件、币种中具有对手标识的账户；显式选择账户时仅缩小该章榜单范围。时间变化章在报告范围内独立筛选，按日历时段分页，空档留空；其期间与收支按完整本章范围统计，其他章节不变。']}
        result['method_paragraphs'] = _method_paragraphs(result)
        return result


def _method_paragraphs(data):
    """Readable methods from this snapshot; technical rules remain in notes."""
    quality, dispositions = data['quality'], data['quality']['dispositions']
    own = data['perspective'] == 'own_ledger'
    account_kind = '本方账户' if own else '交易端点'
    currency = data['currency'] or '币种未明确'
    selected = data['overview']['selected_account']
    selected_label = (selected['account_number'] or '所选账户') if selected else '所选账户'
    scope = selected_label if data['account'] is not None else '全部' + account_kind
    source = f"本报告以当前选定的资料为范围，共登记来源记录{quality['source_records']:,}条。"
    pending = dispositions.get('review',0) + dispositions.get('unresolved',0)
    if pending:
        source += f'{pending:,}条记录尚待核对，未计入资金统计。'
    source += f"本次按{currency}及{scope}归集，纳入当前范围的交易共{data['summary']['transaction_count']:,}笔。"
    if own and quality['unattributed_transactions']:
        source += f"资料中{quality['unattributed_transactions']:,}笔交易未取得本方角色，未计入本方收支。"
    if data['account'] is not None:
        source += '账户收支表仍列示资料中的同币种账户，主要往来章按其独立选定范围列示。'
    elif data['peer_account'] is not None:
        source += '主要往来章另按该章选定账户列示，其他章节范围不变。'
    if data['timeline_scope']['mode']!='all' or data['timeline_scope']['search']:
        source += '资金进出变化章另按该章账户条件统计，其他章节范围不变。'
    process = ('收支方向采用资料记载的本方账户视角，金额按币种分别归集。' if own else
               '收支按资料记载的付款、收款端点分别归集，金额按币种分列，不据此推定已取得各端点完整账簿。')
    process += '同笔交易的重复来源不重复计数；尚未确认同笔的交易记录仍分别保留。户名、账号与卡号沿用来源记载；户名数量按记载名称去重，不作为主体数量认定。同名不直接归并账户，附列卡号不重复计资。户名与卡号择要展示，统计数量不因展示省略而减少。'
    process += '主要往来榜列示有对手标识的账户。对手标识未载明的交易仍计入收支，可在关联明细回查。'
    result = '多账户间的内部转账可能在进、出两侧列示，收支相加不代表独立资金总额；收支差额不等同余额、收益或损失。'
    bases = {period['time_basis'] for period in data['periods']}
    if bases == {'utc'}:
        result += '带时区的交易时间统一换算为UTC后统计。'
    elif bases == {'unqualified'}:
        result += '交易时间沿用资料记载，不补充推定时区。'
    elif bases:
        result += '带时区与未注明时区的时间分别归集，不直接混排。'
    result += '趋势按日历时段展示，未见纳入记录的时段留空；统计结果仅反映已提供资料与当前选定范围。'
    page = data['timeline_pagination']
    if page['total'] > page['page_size']:
        result += '趋势虽分段显示，统计期间和总额仍覆盖完整所选范围。'
    verification = '检材编号、原件信息与SHA-256校验值列于“资料与核验”。SHA-256复核仅核对留存字节与登记指纹是否一致，不代替对交易真实性的核实。'
    if data['scope']['member_digest_kind'] == 'worksheet_locator':
        verification += '工作表摘要仅用于定位，文件字节核验以所属原件为对象。'
    return [{'title':title,'text':text} for title,text in [
        ('资料范围',source),('归集方法',process),('结果说明',result),('留存复核',verification)]]


def account_options(case_id,source_id,member_path,expected_revision,currency,account=None,search='',page=1,page_size=20):
    with db._lock:
        cid,_,_,revision=_scope(case_id,source_id,member_path,expected_revision)
        prefix,params=_selection(cid,source_id,member_path)
        prefix+=''', _option_runtime as (select ?::varchar currency,?::varchar account,?::varchar term),
            _option_accounts as materialized (
                select s.account,s.currency from _report_sides s cross join _option_runtime p
                where s.currency=p.currency and (p.account is null or s.account=p.account)
                and (p.term='' or exists(select 1 from _report_candidates c where c.account=s.account
                    and coalesce(c.fact_currency,'')=s.currency and (contains(lower(c.account_name),lower(p.term))
                        or contains(lower(c.account_number),lower(p.term))
                        or (c.own_number=c.account_number and contains(lower(c.own_card),lower(p.term))))))
                group by s.account,s.currency)
            '''
        params += [currency,account,search.strip()]
        # Count and page share this selection; the count row survives an empty page.
        rows=db.q_raw(prefix+'''select t.n,p.* from (select count(*) n from _option_accounts) t
            left join (select l.* from _option_accounts a join _report_account_labels l using(account,currency)
                order by l.account_number,l.account limit ? offset ?) p on true
            order by p.account_number,p.account''',[*params,page_size,(page-1)*page_size])
        total=rows[0]['n']
        items=[]
        for item in rows:
            del item['n']
            if item['account'] is not None:
                item.update(names=item['names'] or [],cards=item['cards'] or [],account_numbers=item['account_numbers'] or [])
                items.append(item)
        return {'case_id':str(cid),'source_id':source_id,'member_path':member_path,'analysis_revision':revision,
                'items':items,'total':total,'page':page,'page_size':page_size}


def records(case_id,source_id,member_path,expected_revision,currency=None,account=None,counterparty=None,direction=None,page=1,page_size=50,
            timeline_scope='all',timeline_accounts=None,time_start=None,time_end=None,time_basis=None,timeline_search=''):
    timeline_accounts=_trend_selection(timeline_scope,timeline_accounts)
    with db._lock:
        cid,_,_,revision=_scope(case_id,source_id,member_path,expected_revision)
        prefix,params=_selection(cid,source_id,member_path)
        filters=[]
        for key,value in [('currency',currency),('account',account),('counterparty',counterparty),('direction',direction)]:
            if value is not None:
                filters.append(key+'=?');params.append(value)
        if timeline_scope!='all':
            filters.append(('' if timeline_scope=='selected' else 'not ')+'list_contains(?::varchar[],account)')
            params.append(timeline_accounts)
        if timeline_search.strip():
            filters.append('''exists(select 1 from _report_candidates c where c.account=_report_sides.account
                and coalesce(c.fact_currency,'')=_report_sides.currency and
                (contains(lower(c.account_name),lower(?)) or contains(lower(c.account_number),lower(?))
                or (c.own_number=c.account_number and contains(lower(c.own_card),lower(?)))))''')
            params += [timeline_search.strip()]*3
        if any(value is not None for value in (time_start,time_end,time_basis)):
            try:
                start,end=datetime.fromisoformat(time_start),datetime.fromisoformat(time_end)
                if time_basis not in {'utc','unqualified'} or (start.tzinfo is not None)!=(time_basis=='utc') or (end.tzinfo is not None)!=(time_basis=='utc') or start>=end:
                    raise ValueError
            except (TypeError,ValueError):
                raise ServiceError(422,'report_time_range_invalid','统计时间范围或时间依据无效')
            if time_basis=='utc':
                start,end=(value.astimezone(timezone.utc).replace(tzinfo=None) for value in (start,end))
            filters+=['time_basis=?','report_time>=?','report_time<?'];params += [time_basis,start,end]
        where=' and '.join(filters) or 'true'
        # Match fact keys back to every scoped original support, so confirmed
        # transfer composition and duplicate evidence remain inspectable.
        prefix+=f''', _drill_facts as (select distinct report_fact from _report_sides where {where}),
            _drill_records as (select distinct record_no from _report_support s
                where exists(select 1 from _drill_facts f where f.report_fact=s.report_fact)) '''
        rows=db.q_raw(prefix+'''select t.n,p.record_no from (select count(*) n from _drill_records) t
            left join (select record_no from _drill_records order by record_no limit ? offset ?) p on true
            order by p.record_no''', [*params,page_size,(page-1)*page_size])
        total=rows[0]['n']
        numbers=[r['record_no'] for r in rows if r['record_no'] is not None]
        items=imports._record_items(cid,source_id,record_nos=numbers,limit=page_size,include_preview=True)
        for item in items:
            if item.get('preview') is None:
                continue
            ledger=item['details'].get('source_ledger_evidence')
            if ledger:
                # Frozen admitted source values, not a prior file's flow label.
                observed=json.loads(ledger)['row']
                item['preview'].update({key:observed[key] for key in observed if key not in {'trade_time','trade_time_utc','trade_time_original','trade_time_offset'}})
                item['preview'].update(transaction_time.public(observed))
            else:
                evidence=json.loads(item['details'].get('resolution_evidence') or '{}').get('endpoints',{})
                for side in ('payer','payee','self'):
                    endpoint=evidence.get(side,{})
                    if endpoint or side=='self':
                        item['preview'][side+'_name']=endpoint.get('name') or ''
                        item['preview'][side+'_account']=endpoint.get('account') or endpoint.get('card') or ''
        return {'case_id':str(cid),'source_id':source_id,'analysis_revision':revision,'total':total,'page':page,'page_size':page_size,'items':items}


def document(case_id,source_id,member_path,expected_revision,**options):
    data=report(case_id,source_id,member_path,expected_revision=expected_revision,**options)
    # This is an inspectable current-view brief, not a full-source export or a
    # claim that all relationship pages have been rendered.
    e=lambda value:escape('—' if value is None else str(value))
    def table(headers,rows,caption=None):
        return '<table>'+('<caption>'+e(caption)+'</caption>' if caption else '')+'<thead><tr>'+''.join('<th>'+e(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+e(c)+'</td>' for c in r)+'</tr>' for r in rows)+'</tbody></table>'
    currency=data['currency']
    money_unit='元' if currency=='CNY' else currency or '币种未明确'
    money_suffix='元' if currency=='CNY' else f' {currency}' if currency else ''
    def money(value):
        return e('金额未明确' if value is None else f'{value}{money_suffix}')
    incoming=f'进账金额（{money_unit}）'
    outgoing=f'出账金额（{money_unit}）'
    summary=data['summary']
    body='<h1>资金统计分析简报</h1><p>'+e(data['scope']['filename'])+' / '+e(member_path)+'</p>'
    body+='<p>检材编号：'+e(data['scope']['material_name'] or '未登记')+'；原件来源记录：'+e(data['scope']['source_record_total'])+'条</p>'
    if data['scope']['material_origin_name']:
        body+='<p>原编号：'+e(data['scope']['material_origin_name'])+'</p>'
    body+='<p>币种：'+e('无可统计资金' if currency is None else currency or '币种未明确')+'；主体视角：'+e('来源本方账簿' if data['perspective']=='own_ledger' else '交易端点')+'</p>'
    overview=data['overview']
    selected=overview['selected_account']
    global_scope=('、'.join(selected['names']) or '未具名')+'（'+(selected['account_number'] or '来源标识未明确')+'）' if selected else '当前文件、币种的全部账户' if data['account'] is None else '所选账户（本币种无可统计资金）'
    trend=data['timeline_scope']
    trend_active=trend['mode']!='all' or bool(trend['search'])
    body+='<p>全局统计范围（'+('总述、关联明细' if trend_active else '总述、时间变化、关联明细')+'）：'+e(global_scope)+'。</p>'
    body+='<p>所选范围共登记户名'+e(overview['name_count'])+'个、账务账户'+e(overview['account_count'])+'个、来源卡/账号标识'+e(overview['number_count'])+'个；未具名账户'+e(overview['unnamed_account_count'])+'个。登记户名不等同已核实自然人或主体。</p>'
    body+='<p>所选交易'+e(summary['transaction_count'])+'笔；进账'+e(summary['in_count'])+'笔，'+money(summary['in_amount'])+'；出账'+e(summary['out_count'])+'笔，'+money(summary['out_amount'])+'。</p>'
    body+='<h2>账户收支</h2><p>第'+e(data['accounts']['page'])+'页，共'+e(data['accounts']['total'])+'项</p>'+table(['户名','账号','币种','进账笔数',incoming,'出账笔数',outgoing],[(','.join(r['names']),r['account_number'],r['currency'],r['in_count'],r['in_amount'],r['out_count'],r['out_amount']) for r in data['accounts']['items']],caption='全文件当前币种账户')
    peer_scope=data['peer_selected_account']
    body+='<p>主要往来范围：'+e(peer_scope['account_number'] if peer_scope else '当前文件、币种的全部账户')+'</p>'
    for key,label in [('top_in','进账来源前10'),('top_out','出账去向前10')]:
        body+='<h2>'+label+'</h2>'+table(['对手户名','对手账号','笔数',f'金额（{money_unit}）'],[(','.join(r['names']),r['account_number'],r['count'],r['amount']) for r in data[key]])
    body+='<h2>时间变化</h2><p>本章范围：'+e(trend['account_count'])+'个账户'+('；匹配“'+e(trend['search'])+'”' if trend['search'] else '')+'。进账'+e(trend['summary']['in_count'])+'笔，'+money(trend['summary']['in_amount'])+'；出账'+e(trend['summary']['out_count'])+'笔，'+money(trend['summary']['out_amount'])+'。</p>'
    body+='<p>粒度：'+e(data['timeline_grain'])+'；第'+e(data['timeline_pagination']['page'])+'页，共'+e(data['timeline_pagination']['total'])+'个日历时段，每页最多120个。无纳入记录的时段留空。</p>'+table(['时间依据','桶起点','桶终点','进账笔数',incoming,'出账笔数',outgoing,f'收支差额（{money_unit}）'],[(r['time_basis'],r['start'],r['end'],r['in_count'],r['in_amount'],r['out_count'],r['out_amount'],r['net_amount']) for r in data['timeline'] if r['has_records']])
    body+='<h2>关联明细</h2><p>第'+e(data['relations']['page'])+'页，共'+e(data['relations']['total'])+'项</p>'+table(['本方账号','对手账号',incoming,'进账笔数',outgoing,'出账笔数','时间依据','首笔时间','末笔时间'],[(r['account_number'],r['counterparty_number'] or '对手标识未载明',r['in_amount'],r['in_count'],r['out_amount'],r['out_count'],r['time_basis'],r['first_time'],r['last_time']) for r in data['relations']['items']])
    body+='<h2>统计口径与来源</h2>'+''.join('<p><strong>'+e(p['title'])+'。</strong>'+e(p['text'])+'</p>' for p in data['method_paragraphs'])
    machine=json.dumps({'schema':'import-member-funds-report.v1','scope':'current_view',
        'accounts_page':data['accounts']['page'],'relations_page':data['relations']['page'],
        'timeline_page':data['timeline_pagination']['page'],'peer_account':data['peer_account'],
        'page_size':data['accounts']['page_size'],'report':data},ensure_ascii=False,separators=(',',':'))
    for character,replacement in [('&','\\u0026'),('<','\\u003c'),('>','\\u003e'),('\u2028','\\u2028'),('\u2029','\\u2029')]:
        machine=machine.replace(character,replacement)
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>资金统计分析简报</title><style>body{font:15px/1.65 system-ui;margin:40px;color:#222}table{border-collapse:collapse;width:100%;margin:16px 0}th,td{border:1px solid #bbb;padding:7px;text-align:left}th{background:#eee}pre{white-space:pre-wrap;overflow-wrap:anywhere}h2{margin-top:28px}</style><body>'+body+'<script type="application/json" id="funds-report-data">'+machine+'</script></body></html>').encode('utf-8')
