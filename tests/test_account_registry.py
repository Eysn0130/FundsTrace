"""Public read-only account registry contracts with isolated synthetic data."""
import io

from PIL import Image
import pytest

from test_cases import insert_flow
from funds_assertions import unresolved_funds
from decimal import Decimal


def listing(client, case_id, **params):
    response = client.get("/api/account-registry", params={"case_id": case_id, **params})
    assert response.status_code == 200, response.text
    return response.json()


def profile(client, case_id, account):
    response = client.get("/api/account-registry/profile", params={"case_id": case_id, "account": account})
    assert response.status_code == 200, response.text
    return response.json()


def identity(database, case_id, account, id_no="110101199001010011"):
    database.x("insert into account_ids(case_id,account,id_no) values (?,?,?)", [case_id, account, id_no])


def test_untracked_distinct_accounts_keep_strings_and_case_bound_metadata(client, database, make_case, flow_row):
    cid, other = make_case("Synthetic registry"), make_case("Other synthetic registry")
    for account in ("0002", "0001", "0001"):
        insert_flow(database, cid, flow_row, payer_account=account, payer_name="同名合成主体",
                    payee_account="", payee_name="")
    insert_flow(database, other, flow_row, payer_account="OTHER-ONLY", payee_account="0001")
    identity(database, cid, "0001")
    identity(database, other, "0002", "110101199001010029")
    database.x("insert into main_accounts(case_id,account,name,source) values (?,'0001','同名合成主体','auto')", [cid])
    database.x("insert into main_accounts(case_id,account,name,source) values (?,'0002','Other mark','manual')", [other])
    first = listing(client, cid, page_size=1)
    assert first["case_id"] == str(cid) and first["source"] == "flows"
    assert (first["total"], first["page"], first["page_size"]) == (2, 1, 1)
    row = first["items"][0]
    assert row["account"] == "0001" and row["name"] == "同名合成主体"
    assert row["id_no"] == "110101199001010011" and row["identity_updated_at"]
    assert row["has_flows"] is True and row["is_main"] is True and row["main_source"] == "auto"
    assert row["main_marked_at"] and row["photo_at"] is None
    second = listing(client, cid, page_size=1, page=2)["items"][0]
    assert second["account"] == "0002" and second["name"] == row["name"]
    assert second["id_no"] is None and second["main_source"] is None and second["is_main"] is False
    assert listing(client, cid, page_size=1, page=3)["items"] == []
    assert database.q("select count(*) n from tracked_flows where case_id=?", [cid])[0]["n"] == 0


def test_identity_only_accounts_are_separate_and_have_zero_flow_profile(client, database, make_case, flow_row):
    cid = make_case("Synthetic identity registry")
    insert_flow(database, cid, flow_row, payer_account="FLOW-ONLY", payee_account="FLOW-AND-ID")
    identity(database, cid, "000-ID-ONLY")
    identity(database, cid, "FLOW-AND-ID", "110101199001010029")
    rows = listing(client, cid, source="identity")["items"]
    assert [row["account"] for row in rows] == ["000-ID-ONLY", "FLOW-AND-ID"]
    assert rows[0]["name"] == "" and rows[0]["has_flows"] is False
    assert {row["account"] for row in listing(client, cid)["items"]} == {"FLOW-ONLY", "FLOW-AND-ID"}
    value = profile(client, cid, "000-ID-ONLY")
    assert value["account"] == "000-ID-ONLY" and value["case_id"] == str(cid)
    assert value["id_no"] == "110101199001010011" and value["photo"] is None
    assert value["cnt"] == value["in_cnt"] == value["out_cnt"] == 0
    assert value["in_totals"] == value["out_totals"] == value["top_counterparties"] == []
    assert value["in_amt"] == value["out_amt"] == "0.00"
    assert value["tmin"] is None and value["tmax"] is None


