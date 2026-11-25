"""Read-only model verification through the case-bound balance action."""
import csv
from hashlib import sha256
import io
import json
from types import SimpleNamespace

import pytest

from app import table_import
from app.services import desktop_model_trial as models, import_verbatim_balance as verbatim, import_verbatim_money
from test_api_v1 import poll


@pytest.fixture
def review_source(client, database, make_case, monkeypatch, tmp_path, request):
    trial = tmp_path / "funds-trace-model-trial-test"
    trial.mkdir(mode=0o700)
    root = trial / "data"
    root.mkdir()
    monkeypatch.setattr(database, "DATA_DIR", root)
    monkeypatch.setattr(verbatim, "sys", SimpleNamespace(platform="darwin"))
    endpoints = models.parse_model_endpoints({
        model: f"http://127.0.0.1:{65001 + i}" for i, model in enumerate((*models._FIRST, models._RECHECK))
    })
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (endpoints,), raising=False)
    monkeypatch.setattr(client.app.state, "desktop_verbatim_balance_review", None, raising=False)
    stream = io.StringIO(newline="")
    balance_heading = getattr(request, "param", "当前余额")
    csv.writer(stream, delimiter="\t").writerows([
        ["本方账号", "子账户序号", "交易时间", "借贷标志", "交易金额", balance_heading, "对方账号",
         "币种", "交易流水号", "原交易键"],
        ["000010", "02", "2041-04-03 08:00:00", "借", "8.00", "8.00", "000015", "CNY", "VERB-1", ""],
        ["000010", "02", "2041-04-03 08:00:01", "贷", "2.10", "", "000016", "CNY", "VERB-2", ""],
        ["000010", "02", "2041-04-03 08:00:02", "贷", "0.50", "0", "000017", "CNY", "VERB-3"],
    ])
    raw = stream.getvalue().encode()
    case = make_case()
    base = f"/api/v1/cases/{case}"
    ready = client.post(f"{base}/imports/preview", files={"file": ("test.csv", raw)}).raise_for_status().json()
    job = f"{base}/imports/{ready['import_id']}"
    body = {"field": "balance", "revision": ready["layout_review"]["revision"], "member_path": "table.csv",
            "header_row": 1}

    def grant(mode="two_plus_one", purpose="balance"):
        client.app.state.desktop_verbatim_balance_review = verbatim.LocalBalanceReview(
            int(case), ready["import_id"], body["revision"], sha256(raw).hexdigest(), body["member_path"],
            (1, 3, 5, 6, 7, 8, 10), mode, lambda: models._LoopbackProvider(endpoints), purpose)

    return base, job, body, ready, raw, grant


def _answer(role="OWN", column="C6", row="R2", timing="UNPROVED"):
    return {"role": role, "column": column, "row": row, "timing": timing}


