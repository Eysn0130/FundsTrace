"""Owned desktop sidecar. Bootstrap is private stdin, never a command-line token."""
import hmac
import json
import os
from pathlib import Path
import re
import socket
import sys
import threading
import time


PROTOCOL = 1
TOKEN_HEADER = b"x-fundstrace-desktop"
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
       "worker-src 'self' blob:; object-src 'none'; base-uri 'none'; "
       "frame-src 'self'; frame-ancestors 'self'; form-action 'self'")


def read_bootstrap(stream):
    line = stream.readline(4097)
    if len(line) > 4096 or not line.endswith("\n"):
        raise ValueError("Invalid desktop bootstrap")
    value = json.loads(line)
    if (not isinstance(value, dict) or set(value) != {"protocol", "token", "dataDir", "synthetic"}
            or type(value["protocol"]) is not int or value["protocol"] != PROTOCOL
            or not isinstance(value["token"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["token"])
            or not isinstance(value["dataDir"], str) or not Path(value["dataDir"]).is_absolute()
            or type(value["synthetic"]) is not bool):
        raise ValueError("Invalid desktop bootstrap")
    if value["synthetic"] and getattr(sys, "frozen", False):
        raise ValueError("Synthetic mode is unavailable in distributed applications")
    return value


class DesktopBoundary:
    """All HTTP requests, including static files, belong to one owned session.

    This boundary is for the standalone product. It grants no Analytix authority.
    The token is injected by Electron Main into this exact loopback origin only.
    """

    def __init__(self, application, token, port):
        self.application = application
        self.token = token.encode("ascii")
        self.host = f"127.0.0.1:{port}".encode("ascii")
        self.origin = b"http://" + self.host

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.application(scope, receive, send)
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        headers = scope.get("headers", [])
        values = lambda name: [v for k, v in headers if k.lower() == name]
        tokens, hosts, origins = values(TOKEN_HEADER), values(b"host"), values(b"origin")
        authorized = (len(tokens) == 1 and hmac.compare_digest(tokens[0], self.token)
                      and hosts == [self.host]
                      and (not origins or origins == [self.origin])
                      and all(v in (b"same-origin", b"none") for v in values(b"sec-fetch-site")))

        async def secured_send(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                original = [(k, v) for k, v in message.get("headers", [])
                            if k.lower() not in (b"content-security-policy", b"cache-control")]
                message["headers"] = original + [
                    (b"content-security-policy", CSP.encode("ascii")),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                ]
            await send(message)

        async def reply(status, body):
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
            await secured_send({"type": "http.response.start", "status": status,
                                "headers": [(b"content-type", b"application/json")]})
            await secured_send({"type": "http.response.body", "body": payload})

        if not authorized:
            return await reply(403, {"error": "desktop_session_required"})
        if scope["path"] == "/__desktop/health" and scope["method"] == "GET":
            return await reply(200, {"protocol": PROTOCOL, "service": "FundsTrace", "ready": True})
        return await self.application(scope, receive, secured_send)


def main():
    # Configure state before importing any application module. No CWD discovery,
    # browser opener, fixed port, or connection to an already-running service.
    # Node writes UTF-8 bytes; frozen Python ignores PYTHONUTF8 and Windows pipes
    # otherwise inherit the locale encoding, corrupting Chinese data directories.
    sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    bootstrap = read_bootstrap(sys.stdin)
    protocol_output = sys.stdout
    sys.stdout = sys.stderr
    directory = Path(bootstrap["dataDir"]).resolve()
    os.environ["FUND_DATA_DIR"] = str(directory)
    os.environ["MPLCONFIGDIR"] = str(directory / ".cache" / "matplotlib")
    if bootstrap["synthetic"]:
        import importlib
        importlib.import_module("desktop_synthetic").seed()

    from app.main import app
    from app import db
    import uvicorn

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        wrapped = DesktopBoundary(app, bootstrap["token"], port)
        server = uvicorn.Server(uvicorn.Config(wrapped, log_level="error", access_log=False,
                                              ws="none", lifespan="on", timeout_graceful_shutdown=10))

        def supervise():
            while not server.started and not server.should_exit:
                time.sleep(0.025)
            if not server.started:
                return
            # Application diagnostics belong to stderr; the bootstrap channel
            # never contains session tokens or business values.
            print(json.dumps({"protocol": PROTOCOL, "port": port}), file=protocol_output, flush=True)
            try:
                for _ in sys.stdin:
                    break
            finally:
                server.should_exit = True

        threading.Thread(target=supervise, daemon=True, name="desktop-parent").start()
        try:
            server.run(sockets=[listener])
        finally:
            with db._lock:
                if db._con is not None:
                    db._con.close()
                    db._con = None


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    except Exception:
        # Do not echo bootstrap, paths, signing configuration or import values.
        print("FundsTrace backend failed to start or stopped unexpectedly.", file=sys.stderr, flush=True)
        sys.exit(1)