@pytest.mark.parametrize("query", ["%", "_", "\\", "' OR 1=1 --"])
def test_query_is_literal_text_not_sql_or_like_pattern(client, database, make_case, flow_row, query):
    cid = make_case()
    account = f"000-{query}-SYN"
    insert_flow(database, cid, flow_row, payer_account=account, payee_account="PLAIN")
    assert [row["account"] for row in listing(client, cid, q=query)["items"]] == [account]


def test_search_matches_all_source_aliases_main_name_and_identity_without_merging(client, database, make_case, flow_row):
    cid = make_case()
    insert_flow(database, cid, flow_row, payer_account="000-ALIAS", payer_name="First synthetic alias", payee_account="000-OTHER")
    insert_flow(database, cid, flow_row, payer_account="000-ALIAS", payer_name="Second synthetic alias", payee_account="000-OTHER")
    identity(database, cid, "000-ALIAS")
    database.x("insert into main_accounts(case_id,account,name,source) values (?,'000-ALIAS','Retained manual alias','manual')", [cid])
    for keyword in ("first SYNTHETIC", "Second synthetic", "Retained manual", "199001010011"):
        value = listing(client, cid, q=keyword)
        assert value["total"] == 1 and value["items"][0]["account"] == "000-ALIAS"
    value = profile(client, cid, "000-ALIAS")
    assert value["is_main"] is True and value["main_source"] == "manual"
    assert value["name"] == "Retained manual alias"


def test_profile_preserves_large_decimal_totals_and_separates_currencies(client, database, make_case, flow_row):
    cid, other = make_case("Synthetic exact registry"), make_case("Other exact registry")
    account = "000000000000000000012345"
    for number, amount, currency in ((1, "9999999999999999.99", "CNY"), (2, "9999999999999999.99", "CNY"),
                                      (3, "0.01", "USD"), (4, "0.01", None), (5, "0.02", "")):
        insert_flow(database, cid, flow_row, payer_account=account, payee_account="000-COUNTER",
                    trade_no=f"SYN-{number}", trade_time=f"2026-01-0{number} 10:00:00", amount=amount, currency=currency)
    insert_flow(database, cid, flow_row, payer_account="000-COUNTER", payee_account=account,
                trade_no="SYN-IN", trade_time="2026-01-06 10:00:00", amount="4.56", currency="CNY")
    insert_flow(database, other, flow_row, payer_account=account, amount="100.00")
    value = profile(client, cid, account)
    expected = [{"currency": "CNY", "amount": None}, {"currency": "USD", "amount": None},
                {"currency": None, "amount": None}]
    assert value["account"] == account
    assert (value["cnt"], value["out_cnt"], value["in_cnt"]) == (None, None, None)
    assert value["funds"] == unresolved_funds(6, ("CNY", None), ("USD", None), (None, None))
    assert value["out_funds"] == unresolved_funds(5, ("CNY", None), ("USD", None), (None, None))
    observed = client.get("/api/flows", params={"case_id": cid, "granularity": "observations"}).json()["rows"]
    assert sum(Decimal(row["amount"]) for row in observed if row["currency"] == "CNY" and row["payer_account"] == account) == Decimal("19999999999999999.98")
    assert sorted((row["trade_no"], row["amount"], row["currency"]) for row in observed) == [
        ("SYN-1", "9999999999999999.99", "CNY"), ("SYN-2", "9999999999999999.99", "CNY"),
        ("SYN-3", "0.01", "USD"), ("SYN-4", "0.01", None), ("SYN-5", "0.02", ""),
        ("SYN-IN", "4.56", "CNY")]
    assert value["out_totals"] == expected and value["out_amt"] is None
    assert value["in_totals"] == [{"currency": "CNY", "amount": None}] and value["in_amt"] is None
    assert value["in_funds"] == unresolved_funds(1, ("CNY", "4.56"))
    assert value["tmin"] == "2026-01-01 10:00:00" and value["tmax"] == "2026-01-06 10:00:00"
    counter = value["top_counterparties"][0]
    assert counter["acct"] == "000-COUNTER" and counter["out_totals"] == expected
    assert (counter["out_cnt"], counter["in_cnt"], counter["in_amt"]) == (None, None, None)
    assert counter["out_funds"] == value["out_funds"] and counter["in_funds"] == value["in_funds"]
    assert counter["out_amt"] is None