@pytest.mark.parametrize("mode", ["two_plus_one", "single_qwen"])
def test_bound_original_and_cited_reply_on_the_same_read_only_action(
        client, database, monkeypatch, review_source, mode):
    base, job, body, ready, _, grant = review_source
    action = job + "/layout-review/model-trial"
    requests = []

    def transport(_, tag, route, payload):
        requests.append((tag, route, payload))
        if route == "/v1/systemone":
            answer = _answer() if tag == "d1-2b" else _answer(column="C5")
            return {"answers": {k: {"choice": v} for k, v in answer.items()}, "usage": {"output_tokens": 0}}
        if route == "/apply-template":
            text = payload["messages"][0]["content"]
            assert payload["chat_template_kwargs"] == {"enable_thinking": False}
            return {"prompt": "<|im_start|>user\n" + text + "<|im_end|>\n" + models._NO_THINK}
        if route == "/tokenize":
            return {"tokens": [1]}
        assert route == "/completion"
        return {"content": json.dumps(_answer()), "truncated": False, "stop_type": "eos"}

    monkeypatch.setattr(models._LoopbackProvider, "_request", transport)
    baseline = client.post(action, json=body).raise_for_status().json()
    assert baseline["state"] == "rule" and (baseline["first_calls"], baseline["recheck_calls"]) == (0, 0)
    saved = database.q_raw("select request_json from import_sessions where import_id=?", [ready["import_id"]])
    assert not requests

    grant(mode)
    result = client.post(action, json=body).raise_for_status().json()
    assert result["state"] == "rule" and result["reason"] == "verbatim_balance_verified"
    assert result["prepared_preview"] is None
    assert result["source_balance_roles"] == baseline["source_balance_roles"]
    facts = result["packet"]["facts"]
    assert facts["local_original_balance_read_only"] and not facts["financial_admission"]
    assert facts["local_model_added_reads"] == 0
    assert facts["local_model_source_rows"] == [2, 3, 4]
    if mode == "two_plus_one":
        assert (result["first_calls"], result["recheck_calls"]) == (2, 1)
        assert [entry[:2] for entry in requests[:2]] == [(tag, "/v1/systemone") for tag in models._FIRST]
        assert requests[0][2] == requests[1][2]
        assert [entry["admitted_original"] for entry in facts["local_model_replies"]] == [True, False, True]
        raw = requests[0][2]["state"]
        assert json.dumps(_answer(column="C5"), ensure_ascii=False, separators=(",", ":")) in (
            requests[2][2]["messages"][0]["content"])
    else:
        assert (result["first_calls"], result["recheck_calls"]) == (1, 0)
        assert len(requests) == 3 and all(entry[0] == "qwen35-4b" for entry in requests)
        raw = requests[0][2]["messages"][0]["content"]
    transmitted, _ = json.JSONDecoder().raw_decode(raw.split("\n原据=", 1)[1])
    assert transmitted["范围"]["revision"] == body["revision"]
    assert transmitted["表头"]["C6｜当前余额"] == "C6"
    assert transmitted["原行"]["R2"]["C1"] == ["text", "000010"]
    assert transmitted["原行"]["R2"]["C3"][1] == "2041-04-03 08:00:00"
    assert transmitted["原行"]["R3"]["C5"][1] == "2.10"
    assert transmitted["原行"]["R2"]["C6"] == transmitted["原行"]["R2"]["C5"]
    assert transmitted["原行"]["R3"]["C6"] == ["text", ""]
    assert transmitted["原行"]["R3"]["C10"] == ["text", ""]
    assert transmitted["原行"]["R4"]["C6"][1] == "0" and transmitted["原行"]["R4"]["C10"] is None
    assert "000010" not in json.dumps(result)
    assert database.q_raw("select request_json from import_sessions where import_id=?", [ready["import_id"]]) == saved
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0


def test_wrong_claims_and_stale_scope_never_produce_a_verified_role(client, make_case, monkeypatch, review_source):
    base, job, body, ready, original, grant = review_source
    action = job + "/layout-review/model-trial"
    calls = []

    def transport(_, tag, route, payload):
        calls.append((tag, route))
        answer = _answer(timing="POST") if tag == "d1-2b" else _answer(column="C5")
        return {"answers": {k: {"choice": v} for k, v in answer.items()}, "usage": {"output_tokens": 0}}

    monkeypatch.setattr(models._LoopbackProvider, "_request", transport)
    grant()
    alt = make_case("Unrelated synthetic case")
    assert client.post(action.replace(base, f"/api/v1/cases/{alt}"), json=body).status_code == 409
    assert client.post(action, json={**body, "header_row": 2}).status_code == 409
    assert not calls
    result = client.post(action, json=body).raise_for_status().json()
    assert result["state"] == "pending" and "source_balance_roles" not in result
    assert calls == [(tag, "/v1/systemone") for tag in models._FIRST]
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0

    commit = {"mode": ready["mode"], "mapping": ready["mapping"], "ready_revision": body["revision"]}
    assert client.post(job + "/commit", json=commit).raise_for_status().json()["started"]
    case = int(base.rsplit("/", 1)[1])
    receipt = poll(client, case, ready["import_id"])["result"]
    src = f"{base}/sources/{ready['import_id']}"
    assert client.delete(src + "/operation", params={"expected_batch_id": receipt["batch_id"]}).status_code == 200
    assert client.post(action, json=body).status_code == 409
    assert len(calls) == 2
    assert client.get(src + "/file").content == original
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0


