"""Image exports retain display scope as metadata without drawing a footer."""
import base64
import io
import sys
import types

from PIL import Image, PdfParser
import pytest


DESCRIPTION = (
    "当前筛选视图：2 / 7 个账户 · 1 / 9 条关系；标签：自动分级。\n"
    "CNY 不少于 10000000000000000.01；2026-01-01 00:00:00 至 "
    "2026-01-31 23:59:59（原始本地时间，含边界）；排除无时间流水。"
)


def synthetic_image(mode="RGB"):
    image = Image.new(mode, (19, 17))
    colors = [((x * 13 + y * 7) % 256, (x * 3 + y * 17) % 256,
               (x * 11 + y * 5) % 256)
              for y in range(image.height) for x in range(image.width)]
    if mode == "RGBA":
        colors = [(*color, (i * 19) % 256) for i, color in enumerate(colors)]
    image.putdata(colors)
    return image


def image_url(image, fmt="PNG"):
    buffer = io.BytesIO()
    image.save(buffer, fmt)
    return f"data:image/{fmt.lower()};base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def read_pdf_info(content):
    with PdfParser.PdfParser(buf=content) as pdf:
        assert len(pdf.pages) == 1
        return {key.name.decode("ascii"): PdfParser.decode_text(value)
                for key, value in pdf.info.items()}


@pytest.mark.parametrize("mode", ["RGB", "RGBA"])
def test_png_export_preserves_pixels_and_exact_chinese_description(client, mode):
    original = synthetic_image(mode)
    response = client.post("/api/export/graph.png", json={
        "image": image_url(original), "description": DESCRIPTION,
    })
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert "attachment;" in response.headers["content-disposition"]
    with Image.open(io.BytesIO(response.content)) as exported:
        assert exported.size == original.size
        assert exported.mode == original.mode
        assert exported.tobytes() == original.tobytes()
        assert exported.info["Description"] == DESCRIPTION
    assert b"iTXt" in response.content


@pytest.mark.parametrize("img2pdf_state", ["unavailable", "failed"])
def test_pdf_fallback_preserves_exact_chinese_description(client, monkeypatch, img2pdf_state):
    if img2pdf_state == "unavailable":
        monkeypatch.setitem(sys.modules, "img2pdf", None)
    else:
        module = types.ModuleType("img2pdf")

        def failed_convert(*args, **kwargs):
            raise RuntimeError("synthetic conversion failure")

        module.convert = failed_convert
        monkeypatch.setitem(sys.modules, "img2pdf", module)
    response = client.post("/api/export/graph.pdf", json={
        "image": image_url(synthetic_image()), "description": DESCRIPTION,
    })
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment;" in response.headers["content-disposition"]
    info = read_pdf_info(response.content)
    assert info["Subject"] == DESCRIPTION
    assert info["Title"] == "资金图谱"
    assert info["Creator"] == "FundsTrace"
    with PdfParser.PdfParser(buf=response.content) as pdf:
        page = pdf.read_indirect(pdf.pages[0])
        objects = page[b"Resources"][b"XObject"]
        assert len(objects) == 1
        image = pdf.read_indirect(next(iter(objects.values())))
        assert (image.dictionary[b"Width"], image.dictionary[b"Height"]) == (19, 17)
        assert b"BT" not in pdf.read_indirect(page[b"Contents"]).decode()


def test_pdf_passes_description_to_optional_img2pdf(client, monkeypatch):
    # The optional converter is not a development dependency. Exercise its call
    # contract here; the fallback test above parses an actual generated PDF.
    original = synthetic_image()
    converted = b"%PDF-synthetic-img2pdf-result"
    module = types.ModuleType("img2pdf")
    calls = []

    def convert(png, **metadata):
        with Image.open(io.BytesIO(png)) as image:
            assert image.size == original.size
            assert image.tobytes() == original.tobytes()
        calls.append(metadata)
        return converted

    module.convert = convert
    monkeypatch.setitem(sys.modules, "img2pdf", module)
    response = client.post("/api/export/graph.pdf", json={
        "image": image_url(original), "description": DESCRIPTION,
    })
    assert response.status_code == 200, response.text
    assert response.content == converted
    assert calls == [{"title": "资金图谱", "subject": DESCRIPTION, "creator": "FundsTrace"}]


@pytest.mark.parametrize("extension", ["png", "pdf"])
@pytest.mark.parametrize("optional", [{}, {"description": None}, {"description": ""}])
def test_image_export_accepts_legacy_payload_without_description(client, monkeypatch, extension, optional):
    monkeypatch.setitem(sys.modules, "img2pdf", None)
    original = synthetic_image()
    response = client.post(f"/api/export/graph.{extension}", json={
        "image": image_url(original), **optional,
    })
    assert response.status_code == 200, response.text
    if extension == "png":
        with Image.open(io.BytesIO(response.content)) as exported:
            assert exported.size == original.size
            assert exported.tobytes() == original.tobytes()
            assert exported.info.get("Description", "") == ""
    else:
        assert read_pdf_info(response.content).get("Subject", "") == ""


@pytest.mark.parametrize("extension", ["png", "pdf"])
@pytest.mark.parametrize("invalid_image", [
    "no-data-url",
    "data:image/png;base64,not-valid-base64!",
    "data:image/png;base64," + base64.b64encode(b"not a png").decode("ascii"),
    image_url(synthetic_image(), "JPEG"),
    image_url(synthetic_image(), "JPEG").replace("image/jpeg", "image/png"),
    image_url(synthetic_image()).replace("image/png", "image/jpeg"),
    "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii"),
], ids=["missing-data-url", "invalid-base64", "not-png", "jpeg", "disguised-jpeg", "wrong-mime", "truncated-png"])
def test_image_export_rejects_invalid_png(client, extension, invalid_image):
    response = client.post(f"/api/export/graph.{extension}", json={"image": invalid_image})
    assert response.status_code == 400
    assert "data:image" not in response.text


@pytest.mark.parametrize("extension", ["png", "pdf"])
@pytest.mark.parametrize("description", ["长" * 8193, 123, ["invalid"]], ids=["too-long", "number", "list"])
def test_image_export_rejects_invalid_description(client, extension, description):
    response = client.post(f"/api/export/graph.{extension}", json={
        "image": image_url(synthetic_image()), "description": description,
    })
    assert response.status_code == 422