def test_metadata_only_known_accounts_and_photo_crop_are_case_bound_without_paths(client, database, make_case):
    cid, other = make_case("Synthetic photo registry"), make_case("Other photo registry")
    identity(database, cid, "PHOTO")
    image = io.BytesIO()
    Image.new("RGB", (20, 20), "navy").save(image, format="PNG")
    response = client.post("/api/account-photos/set", data={"case_id": cid, "account": "PHOTO", "crop_cx": "0.25", "crop_cy": "0.75", "crop_r": "0.3"},
                           files={"file": ("synthetic.png", image.getvalue(), "image/png")})
    assert response.status_code == 200, response.text
    value = profile(client, cid, "PHOTO")
    assert value["photo_at"] and value["photo"] == {"updated_at": value["photo_at"], "crop_cx": 0.25, "crop_cy": 0.75, "crop_r": 0.3}
    assert "fname" not in str(value) and str(database.DATA_DIR) not in str(value)
    assert listing(client, cid, source="identity")["items"][0]["photo_at"] == value["photo_at"]
    assert client.get("/api/account-registry/profile", params={"case_id": other, "account": "PHOTO"}).status_code == 404
    database.x("insert into main_accounts(case_id,account,name,source) values (?,'MARK-ONLY','Synthetic mark','manual')", [cid])
    assert profile(client, cid, "MARK-ONLY")["has_flows"] is False
    database.x("insert into focus_nodes(case_id,account,scale) values (?,'FOCUS-ONLY',2)", [cid])
    assert profile(client, cid, "FOCUS-ONLY")["cnt"] == 0
    assert client.post("/api/account-photos/clear", json={"case_id": cid, "account": "PHOTO"}).status_code == 200
    assert profile(client, cid, "PHOTO")["photo"] is None


@pytest.mark.parametrize("crop,expected", [
    ((None, None, None), (0.5, 0.5, 0.5)),
    ((0, 0, 0), (0, 0, 0)),
    ((0.2, 0.7, 0.35), (0.2, 0.7, 0.35)),
])
def test_legacy_photo_crop_metadata_defaults_only_null_coordinates(client, database, make_case, crop, expected):
    cid = make_case("Synthetic legacy photo crop metadata")
    database.x("insert into account_photos(case_id,account,fname,crop_cx,crop_cy,crop_r) values (?,'LEGACY-PHOTO','synthetic.png',?,?,?)", [cid, *crop])
    value = profile(client, cid, "LEGACY-PHOTO")
    assert value["photo"] == {"updated_at": value["photo_at"], "crop_cx": expected[0], "crop_cy": expected[1], "crop_r": expected[2]}
    assert all(isinstance(value["photo"][field], (int, float)) for field in ("crop_cx", "crop_cy", "crop_r"))


@pytest.mark.parametrize("params", [{"source": "people"}, {"page": 0}, {"page": -1}, {"page_size": 0},
                                    {"page_size": 101}, {"case_id": 0}])
def test_invalid_registry_inputs_are_rejected(client, make_case, params):
    assert client.get("/api/account-registry", params={"case_id": make_case(), **params}).status_code == 422


def test_missing_case_or_unknown_account_is_not_an_empty_success(client, make_case):
    cid = make_case()
    assert client.get("/api/account-registry", params={"case_id": 999999}).status_code == 404
    assert client.get("/api/account-registry/profile", params={"case_id": 999999, "account": "UNKNOWN"}).status_code == 404
    assert client.get("/api/account-registry/profile", params={"case_id": cid, "account": "UNKNOWN"}).status_code == 404


