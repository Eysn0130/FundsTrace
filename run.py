# -*- coding: utf-8 -*-
"""封装入口：PyInstaller 打包用（python run.py 亦可启动）

加固设计（针对内网环境）：
1. 任何启动异常都写入"运行日志.txt"并保持窗口打开，不再黑屏闪退；
2. stdout/stderr 强制 UTF-8 容错，避免控制台编码问题导致崩溃；
3. 禁用控制台 QuickEdit（误点窗口会冻结程序）；
4. 端口被占用时提示"系统可能已在运行"并直接打开浏览器。
"""
import os
import socket
import sys
import threading
import traceback
import webbrowser
from datetime import datetime

from app.paths import DATA_DIR

FROZEN = getattr(sys, "frozen", False)
EXE_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))
LOG_PATH = str(DATA_DIR / "runtime.log")


def log(msg):
    """同时输出到控制台和日志文件"""
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        print(line)
    except Exception:
        pass
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8", errors="replace") as f:
            f.write(line + "\n")
    except Exception:
        pass


def main():
    # ---- 输出编码保护：控制台编码不支持时用替换符，防止 print 崩溃 ----
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # ---- 禁用控制台 QuickEdit：误点黑窗会冻结程序输出 ----
    if os.name == "nt":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            h = k32.GetStdHandle(-10)  # STD_INPUT_HANDLE
            mode = ctypes.c_uint()
            if k32.GetConsoleMode(h, ctypes.byref(mode)):
                k32.SetConsoleMode(h, mode.value & ~0x40)  # 关闭 ENABLE_QUICK_EDIT_MODE
        except Exception:
            pass

    port = int(os.environ.get("FUND_PORT", "8300"))
    url = f"http://127.0.0.1:{port}"

    try:
        # ---- 端口占用检测：系统可能已在运行 ----
        probe = socket.socket()
        try:
            probe.bind(("127.0.0.1", port))
            probe.close()
        except OSError:
            log(f"端口 {port} 已被占用——系统可能已经在运行，即将打开浏览器。")
            if FROZEN:
                threading.Timer(0.8, lambda: webbrowser.open(url)).start()
            else:
                print(f"如未运行，请关闭占用端口的程序后重试（或设置 FUND_PORT 换端口）。")
            input("按回车键关闭本窗口...")
            return

        # ---- 延迟导入主体，任何异常都被捕获并落盘 ----
        from app import main as app_main
        import uvicorn

        if FROZEN:
            threading.Timer(1.5, lambda: webbrowser.open(url)).start()
            log("FundsTrace · 资金流水追踪与关联分析")
            log(f"访问地址: {url} （浏览器将自动打开，关闭本窗口即退出系统）")
            log(f"数据目录: {DATA_DIR}    运行日志: {LOG_PATH}")
        uvicorn.run(app_main.app, host="127.0.0.1", port=port)
    except Exception:
        err = traceback.format_exc()
        log("启动失败！错误详情如下：")
        for line in err.splitlines():
            log(line)
        print("\n启动失败：请在数据目录查看 runtime.log，分享前移除敏感信息。")
        try:
            input("按回车键关闭窗口...")
        except Exception:
            pass


if __name__ == "__main__":
    main()
