"""Account readers follow reversible metadata projections, preserving declarations."""
import json

from app import evidence_resolution
from test_account_registry import listing, profile
from funds_assertions import unresolved_funds
from test_account_suggestions import suggestions


BANK = "Synthetic metadata bank"
CARD = "SYN-METADATA-CARD"
LEDGER = "SYN-METADATA-LEDGER"


def entity(database, case_id, kind, number, *, cards=(), status="resolved"):
    key = evidence_resolution._entity_id(BANK, kind, number)
    value = {"entity_id": key, "kind": kind, "account_number": number,
             "display_account": CARD if CARD in cards else number, "bank": BANK,
             "cards": list(cards), "names": [], "parent_entity_id": None, "status": status}
    database.x("""insert into resolution_entities(case_id,entity_id,kind,account_number,
                  display_account,bank,details_json) values (?,?,?,?,?,?,?)""",
               [case_id, key, kind, number, value["display_account"], BANK, json.dumps(value)])
    # The production rebuild atomically derives this cache from current entities.
    current = [json.loads(row["details_json"]) for row in database.q_raw(
        "select details_json from resolution_entities where case_id=?", [case_id])]
    database.x("delete from resolution_subject_aliases where case_id=?", [case_id])
    database.executemany("insert into resolution_subject_aliases(case_id,raw_account,account) values (?,?,?)",
                         [[case_id, alias, target] for alias, target in evidence_resolution._subject_aliases(current)])
    return key


def observation(database, case_id, key):
    flow_id = database.q_raw("""insert into flows(case_id,payer_account,payer_name,payee_account,
                             amount,currency,self_account)
                             values (?,?,'Synthetic metadata owner','SYN-COUNTER',1.25,'CNY',?) returning id""",
                            [case_id, CARD, CARD])[0]["id"]
    endpoint(database, case_id, flow_id, key)
    return flow_id


def endpoint(database, case_id, flow_id, key):
    database.x("delete from resolved_endpoints where case_id=? and flow_id=?", [case_id, flow_id])
    for role in ("payer", "self"):
        database.x("""insert into resolved_endpoints(case_id,flow_id,role,entity_id,state,reason)
                      values (?,?,?,?,'resolved','synthetic fixture')""", [case_id, flow_id, role, key])


def identity(database, case_id, key, value="SYN-METADATA-ID"):
    database.x("insert into account_ids(case_id,account,id_no) values (?,?,?)", [case_id, key, value])


def photo(database, case_id, key, *, filename="synthetic.png", crop=(0.0, 0.75, 0.25)):
    database.x("""insert into account_photos(case_id,account,fname,crop_cx,crop_cy,crop_r)
                  values (?,?,?,?,?,?)""", [case_id, key, filename, *crop])