def test_clearing_identity_only_account_removes_its_known_profile(client, database, make_case):
    cid, other = make_case("Synthetic removable identity"), make_case("Other removable identity")
    identity(database, cid, "ID-ONLY")
    identity(database, other, "ID-ONLY", "110101199001010029")
    assert profile(client, cid, "ID-ONLY")["has_flows"] is False
    cleared = client.post("/api/account-ids/set", json={"case_id": cid, "account": "ID-ONLY", "id_no": ""})
    assert cleared.status_code == 200, cleared.text
    assert listing(client, cid, source="identity")["items"] == []
    assert client.get("/api/account-registry/profile", params={"case_id": cid, "account": "ID-ONLY"}).status_code == 404
    assert profile(client, other, "ID-ONLY")["id_no"] == "110101199001010029"


def test_profile_counts_current_flow_facts_in_both_directions_without_tracking(client, database, make_case, flow_row):
    cid = make_case("Synthetic flow profile directions")
    for payer, payee, amount in (("SELF", "SELF", "1.01"), ("SELF", "OTHER", "2.02"),
                                 ("OTHER", "SELF", "3.03")):
        insert_flow(database, cid, flow_row, payer_account=payer, payee_account=payee, amount=amount)
    value = profile(client, cid, "SELF")
    assert (value["cnt"], value["out_cnt"], value["in_cnt"]) == (None, None, None)
    assert value["funds"] == unresolved_funds(3, ("CNY", "6.06"))
    assert value["out_funds"] == unresolved_funds(2, ("CNY", "3.03"))
    assert value["in_funds"] == unresolved_funds(2, ("CNY", "4.04"))
    assert value["out_totals"] == [{"currency": "CNY", "amount": None}]
    assert value["in_totals"] == [{"currency": "CNY", "amount": None}]
    assert client.get("/api/flows", params={"case_id": cid, "account": "SELF", "account_exact": 1}).json()["total"] == value["funds"]["observation_count"]
    assert database.q("select count(*) n from tracked_flows where case_id=?", [cid])[0]["n"] == 0


@pytest.mark.parametrize("cx,cy", [(0, 0.5), (0.5, 0), (0, 0)])
def test_zero_crop_centers_preserve_the_selected_image_region(client, make_case, cx, cy):
    cid = make_case()
    image = Image.new("RGB", (100, 100))
    # The four quadrants have independent red/green channels. A zero center
    # samples the near quadrant; 0.5 samples the far quadrant at this output point.
    for y in range(100):
        for x in range(100):
            image.putpixel((x, y), (32 if x < 50 else 224, 32 if y < 50 else 224, 96))
    encoded = io.BytesIO()
    image.save(encoded, format="PNG")
    pixels = {}
    for account, crop_x, crop_y in (("ZERO-CROP", cx, cy), ("CENTER-CROP", 0.5, 0.5)):
        response = client.post("/api/account-photos/set", data={"case_id": cid, "account": account,
                               "crop_cx": str(crop_x), "crop_cy": str(crop_y), "crop_r": "0.5"},
                               files={"file": ("synthetic-quadrants.png", encoded.getvalue(), "image/png")})
        assert response.status_code == 200, response.text
        saved = profile(client, cid, account)["photo"]
        assert (saved["crop_cx"], saved["crop_cy"]) == (crop_x, crop_y)
        avatar = client.get("/api/account-photos/img", params={"case_id": cid, "account": account})
        assert avatar.status_code == 200, avatar.text
        pixels[account] = Image.open(io.BytesIO(avatar.content)).getpixel((108, 108))
    assert pixels["CENTER-CROP"] == (224, 224, 96, 255)
    assert pixels["ZERO-CROP"] == (32 if cx == 0 else 224, 32 if cy == 0 else 224, 96, 255)
    assert pixels["ZERO-CROP"] != pixels["CENTER-CROP"]


