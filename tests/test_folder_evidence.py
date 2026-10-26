"""Public synthetic folder-evidence loop; no private files or provider calls."""
import csv
import io
import json
import time
import zipfile
from decimal import Decimal

import pandas as pd
import pytest

from app import evidence_resolution, feedback_import
from app.services import imports


def table(role, records):
    columns = feedback_import.SCHEMAS[role]
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(columns)
    for row in records:
        writer.writerow([row.get(column, "") for column in columns])
    return stream.getvalue().encode("utf-8-sig")


def transaction(**changes):
    row = {"交易卡号": "SYN-C1", "交易账号": "SYN-A", "交易时间": "2026-01-01 10:00:00",
           "交易金额": "10.25", "交易余额": "100.00", "收付标志": "出", "交易对手账卡号": "SYN-B",
           "对手户名": "Synthetic counterparty", "对手开户银行": "Synthetic other bank",
           "交易币种": "CNY", "交易是否成功": "1", "交易流水号": "SYN-T1"}
    row.update(changes)
    return row


def account(number="SYN-A", card="SYN-C1", bank="Synthetic bank", **changes):
    return {"交易账号": number, "交易卡号": card, "账号开户银行": bank, "账户开户名称": "Synthetic owner", **changes}


def archive(members):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as target:
        for name, content in members:
            target.writestr(name, content)
    return stream.getvalue()


def preview(client, cid, members, *, zipped=False, legacy=False, scope_bank="Synthetic bank"):
    members = scoped(members, scope_bank) if scope_bank else members
    base = f"/api/v1/cases/{cid}/imports"
    if legacy:
        url = f"/api/import/directory/preview?case_id={cid}"
    else:
        url = base + ("/preview" if zipped else "/directory/preview")
    response = client.post(url, files={"file": ("sources.zip", archive(members))} if zipped else
                           [("files", (path, content, "application/octet-stream")) for path, content in members])
    assert response.status_code == 200, response.text
    return response.json()


def complete(client, cid, view, *, legacy=False):
    base = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
    body = {"mode": view["mode"], "mapping": view["mapping"], "mark_accounts": []}
    if legacy:
        response = client.post("/api/import/commit", json={**body, "case_id": cid, "import_id": view["import_id"], "mark_accounts": ""})
    else:
        response = client.post(base + "/commit", json=body)
    assert response.status_code == 200, response.text
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = client.get("/api/import/progress", params={"case_id": cid, "import_id": view["import_id"]}) if legacy else client.get(base)
        assert response.status_code == 200, response.text
        job = response.json()
        if job["stage"] in {"done", "error"}:
            return job
        time.sleep(.005)
    raise AssertionError("synthetic task timed out")


def scoped(members, bank):
    result, has_tx = [], False
    for path, content in members:
        with io.TextIOWrapper(io.BytesIO(content), encoding="utf-8-sig", newline="") as stream:
            header = next(csv.reader(stream))
        if frozenset(header) == frozenset(feedback_import.COLUMNS) and not path.endswith("交易明细信息.csv"):
            path = "SYN-REQUEST/" + bank + "交易明细信息.csv"
            has_tx = True
        result.append((path, content))
    if has_tx:
        result.append(("manifest.csv", table("task", [{"请求单号": "SYN-REQUEST", "银行名称": bank}])))
    return result


def accepted(client, cid, members, **options):
    view = preview(client, cid, members, **options)
    job = complete(client, cid, view, legacy=options.get("legacy", False))
    assert job["stage"] == "done", job
    return view, job["result"]


def entities(client, cid):
    response = client.get(f"/api/v1/cases/{cid}/resolution")
    assert response.status_code == 200, response.text
    return response.json()


def test_directory_auxiliary_late_replay_card_account_nodes_and_consistent_money(client, database, make_case):
    cid = make_case("Synthetic folder")
    transactions = table("transaction", [transaction(**{"交易账号": ""}),
        transaction(**{"交易卡号": "SYN-C2", "交易流水号": "SYN-T2", "交易币种": "USD", "交易金额": "20.03"}),
        transaction(**{"交易卡号": "", "交易流水号": "SYN-T3"})])
    first, result = accepted(client, cid, [("folder/transactions.csv", transactions)])
    before = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert len({row["payer_account"] for row in before}) == 2  # one known account, one card carrier
    raw = database.q_raw("select payer_account,payee_account,amount,currency from flows where case_id=? order by id", [cid])
    _, auxiliary = accepted(client, cid, [("folder/accounts.csv", table("account", [account(), account(card="SYN-C2")]))])
    assert auxiliary["ok"] == 0 and auxiliary["resolution"]["auxiliary_records"] == 3
    after = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    keys = {row["payer_account"] for row in after}
    assert len(keys) == 1 and next(iter(keys)).startswith("fte_")
    assert {row["raw_payer_account"] for row in after} == {"SYN-A", "SYN-C1"}
    assert {row["payer_display_account"] for row in after} == {"SYN-C1"}
    own = next(item for item in entities(client, cid)["entities"] if item["entity_id"] in keys)
    assert own["kind"] == "account" and own["account_number"] == "SYN-A"
    assert own["cards"] == ["SYN-C1", "SYN-C2"]
    assert database.q_raw("select payer_account,payee_account,amount,currency from flows where case_id=? order by id", [cid]) == raw
    ids = [row["id"] for row in after]
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": ids}).status_code == 200
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    assert graph["statistics_policy"] == evidence_resolution.POLICY
    assert len(graph["edges"]) == 1 and graph["edges"][0]["cnt"] == 3
    assert graph["edges"][0]["amount_totals"] == [{"currency": "CNY", "amount": "20.50"}, {"currency": "USD", "amount": "20.03"}]
    stats = client.get(f"/api/v1/cases/{cid}/stats").json()
    assert stats["amount_totals"] == graph["edges"][0]["amount_totals"]
    profile = client.get("/api/account-registry/profile", params={"case_id": cid, "account": own["entity_id"]})
    assert profile.status_code == 200, profile.text
    assert profile.json()["out_totals"] == stats["amount_totals"]
    assert client.get(f"/api/v1/cases/{cid}/flows", params={"account": "SYN-C1"}).json()["total"] == 3


