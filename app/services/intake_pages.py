"""Render one retained PDF page for the isolated desktop material inspector."""
from contextlib import closing
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile

from . import imports
from .cases import ServiceError

_PAGE_BYTES = 32 * 1024 * 1024
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def pdf_page(case_id, source_id, member_path, page):
    renderer = shutil.which("pdftoppm")
    if not renderer:
        raise ServiceError(503, "pdf_page_tool_unavailable", "本机未安装页预览工具，可下载原件并按页号核对")
    stream, filename = imports.get_source_member_file(case_id, source_id, member_path)
    with closing(stream), tempfile.TemporaryDirectory(prefix="fundstrace-pdf-page-") as scratch:
        if Path(filename).suffix.lower() != ".pdf":
            raise ServiceError(422, "pdf_page_wrong_member", "当前材料成员不是 PDF")
        root = Path(scratch)
        source = root / "source.pdf"
        with source.open("wb") as file:
            count = 0
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                count += len(block)
                if count > _PAGE_BYTES:
                    raise ServiceError(422, "pdf_page_size_limit", "原件超过页预览限制，请下载原件核对")
                file.write(block)
        target = root / "page"
        try:
            reply = subprocess.run(
                [renderer, "-f", str(page), "-l", str(page), "-singlefile", "-scale-to", "1400", "-png", str(source), str(target)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=12, check=False,
            )
            rendered = [file for file in root.glob("page*.png")
                        if re.fullmatch(rf"page(?:-0*{page})?\.png", file.name)]
            if not reply.returncode and len(rendered) == 1 and rendered[0].stat().st_size <= 12 * 1024 * 1024:
                data = rendered[0].read_bytes()
                if len(data) >= 24 and data.startswith(_PNG_MAGIC) and data[12:16] == b"IHDR":
                    width, height = struct.unpack(">II", data[16:24])
                    if 0 < width <= 1400 and 0 < height <= 1400:
                        return data
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise ServiceError(422, "pdf_page_unavailable", "无法读取这页原件，请下载 PDF 核对页码")
