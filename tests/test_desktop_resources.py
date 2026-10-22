"""Distribution resources must cover runtime pages and contain no secret tail."""
from html.parser import HTMLParser
from urllib.parse import urlsplit

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from scripts import build_desktop_backend as builder


def test_resource_boundary_includes_runtime_html_scripts(tmp_path, monkeypatch):
    class ScriptSources(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == 'script':
                source = dict(attrs).get('src')
                if source:
                    references.add(urlsplit(source).path.lstrip('/'))

    references = set()
    for page in ('index.html', 'analysis.html', 'license.html'):
        parser = ScriptSources()
        parser.feed((builder.ROOT / 'static' / page).read_text(encoding='utf-8'))
        parser.close()
    assert references
    assert all(name.startswith('static/') for name in references)
    # Exercise the actual resource collector with synthetic files, so this does
    # not read local activation material or require an existing frontend build.
    names = {'LICENSE', 'THIRD_PARTY_NOTICES.md', 'LICENSES/MIT.txt',
             'docs/licensing.md', 'docs/licensing.en.md', 'frontend/dist/index.html',
             *('static/' + name for name in builder.STATIC_NAMES), *references}
    for name in names:
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('synthetic resource', encoding='utf-8')
    monkeypatch.setattr(builder, 'ROOT', tmp_path)
    packaged = {relative.replace('\\', '/') for _, relative in builder.resource_files()}
    assert references <= packaged, f'Runtime scripts missing from desktop resources: {sorted(references - packaged)}'
    assert {'static/logo.png', 'static/favicon.ico', 'static/favicon-32.png'} <= packaged
    assert not {'static/logo.svg', 'static/favicon.svg'} & packaged


def test_all_retained_html_dependencies_are_packaged():
    from html.parser import HTMLParser
    from urllib.parse import unquote, urlsplit

    required = set()

    class Dependencies(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                if key in {"src", "href"} and value and value.startswith("/static/"):
                    required.add(unquote(urlsplit(value).path.removeprefix("/static/")))

    for page in (builder.ROOT / "static").glob("*.html"):
        Dependencies().feed(page.read_text(encoding="utf-8"))
    assert "graph-selection.js" in required
    # This worker is launched from app.js rather than an HTML script element.
    required.add("graph-layout-worker.js")
    worker = (builder.ROOT / "static" / "graph-layout-worker.js").read_text(encoding="utf-8")
    for kernel in ("graph-layout.js", "graph-layout-quality.js", "graph-routing.js"):
        assert f"/static/{kernel}?v=" in worker
        required.add(kernel)
    assert required <= set(builder.STATIC_NAMES), f"Unpackaged UI dependencies: {required - set(builder.STATIC_NAMES)}"
    assert all((builder.ROOT / "static" / name).is_file() for name in required)


@pytest.mark.parametrize('tail,accepted', [
    (b'', True),
    (b'\nnot-a-public-key', False),
    (b'\n-----BEGIN PRIVATE KEY-----\nU1lOVEhFVElD\n-----END PRIVATE KEY-----\n', False),
])
def test_resource_boundary_accepts_only_a_complete_public_key(tmp_path, monkeypatch, tail, accepted):
    monkeypatch.setattr(builder, 'ROOT', tmp_path)
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'LICENSES/MIT.txt',
                 'docs/licensing.md', 'docs/licensing.en.md',
                 *('static/' + n for n in builder.STATIC_NAMES), 'frontend/dist/index.html'):
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


def test_license_resources_preserve_both_languages_and_historical_notices(tmp_path, monkeypatch):
    names = ('LICENSE', 'LICENSES/MIT.txt', 'THIRD_PARTY_NOTICES.md',
             'docs/licensing.md', 'docs/licensing.en.md')
    contents = {name: (builder.ROOT / name).read_bytes() for name in names}
    assert '循迹 FundsTrace'.encode() in contents['LICENSE']
    assert b'I. Scope and Existing Rights' in contents['LICENSE']
    assert b'XI. Miscellaneous' in contents['LICENSE']
    assert contents['LICENSES/MIT.txt'].startswith(b'MIT License\n')
    assert contents['LICENSE'] != contents['LICENSES/MIT.txt']
    for name, content in contents.items():
        source = tmp_path / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(content)
    dist = tmp_path / 'frontend' / 'dist'
    dist.mkdir(parents=True)
    (dist / 'index.html').write_text('synthetic frontend')
    monkeypatch.setattr(builder, 'ROOT', tmp_path)
    monkeypatch.setattr(builder, 'STATIC_NAMES', ())

    resources = {relative: source for source, relative in builder.resource_files()}

    for name, content in contents.items():
        assert resources[name] == tmp_path / name
        assert resources[name].read_bytes() == content