def test_complete_zip_parent_subaccounts_one_card_many_ledgers_and_card_only(client, database, make_case):
    cid = make_case("Synthetic typed subaccounts")
    rows = [transaction(**{"交易账号": "SYN-S1"}), transaction(**{"交易账号": "SYN-S2", "交易流水号": "SYN-T2", "交易币种": "USD"}),
            transaction(**{"交易账号": "", "交易流水号": "SYN-T3"}), transaction(**{"交易账号": "SYN-X", "交易流水号": "SYN-T4"})]
    members = [("files/transactions.csv", table("transaction", rows)),
               ("files/accounts.csv", table("account", [account(number="SYN-P")])),
               ("files/subaccounts.csv", table("subaccount", [{"银行名称": "Synthetic bank", "开户账号": "SYN-P", "子账户账号": "SYN-S1", "币种": "CNY"},
                                                              {"银行名称": "Synthetic bank", "开户账号": "SYN-P", "子账户账号": "SYN-S2", "币种": "USD"}]))]
    _, result = accepted(client, cid, members, zipped=True)
    assert result["ok"] == 4 and result["resolution"]["conflicts"] >= 1
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    by_no = {row["trade_no"]: row for row in flows}
    assert len({row["payer_account"] for row in flows}) == 4
    registry = {item["entity_id"]: item for item in entities(client, cid)["entities"]}
    assert registry[by_no["SYN-T1"]["payer_account"]]["kind"] == "subaccount"
    assert registry[by_no["SYN-T2"]["payer_account"]]["kind"] == "subaccount"
    assert registry[by_no["SYN-T3"]["payer_account"]]["kind"] == "card"
    assert registry[by_no["SYN-T4"]["payer_account"]]["account_number"] == "SYN-X"
    assert {row["payer_account"] for row in database.q_raw("select payer_account from flows where case_id=?", [cid])} == {"SYN-S1", "SYN-S2", "SYN-C1", "SYN-X"}
    report = client.get(f"/api/v1/cases/{cid}/sources/{result['source_id']}/report").json()
    assert any("resolution_evidence" in row["details"] for row in report["items"])


@pytest.mark.parametrize("auxiliary_role", ["account", "subaccount"])
def test_ordinary_zip_activates_with_one_supported_auxiliary_role(client, database, make_case, auxiliary_role):
    cid = make_case("Synthetic partial role family")
    number = "SYN-A" if auxiliary_role == "account" else "SYN-S1"
    auxiliary = table("account", [account()]) if auxiliary_role == "account" else table("subaccount", [
        {"银行名称": "Synthetic bank", "开户账号": "SYN-P", "子账户账号": number}])
    view, receipt = accepted(client, cid, [("tx.csv", table("transaction", [transaction(**{"交易账号": number, "交易卡号": ""})])),
                                          ("aux.csv", auxiliary)], zipped=True)
    assert view["resolution"] and receipt["resolution"]
    assert database.q_raw("select parser_version from import_sources where case_id=?", [cid])[0]["parser_version"] in evidence_resolution.PROFILES
    flow = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]
    own = next(row for row in entities(client, cid)["entities"] if row["entity_id"] == flow["payer_account"])
    assert own["kind"] == auxiliary_role and own["account_number"] == number
    assert own["cards"] == (["SYN-C1"] if auxiliary_role == "account" else [])
    assert bool(own["parent_entity_id"]) == (auxiliary_role == "subaccount")


@pytest.mark.parametrize("encoding", ["utf-8-sig", "gb18030"])
def test_ordinary_zip_large_manifest_uses_complete_bounded_csv_records(client, database, make_case, encoding):
    cid = make_case("Synthetic complete large manifest")
    manifest = table("task", [{"请求单号": "SYN-REQUEST", "银行名称": "Synthetic bank",
                               "任务流水号": f"SYN-ROW-{index}", "反馈结果": 'Synthetic quoted, "response"\n' * 5}
                              for index in range(700)]).decode("utf-8-sig").encode(encoding)
    assert len(manifest) > 65536
    members = [("SYN-REQUEST/Synthetic bank交易明细信息.csv", table("transaction", [transaction(**{"交易账号": ""})])),
               ("accounts.csv", table("account", [account()])), ("manifest.csv", manifest)]
    view, receipt = accepted(client, cid, members, zipped=True, scope_bank=None)
    assert view["resolution"] and receipt["resolution"]["auxiliary_records"] == 701
    assert database.q_raw("select parser_version from import_sources where case_id=?", [cid])[0]["parser_version"] == feedback_import.EVIDENCE_PROFILE
    row = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]
    own = next(item for item in entities(client, cid)["entities"] if item["entity_id"] == row["payer_account"])
    assert own["bank"] == "Synthetic bank" and own["kind"] == "account"


def test_late_contradiction_revokes_card_to_account_projection(client, database, make_case):
    cid = make_case("Synthetic changing declarations")
    accepted(client, cid, [("transactions.csv", table("transaction", [transaction(**{"交易账号": ""})])),
                           ("account.csv", table("account", [account()]))])
    first = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]
    assert first["payer_account"] != first["raw_payer_account"]
    accepted(client, cid, [("later.csv", table("account", [account(number="SYN-A2")]))])
    second = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]
    assert second["id"] == first["id"] and second["payer_account"] != first["payer_account"]
    own = next(item for item in entities(client, cid)["entities"] if item["entity_id"] == second["payer_account"])
    assert own["kind"] == "card" and own["account_number"] == "SYN-C1"
    assert database.q_raw("select count(*) n from resolution_revisions where case_id=?", [cid])[0]["n"] == 2


def test_same_bytes_new_bank_scope_retains_distinct_observations_and_cases(client, database, make_case):
    cid, other = make_case("Synthetic bank scopes"), make_case("Synthetic isolated")
    tx = table("transaction", [transaction()])
    first_members = [("tx.csv", tx), ("accounts.csv", table("account", [account(bank="Synthetic bank A")]))]
    second_members = [("tx.csv", tx), ("accounts.csv", table("account", [account(bank="Synthetic bank B")]))]
    accepted(client, cid, first_members, scope_bank="Synthetic bank A")
    _, second = accepted(client, cid, second_members, scope_bank="Synthetic bank B")
    assert (second["ok"], second["rows_dup"]) == (1, 0)
    rows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert len(rows) == 2 and rows[0]["payer_account"] != rows[1]["payer_account"]
    assert client.get(f"/api/v1/cases/{other}/flows").json()["total"] == 0
    assert client.get(f"/api/v1/cases/{other}/resolution").json()["entities"] == []