def test_money_claims_share_the_active_original_and_reject_a_same_value_role_swap(
        client, database, monkeypatch, review_source):
    base, job, body, ready, _, grant = review_source
    requests = []
    answer = {"amount": "C5", "balance": "C6", "currency": "C8", "amount_unit": "NOT_DECLARED",
              "balance_unit": "NOT_DECLARED", "row": "R2"}
    swapped = {**answer, "amount": "C6", "balance": "C5"}

    def transport(_, tag, route, payload):
        requests.append((tag, route, payload))
        if route == "/v1/systemone":
            selected = answer if tag == "d1-2b" else swapped
            return {"answers": {k: {"choice": v} for k, v in selected.items()}, "usage": {"output_tokens": 0}}
        if route == "/apply-template":
            prompt = payload["messages"][0]["content"]
            assert '"首判原答"' in prompt
            assert "admitted_original" not in prompt
            return {"prompt": "<|im_start|>user\n" + prompt + "<|im_end|>\n" + models._NO_THINK}
        if route == "/tokenize":
            return {"tokens": [1]}
        assert route == "/completion"
        return {"content": json.dumps(answer), "truncated": False, "stop_type": "eos"}

    monkeypatch.setattr(models._LoopbackProvider, "_request", transport)
    saved = database.q_raw("select request_json from import_sessions where import_id=?", [ready["import_id"]])
    grant(purpose="units")
    result = client.post(job + "/layout-review/model-trial", json=body).raise_for_status().json()
    assert result["reason"] == "verbatim_money_verified" and result["prepared_preview"] is None
    assert result["recheck_calls"] == 1
    assert requests[0][2] == requests[1][2]
    assert "原问题=" in requests[0][2]["state"]
    assert [entry["admitted_original"] for entry in result["packet"]["facts"]["local_model_replies"]] == [True, False, True]
    facts = result["packet"]["facts"]
    assert facts["local_original_money_read_only"] and not facts["financial_admission"]
    assert facts["local_model_money_finding"]["amount"]["column"] == "C5"
    assert database.q_raw("select request_json from import_sessions where import_id=?", [ready["import_id"]]) == saved
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0


def test_money_proof_separates_declared_scale_currency_and_equal_conversion_value():
    header = [{"value": value} for value in (
        "交易金额", "账户余额（分）", "币种", "交易金额折人民币", "交易金额折美元",
    )]
    rows = {2: [{"value": value} for value in ("10.00", "120", "07", "10.00", "1.41")]}
    profile = table_import.CSV_TAIL_COMPLETE_ROWS_PROFILE
    proof = import_verbatim_money.proof(header, rows, profile, (1, 2, 3, 4, 5), "amount_roles")
    assert proof["amount"] == {"column": "C1", "source_rows": [2], "unit": "NOT_DECLARED"}
    assert proof["balance"] == {"column": "C2", "source_rows": [2], "unit": "CENT"}
    assert proof["currency"] == {"column": "C3", "source_rows": [2]}
    assert proof["converted_cny"] == {"column": "C4", "source_rows": [2], "conversion_currency": "CNY"}
    assert proof["converted_usd"] == {"column": "C5", "source_rows": [2], "conversion_currency": "USD"}
    omitted = import_verbatim_money.proof(header, rows, profile, (1, 2, 4, 5), "amount_roles")
    assert "currency" not in omitted


def test_money_proof_requires_absence_of_relevant_unresolved_headers_and_reads_scoped_row_units():
    headings = ["交易金额", "交易金额单位", "折合人民币金额", "折合美元金额"]
    rows = {2: [{"value": value} for value in ("125", "分", "125", "17")]}
    profile = table_import.CSV_TAIL_COMPLETE_ROWS_PROFILE

    def proof():
        return import_verbatim_money.proof([{"value": label} for label in headings], rows, profile,
                                          (1, 2, 3, 4), "amount_roles")

    scoped = proof()
    assert "converted_cny" not in scoped and "converted_usd" not in scoped
    assert scoped["amount"] == {"column": "C1", "source_rows": [2], "unit": "CENT", "unit_columns": [2]}
    headings[2:] = ["折合人民币（元）", "折合美元（元）"]
    assert "converted_cny" not in proof() and "converted_usd" not in proof()
    headings[1] = "附注"
    assert proof()["amount"] == {"column": "C1", "source_rows": [2], "unit": "NOT_DECLARED"}
    headings[2:] = ["交易金额折美元", "折合美元金额"]
    assert "converted_usd" not in proof()
    headings[3] = "附注"
    assert proof()["converted_usd"]["column"] == "C3"