@pytest.mark.parametrize("extension,image_format,media", [
    ("jpg", "JPEG", "image/jpeg"), ("jpeg", "JPEG", "image/jpeg"),
    ("png", "PNG", "image/png"), ("webp", "WEBP", "image/webp"), ("bmp", "BMP", "image/bmp"),
])
def test_original_photo_uses_its_supported_format_media_type(client, make_case, extension, image_format, media):
    cid, other = make_case("Synthetic original media"), make_case("Other original media")
    encoded = io.BytesIO()
    Image.new("RGB", (16, 16), "navy").save(encoded, format=image_format)
    original = encoded.getvalue()
    response = client.post("/api/account-photos/set", data={"case_id": cid, "account": "PHOTO-MEDIA"},
                           files={"file": (f"synthetic.{extension}", original, media)})
    assert response.status_code == 200, response.text
    downloaded = client.get("/api/account-photos/orig", params={"case_id": cid, "account": "PHOTO-MEDIA"})
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.headers["content-type"] == media
    assert downloaded.content == original
    assert client.get("/api/account-photos/orig", params={"case_id": other, "account": "PHOTO-MEDIA"}).status_code == 404


EXACT_ACCOUNTS = [("ACCT.0", "ACCT"), ("0000123", "123"),
                  ("000123456789012345678901234567890", "123456789012345678901234567890")]


def exact_account_cases(database, make_case, flow_row, account, neighbor):
    cid, other = make_case("Synthetic exact account writes"), make_case("Other exact account writes")
    for owner in (cid, other):
        insert_flow(database, owner, flow_row, payer_account=account, payee_account=neighbor)
    return cid, other


@pytest.mark.parametrize("account,neighbor", EXACT_ACCOUNTS)
def test_single_identity_write_and_clear_preserve_exact_account_strings(client, database, make_case, flow_row, account, neighbor):
    cid, other = exact_account_cases(database, make_case, flow_row, account, neighbor)
    identity(database, cid, neighbor, "110101199001010029")
    identity(database, other, account, "110101199001010037")
    response = client.post("/api/account-ids/set", json={"case_id": cid, "account": f" {account} ", "id_no": "110101199001010011"})
    assert response.status_code == 200, response.text
    assert response.json()["account"] == account
    assert profile(client, cid, account)["id_no"] == "110101199001010011"
    assert profile(client, cid, neighbor)["id_no"] == "110101199001010029"
    assert profile(client, other, account)["id_no"] == "110101199001010037"
    cleared = client.post("/api/account-ids/set", json={"case_id": cid, "account": account, "id_no": ""})
    assert cleared.status_code == 200 and cleared.json()["account"] == account
    assert profile(client, cid, account)["id_no"] is None
    assert profile(client, cid, neighbor)["id_no"] == "110101199001010029"
    assert profile(client, other, account)["id_no"] == "110101199001010037"


@pytest.mark.parametrize("account,neighbor", EXACT_ACCOUNTS)
def test_single_photo_write_and_clear_preserve_exact_account_strings(client, database, make_case, flow_row, account, neighbor):
    cid, other = exact_account_cases(database, make_case, flow_row, account, neighbor)
    photos = []
    for color in ("navy", "orange"):
        image = io.BytesIO()
        Image.new("RGB", (16, 16), color).save(image, format="PNG")
        photos.append(image.getvalue())
    # Exact pre-existing keys in both cases must survive edits to another account.
    for owner, key in ((cid, neighbor), (other, account)):
        directory = database.DATA_DIR / "photos" / f"c{owner}"
        directory.mkdir(parents=True)
        (directory / "synthetic-original.png").write_bytes(photos[0])
        database.x("insert into account_photos(case_id,account,fname,crop_cx,crop_cy,crop_r) values (?,?,'synthetic-original.png',0.5,0.5,0.5)", [owner, key])
    response = client.post("/api/account-photos/set", data={"case_id": cid, "account": f" {account} "},
                           files={"file": ("synthetic-exact.png", photos[1], "image/png")})
    assert response.status_code == 200, response.text
    assert response.json()["account"] == account
    for owner, key, expected in ((cid, account, photos[1]), (cid, neighbor, photos[0]), (other, account, photos[0])):
        original = client.get("/api/account-photos/orig", params={"case_id": owner, "account": key})
        assert original.status_code == 200 and original.content == expected
        assert profile(client, owner, key)["photo"] is not None
    cleared = client.post("/api/account-photos/clear", json={"case_id": cid, "account": account})
    assert cleared.status_code == 200 and cleared.json() == {"ok": True, "cleared": True}
    assert profile(client, cid, account)["photo"] is None
    assert profile(client, cid, neighbor)["photo"] is not None
    assert profile(client, other, account)["photo"] is not None