def test_order_wrapper_idempotence_package_roundtrip_and_batch_delete(client, database, make_case):
    cid = make_case("Synthetic package evidence")
    members = [("folder/tx.csv", table("transaction", [transaction(), transaction(**{"交易流水号": "SYN-T2"})])),
               ("folder/acc.csv", table("account", [account()])),
               ("folder/sub.csv", table("subaccount", [{"银行名称": "Synthetic bank", "开户账号": "SYN-A", "子账户账号": "SYN-S"}]))]
    _, first = accepted(client, cid, members)
    rows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    _, repeated = accepted(client, cid, list(reversed(members)), zipped=True)
    assert (first["ok"], repeated["ok"], repeated["rows_dup"]) == (2, 0, 2)
    assert entities(client, cid)["resolution"]["revision"] == first["resolution"]["revision"]
    assert repeated["resolution"] == entities(client, cid)["resolution"] == client.get(f"/api/v1/cases/{cid}/stats").json()["resolution"]
    exported = client.get(f"/api/cases/{cid}/export")
    assert exported.status_code == 200, exported.text
    with zipfile.ZipFile(io.BytesIO(exported.content)) as package:
        assert json.loads(package.read("meta.json"))["format"] == 5
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", exported.content)})
    assert restored.status_code == 200, restored.text
    target = restored.json()["case_id"]
    restored_rows = client.get(f"/api/v1/cases/{target}/flows").json()["items"]
    assert {row["payer_account"] for row in restored_rows} == {row["payer_account"] for row in rows}
    assert client.delete(f"/api/batches/{first['batch_id']}", params={"case_id": cid}).status_code == 200
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 0
    assert client.get(f"/api/v1/cases/{target}/flows").json()["total"] == 2


def test_directory_unknown_and_unsafe_members_reject_before_retaining_source(client, database, make_case):
    cid = make_case("Synthetic safe directory")
    for files in [[("files", ("../escape.csv", b"a,b\n1,2\n"))],
                  [("files", ("same.csv", b"a,b\n")), ("files", ("same.csv", b"c,d\n"))]]:
        response = client.post(f"/api/v1/cases/{cid}/imports/directory/preview", files=files)
        assert response.status_code == 400, response.text
    assert database.q_raw("select count(*) n from import_sessions where case_id=?", [cid])[0]["n"] == 0
    assert database.q_raw("select count(*) n from flows where case_id=?", [cid])[0]["n"] == 0


def test_resolution_write_failure_rolls_back_whole_import_and_retry_is_explicit(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic evidence rollback")
    members = [("tx.csv", table("transaction", [transaction()])), ("accounts.csv", table("account", [account()]))]
    view = preview(client, cid, members)
    with monkeypatch.context() as patch:
        patch.setattr(evidence_resolution, "rebuild", lambda _cid: (_ for _ in ()).throw(RuntimeError("synthetic failure")))
        result = complete(client, cid, view)
    assert result["stage"] == "error"
    for name in ("flows", "batches", "import_sources", "import_records", "resolved_endpoints", "resolution_state"):
        assert database.q_raw(f"select count(*) n from {name} where case_id=?", [cid])[0]["n"] == 0
    accepted(client, cid, members, legacy=True)
    assert database.q_raw("select count(*) n from flows where case_id=?", [cid])[0]["n"] == 1


def test_new_projection_uses_accepted_ledger_records_without_temporal_mirror_guess(client, make_case):
    cid = make_case("Synthetic dual ledgers")
    rows = [transaction(**{"交易账号": "SYN-A", "交易卡号": "SYN-C1", "交易金额": "100.00", "对手开户银行": "Synthetic bank B"}),
            transaction(**{"交易账号": "SYN-B", "交易卡号": "SYN-C2", "交易流水号": "SYN-T2", "交易金额": "100.00", "收付标志": "入", "交易对手账卡号": "SYN-A", "对手开户银行": "Synthetic bank A"})]
    accepted(client, cid, [("SYN-REQUEST/Synthetic bank A交易明细信息.csv", table("transaction", rows[:1])),
                           ("SYN-REQUEST/Synthetic bank B交易明细信息.csv", table("transaction", rows[1:])),
                           ("manifest.csv", table("task", [{"请求单号": "SYN-REQUEST", "银行名称": "Synthetic bank A"}, {"请求单号": "SYN-REQUEST", "银行名称": "Synthetic bank B"}])),
                           ("accounts.csv", table("account", [account(bank="Synthetic bank A"), account(number="SYN-B", card="SYN-C2", bank="Synthetic bank B")]))], scope_bank=None)
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": [row["id"] for row in flows]}).status_code == 200
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    stats = client.get(f"/api/v1/cases/{cid}/stats").json()
    assert graph["edges"][0]["cnt"] == 2 and graph["edges"][0]["amount_totals"] == [{"currency": "CNY", "amount": "200.00"}]
    assert stats["flows"] == 2 and stats["amount_totals"] == graph["edges"][0]["amount_totals"]
    resolution = graph["resolution"]
    assert resolution["policy"] == evidence_resolution.POLICY
    investigation = client.post("/api/canvas/investigation/time", json={"case_id": cid, "canvas_id": 0})
    assert investigation.status_code == 200, investigation.text
    body = investigation.json()
    assert body["summary"]["mirror_duplicate_count"] == 0
    assert body["summary"]["amount_totals"] == stats["amount_totals"]


def test_mixed_counterparty_card_and_explicit_ledger_share_entity_without_event_dedup(client, make_case):
    cid = make_case("Synthetic typed counterparty")
    a = transaction(**{"交易账号": "SYN-A", "交易卡号": "SYN-CA", "交易对手账卡号": "SYN-CB", "对手开户银行": "Synthetic bank B"})
    b = transaction(**{"交易账号": "SYN-B", "交易卡号": "SYN-CB", "交易对手账卡号": "SYN-CA", "对手开户银行": "Synthetic bank A", "收付标志": "入", "交易流水号": "SYN-T2"})
    members = [("SYN-REQUEST/Synthetic bank A交易明细信息.csv", table("transaction", [a])),
               ("SYN-REQUEST/Synthetic bank B交易明细信息.csv", table("transaction", [b])),
               ("accounts.csv", table("account", [account(card="SYN-CA", bank="Synthetic bank A"), account(number="SYN-B", card="SYN-CB", bank="Synthetic bank B")])),
               ("manifest.csv", table("task", [{"请求单号": "SYN-REQUEST", "银行名称": "Synthetic bank A"}, {"请求单号": "SYN-REQUEST", "银行名称": "Synthetic bank B"}]))]
    accepted(client, cid, members, scope_bank=None)
    rows = sorted(client.get(f"/api/v1/cases/{cid}/flows").json()["items"], key=lambda row: row["trade_no"])
    assert rows[0]["payer_account"] == rows[1]["payer_account"]
    assert rows[0]["payee_account"] == rows[1]["payee_account"]
    assert rows[0]["raw_payee_account"] == "SYN-CB" and rows[1]["raw_payee_account"] == "SYN-B"
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": [row["id"] for row in rows]}).status_code == 200
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    assert len(graph["nodes"]) == 2 and graph["edges"][0]["cnt"] == 2


def test_single_observed_bank_is_not_an_institution_proof(client, make_case):
    cid = make_case("Synthetic unscoped source")
    accepted(client, cid, [("arbitrary-folder/Synthetic bank transactions.csv", table("transaction", [transaction()])),
                           ("accounts.csv", table("account", [account()]))], scope_bank=None)
    body = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]
    own = next(item for item in entities(client, cid)["entities"] if item["entity_id"] == body["payer_account"])
    assert own["bank"] == "" and own["status"] == "unscoped"
    assert own["entity_id"] != next(item["entity_id"] for item in entities(client, cid)["entities"] if item["bank"] == "Synthetic bank")


