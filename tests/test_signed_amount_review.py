import pytest

from app import feedback_import
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_feedback31_reference import payload
from test_folder_evidence import transaction


@pytest.mark.parametrize("short_blank", [False, True])
def test_original_sign_counts_and_candidates_are_source_bound_and_read_only(client, make_case, monkeypatch, short_blank):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic signed amount reading")

    def row(number, amount, reference, **columns):
        fields = {"交易时间": f"2037-03-02 10:00:{number:02}", "交易金额": amount,
                  "交易流水号": reference, "交易币种": "CNY", "收付标志": "进"}
        return transaction(**{**fields, **columns})

    data = payload([
        row(1, "-1.23", "SYN-LINK", 交易账号=""),
        row(2, "1.23", "SYN-LINK"),
        row(3, "5.00", "SYN-LINK"),
        row(4, "-0.00", "SYN-ZERO"),
        row(5, "", "SYN-BLANK"),
        row(6, "金额无效", "SYN-UNREADABLE"),
        row(7, "-2.345", "SYN-PRECISION"),
        row(8, "1.23", "SYN-LINK", 交易币种="USD"),
    ], feedback_import.COLUMNS)
    if short_blank:
        lines = data.splitlines(keepends=True)
        data = b"".join([*lines[:5], b",,\n", *lines[5:]])
    ready = preview(client, case, data, "synthetic-signed.csv")
    done = complete(client, case, ready)
    assert done["stage"] == "done"
    source_id = ready["import_id"]
    base = f"/api/v1/cases/{case}"
    scope = {"member_path": "table.csv"}
    url = f"{base}/imports/{source_id}/review/signed-amounts"
    initial = client.get(base + "/flows").raise_for_status().json()
    evidence = client.get(url, params=scope).raise_for_status().json()
    assert tuple(evidence[name + "_count"] for name in ("negative", "positive", "zero", "blank",
                 "unreadable", "finer_than_cent", "candidate_group")) == (2, 3, 1, 1 + int(short_blank), 1, 1, 1)
    assert evidence["negative_directions"] == [{"marker": "进", "count": 2}]
    assert evidence["candidate_groups"] == [{
        "negative_rows": [{"source_row": 2, "raw_amount": "-1.23", "direction_marker": "进"}],
        "positive_rows": [{"source_row": 3, "raw_amount": "1.23", "direction_marker": "进"}],
        "negative_count": 1, "positive_count": 1, "other_same_reference_count": 2,
        "own_account_missing": True,
    }]
    assert client.get(url, params={"member_path": "worksheets/0001"}).raise_for_status().json() is None
    assert client.get(url.replace(base, f"/api/v1/cases/{make_case()}"), params=scope).raise_for_status().json() is None
    assert client.get(base + "/flows").raise_for_status().json() == initial
    client.delete(f"{base}/sources/{source_id}/operation",
                  params={"expected_batch_id": done["result"]["batch_id"]}).raise_for_status()
    assert client.get(url, params=scope).raise_for_status().json() is None
    monkeypatch.setattr(client.app.state, "desktop_model_trial", None)
    assert client.get(url, params=scope).status_code == 404