def test_photo_clear_does_not_target_the_account_without_literal_dot_zero(client, database, make_case, flow_row):
    cid, other = exact_account_cases(database, make_case, flow_row, "ACCT.0", "ACCT")
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "navy").save(image, format="PNG")
    for index, (owner, account) in enumerate(((cid, "ACCT.0"), (cid, "ACCT"), (other, "ACCT.0"))):
        directory = database.DATA_DIR / "photos" / f"c{owner}"
        directory.mkdir(parents=True, exist_ok=True)
        filename = f"synthetic-{index}.png"
        (directory / filename).write_bytes(image.getvalue())
        database.x("insert into account_photos(case_id,account,fname,crop_cx,crop_cy,crop_r) values (?,?,?,0.5,0.5,0.5)", [owner, account, filename])
    cleared = client.post("/api/account-photos/clear", json={"case_id": cid, "account": " ACCT.0 "})
    assert cleared.status_code == 200
    assert profile(client, cid, "ACCT.0")["photo"] is None
    assert profile(client, cid, "ACCT")["photo"] is not None
    assert profile(client, other, "ACCT.0")["photo"] is not None


@pytest.mark.parametrize("account,neighbor", EXACT_ACCOUNTS)
def test_single_main_and_focus_changes_preserve_exact_account_strings(client, database, make_case, flow_row, account, neighbor):
    cid, other = exact_account_cases(database, make_case, flow_row, account, neighbor)
    for owner, key in ((cid, neighbor), (other, account)):
        database.x("insert into main_accounts(case_id,account,name,source) values (?,?,'Synthetic existing','manual')", [owner, key])
        database.x("insert into focus_nodes(case_id,account,scale) values (?,?,3)", [owner, key])
    marked = client.post("/api/main-accounts", json={"case_id": cid, "account": f" {account} "})
    assert marked.status_code == 200 and marked.json()["account"] == account
    focused = client.post("/api/focus-nodes/set", json={"case_id": cid, "account": f" {account} ", "on": True, "scale": 2})
    assert focused.status_code == 200 and focused.json()["account"] == account
    assert profile(client, cid, account)["main_source"] == "manual"
    assert {row["account"]: row["scale"] for row in client.get("/api/graph", params={"case_id": cid}).json()["focus_nodes"]} == {account: 2, neighbor: 3}
    assert client.request("DELETE", "/api/main-accounts", json={"case_id": cid, "account": f" {account} "}).status_code == 200
    assert client.post("/api/focus-nodes/set", json={"case_id": cid, "account": account, "on": False}).status_code == 200
    assert profile(client, cid, account)["is_main"] is False
    assert profile(client, cid, neighbor)["is_main"] is True
    assert profile(client, other, account)["is_main"] is True
    for owner, key in ((cid, neighbor), (other, account)):
        assert client.get("/api/graph", params={"case_id": owner}).json()["focus_nodes"] == [{"account": key, "scale": 3}]


@pytest.mark.parametrize("account,neighbor", [("ACCT", "ACCT.0"), ("AC%T", "AC-ANY-T"),
                                             ("AC_T", "ACXT"), ("MiXeD", "mixed")])