@pytest.mark.parametrize("review_source", ["说明"], indirect=True)
def test_money_trial_keeps_the_raw_reply_for_a_proved_absent_balance(client, monkeypatch, review_source):
    base, job, body, _, _, grant = review_source
    answer = {"amount": "C5", "balance": "NONE", "currency": "C8", "amount_unit": "NOT_DECLARED",
              "balance_unit": "NOT_DECLARED", "row": "R2"}
    literal = {**answer, "balance_unit": "NONE"}
    requests = []

    def transport(_, tag, route, payload):
        requests.append((tag, route, payload))
        assert route == "/v1/systemone"
        draft = answer if tag == "d1-2b" else literal
        return {"answers": {key: {"choice": value} for key, value in draft.items()}, "usage": {"output_tokens": 0}}

    monkeypatch.setattr(models._LoopbackProvider, "_request", transport)
    grant(purpose="units")
    result = client.post(job + "/layout-review/model-trial", json=body).raise_for_status().json()
    facts = result["packet"]["facts"]
    assert result["reason"] == "verbatim_money_verified" and result["recheck_calls"] == 0
    assert facts["local_model_money_finding"]["balance"] == {"column": "NONE", "unit": "NONE", "source_rows": []}
    assert facts["local_model_replies"] == [
        {"model": "d1-2b", "proposal": answer, "admitted_original": True,
         "program_output_interpretations": {"balance_unit": {"proposal": "NOT_DECLARED", "effective": "NONE",
                                                              "basis": "proved_absent_balance_column"}},
         "literal_verification_reason": "verbatim_money_unproved"},
        {"model": "d3-nano", "proposal": literal, "admitted_original": True},
    ]
    assert len(requests) == 2 and requests[0][2] == requests[1][2]
    assert not facts["financial_admission"] and client.get(base + "/flows").raise_for_status().json()["total"] == 0


@pytest.mark.parametrize(("headings", "cells", "columns", "column", "unit"), [
    (["交易金额", "当前余额"], ["8.00", ""], (1, 2), "NONE", "NOT_DECLARED"),
    (["交易金额", "当前余额"], ["8.00"], (1, 2), "NONE", "NOT_DECLARED"),
    (["交易金额", "当前余额"], ["8.00", "8.00"], (1,), "NONE", "NOT_DECLARED"),
    (["交易金额", "当前余额", "交易余额"], ["8.00", "8.00", "8.00"], (1, 2, 3), "NONE", "NOT_DECLARED"),
    (["交易金额", "本期结余"], ["8.00", "8.00"], (1, 2), "NONE", "NOT_DECLARED"),
    (["交易金额", "说明"], ["8.00", "8.00"], (1, 2), "C2", "NOT_DECLARED"),
    (["交易金额", "说明"], ["8.00", "8.00"], (1, 2), "NONE", "YUAN"),
])
def test_money_unit_derivation_rejects_an_unresolved_balance_or_an_asserted_unit(headings, cells, columns, column, unit):
    header = [{"value": heading} for heading in headings]
    rows = {2: [{"value": cell} for cell in cells]}
    proof = import_verbatim_money.proof(header, rows, table_import.CSV_TAIL_COMPLETE_ROWS_PROFILE, columns, "units")
    answer = {"amount": "C1", "balance": column, "currency": "NONE", "amount_unit": "NOT_DECLARED",
              "balance_unit": unit, "row": "R2"}
    assert not import_verbatim_money.output_interpretations(answer, proof)
    assert import_verbatim_money.verified(answer, proof, answer) is None


@pytest.mark.parametrize(("mode", "first"), [
    ("two_plus_one", ("d1-2b", "/v1/systemone")),
    ("single_qwen", ("qwen35-4b", "/apply-template")),
])
def test_unapproved_host_and_revocation_during_request_stop_before_further_reads(
        client, monkeypatch, review_source, mode, first):
    _, job, body, _, _, grant = review_source
    action = job + "/layout-review/model-trial"
    calls = []

    def transport(_, tag, route, payload):
        calls.append((tag, route))
        client.app.state.desktop_verbatim_balance_review = None
        if route == "/apply-template":
            return {"prompt": "<|im_start|>user\n" + payload["messages"][0]["content"]
                    + "<|im_end|>\n" + models._NO_THINK}
        return {"answers": {k: {"choice": v} for k, v in _answer().items()}, "usage": {"output_tokens": 0}}

    monkeypatch.setattr(models._LoopbackProvider, "_request", transport)
    grant(mode)
    monkeypatch.setattr(verbatim.sys, "frozen", True, raising=False)
    denied = client.post(action, json=body)
    assert denied.status_code == 409 and denied.json()["error"]["code"] == "original_balance_host_unavailable"
    assert not calls
    monkeypatch.setattr(verbatim.sys, "frozen", False)
    revoked = client.post(action, json=body)
    assert revoked.status_code == 409 and revoked.json()["error"]["code"] == "original_balance_review_revoked"
    assert calls == [first]