def test_registry_profiles_and_suggestions_follow_reversible_card_metadata_projection(client, database, make_case):
    cid, other = make_case("Synthetic metadata projection"), make_case("Synthetic metadata isolation")
    old_key = entity(database, cid, "card", CARD, cards=[CARD], status="card_only")
    flow_id = observation(database, cid, old_key)
    identity(database, cid, old_key)
    photo(database, cid, old_key)
    identity(database, other, old_key, "SYN-FOREIGN-ID")
    photo(database, other, old_key, filename="synthetic-foreign.png")
    declarations = database.q_raw("select * from account_ids order by case_id,account")
    photos = database.q_raw("select * from account_photos order by case_id,account")
    before = profile(client, cid, old_key)
    assert before["id_no"] == "SYN-METADATA-ID" and before["photo"] is not None

    # A later, unique typed relation changes only the effective subject key.
    database.x("delete from resolution_entities where case_id=?", [cid])
    current = entity(database, cid, "account", LEDGER, cards=[CARD])
    endpoint(database, cid, flow_id, current)
    value = profile(client, cid, current)
    assert value["account"] == current and value["display_account"] == CARD
    assert value["id_no"] == before["id_no"] and value["photo"] == before["photo"]
    assert (value["cnt"], value["out_amt"]) == (None, None)
    assert value["out_funds"] == unresolved_funds(1, ("CNY", "1.25"))
    identities = listing(client, cid, source="identity")
    assert identities["total"] == 1 and identities["items"][0]["account"] == current
    assert listing(client, cid, q="SYN-METADATA-ID")["items"][0]["account"] == current
    for source in ("flows", "identity", "all"):
        found = suggestions(client, cid, "SYN-METADATA-ID", source=source)["items"]
        assert len(found) == 1 and found[0]["account"] == current
    assert suggestions(client, cid, "SYN-FOREIGN-ID", source="all")["items"] == []
    assert profile(client, other, old_key)["id_no"] == "SYN-FOREIGN-ID"

    # Withdrawing the relation restores the old effective subject without writes.
    database.x("delete from resolution_entities where case_id=?", [cid])
    restored = entity(database, cid, "card", CARD, cards=[CARD], status="card_only")
    endpoint(database, cid, flow_id, restored)
    assert restored == old_key
    assert profile(client, cid, restored)["photo"] == before["photo"]
    assert listing(client, cid, source="identity")["items"][0]["account"] == old_key
    assert database.q_raw("select * from account_ids order by case_id,account") == declarations
    assert database.q_raw("select * from account_photos order by case_id,account") == photos


def test_multiple_declarations_deduplicate_equal_values_and_hide_conflicting_metadata(client, database, make_case):
    cid = make_case("Synthetic metadata conflicts")
    current = entity(database, cid, "account", LEDGER, cards=[CARD])
    old_key = evidence_resolution._entity_id(BANK, "card", CARD)
    observation(database, cid, current)
    for key in (old_key, current):
        identity(database, cid, key)
        photo(database, cid, key)
    values = listing(client, cid, source="identity")
    assert values["total"] == 1 and values["items"][0]["account"] == current
    assert values["items"][0]["id_no"] == "SYN-METADATA-ID"
    assert profile(client, cid, current)["photo"] is not None
    assert len(suggestions(client, cid, "SYN-METADATA-ID", source="identity")["items"]) == 1

    database.x("update account_ids set id_no='SYN-CONFLICT-ID' where case_id=? and account=?", [cid, current])
    database.x("update account_photos set crop_cx=0.5 where case_id=? and account=?", [cid, current])
    conflict = profile(client, cid, current)
    assert conflict["id_no"] is None and conflict["identity_updated_at"] is None
    assert conflict["photo"] is None and conflict["photo_at"] is None
    for endpoint in ("orig", "img"):
        assert client.get(f"/api/account-photos/{endpoint}", params={"case_id": cid, "account": current}).status_code == 404
    identities = listing(client, cid, source="identity")
    assert identities["total"] == 1 and identities["items"][0]["account"] == current
    assert identities["items"][0]["id_no"] is None
    assert suggestions(client, cid, "SYN-METADATA-ID", source="all")["items"] == []
    assert suggestions(client, cid, "SYN-CONFLICT-ID", source="all")["items"] == []
    assert len(database.q_raw("select * from account_ids where case_id=?", [cid])) == 2
    assert len(database.q_raw("select * from account_photos where case_id=?", [cid])) == 2


def test_shared_card_does_not_copy_metadata_to_multiple_ledgers(client, database, make_case):
    cid = make_case("Synthetic metadata ambiguous carrier")
    targets = [entity(database, cid, "account", number, cards=[CARD]) for number in (LEDGER, LEDGER + "-OTHER")]
    old_key = evidence_resolution._entity_id(BANK, "card", CARD)
    identity(database, cid, old_key)
    photo(database, cid, old_key)
    for key in targets:
        observation(database, cid, key)
        value = profile(client, cid, key)
        assert value["id_no"] is None and value["photo"] is None
    assert not any(item["account"] in targets for item in listing(client, cid, source="identity")["items"])
    assert suggestions(client, cid, "SYN-METADATA-ID", source="flows")["items"] == []
