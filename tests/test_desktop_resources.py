"""Distribution boundary: a public-key file must contain no secret tail."""
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from scripts import build_desktop_backend as builder


@pytest.mark.parametrize('tail,accepted', [
    (b'', True),
    (b'\nnot-a-public-key', False),
    (b'\n-----BEGIN PRIVATE KEY-----\nU1lOVEhFVElD\n-----END PRIVATE KEY-----\n', False),
])
def test_resource_boundary_accepts_only_a_complete_public_key(tmp_path, monkeypatch, tail, accepted):
    monkeypatch.setattr(builder, 'ROOT', tmp_path)
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md', *('static/' + n for n in builder.STATIC_NAMES), 'frontend/dist/index.html'):
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('synthetic resource')
    public = Ed25519PublicKey.from_public_bytes(bytes.fromhex('d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a'))
    key = tmp_path / 'activation-public-key.pem'
    key.write_bytes(public.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo) + tail)
    if accepted:
        assert (key, 'activation-public-key.pem') in builder.resource_files()
    else:
        with pytest.raises(SystemExit):
            builder.resource_files()
