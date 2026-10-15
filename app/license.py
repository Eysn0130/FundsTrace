# -*- coding: utf-8 -*-
"""离线授权：FT2 公钥验签，以及显式配置的旧 HMAC 兼容。

流程：
1. 对方运行程序 → 页面显示本机"机器码"
2. 对方把机器码发给你（微信/短信均可）
3. 你用「授权码生成器」输入机器码 → 生成授权码 → 发回
4. 对方在激活页面输入授权码 → 激活成功，绑定该台电脑

客户端仅持有 Ed25519 公钥，签发私钥由独立维护工具管理。
旧 HMAC 仅在部署方显式配置共享材料时可用。两种格式均不防系统时间回拨。
"""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
from datetime import date, datetime, timedelta

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .paths import DATA_DIR

LICENSE_PATH = str(DATA_DIR / "license.dat")
_PUBLIC_KEY_PATH = Path(__file__).resolve().parents[1] / "activation-public-key.pem"
_FT2_DOMAIN = b"FundsTrace-license-v2\0"
_MACHINE_CODE = re.compile(r"(?:[0-9A-F]{4}-){3}[0-9A-F]{4}")
_MACHINE_HASH = re.compile(r"[0-9A-F]{16}")
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+")
_PUBLIC_KEY_PEM = re.compile(
    rb"-----BEGIN PUBLIC KEY-----\r?\n(?:[A-Za-z0-9+/=]+\r?\n)+-----END PUBLIC KEY-----"
)


def _signing_secret():
    value = os.environ.get("FUNDS_TRACE_LICENSE_SECRET", "")
    if not value:
        raise RuntimeError("未配置授权签名材料；请由维护者通过受保护的运行环境配置")
    return value.encode("utf-8")


# ---------------- 机器码 ----------------

def _volume_serial():
    """C 盘卷序列号"""
    try:
        import ctypes
        vol = ctypes.create_unicode_buffer(260)
        fs = ctypes.create_unicode_buffer(260)
        serial = ctypes.c_ulong(0)
        maxlen = ctypes.c_ulong(0)
        flags = ctypes.c_ulong(0)
        if ctypes.windll.kernel32.GetVolumeInformationW(
                "C:\\", vol, 260, ctypes.byref(serial),
                ctypes.byref(maxlen), ctypes.byref(flags), fs, 260):
            return format(serial.value, "08X")
    except Exception:
        pass
    return "NOSERIAL"


def _machine_guid():
    """Windows 安装唯一 GUID（重装系统才会变化）"""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
        v, _ = winreg.QueryValueEx(k, "MachineGuid")
        winreg.CloseKey(k)
        return v
    except Exception:
        return "NOGUID"


def machine_code():
    """机器码：由主机名/系统GUID/卷序列号/CPU信息联合散列，16位大写十六进制"""
    import socket
    raw = "|".join([
        socket.gethostname(),
        _machine_guid(),
        _volume_serial(),
        os.environ.get("PROCESSOR_IDENTIFIER", "NOCPU"),
    ])
    h = hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:16].upper()
    return "-".join(h[i:i + 4] for i in range(0, 16, 4))


def _machine_hash(machine_code):
    return hashlib.sha256((machine_code or "").strip().upper().encode()).hexdigest()[:16].upper()


# ---------------- 授权码生成与校验 ----------------

def issue(machine_code, days=0):
    """旧 HMAC 兼容签发；新签发应使用独立工具。days=0 表示永久授权。"""
    mh = _machine_hash(machine_code)
    exp = "0" if not days else (datetime.now() + timedelta(days=int(days))).strftime("%Y%m%d")
    body = f"{mh}.{exp}"
    sig = hmac.new(_signing_secret(), body.encode(), hashlib.sha256).hexdigest()[:32].upper()
    return f"{body}.{sig}"


def _public_key():
    """只读取部署公钥；配置错误不泄漏路径或文件内容。"""
    try:
        configured = os.environ.get("FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE")
        path = Path(configured) if configured is not None else _PUBLIC_KEY_PATH
        if not path.is_absolute():
            return None, ""
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
            return None, ""
        with path.open("rb") as source:
            pem = source.read(4097).strip()
        if len(pem) > 4096 or _PUBLIC_KEY_PEM.fullmatch(pem) is None:
            return None, ""
        key = serialization.load_pem_public_key(pem)
        if not isinstance(key, Ed25519PublicKey):
            return None, ""
        der = key.public_bytes(serialization.Encoding.DER,
                               serialization.PublicFormat.SubjectPublicKeyInfo)
        return key, hashlib.sha256(der).hexdigest()[:16].upper()
    except (OSError, ValueError, TypeError, UnsupportedAlgorithm):
        return None, ""