def test_account_flow_navigation_is_exact_while_default_search_remains_fuzzy(client, database, make_case, flow_row, account, neighbor):
    cid, other = make_case("Synthetic exact flow navigation"), make_case("Other exact flow navigation")
    sent = insert_flow(database, cid, flow_row, payer_account=account, payee_account="TAIL",
                       trade_time="2026-01-01 10:00:00")
    received = insert_flow(database, cid, flow_row, payer_account="TAIL", payee_account=account,
                           trade_time="2026-01-01 12:00:00")
    insert_flow(database, cid, flow_row, payer_account=neighbor, payee_account="TAIL",
                trade_time="2026-01-01 11:00:00")
    other_id = insert_flow(database, other, flow_row, payer_account=account, payee_account="TAIL")
    params = {"case_id": cid, "account": account}
    fuzzy = client.get("/api/flows", params=params).json()
    assert fuzzy["total"] == 3
    assert client.get("/api/flows", params={**params, "account_exact": False}).json() == fuzzy
    exact = {**params, "account": f" {account} ", "account_exact": 1, "size": 1}
    for page, expected in ((1, [received]), (2, [sent]), (3, [])):
        value = client.get("/api/flows", params={**exact, "page": page}).json()
        assert (value["total"], value["page"], value["size"]) == (2, page, 1)
        assert [row["id"] for row in value["rows"]] == expected
    assert [row["id"] for row in client.get("/api/flows", params={**exact, "case_id": other}).json()["rows"]] == [other_id]


def test_exact_account_mode_composes_with_every_existing_flow_filter(client, database, make_case, flow_row):
    cid = make_case("Synthetic combined exact flow filters")
    canvas = client.post("/api/canvases/save", json={"case_id": cid, "name": "Synthetic filter canvas", "snapshot": {}}).json()["canvas_id"]
    base = {"payer_account": "ACCT", "payer_name": "Target Holder", "payee_account": "COUNTER-123",
            "payee_name": "Receiver", "amount": "10.10", "trade_time": "2026-01-02 12:00:00",
            "summary": "needle", "trade_no": "SYN-COMBINED"}
    kept = insert_flow(database, cid, flow_row, **base)
    included = [kept]
    tracked = [kept]
    distractors = [
        {"payer_account": "ACCT.0"}, {"payer_name": "Different Holder"},
        {"payee_account": "OTHER-COUNTER"}, {"summary": "plain"},
        {"amount": "10.09"}, {"amount": "10.11"},
        {"trade_time": "2026-01-01 12:00:00"}, {"trade_time": "2026-01-03 12:00:00"},
    ]
    for changed in distractors:
        flow_id = insert_flow(database, cid, flow_row, **(base | changed))
        included.append(flow_id)
        tracked.append(flow_id)
    untracked = insert_flow(database, cid, flow_row, **base)
    included.append(untracked)
    excluded_id = insert_flow(database, cid, flow_row, **base)
    tracked.append(excluded_id)
    assert client.post("/api/track", json={"case_id": cid, "canvas_id": canvas, "flow_ids": tracked}).status_code == 200
    value = client.get("/api/flows", params={
        "case_id": cid, "account": "ACCT", "account_exact": 1, "name": "Target Holder",
        "counter": "COUNTER-123", "kw": "needle", "min_amount": "10.10", "max_amount": "10.10",
        "tfrom": "2026-01-02", "tto": "2026-01-02", "tracked": 1, "canvas_id": canvas,
        "ids": ",".join(map(str, included)),
    }).json()
    assert value["total"] == 1 and [row["id"] for row in value["rows"]] == [kept]