def test_equal_card_account_text_remains_card_and_auxiliary_delete_revokes_relation(client, database, make_case):
    cid = make_case("Synthetic equal typed strings")
    accepted(client, cid, [("tx.csv", table("transaction", [transaction(**{"交易账号": ""})])),
                           ("acc.csv", table("account", [account(number="SYN-C1")]))])
    own_id = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]["payer_account"]
    own = next(item for item in entities(client, cid)["entities"] if item["entity_id"] == own_id)
    assert own["kind"] == "card"
    other = make_case("Synthetic removable auxiliary")
    accepted(client, other, [("tx.csv", table("transaction", [transaction(**{"交易账号": ""})]))])
    _, auxiliary = accepted(client, other, [("accounts.csv", table("account", [account()]))])
    first = client.get(f"/api/v1/cases/{other}/flows").json()["items"][0]
    assert client.delete(f"/api/batches/{auxiliary['batch_id']}", params={"case_id": other}).status_code == 200
    second = client.get(f"/api/v1/cases/{other}/flows").json()["items"][0]
    assert first["payer_account"] != second["payer_account"]
    assert next(item for item in entities(client, other)["entities"] if item["entity_id"] == second["payer_account"])["kind"] == "card"
    assert database.q_raw("select active from evidence_sources where case_id=? and batch_id=?", [other, auxiliary["batch_id"]])[0]["active"] is False


def test_historical_financial_occurrence_overlap_is_explicitly_rejected(client, database, make_case):
    cid = make_case("Synthetic legacy migration boundary")
    members = [("tx.csv", table("transaction", [transaction()]))]
    old = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("old.zip", archive(members))})
    assert old.status_code == 200, old.text
    assert complete(client, cid, old.json())["stage"] == "done"
    response = client.post(f"/api/v1/cases/{cid}/imports/directory/preview", files=[("files", (name, content)) for name, content in scoped(members, "Synthetic bank")])
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "historical_evidence_overlap"
    assert database.q_raw("select count(*) n from flows where case_id=?", [cid])[0]["n"] == 1


def test_generic_table_propagates_mixed_identity_type_and_explicit_bank(client, make_case):
    cid = make_case("Synthetic generic mixed identities")
    frame = pd.DataFrame([
        {"本方账号": "SYN-A", "本方卡号": "SYN-CA", "本方开户银行": "Synthetic bank A", "交易时间": "2026-01-01 10:00:00", "交易金额": "10.25", "收付标志": "出", "交易对手账卡号": "SYN-CB", "对手开户银行": "Synthetic bank B", "交易币种": "CNY"},
        {"本方账号": "SYN-B", "本方卡号": "SYN-CB", "本方开户银行": "Synthetic bank B", "交易时间": "2026-01-01 10:00:01", "交易金额": "10.25", "收付标志": "入", "交易对手账卡号": "SYN-CA", "对手开户银行": "Synthetic bank A", "交易币种": "CNY"},
    ])
    members = [("generic.csv", frame.to_csv(index=False).encode("utf-8-sig")),
               ("accounts.csv", table("account", [account(card="SYN-CA", bank="Synthetic bank A"), account(number="SYN-B", card="SYN-CB", bank="Synthetic bank B")]))]
    _, result = accepted(client, cid, members, scope_bank=None)
    assert result["ok"] == 2
    rows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert len({row["payer_account"] for row in rows}) == len({row["payee_account"] for row in rows}) == 1
    records = client.get(f"/api/v1/cases/{cid}/imports/{result['source_id']}/records").json()["items"]
    evidence = json.loads(next(row["details"]["resolution_evidence"] for row in records if row["disposition"] == "imported"))
    assert evidence["endpoints"]["payee"]["identifier_type"] == "account_or_card"


def test_supported_workbook_and_unsupported_attachment_are_retained_with_explicit_gap(client, make_case):
    cid = make_case("Synthetic attachments")
    workbook = io.BytesIO()
    pd.DataFrame([{"交易时间": "2026-01-01 10:00:00", "付款人账号": "SYN-X", "收款人账号": "SYN-Y", "交易金额": "9.03"}]).to_excel(workbook, index=False)
    members = [("folder/ledger.xlsx", workbook.getvalue()), ("folder/legacy.rar", b"Rar!synthetic unsupported bytes"), ("folder/.DS_Store", b"\0\0\0\1Bud1synthetic metadata")]
    view, result = accepted(client, cid, members, scope_bank=None)
    assert result["ok"] == 1
    assert view["table_summary"]["unsupported_attachments"] == 1 and view["table_summary"]["non_business_attachments"] == 1
    stats = client.get(f"/api/v1/cases/{cid}/stats").json()
    assert stats["import_coverage"]["unresolved_records"] == 1
    original = client.get(f"/api/v1/cases/{cid}/sources/{result['source_id']}/file")
    assert original.status_code == 200
    with zipfile.ZipFile(io.BytesIO(original.content)) as source:
        assert {path: source.read(path) for path in source.namelist()} == dict(members)
    exported = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", exported.content)})
    assert restored.status_code == 200, restored.text
    assert client.get(f"/api/v1/cases/{restored.json()['case_id']}/stats").json()["import_coverage"]["unresolved_records"] == 1


