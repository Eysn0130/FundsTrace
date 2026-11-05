"""An auxiliary ZIP needs explicit unique source scope, never a suggestive name."""
import io
import pytest
from app import evidence_resolution
from test_folder_evidence import table, account, archive, preview, complete

def members(*, bank="Synthetic bank", requests=("SYN-R1",), account_bank="Synthetic bank", card="SYN-C1"):
    return [("SYN-R1/Synthetic bank账户信息.csv", table("account", [account(bank=account_bank, card=card)])),
            ("scope.csv", table("task", [{"请求单号": x, "银行名称": bank} for x in requests]))]

@pytest.mark.parametrize("changes", [{"requests": ()}, {"requests": ("",)}, {"requests": ("SYN-R1", "SYN-R2")},
                                      {"bank": "Other bank"}, {"account_bank": ""}, {"card": ""}])
def test_incomplete_or_ambiguous_declarations_cannot_opt_in(changes):
    assert not evidence_resolution.declared_auxiliary_family(io.BytesIO(archive(members(**changes))))

def test_unknown_member_cannot_expand_closed_auxiliary_admission():
    assert not evidence_resolution.declared_auxiliary_family(io.BytesIO(archive(members()+[("note.csv", b"note\nhello\n")])))

def test_partial_zip_public_source_retention_and_typed_relation(client, database, make_case):
    cid=make_case("Synthetic declared auxiliary ZIP")
    content=archive(members())
    response=client.post(f'/api/v1/cases/{cid}/imports/preview',
                         files={'file': ('synthetic.zip', content)})
    assert response.status_code==200,response.text
    view=response.json()
    job=complete(client,cid,view)
    assert job['stage']=='done',job
    assert database.q_raw('select * from flows where case_id=?',[cid])==[]
    entities=client.get(f'/api/v1/cases/{cid}/resolution').json()['entities']
    assert any(x['kind']=='account' and x['account_number']=='SYN-A' and 'SYN-C1' in x['cards'] for x in entities)
    source=client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file")
    assert source.status_code==200 and source.content==content

@pytest.mark.parametrize("changes", [{"requests": ()}, {"requests": ("SYN-R1", "SYN-R2")}, {"account_bank": ""}])
def test_unbound_partial_zip_never_publishes_account_relation(client,database,make_case,changes):
    cid=make_case("Synthetic unbound auxiliary ZIP")
    view=preview(client,cid,members(**changes),zipped=True,scope_bank=None)
    job=complete(client,cid,view)
    assert job['stage']=='done',job
    assert database.q_raw('select * from flows where case_id=?',[cid])==[]
    assert client.get(f'/api/v1/cases/{cid}/resolution').json()['entities']==[]