def test_default_empty_registry_count_avoids_metadata_before_paginating(
        client, database, make_case, flow_row, monkeypatch):
    """An unsearched flow-account count does not need identity/photo/mark joins."""
    import json

    cid, other = make_case("Synthetic empty registry count"), make_case("Synthetic registry count isolation")
    for account, name in (("0001", "Synthetic first flow name"), ("0002", "Synthetic second flow name")):
        insert_flow(database, cid, flow_row, payer_account=account, payer_name=name,
                    payee_account="", payee_name="")
    insert_flow(database, other, flow_row, payer_account="0001", payer_name="Synthetic foreign flow name",
                payee_account="", payee_name="")
    identity(database, cid, "0001", "SYN-ID-LOCAL")
    identity(database, other, "0001", "SYN-ID-FOREIGN")
    database.executemany("insert into account_photos(case_id,account,fname,crop_cx,crop_cy,crop_r) values (?,'0001',?,0,0.75,0.25)",
                         [(cid, "synthetic-local.png"), (other, "synthetic-foreign.png")])
    database.executemany("insert into main_accounts(case_id,account,name,source) values (?,'0001',?,?)",
                         [(cid, "Synthetic local manual name", "manual"), (other, "Synthetic foreign auto name", "auto")])
    captured = []
    original_query = database.q_raw

    def capture_registry_count(sql, params=None):
        result = original_query(sql, params)
        if len(result) == 1 and set(result[0]) == {"total"}:
            captured.append((sql, params))
        return result

    monkeypatch.setattr(database, "q_raw", capture_registry_count)
    primary_count_queries, baseline_pages = [], []
    for keyword in ("", "\u3000"):
        pages = []
        for page, expected_account in ((1, "0001"), (2, "0002"), (3, None)):
            value = listing(client, cid, q=keyword, page=page, page_size=1)
            assert (value["case_id"], value["source"], value["total"], value["page"], value["page_size"]) == (
                str(cid), "flows", 2, page, 1)
            assert [item["account"] for item in value["items"]] == ([] if expected_account is None else [expected_account])
            if page == 1:
                primary_count_queries.append(captured[-1])
                row = value["items"][0]
                assert (row["name"], row["id_no"], row["is_main"], row["main_source"], row["has_flows"]) == (
                    "Synthetic local manual name", "SYN-ID-LOCAL", True, "manual", True)
                assert row["identity_updated_at"] and row["photo_at"] and row["main_marked_at"]
            elif page == 2:
                row = value["items"][0]
                assert row["name"] == "Synthetic second flow name" and row["has_flows"] is True
                assert row["id_no"] is None and row["photo_at"] is None
                assert row["is_main"] is False and row["main_source"] is None
            pages.append(value)
        if keyword == "":
            baseline_pages = pages
        else:
            assert pages == baseline_pages, "NFKC-empty text has the same complete paginated response"
        foreign = listing(client, other, q=keyword, page_size=1)
        assert foreign["total"] == 1 and foreign["case_id"] == str(other)
        row = foreign["items"][0]
        assert (row["account"], row["name"], row["id_no"], row["main_source"]) == (
            "0001", "Synthetic foreign auto name", "SYN-ID-FOREIGN", "auto")
        assert row["photo_at"] and row["is_main"] is True
    assert len(captured) == 8

    def operators(node):
        yield node
        for child in node.get("children", []):
            yield from operators(child)

    forbidden = {"account_ids", "account_photos", "main_accounts", "resolution_subject_aliases"}
    examined = set()
    for sql, params in primary_count_queries:
        signature = (sql, tuple(params))
        if signature in examined:
            continue
        examined.add(signature)
        plan = json.loads(database.con().execute(
            "EXPLAIN (ANALYZE, FORMAT JSON) " + sql, params).fetchone()[1])
        metadata_scans = [node["extra_info"] for node in operators(plan)
                          if node.get("operator_type") == "TABLE_SCAN"
                          and str(node.get("extra_info", {}).get("Table", "")).rsplit(".", 1)[-1].strip('"') in forbidden]
        assert not metadata_scans, (
            "default flow registry count scans unrelated metadata before pagination: "
            + json.dumps(metadata_scans, ensure_ascii=False))