def test_new_case_policy_covers_legacy_unprojected_rows_and_export_revision(client, database, make_case, flow_row):
    from app import importer
    cid = make_case("Synthetic mixed policy")
    first = flow_row(trade_no="SYN-L1", payer_account="SYN-A", payee_account="SYN-B", self_account="SYN-A", amount=Decimal("100.00"))
    second = flow_row(trade_no="SYN-L2", payer_account="SYN-A", payee_account="SYN-B", self_account="SYN-B", amount=Decimal("100.00"))
    importer.commit_rows("historical.csv", "A", [first, second], case_id=cid)
    accepted(client, cid, [("aux.csv", table("account", [account(number="SYN-UNRELATED")]))], scope_bank=None)
    ids = [row["id"] for row in database.q_raw("select id from flows where case_id=?", [cid])]
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": ids}).status_code == 200
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    assert graph["edges"][0]["cnt"] == 2 and graph["edges"][0]["total"] == "200.00"
    exported = client.get("/api/export/tracked.xlsx", params={"case_id": cid})
    assert exported.status_code == 200, exported.text
    sheets = pd.read_excel(io.BytesIO(exported.content), sheet_name=None, dtype=str, keep_default_na=False)
    notes = dict(sheets["统计口径"].itertuples(index=False, name=None))
    assert notes["统计策略"] == evidence_resolution.POLICY and notes["解析修订"] == graph["resolution"]["revision"]


def test_unscoped_nodes_survive_directory_order_and_zip_timestamp_changes(client, make_case):
    cid = make_case("Synthetic stable logical family")
    members = [("folder/tx.csv", table("transaction", [transaction(**{"对手开户银行": ""})])),
               ("folder/acc.csv", table("account", [account()]))]
    accepted(client, cid, members, scope_bank=None)
    first = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    _, repeated = accepted(client, cid, list(reversed(members)), scope_bank=None)
    assert repeated["ok"] == 0 and repeated["rows_dup"] == 1
    replayed = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert replayed[0]["analysis_revision"] != first[0]["analysis_revision"]
    assert [{k:v for k,v in row.items() if k != "analysis_revision"} for row in replayed] == [
        {k:v for k,v in row.items() if k != "analysis_revision"} for row in first]
    # Force the new directory contract through a ZIP, with deliberately changed
    # directory order and ZIP timestamps, retaining every logical member byte.
    changed = io.BytesIO()
    with zipfile.ZipFile(changed, "w", zipfile.ZIP_STORED) as target:
        for name, content in reversed(members):
            info = zipfile.ZipInfo(name, date_time=(2001, 2, 3, 4, 5, 6))
            target.writestr(info, content)
    view = client.post(f"/api/v1/cases/{cid}/imports/directory/preview", files=[("files", ("wrapper.zip", changed.getvalue()))])
    assert view.status_code == 200, view.text
    job = complete(client, cid, view.json())
    assert job["stage"] == "done", job
    assert (job["result"]["ok"], job["result"]["rows_dup"]) == (0, 1)
    packaged_replay = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert packaged_replay[0]["analysis_revision"] != replayed[0]["analysis_revision"]
    assert [{k:v for k,v in row.items() if k != "analysis_revision"} for row in packaged_replay] == [
        {k:v for k,v in row.items() if k != "analysis_revision"} for row in first]


def test_manifest_from_another_logical_source_family_does_not_bind_a_bare_source(client, make_case):
    cid = make_case("Synthetic separate source families")
    accepted(client, cid, [("manifest.csv", table("task", [{"请求单号": "SYN-REQUEST", "银行名称": "Synthetic bank"}]))], scope_bank=None)
    accepted(client, cid, [("SYN-REQUEST/Synthetic bank交易明细信息.csv", table("transaction", [transaction()]))], scope_bank=None)
    row = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]
    own = next(item for item in entities(client, cid)["entities"] if item["entity_id"] == row["payer_account"])
    assert own["bank"] == "" and own["status"] == "unscoped"


def test_legacy_feedback_cancellation_counts_survive_mixed_workbook_envelope(client, make_case):
    records = [transaction(**{"交易时间": "2026-01-01 10:00:00", "交易金额": "1.00", "交易余额": "100.00", "交易流水号": "SYN-WITNESS"}),
               transaction(**{"交易时间": "2026-01-01 10:01:00", "交易金额": "10.25", "交易余额": "89.75", "交易流水号": "SYN-PAIR"}),
               transaction(**{"交易时间": "2026-01-01 10:02:00", "交易金额": "-10.25", "交易余额": "100.00", "交易流水号": "SYN-PAIR", "摘要说明": "冲正"})]
    members = scoped([("tx.csv", table("transaction", records)), ("acc.csv", table("account", [account()]))], "Synthetic bank")
    cid = make_case("Synthetic feedback policy")
    _, plain = accepted(client, cid, members, scope_bank=None)
    other = make_case("Synthetic mixed-envelope cancellation")
    empty_workbook = io.BytesIO()
    pd.DataFrame(columns=["交易时间", "付款人账号", "收款人账号", "交易金额"]).to_excel(empty_workbook, index=False)
    _, mixed = accepted(client, other, [*members, ("other.xlsx", empty_workbook.getvalue()), ("legacy.rar", b"Rar!unsupported")], scope_bank=None)
    assert plain["ok"] == mixed["ok"] == 1
    assert plain["feedback"]["cancelled"] == mixed["table_summary"]["cancelled"] == 2
    assert client.get(f"/api/v1/cases/{cid}/stats").json()["amount_totals"] == client.get(f"/api/v1/cases/{other}/stats").json()["amount_totals"] == [{"currency": "CNY", "amount": "1.00"}]


def test_updated_whole_folder_cannot_silently_duplicate_unscoped_original(client, database, make_case):
    cid = make_case("Synthetic forward context update")
    tx = table("transaction", [transaction()])
    accepted(client, cid, [("SYN-REQUEST/Synthetic bank交易明细信息.csv", tx)], scope_bank=None)
    response = client.post(f"/api/v1/cases/{cid}/imports/directory/preview", files=[
        ("files", ("SYN-REQUEST/Synthetic bank交易明细信息.csv", tx)),
        ("files", ("accounts.csv", table("account", [account()]))),
        ("files", ("manifest.csv", table("task", [{"请求单号": "SYN-REQUEST", "银行名称": "Synthetic bank"}]))),
    ])
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "evidence_context_overlap"
    assert database.q_raw("select count(*) n,sum(amount) amount from flows where case_id=?", [cid]) == [{"n": 1, "amount": Decimal("10.25")}]


