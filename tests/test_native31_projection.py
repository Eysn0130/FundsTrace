"""Native31 observations and source-local reciprocal ledger identities."""
import io
import json

from openpyxl import load_workbook
import pytest

from test_bank_posting_pairs import encoded, entries, targets, tracked_graph
from test_folder_evidence import account, archive, table
from test_native_bank_observations import upload_bytes
from test_source_contract_68 import HEADERS
from test_source_ledger import add_source
from test_unknown_direction_source import unknown


@pytest.mark.parametrize("kind", ["csv", "zip", "xlsx", "xls"])
def test_native31_unknown_direction_is_queryable_without_inventing_funds(client, make_case, kind):
    cid = make_case("SYN native31 unknown direction")
    raw, name = upload_bytes([unknown(), unknown("0.00", 交易时间="2026-01-01 10:01:00")], kind, HEADERS)
    view, _ = add_source(client, cid, raw, name)
    base = f"/api/v1/cases/{cid}"
    page = client.get(base + "/flows", params={"account": "SYN-UNORIENTED"}).json()
    assert page["total"] == 2
    assert {row["amount"] for row in page["items"]} == {"12.34", "0.00"}
    assert all(row["payer_account"] == row["payee_account"] == "" for row in page["items"])
    assert all(row["source_ledger"]["direction"] == "unknown" for row in page["items"])
    assert client.get(base + "/stats").json()["amount_total"] == "0.00"
    graph = tracked_graph(client, cid, page["items"])
    assert len(graph["nodes"]) == 1 and graph["edges"] == []
    assert graph["nodes"][0]["in_amt"] == graph["nodes"][0]["out_amt"] == "0.00"
    response = client.get("/api/export/tracked.xlsx", params={"case_id": cid})
    assert response.status_code == 200
    book = load_workbook(io.BytesIO(response.content), data_only=True)
    rows = list(book["追踪流水"].values)
    assert all(dict(zip(rows[0], row))["方向状态"] == "unknown" for row in rows[1:])
    receipt = client.get(base + f"/imports/{view['import_id']}").json()
    packed = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("native31.fundcase", packed.content)})
    assert restored.status_code == 200, restored.text
    assert client.get(f"/api/v1/cases/{restored.json()['case_id']}/flows").json()["total"] == 2
    assert client.delete(f"/api/batches/{receipt['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    assert client.get(base + "/flows").json()["total"] == 0


def scoped_pair(rows, *, bank="Synthetic bank", member=""):
    return archive([
        (member or f"SYN-REQUEST/{bank}交易明细信息.csv", encoded(rows)),
        (f"SYN-REQUEST/{bank}账户信息.csv", table("account", [account("SYN-A", "SYN-A", bank), account("SYN-B", "SYN-B", bank)])),
        ("manifest.csv", table("task", [{"请求单号": "SYN-REQUEST", "银行名称": bank}])),
    ])


def numeric_bank_entries():
    rows = entries()
    for row in rows:
        row["对手开户银行"] = "990011"
    return rows


def test_scoped_reciprocal_postings_bind_only_their_proved_endpoint_roles(client, database, make_case):
    cid = make_case("SYN local bank-code roles")
    original = scoped_pair(numeric_bank_entries())
    view, _ = add_source(client, cid, original, "native.zip")
    rows = targets(client, cid)
    graph = tracked_graph(client, cid, rows)
    assert len(graph["nodes"]) == 2
    edge, = graph["edges"]
    assert (edge["cnt"], edge["total"], graph["transaction_count"]) == (1, "37.41", 1)
    proof, = database.q_raw("select evidence_json from resolved_events where case_id=?", [cid])
    proof = json.loads(proof["evidence_json"])
    assert {side["difference"] for side in proof["sides"]} == {"-37.41", "37.41"}
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == original
    # The numeric code is retained in immutable source facts, not registered as
    # an institution alias. An unrelated same-number endpoint remains separate.
    separate = dict(entries()[0], **{"交易流水号": "SYN-UNRELATED", "交易对手账卡号": "SYN-B", "对手开户银行": "990011"})
    add_source(client, cid, encoded([separate]), "unbound.csv")
    unrelated, = client.get(f"/api/v1/cases/{cid}/flows", params={"kw": "SYN-UNRELATED"}).json()["items"]
    assert unrelated["payee_account"] != rows[0]["payee_account"]
    packed = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("paired-native31.fundcase", packed.content)})
    assert restored.status_code == 200, restored.text
    restored_graph = client.get("/api/graph", params={"case_id": restored.json()["case_id"]}).json()
    assert (len(restored_graph["nodes"]), restored_graph["edges"][0]["total"]) == (2, "37.41")