def _decode_base64url(value):
    if _BASE64URL.fullmatch(value) is None:
        raise ValueError("invalid base64url")
    raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != value:
        raise ValueError("noncanonical base64url")
    return raw


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("invalid JSON constant")


def _verify_ft2(token, machine):
    if len(token) > 4096 or not isinstance(machine, str):
        return None
    normalized_machine = machine.strip().upper()
    if _MACHINE_CODE.fullmatch(normalized_machine) is None:
        return None
    try:
        prefix, encoded, signature = token.split(".")
        if prefix != "FT2":
            return None
        payload_bytes = _decode_base64url(encoded)
        signature_bytes = _decode_base64url(signature)
        if len(payload_bytes) > 512 or len(signature_bytes) != 64:
            return None
        payload = json.loads(payload_bytes.decode("utf-8"), object_pairs_hook=_unique_object,
                             parse_constant=_reject_constant)
        if not isinstance(payload, dict) or set(payload) != {"v", "p", "m", "e"}:
            return None
        if type(payload["v"]) is not int or payload["v"] != 2 or payload["p"] != "FundsTrace":
            return None
        mh, exp = payload["m"], payload["e"]
        if not isinstance(mh, str) or _MACHINE_HASH.fullmatch(mh) is None:
            return None
        if mh != _machine_hash(normalized_machine) or not isinstance(exp, str):
            return None
        if exp != "0":
            if re.fullmatch(r"[0-9]{8}", exp) is None:
                return None
            if date.today() > datetime.strptime(exp, "%Y%m%d").date():
                return None
        public_key, _ = _public_key()
        if public_key is None:
            return None
        public_key.verify(signature_bytes, _FT2_DOMAIN + payload_bytes)
        return exp
    except (ValueError, TypeError, UnicodeError, InvalidSignature):
        return None


def _verify_legacy(key, machine_code):
    try:
        parts = (key or "").strip().upper().split(".")
        if len(parts) != 3:
            return None
        mh, exp, sig = parts
        body = f"{mh}.{exp}"
        good = hmac.new(_signing_secret(), body.encode(), hashlib.sha256).hexdigest()[:32].upper()
        if not hmac.compare_digest(good, sig.upper()):
            return None
        if mh != _machine_hash(machine_code):
            return None
        if exp != "0":
            datetime.strptime(exp, "%Y%m%d")  # 格式校验
            if datetime.now() > datetime.strptime(exp, "%Y%m%d"):
                return None
        return exp
    except Exception:
        return None


def verify(key, machine_code):
    """成功返回到期日（0=永久）；FT2 大小写及原始签名字节不可变。"""
    if not isinstance(key, str):
        return None
    if key.strip().startswith("FT2."):
        return _verify_ft2(key, machine_code)
    return _verify_legacy(key, machine_code)


# ---------------- 授权状态存取 ----------------

def read_saved_key():
    try:
        with open(LICENSE_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def save_key(key):
    os.makedirs(os.path.dirname(LICENSE_PATH), exist_ok=True)
    with open(LICENSE_PATH, "w", encoding="utf-8") as f:
        f.write((key or "").strip())


def status():
    """当前授权状态及可公开的验签配置摘要。"""
    mc = machine_code()
    key = read_saved_key()
    exp = verify(key, mc)
    public_key, fingerprint = _public_key()
    mode = ("public-key" if public_key is not None else
            "legacy-hmac" if os.environ.get("FUNDS_TRACE_LICENSE_SECRET") else "unconfigured")
    if exp is not None and not key.startswith("FT2."):
        mode = "legacy-hmac"
    verification = {"verification_mode": mode, "public_key_fingerprint": fingerprint}
    if exp is not None:
        return {"activated": True, "machine_code": mc,
                "expiry": "永久" if exp == "0" else f"{exp[:4]}-{exp[4:6]}-{exp[6:]}",
                "permanent": exp == "0", **verification}
    return {"activated": False, "machine_code": mc, "expiry": "", "permanent": False,
            **verification}


def is_activated():
    st = status()
    return st["activated"]