def test_main_declarations_project_revoke_and_unmark_as_one_logical_account(client, database, make_case):
    cid = make_case("Synthetic main declarations")
    accepted(client, cid, [("tx.csv", table("transaction", [transaction(**{"交易账号": ""}), transaction(**{"交易流水号": "SYN-T2"})])),
                           ("acc.csv", table("account", [account()]))])
    own = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]["payer_account"]
    database.x("insert into main_accounts(case_id,account,name,source) values (?,?,?,'manual') on conflict do nothing", [cid, "SYN-C1", "Synthetic source label"])
    database.x("insert into main_accounts(case_id,account,name,source) values (?,?,?,'auto') on conflict do nothing", [cid, "SYN-A", "Synthetic auto label"])
    mains = client.get("/api/main-accounts", params={"case_id": cid}).json()
    assert len([item for item in mains if item["account"] == own]) == 1
    assert {item["account"] for item in database.q_raw("select account from main_accounts where case_id=?", [cid])} == {"SYN-C1", "SYN-A"}
    response = client.request("DELETE", "/api/main-accounts", json={"case_id": cid, "account": own})
    assert response.status_code == 200, response.text
    assert client.get("/api/main-accounts", params={"case_id": cid}).json() == []


@pytest.mark.parametrize("reader", ["feedback28", "generic32"])
@pytest.mark.parametrize("counter_bank", ["", "Synthetic bank B"])
def test_own_bank_role_does_not_leak_to_equal_counterparty_identifier(client, make_case, reader, counter_bank):
    from app import table_import
    cid = make_case("Synthetic endpoint role " + reader + counter_bank)
    record = transaction(**{"交易卡号": "", "交易账号": "SYN-SAME", "交易对手账卡号": "SYN-SAME", "对手开户银行": counter_bank})
    if reader == "feedback28":
        payload = table("transaction", [record])
    else:
        stream = io.StringIO(newline="")
        columns = sorted(table_import.BANK_FEEDBACK_32_COLUMNS)
        writer = csv.writer(stream)
        writer.writerow(columns)
        writer.writerow([record.get(column, "") for column in columns])
        payload = stream.getvalue().encode("utf-8-sig")
    _, receipt = accepted(client, cid, [
        ("SYN-REQUEST/Synthetic bank交易明细信息.csv", payload),
        ("manifest.csv", table("task", [{"请求单号": "SYN-REQUEST", "银行名称": "Synthetic bank"}]))
    ], scope_bank=None)
    row = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]
    assert row["payer_account"] != row["payee_account"]
    assert row["payer_bank"] == "Synthetic bank" and row["payee_bank"] == counter_bank
    records = client.get(f"/api/v1/cases/{cid}/imports/{receipt['source_id']}/records").json()["items"]
    observation = json.loads(next(item["details"]["resolution_evidence"] for item in records if item["disposition"] == "imported"))
    assert observation["own_side"] == "payer"
    assert observation["endpoints"]["payee"]["bank"] == counter_bank


def test_new_envelope_expanded_budget_is_versioned_and_checks_nested_bytes(monkeypatch):
    from app import table_archive
    assert table_archive.MAX_EXPANDED_BYTES == 512 * 1024 * 1024
    assert table_archive.EVIDENCE_MAX_EXPANDED_BYTES == 1024 * 1024 * 1024
    assert table_archive.MAX_MEMBERS == 256 and table_archive.EVIDENCE_MAX_MEMBERS == 2048
    payload = table("transaction", [transaction()])
    inner = archive([("tx.csv", payload)])
    outer = archive([("original.zip", inner)])
    actual = len(inner) + len(payload)
    monkeypatch.setattr(table_archive, "MAX_EXPANDED_BYTES", actual - 1)
    monkeypatch.setattr(table_archive, "EVIDENCE_MAX_EXPANDED_BYTES", actual)
    from app.table_import import TableImportError
    with pytest.raises(TableImportError, match="累计展开"):
        table_archive.parse(io.BytesIO(outer), profile=table_archive.PROFILE)
    plan = table_archive.parse(io.BytesIO(outer), profile=table_archive.EVIDENCE_PROFILE)
    assert len(plan["rows"]) == 1
    monkeypatch.setattr(table_archive, "EVIDENCE_MAX_EXPANDED_BYTES", actual - 1)
    with pytest.raises(TableImportError, match="累计展开"):
        table_archive.parse(io.BytesIO(outer), profile=table_archive.EVIDENCE_PROFILE)


def test_deleted_auxiliary_policy_survives_package_roundtrip(client, make_case, flow_row):
    from app import importer
    cid = make_case("Synthetic retained statistical policy")
    importer.commit_rows("historical.csv", "A", [
        flow_row(trade_no="SYN-L1", payer_account="SYN-A", payee_account="SYN-B", self_account="SYN-A", amount=Decimal("100.00")),
        flow_row(trade_no="SYN-L2", payer_account="SYN-A", payee_account="SYN-B", self_account="SYN-B", amount=Decimal("100.00")),
    ], case_id=cid)
    _, auxiliary = accepted(client, cid, [("aux.csv", table("account", [account(number="SYN-OTHER")]))], scope_bank=None)
    assert client.delete(f"/api/batches/{auxiliary['batch_id']}", params={"case_id": cid}).status_code == 200
    source_rows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": [row["id"] for row in source_rows]}).status_code == 200
    before = client.get("/api/graph", params={"case_id": cid}).json()
    package = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    after = client.get("/api/graph", params={"case_id": restored.json()["case_id"]}).json()
    assert before["statistics_policy"] == after["statistics_policy"] == evidence_resolution.POLICY
    assert before["edges"][0]["cnt"] == after["edges"][0]["cnt"] == 2
    assert before["edges"][0]["total"] == after["edges"][0]["total"] == "200.00"


def test_missing_counterparty_is_an_endpoint_gap_without_a_fabricated_node(client, make_case):
    cid = make_case("Synthetic missing endpoint")
    _, receipt = accepted(client, cid, [("tx.csv", table("transaction", [transaction(**{"交易对手账卡号": "", "对手户名": ""})]))])
    assert receipt["resolution"]["unresolved_endpoints"] == 1
    row = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]
    assert row["payee_account"] == "" and row["amount"] == "10.25"
    assert all(item["account_number"] for item in entities(client, cid)["entities"])


def test_resolution_revision_covers_summary_when_new_evidence_preserves_projection(client, make_case):
    cid = make_case("Synthetic immutable resolution summaries")
    _, first = accepted(client, cid, [("first.csv", table("account", [account()]))], scope_bank=None)
    first_registry = entities(client, cid)
    _, second = accepted(client, cid, [("second.csv", table("account", [account()]))], scope_bank=None)
    second_registry = entities(client, cid)
    assert second_registry["entities"] == first_registry["entities"]
    assert second["resolution"]["evidence_records"] == first["resolution"]["evidence_records"] + 1
    assert second["resolution"] == second_registry["resolution"] == client.get(f"/api/v1/cases/{cid}/stats").json()["resolution"]
    assert second["resolution"]["revision"] != first["resolution"]["revision"]
    _, repeated = accepted(client, cid, [("second.csv", table("account", [account()]))], scope_bank=None)
    assert repeated["resolution"] == second["resolution"] == entities(client, cid)["resolution"]


