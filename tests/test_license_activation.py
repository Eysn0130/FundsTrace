"""Real activation HTTP front door, using only the public RFC test seed."""
from pathlib import Path
import subprocess

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from app import license as licensing
from app.main import app

MACHINE = 'ABCD-1234-5678-90EF'
SEED = '9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60'


def issued_code(machine):
    root = Path(__file__).resolve().parents[1]
    # The actual issuer code produces the token; Python verifies it independently.
    source = """
const crypto=require('node:crypto');
const {issueCode}=require('./desktop/issuer/core.cjs');
const key=crypto.createPrivateKey({format:'der',type:'pkcs8',key:Buffer.from('302e020100300506032b657004220420'+process.argv[1],'hex')});
process.stdout.write(issueCode(key,{machineCode:process.argv[2],expires:'0'}));
"""
    return subprocess.check_output(['node', '-e', source, SEED, machine], cwd=root, text=True)


def test_issuer_code_activates_only_matching_machine_and_persists(database, tmp_path, monkeypatch):
    monkeypatch.delenv('FUNDS_TRACE_LICENSE_SECRET', raising=False)
    monkeypatch.setattr(licensing, 'machine_code', lambda: MACHINE)
    public_file = tmp_path / '合成公钥.pem'
    private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(SEED))
    public_file.write_bytes(private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    monkeypatch.setenv('FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE', str(public_file))
    with TestClient(app) as client:
        assert client.get('/api/cases').status_code == 403
        assert client.get('/api/license/info').json()['verification_mode'] == 'public-key'
        assert client.post('/api/license/activate', json={'key': issued_code('0000-0000-0000-0000')}).status_code == 400
        assert client.get('/api/license/info').json()['activated'] is False
        code = issued_code(MACHINE)
        assert client.post('/api/license/activate', json={'key': code}).json() == {'ok': True, 'expiry': '永久'}
        assert client.get('/api/cases').status_code == 200
        assert Path(licensing.LICENSE_PATH).read_text() == code
    with TestClient(app) as reopened:
        assert reopened.get('/api/license/info').json()['activated'] is True
        assert reopened.get('/api/cases').status_code == 200
        public_file.unlink()
        assert reopened.get('/api/cases').status_code == 403


def test_missing_verification_key_has_actionable_error_and_no_activation(database, tmp_path, monkeypatch):
    monkeypatch.delenv('FUNDS_TRACE_LICENSE_SECRET', raising=False)
    monkeypatch.setenv('FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE', str(tmp_path / 'absent.pem'))
    monkeypatch.setattr(licensing, 'machine_code', lambda: MACHINE)
    with TestClient(app) as client:
        result = client.post('/api/license/activate', json={'key': issued_code(MACHINE)})
        assert result.status_code == 400 and '公钥' in result.json()['detail']
        assert client.get('/api/license/info').json()['verification_mode'] == 'unconfigured'
        assert client.get('/api/cases').status_code == 403
