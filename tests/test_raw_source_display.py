"""Original observations stay usable independently of economic confirmation."""
import io
import json

import pytest

from openpyxl import load_workbook

from test_bank_posting_pairs import encoded, entries, targets, tracked_graph
from test_native_bank_observations import upload_bytes
from test_source_ledger import add_source
from test_unknown_direction_source import unknown


def raw_observations():
    rows = [unknown(value, **{"交易流水号": f"SYN-RAW-{index}", "收付标志": "\t",
        "交易账号": f"SYN-RAW-OWN-{index}", "交易卡号": f"SYN-RAW-CARD-{index}",
        "交易对手账卡号": "SYN-COUNTER-Y" if index == 2 else "SYN-COUNTER-X",
        "对手户名": "SYN counter", "对手开户银行": "SYN counter bank"})
        for index, value in enumerate(("12.34", "0.02", "3.21"))]
    rows.append(unknown("0.00", **{"交易流水号": "SYN-RAW-ZERO", "交易账号": "SYN-RAW-OWN-0", "交易卡号": "SYN-RAW-CARD-0", "收付标志": "-\t"}))
    return rows


@pytest.mark.parametrize('kind', ['csv', 'zip', 'xlsx', 'xls'])
def test_unknown_direction_keeps_known_counterparty_and_unmapped_code_as_source_observations(client, make_case, kind):
    cid = make_case("SYN unknown-direction source records")
    raw, name = upload_bytes(raw_observations(), kind)
    view, job = add_source(client, cid, raw, name)
    base = f"/api/v1/cases/{cid}"
    receipt_url = base + f"/imports/{view['import_id']}"
    receipt = client.get(receipt_url).json()
    page = client.get(base + "/flows").json()
    assert page["total"] == 4
    assert {row["amount"] for row in page["items"]} == {"12.34", "0.02", "3.21", "0.00"}
    for row in page["items"]:
        assert row["payer_account"] == row["payee_account"] == ""
        info = row["source_ledger"]
        assert info["direction"] == "unknown"
        assert info["source_direction"] == ("-\t" if row["trade_no"] == "SYN-RAW-ZERO" else "\t")
        assert info.get("counter_account", "") == ("" if row["trade_no"] == "SYN-RAW-ZERO" else "SYN-COUNTER-Y" if row["trade_no"] == "SYN-RAW-2" else "SYN-COUNTER-X")
    response = client.get(base + "/flows", params={"account": "SYN-COUNTER-X"})
    assert response.status_code == 200, response.text
    by_counter = response.json()
    assert by_counter["total"] == 2
    assert client.get("/api/flows", params={"case_id": cid, "counter": "SYN-COUNTER-X"}).json()["total"] == 2
    graph = tracked_graph(client, cid, page["items"])
    assert graph["edges"] == [] and graph["transaction_count"] == 0
    assert len(graph["nodes"]) == 5
    assert client.get(base + '/stats').json()['accounts'] == 5
    assert client.get('/api/stats', params={'case_id': cid}).json()['accounts'] == 5
    assert {node.get("account_number", node["account"]) for node in graph["nodes"]} == {"SYN-RAW-OWN-0", "SYN-RAW-OWN-1", "SYN-RAW-OWN-2", "SYN-COUNTER-X", "SYN-COUNTER-Y"}
    counter = next(node for node in graph["nodes"] if node.get("account_number") == "SYN-COUNTER-X")
    assert client.get("/api/account-registry/profile", params={"case_id": cid, "account": counter["account"]}).status_code == 200
    assert all(node["in_amt"] == node["out_amt"] == "0.00" for node in graph["nodes"])
    exported = client.get('/api/export/tracked.xlsx', params={'case_id': cid})
    assert exported.status_code == 200
    book = load_workbook(io.BytesIO(exported.content), data_only=True)
    for sheet in ('追踪流水', '原始账簿观察'):
        values = list(book[sheet].values)
        records = [dict(zip(values[0], row)) for row in values[1:]]
        assert len(records) == 4
        assert {row['来源对方账号'] for row in records} == {None, 'SYN-COUNTER-X', 'SYN-COUNTER-Y'}
        assert {row['来源标注方向'] for row in records} == {'\t', '-\t'}
        assert {row['来源对方开户行'] for row in records} == {None, 'SYN counter bank'}
    assert client.get(base + f"/sources/{view['import_id']}/file").content == raw
    packed = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("raw-observations.fundcase", packed.content)})
    assert restored.status_code == 200, restored.text
    assert client.get(f"/api/v1/cases/{restored.json()['case_id']}/flows").json()["total"] == 4
    assert client.delete(f"/api/batches/{job['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    assert client.get(base + "/flows").json()["total"] == 0
    assert client.get(receipt_url).json() == receipt


@pytest.mark.parametrize('direction', ['出', '进'])
def test_package_rejects_recognized_direction_disguised_as_unknown(client, make_case, tmp_path, direction):
    from test_feedback_package import _members, _mutate, _archive
    cid = make_case('SYN portable unknown-direction consistency')
    raw, name = upload_bytes(raw_observations())
    view, _ = add_source(client, cid, raw, name)
    record, *_ = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()['items']
    details = record['details']
    evidence = json.loads(details['source_ledger_evidence'])
    evidence.update(source_direction=direction, issues=['invalid_direction'])
    details['source_ledger_evidence'] = json.dumps(evidence)
    text = json.dumps(details, ensure_ascii=False).replace("'", "''")
    members = _members(client.get(f'/api/cases/{cid}/export').content)
    _mutate(members, 'import_records', f"update entries set details_json='{text}' where record_no={int(record['record_no'])}", tmp_path)
    response = client.post('/api/cases/import', files={'file': ('tampered.fundcase', _archive(members))})
    assert response.status_code == 400


def test_new_profiles_keep_archive_and_table_entrypoints_disjoint_and_old_unknown_rules_fixed():
    from app import table_import, table_archive
    assert not table_import.SUPPORTED_PROFILES & table_archive.SUPPORTED_PROFILES
    assert {table_import.SOURCE_OBSERVATION_PROFILE, table_import.SOURCE_OBSERVATION_XLS_PROFILE,
            table_import.SOURCE_OBSERVATION_EVIDENCE_PROFILE} <= table_import.SUPPORTED_PROFILES
    assert {table_archive.SOURCE_OBSERVATION_PROFILE, table_archive.SOURCE_OBSERVATION_EVIDENCE_PROFILE,
            table_archive.SOURCE_OBSERVATION_XLS_PROFILE} <= table_archive.SUPPORTED_PROFILES
    raw, _ = upload_bytes(raw_observations())
    for profile in (table_import.NATIVE31_PROFILE, table_import.NATIVE31_EVIDENCE_PROFILE):
        plan = table_import.parse(raw, suffix='.csv', profile=profile)
        assert not plan['rows']
        assert all('source_ledger_evidence' not in row['details'] for row in plan['records'])


def test_competing_postings_keep_source_values_in_graph_profiles_details_and_excel(client, make_case):
    cid = make_case("SYN original competing postings")
    rows = entries()
    rows[0]["交易余额"] = "1000.01"
    add_source(client, cid, encoded(rows))
    observed = targets(client, cid)
    graph = tracked_graph(client, cid, observed)
    edge, = graph["edges"]
    assert (graph["transaction_count"], edge["cnt"], edge["total"]) == (2, 2, "74.82")
    assert edge["amount_totals"] == [{"currency": "CNY", "amount": "74.82"}]
    assert edge["funds"]["totals"] == [{"currency": "CNY", "amount": None, "lower": "0.00", "upper": "74.82"}]
    assert edge["funds"]["confirmed_event_count"] == 0
    payer = next(node for node in graph["nodes"] if node["out_cnt"] == 2)
    assert payer["out_amt"] == "74.82"
    assert payer["out_funds"]["confirmed_event_count"] == 0
    detail = client.get("/api/flows", params={"case_id": cid, "ids": ",".join(row["id"] for row in observed)}).json()
    assert detail["total"] == 2 and {row["amount"] for row in detail["rows"]} == {"37.41"}
    response = client.get("/api/export/tracked.xlsx", params={"case_id": cid})
    assert response.status_code == 200
    book = load_workbook(io.BytesIO(response.content), data_only=True)
    values = list(book["追踪流水"].values)
    assert [dict(zip(values[0], row))["交易金额"] for row in values[1:]] == ["37.41", "37.41"]
    values = list(book["资金统计"].values)
    stat, = [dict(zip(values[0], row)) for row in values[1:]]
    assert stat["精确事件金额"] is None and stat["可能上界"] == "74.82"
    assert "原始账簿观察" in book.sheetnames


def test_unknown_counterparties_use_existing_institution_scope_even_when_raw_identifiers_match(client, make_case):
    cid = make_case("SYN neutral institution scope")
    rows = [unknown("1.23", **{"交易账号": "SYN-SHARED", "交易卡号": "", "交易流水号": f"SYN-SCOPE-{index}",
        "交易对手账卡号": "SYN-SHARED", "对手开户银行": bank})
        for index, bank in enumerate(("SYN Bank X", "SYN Bank Y"))]
    raw, name = upload_bytes(rows)
    add_source(client, cid, raw, name)
    page = client.get(f"/api/v1/cases/{cid}/flows").json()
    graph = tracked_graph(client, cid, page["items"])
    assert graph["edges"] == [] and len(graph["nodes"]) == 3
    assert {node["account_number"] for node in graph["nodes"]} == {"SYN-SHARED"}
    assert len({node["account"] for node in graph["nodes"]}) == 3
    assert {node["bank"] for node in graph["nodes"]} == {"", "SYN Bank X", "SYN Bank Y"}
    registry = client.get("/api/account-registry", params={"case_id": cid, "q": "SYN-SHARED"}).json()
    assert registry["total"] == 3
    for node in graph["nodes"]:
        profile = client.get("/api/account-registry/profile", params={"case_id": cid, "account": node["account"]})
        assert profile.status_code == 200
        assert profile.json()["source_ledger"]["count"] == (2 if node["bank"] == "" else 1)


def test_mixed_confirmed_event_and_original_observation_keep_three_sources_and_two_display_counts(client, make_case):
    from app import event_resolution
    from test_event_graph import imported, pair
    from test_event_import import transaction
    cid = make_case('SYN mixed event and source')
    raw = transaction('SYN-A', **{'交易流水号': 'SYN-RAW-EXTRA', '交易金额': '7.00',
        **{header: '' for header in event_resolution.HEADERS.values()}})
    rows = imported(client, cid, pair('SYN-MIXED') + [raw])
    graph = tracked_graph(client, cid, rows)
    edge, = graph['edges']
    assert (edge['cnt'], edge['total']) == (2, '107.00')
    assert edge['funds']['observation_count'] == 3
    assert edge['funds']['confirmed_event_count'] == edge['funds']['unresolved_observation_count'] == 1
    assert edge['funds']['totals'] == [{'currency': 'CNY', 'amount': None, 'lower': '100.00', 'upper': '107.00'}]


def test_identity_conflict_keeps_raw_accounts_direction_money_without_confirming_single_postings(client, make_case):
    from test_bank_single_postings import entries as singles
    from test_folder_evidence import account, archive, table
    bank = 'SYN identity bank'
    cid = make_case('SYN conflicting account-card declarations')
    payload = archive([
        (f'SYN-REQUEST/{bank}交易明细信息.csv', encoded(singles())),
        (f'SYN-REQUEST/{bank}账户信息.csv', table('account', [account('SYN-SINGLE-A', 'SYN-SINGLE-CARD', bank),
            account('SYN-OTHER-CLAIM', 'SYN-SINGLE-CARD', bank)])),
        ('manifest.csv', table('task', [{'请求单号': 'SYN-REQUEST', '银行名称': bank}])),
    ])
    add_source(client, cid, payload, 'identity.zip')
    rows = targets(client, cid)
    assert len(rows) == 2
    assert all(row['raw_payer_account'] == 'SYN-SINGLE-A' and row['raw_payee_account'] == 'SYN-SINGLE-B' for row in rows)
    assert all(row['payer_card_state'] == 'conflict' for row in rows)
    graph = tracked_graph(client, cid, rows)
    edge, = graph['edges']
    assert (edge['cnt'], edge['total']) == (2, '24.68')
    assert edge['funds']['confirmed_event_count'] == 0
    assert {node['account_number'] for node in graph['nodes']} == {'SYN-SINGLE-A', 'SYN-SINGLE-B'}


def test_pending_source_list_keeps_tracked_original_after_later_event_conflict(client, make_case):
    from test_event_graph import imported, pair
    from test_event_import import csv_rows
    from test_folder_evidence import accepted
    from test_export_integrity import workbook, records
    cid = make_case('SYN pending counterpart under conflict')
    rows = imported(client, cid, pair('SYN-PENDING-CONFLICT')[:1])
    tracked_graph(client, cid, rows)
    accepted(client, cid, [('conflict.csv', csv_rows(pair('SYN-PENDING-CONFLICT', '101.00')[:1]))], scope_bank=None)
    pending, = records(workbook(client, cid)['待调取账号'])
    assert pending['账号'] == 'SYN-B'
    assert pending['转入笔数'] == '1' and pending['转入金额'] == pending['交易总额'] == '100.00'