def test_user_metadata_follows_current_unique_card_relation_and_retracts(client, database, make_case):
    from PIL import Image
    cid = make_case("Synthetic user metadata lifecycle")
    accepted(client, cid, [("tx.csv", table("transaction", [transaction(**{"交易账号": ""})]))])
    first = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]
    card_key = first["payer_account"]
    assert client.post("/api/main-accounts", json={"case_id": cid, "account": card_key, "name": "Synthetic user declaration"}).status_code == 200
    assert client.post("/api/account-ids/set", json={"case_id": cid, "account": card_key, "id_no": "123456789012345"}).status_code == 200
    picture = io.BytesIO()
    Image.new("RGB", (10, 10), (20, 50, 80)).save(picture, "PNG")
    png = picture.getvalue()
    response = client.post("/api/account-photos/set", data={"case_id": cid, "account": card_key}, files={"file": ("synthetic.png", png, "image/png")})
    assert response.status_code == 200, response.text
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": [first["id"]]}).status_code == 200
    originals = {table: database.q_raw(f"select * from {table} where case_id=?", [cid])
                 for table in ("account_ids", "account_photos", "main_accounts")}
    _, auxiliary = accepted(client, cid, [("acc.csv", table("account", [account()]))])
    ledger_key = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]["payer_account"]
    assert ledger_key != card_key

    def check(case_id, key):
        profile = client.get("/api/account-registry/profile", params={"case_id": case_id, "account": key}).json()
        assert profile["id_no"] == "123456789012345" and profile["photo"] is not None and profile["is_main"]
        node = next(row for row in client.get("/api/graph", params={"case_id": case_id}).json()["nodes"] if row["account"] == key)
        assert node["id_no"] == profile["id_no"] and node["photo_at"] and node["is_main"]
        assert client.get("/api/account-photos/orig", params={"case_id": case_id, "account": key}).content == png
        assert client.get("/api/account-photos/img", params={"case_id": case_id, "account": key}).status_code == 200
        identity = client.get("/api/account-registry", params={"case_id": case_id, "source": "identity"}).json()
        assert identity["total"] == 1 and identity["items"][0]["account"] == key and identity["items"][0]["has_flows"]

    check(cid, ledger_key)
    package = client.get(f"/api/cases/{cid}/export").content
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package)})
    assert restored.status_code == 200, restored.text
    check(restored.json()["case_id"], ledger_key)
    _, contradiction = accepted(client, cid, [("conflict.csv", table("account", [account(number="SYN-A2")]))])
    check(cid, card_key)
    assert client.get("/api/account-registry/profile", params={"case_id": cid, "account": ledger_key}).json()["id_no"] is None
    assert client.delete(f"/api/batches/{contradiction['batch_id']}", params={"case_id": cid}).status_code == 200
    check(cid, ledger_key)
    assert client.delete(f"/api/batches/{auxiliary['batch_id']}", params={"case_id": cid}).status_code == 200
    check(cid, card_key)
    for table_name, rows in originals.items():
        assert database.q_raw(f"select * from {table_name} where case_id=?", [cid]) == rows


@pytest.mark.parametrize("marker", ["--", "-", "N/A", "null"])
def test_auxiliary_missing_card_literal_is_preserved_but_not_a_relation_or_alias(client, make_case, marker):
    cid = make_case("Synthetic missing card declaration")
    view, receipt = accepted(client, cid, [("tx.csv", table("transaction", [transaction(**{"交易卡号": ""})])),
                                         ("acc.csv", table("account", [account(card=marker)]))])
    own = next(row for row in entities(client, cid)["entities"] if row["account_number"] == "SYN-A")
    assert own["display_account"] == "SYN-A" and own["cards"] == []
    assert view["resolution"]["entities"] == receipt["resolution"]["entities"]
    matches = client.get("/api/account-suggestions", params={"case_id": cid, "q": marker}).json()["items"]
    assert not any(row["matched_text"] == marker for row in matches)
    records = client.get(f"/api/v1/cases/{cid}/sources/{receipt['source_id']}/report").json()["items"]
    declarations = [json.loads(row["details"][evidence_resolution.DETAIL_FIELD]) for row in records
                    if evidence_resolution.DETAIL_FIELD in row["details"]]
    assert next(row for row in declarations if row["role"] == "account")["card"] == marker
    exported = client.get(f"/api/cases/{cid}/export").content
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", exported)})
    assert restored.status_code == 200, restored.text
    after = next(row for row in entities(client, restored.json()["case_id"])["entities"] if row["account_number"] == "SYN-A")
    assert after["display_account"] == "SYN-A" and after["cards"] == []


def test_streamed_preview_and_commit_share_typed_endpoint_counts(client, make_case):
    cid = make_case("Synthetic shared preview decisions")
    rows = [transaction(**{"交易卡号": "", "交易流水号": "SYN-EXPLICIT"}),
            transaction(**{"交易账号": "", "交易流水号": "SYN-CARD"}),
            transaction(**{"交易账号": "SYN-S", "交易流水号": "SYN-SUB"}),
            transaction(**{"交易账号": "SYN-X", "交易流水号": "SYN-CONFLICT", "交易对手账卡号": ""})]
    view, receipt = accepted(client, cid, [("tx.csv", table("transaction", rows)),
        ("acc.csv", table("account", [account(number="SYN-P")])),
        ("sub.csv", table("subaccount", [{"银行名称": "Synthetic bank", "开户账号": "SYN-P", "子账户账号": "SYN-S"}]))])
    for key in ("policy", "entities", "resolved_endpoints", "unresolved_endpoints", "conflicts", "auxiliary_records", "evidence_records"):
        assert view["resolution"][key] == receipt["resolution"][key]


@pytest.mark.parametrize("number,card", [("SYN-A", "000000"), ("000000", "ALPHA-NUMBER-001")])
def test_missing_marker_rule_retains_literal_alphanumeric_and_zero_identifiers(client, make_case, number, card):
    cid = make_case("Synthetic literal identifier preservation")
    accepted(client, cid, [("tx.csv", table("transaction", [transaction(**{"交易账号": number, "交易卡号": card})])),
                           ("acc.csv", table("account", [account(number=number, card=card)]))])
    own = next(row for row in entities(client, cid)["entities"] if row["account_number"] == number)
    assert own["cards"] == [card] and own["display_account"] == card
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]["raw_payer_account"] == number


