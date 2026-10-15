# -*- coding: utf-8 -*-
"""离线授权模块：机器码 + HMAC 授权码（纯内网可用，无需联外网）

流程：
1. 对方运行程序 → 页面显示本机"机器码"
2. 对方把机器码发给你（微信/短信均可）
3. 你用「授权码生成器」输入机器码 → 生成授权码 → 发回
4. 对方在激活页面输入授权码 → 激活成功，绑定该台电脑

兼容原有 HMAC 格式。签名配置由部署方在源码目录之外提供；未配置时保持未激活。
此方案不提供可靠的系统时间回拨防护，也不适合把共享签名材料随客户端公开分发。
"""
import hashlib
import hmac
import os
from datetime import datetime, timedelta

from .paths import DATA_DIR

LICENSE_PATH = str(DATA_DIR / "license.dat")


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
    """生成授权码。days=0 表示永久授权"""
    mh = _machine_hash(machine_code)
    exp = "0" if not days else (datetime.now() + timedelta(days=int(days))).strftime("%Y%m%d")
    body = f"{mh}.{exp}"
    sig = hmac.new(_signing_secret(), body.encode(), hashlib.sha256).hexdigest()[:32].upper()
    return f"{body}.{sig}"


def verify(key, machine_code):
    """校验授权码。成功返回到期日字符串（0=永久），失败返回 None"""
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
    """当前授权状态：{activated, machine_code, expiry, permanent}"""
    mc = machine_code()
    key = read_saved_key()
    exp = verify(key, mc)
    if exp is not None:
        return {"activated": True, "machine_code": mc,
                "expiry": "永久" if exp == "0" else f"{exp[:4]}-{exp[4:6]}-{exp[6:]}",
                "permanent": exp == "0"}
    return {"activated": False, "machine_code": mc, "expiry": "", "permanent": False}


def is_activated():
    st = status()
    return st["activated"]