def test_explicit_reimport_supplies_reversible_role_proof_without_changing_old_receipt(client, make_case, monkeypatch):
    from app import table_archive
    cid = make_case("SYN old numeric bank roles")
    raw = scoped_pair(numeric_bank_entries())
    with monkeypatch.context() as old:
        old.setattr(table_archive, "evidence_profile", lambda _: table_archive.UNORIENTED_EVIDENCE_PROFILE)
        view, job = add_source(client, cid, raw, "native.zip")
    base = f"/api/v1/cases/{cid}"
    receipt = client.get(base + f"/imports/{view['import_id']}").json()
    before = tracked_graph(client, cid, targets(client, cid))
    assert len(before["nodes"]) == 4 and before["funds"]["confirmed_event_count"] == 0
    _, new = add_source(client, cid, raw, "native.zip")
    current = client.get("/api/graph", params={"case_id": cid}).json()
    assert len(current["nodes"]) == 2 and current["edges"][0]["total"] == "37.41"
    assert client.get(base + f"/imports/{view['import_id']}").json() == receipt
    assert client.delete(f"/api/batches/{new['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    withdrawn = client.get("/api/graph", params={"case_id": cid}).json()
    assert len(withdrawn["nodes"]) == 4 and withdrawn["funds"]["confirmed_event_count"] == 0
    assert client.get(base + f"/imports/{view['import_id']}").json() == receipt


def test_native31_old_ready_unknown_observation_requires_explicit_reimport(client, make_case, monkeypatch):
    from app import table_import
    from app.services import imports
    from test_automatic_table_import import preview
    from test_folder_evidence import complete
    cid = make_case("SYN native31 old ready")
    raw, name = upload_bytes([unknown()], "csv", HEADERS)
    with monkeypatch.context() as old:
        old.setattr(imports, "TABLE_PROFILE", table_import.UNORIENTED_PROFILE)
        ready = preview(client, cid, raw, name)
    base = f"/api/v1/cases/{cid}"
    refreshed = client.post(base + f"/imports/{ready['import_id']}/preview", json={"mode": ready["mode"], "mapping": ready["mapping"]})
    assert refreshed.status_code == 200
    assert refreshed.json()["table_summary"]["profile"] == "funds-trace-table-v60"
    assert complete(client, cid, refreshed.json())["stage"] == "done"
    old_receipt = client.get(base + f"/imports/{ready['import_id']}").json()
    assert client.get(base + "/flows").json()["total"] == 0
    _, new = add_source(client, cid, raw, name)
    assert client.get(base + "/flows").json()["total"] == 1
    assert client.get(base + f"/imports/{ready['import_id']}").json() == old_receipt
    assert client.delete(f"/api/batches/{new['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    assert client.get(base + "/flows").json()["total"] == 0


@pytest.mark.parametrize("changes", [{"交易对手账卡号": "SYN-COUNTER"}, {"对手户名": "SYN counter"},
                                      {"对手开户银行": "Synthetic bank"}])
def test_native31_unknown_direction_preserves_known_counterparty_without_inventing_direction(client, make_case, changes):
    cid = make_case("SYN native31 unknown counterparty")
    raw, name = upload_bytes([unknown(**changes)], "csv", HEADERS)
    view, _ = add_source(client, cid, raw, name)
    base = f"/api/v1/cases/{cid}"
    page = client.get(base + "/flows").json()
    assert page["total"] == 1
    row, = page['items']
    assert row['payer_account'] == row['payee_account'] == ''
    assert row['source_ledger']['direction'] == 'unknown'
    assert row['source_ledger']['counter_account'] == changes.get('交易对手账卡号', '')
    assert row['source_ledger']['counter_name'] == changes.get('对手户名', '')
    assert row['source_ledger']['counter_bank'] == changes.get('对手开户银行', '')
    assert tracked_graph(client, cid, page['items'])['edges'] == []
    record, = client.get(base + f"/sources/{view['import_id']}/report").json()["items"]
    assert record["disposition"] == "unresolved" and record["flow_id"] == row['id']


@pytest.mark.parametrize("change", ["named_bank_conflict", "missing_witness", "different_member"])
def test_bank_code_does_not_override_source_or_balance_conflicts(client, make_case, change):
    cid = make_case("SYN bank-code rejection")
    rows = numeric_bank_entries()
    if change == "named_bank_conflict":
        for row in rows:
            row["对手开户银行"] = "Synthetic other bank"
    elif change == "missing_witness":
        rows[3]["交易余额"] = "2037.42"
    if change == "different_member":
        raw = archive([
            ("SYN-REQUEST/Synthetic bank A交易明细信息.csv", encoded(rows[:2])),
            ("SYN-REQUEST/Synthetic bank B交易明细信息.csv", encoded(rows[2:])),
            ("manifest.csv", table("task", [{"请求单号": "SYN-REQUEST", "银行名称": bank} for bank in ("Synthetic bank A", "Synthetic bank B")])),
        ])
    else:
        raw = scoped_pair(rows)
    add_source(client, cid, raw, "rejected.zip")
    graph = tracked_graph(client, cid, targets(client, cid))
    assert graph["funds"]["confirmed_event_count"] == 0