@pytest.mark.parametrize("marker", ["N/A", "null", "--"])
def test_missing_bank_marker_is_unscoped_and_never_a_shared_institution(client, database, make_case, marker):
    cid = make_case("Synthetic absent institution declaration")
    members = []
    for index in range(2):
        frame = pd.DataFrame([{"本方账号": "SYN-SAME", "本方开户银行": marker, "交易时间": "2026-01-01 10:00:00",
                               "交易金额": "10.25", "收付标志": "出", "交易对手账卡号": "SYN-OTHER",
                               "交易币种": "CNY", "交易流水号": f"SYN-BANK-GAP-{index}"}])
        members.append((f"source-{index}.csv", frame.to_csv(index=False).encode("utf-8-sig")))
    _, receipt = accepted(client, cid, members, scope_bank=None)
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    keys = {row["payer_account"] for row in flows}
    assert len(keys) == 2
    own = [row for row in entities(client, cid)["entities"] if row["entity_id"] in keys]
    assert all(row["bank"] == "" and row["status"] == "unscoped" for row in own)
    report = client.get(f"/api/v1/cases/{cid}/sources/{receipt['source_id']}/report").json()["items"]
    declarations = [json.loads(row["details"][evidence_resolution.DETAIL_FIELD]) for row in report
                    if evidence_resolution.DETAIL_FIELD in row["details"]]
    assert {row["endpoints"]["self"]["bank"] for row in declarations if row["role"] == "transaction"} == {marker}


def test_manifest_can_fill_explicit_missing_auxiliary_bank_without_rewriting_source(client, make_case):
    cid = make_case("Synthetic missing institution field with source scope")
    members = [("SYN-REQUEST/Synthetic bank交易明细信息.csv", table("transaction", [transaction(**{"交易账号": ""})])),
               ("SYN-REQUEST/Synthetic bank账户信息.csv", table("account", [account(bank="N/A")])),
               ("manifest.csv", table("task", [{"请求单号": "SYN-REQUEST", "银行名称": "Synthetic bank"}]))]
    _, receipt = accepted(client, cid, members, zipped=True, scope_bank=None)
    row = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]
    own = next(item for item in entities(client, cid)["entities"] if item["entity_id"] == row["payer_account"])
    assert own["kind"] == "account" and own["bank"] == "Synthetic bank" and own["account_number"] == "SYN-A"
    report = client.get(f"/api/v1/cases/{cid}/sources/{receipt['source_id']}/report").json()["items"]
    evidence = [json.loads(item["details"][evidence_resolution.DETAIL_FIELD]) for item in report
                if evidence_resolution.DETAIL_FIELD in item["details"]]
    assert next(item for item in evidence if item["role"] == "account")["bank"] == "N/A"


def test_missing_counterparty_markers_preserve_observations_without_graph_endpoints(client, database, make_case):
    cid = make_case("Synthetic missing graph endpoints")
    rows = [transaction(), transaction(**{"交易流水号": "SYN-MISSING-1", "交易对手账卡号": "--"}),
            transaction(**{"交易流水号": "SYN-MISSING-2", "交易对手账卡号": "/"})]
    view, receipt = accepted(client, cid, [("tx.csv", table("transaction", rows)),
        ("acc.csv", table("account", [account(), account(number="SYN-B", card="SYN-CB", bank="Synthetic other bank")]))])
    assert receipt["ok"] == 3
    for key in ("entities", "resolved_endpoints", "unresolved_endpoints", "conflicts", "evidence_records"):
        assert view["resolution"][key] == receipt["resolution"][key]
    assert receipt["resolution"]["unresolved_endpoints"] == 2
    raw = database.q_raw("select trade_no,payee_account,amount,currency from flows where case_id=? order by trade_no", [cid])
    assert len(raw) == 3 and {row["payee_account"] for row in raw} == {"SYN-B", "--", "/"}
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    gaps = [row for row in flows if row["trade_no"].startswith("SYN-MISSING")]
    assert len(gaps) == 2 and all(row["payee_account"] == "" for row in gaps)
    assert {row["raw_payee_account"] for row in gaps} == {"--", "/"}
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": [row["id"] for row in flows]}).status_code == 200
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    assert len(graph["nodes"]) == 2 and len(graph["edges"]) == 1
    assert sum(row["cnt"] for row in graph["edges"]) == 1  # 3 admitted minus 2 missing endpoints
    assert graph["edges"][0]["amount_totals"] == [{"currency": "CNY", "amount": "10.25"}]
    assert not any(row["account_number"] in {"--", "/"} for row in entities(client, cid)["entities"])
    stats = client.get(f"/api/v1/cases/{cid}/stats").json()
    assert stats["flows"] == 3 and stats["amount_totals"] == [{"currency": "CNY", "amount": "30.75"}]
    assert database.q_raw("select trade_no,payee_account,amount,currency from flows where case_id=? order by trade_no", [cid]) == raw


@pytest.mark.parametrize("parent,child", [("--", "SYN-CHILD"), ("SYN-PARENT", "--"), ("--", "--")])
def test_missing_parent_or_child_never_creates_an_empty_entity_or_relationship(client, make_case, parent, child):
    cid = make_case("Synthetic incomplete subaccount relation")
    view, receipt = accepted(client, cid, [("sub.csv", table("subaccount", [
        {"银行名称": "Synthetic bank", "开户账号": parent, "子账户账号": child}]))], scope_bank=None)
    registry = entities(client, cid)
    assert all(item["account_number"] and item["account_number"] != "--" for item in registry["entities"])
    assert all(item["parent_entity_id"] is None for item in registry["entities"])
    expected = [] if parent == child == "--" else [("subaccount", child)] if parent == "--" else [("account", parent)]
    assert sorted((item["kind"], item["account_number"]) for item in registry["entities"]) == expected
    assert view["resolution"]["entities"] == receipt["resolution"]["entities"] == len(expected)
    report = client.get(f"/api/v1/cases/{cid}/sources/{receipt['source_id']}/report").json()["items"]
    statement = json.loads(next(item["details"][evidence_resolution.DETAIL_FIELD] for item in report
                                if evidence_resolution.DETAIL_FIELD in item["details"]))
    assert statement["parent"] == parent and statement["account"] == child
    package = client.get(f"/api/cases/{cid}/export").content
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package)})
    assert restored.status_code == 200, restored.text
    after = entities(client, restored.json()["case_id"])["entities"]
    assert sorted((item["kind"], item["account_number"]) for item in after) == expected
    assert all(item["parent_entity_id"] is None for item in after)
