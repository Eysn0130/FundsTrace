"""RAR metadata and bounded stdout decoding; never extract member paths to disk."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import platform
import stat
import subprocess
import sys
import tempfile
import threading
import zipfile


MAX_MEMBERS = 2048
MAX_SOURCE_BYTES = 300 * 1024 * 1024
DECODE_SECONDS = 30
RESOURCE_ROOT = Path(__file__).resolve().parents[1] / "resources" / "rar-decoder"


def decoder_resources():
    """The same pinned files are used by source runtime and native sidecars."""
    key = "darwin" if sys.platform == "darwin" else "win32-x64" if sys.platform == "win32" and platform.machine().lower() in {"amd64", "x86_64"} else sys.platform + "-" + platform.machine().lower()
    directory = RESOURCE_ROOT / key
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        files = manifest["files"]
        expected = {"7z.exe", "7z.dll"} if key == "win32-x64" else {"7zz"}
        if set(files) != expected or manifest["version"] != "26.05":
            raise ValueError("RAR解码器清单无效")
        result = [(directory / "manifest.json", f"resources/rar-decoder/{key}/manifest.json")]
        for name, digest in files.items():
            path = directory / name
            if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("RAR解码器校验失败")
            result.append((path, f"resources/rar-decoder/{key}/{name}"))
        for name in ("LICENSE.txt", "LGPL-2.1.txt"):
            result.append((RESOURCE_ROOT / name, f"resources/rar-decoder/{name}"))
        if any(not path.is_file() for path, _ in result):
            raise ValueError("RAR解码器许可文件缺失")
        return result
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("RAR解码器缺失或损坏，请使用完整的FundsTrace安装文件") from error


def _decoder():
    return next(path for path, _ in decoder_resources() if path.name in {"7zz", "7z.exe"})


@dataclass(frozen=True)
class _Member:
    entry: object
    filename: str
    file_size: int
    flag_bits: int
    external_attr: int

    @property
    def orig_filename(self):
        return self.filename

    def is_dir(self):
        return self.entry.is_dir()


class _DecodedStream:
    def __init__(self, process, size):
        self.process, self.size = process, size
        self.count = 0

    def read(self, size=-1):
        # A dishonest size cannot make a prefix read or a caller's read-all
        # allocate beyond the advertised member plus one overflow byte.
        remaining = self.size - self.count + 1
        block = self.process.stdout.read(remaining if size < 0 else min(size, remaining))
        self.count += len(block)
        if self.count > self.size:
            raise ValueError("RAR成员展开大小与清单不一致")
        return block


class _RarArchive:
    def __init__(self, source):
        try:
            import rarfile
        except ImportError as error:
            raise ValueError("RAR读取组件缺失，请使用完整的FundsTrace安装文件") from error
        self.source, self.temporary = source, None
        count = 0

        def count_entry(entry):
            nonlocal count
            if entry.type == rarfile.RAR_BLOCK_FILE:
                count += 1
                if count > MAX_MEMBERS:
                    raise ValueError("RAR成员数超出安全处理范围")

        try:
            self.archive = rarfile.RarFile(source, errors="strict", info_callback=count_entry)
            if self.archive.needs_password() or len(self.archive.volumelist()) != 1:
                raise ValueError("暂不接受加密或分卷RAR，请提供完整的未加密归档")
            self.members = []
            for entry in self.archive.infolist():
                if entry.flags & (rarfile.RAR_FILE_SPLIT_BEFORE | rarfile.RAR_FILE_SPLIT_AFTER):
                    raise ValueError("暂不接受分卷RAR，请提供完整的单文件归档")
                link = entry.is_symlink() or bool(entry.file_redir) or not (entry.is_file() or entry.is_dir())
                self.members.append(_Member(entry, entry.filename, entry.file_size,
                    int(entry.needs_password()), (stat.S_IFLNK if link else stat.S_IFDIR if entry.is_dir() else stat.S_IFREG) << 16))
        except (rarfile.Error, ValueError) as error:
            if getattr(self, "archive", None) is not None:
                self.archive.close()
            if isinstance(error, rarfile.Error):
                raise ValueError("RAR结构损坏、加密或分卷不完整，整包未接纳") from error
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.archive.close()
        if self.temporary is not None:
            self.temporary.cleanup()

    def infolist(self):
        return self.members

    def _source_path(self):
        if self.temporary is None:
            self.temporary = tempfile.TemporaryDirectory(prefix="funds-trace-rar-")
            path = Path(self.temporary.name) / "source.rar"
            self.source.seek(0)
            total = 0
            with path.open("xb") as target:
                while block := self.source.read(min(1024 * 1024, MAX_SOURCE_BYTES - total + 1)):
                    total += len(block)
                    if total > MAX_SOURCE_BYTES:
                        raise ValueError("RAR原件超出安全处理大小")
                    target.write(block)
        return Path(self.temporary.name) / "source.rar"

    @contextmanager
    def open(self, member):
        member = next(item for item in self.members if item.filename == member) if isinstance(member, str) else member
        executable = _decoder()
        command = [str(executable), "x", "-so", "-bd", "-bb0", "-y", "-spd", "-ssc", "--",
                   str(self._source_path()), member.filename]
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=self.temporary.name,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        timer = threading.Timer(DECODE_SECONDS, process.kill)
        timer.daemon = True
        timer.start()
        stream = _DecodedStream(process, member.file_size)
        try:
            yield stream
            if stream.count == member.file_size:
                if process.wait(timeout=DECODE_SECONDS) != 0:
                    raise ValueError("RAR解码失败或成员校验未通过，整包未接纳")
        except subprocess.TimeoutExpired as error:
            raise ValueError("RAR解码超出安全处理时间，整包未接纳") from error
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
            process.stdout.close()
            process.wait()

    def read(self, member):
        with self.open(member) as stream:
            return stream.read()


def is_rar(source):
    source.seek(0)
    magic = source.read(8)
    source.seek(0)
    return magic.startswith(b"Rar!\x1a\x07\x00") or magic == b"Rar!\x1a\x07\x01\x00"


def open_archive(source):
    return _RarArchive(source) if is_rar(source) else zipfile.ZipFile(source)
