"""Exercise the real aiohttp transport against a local HTTP portal.

Run outside pytest (no socket bans):  python tests/manual_http_check.py
Uses only synthetic data, no real credentials.
"""

from __future__ import annotations

import asyncio
import gzip
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BALANCE = (
    "<table class='uTable'>"
    "<tr><td>余额 ：</td><td>123.4500 元</td></tr>"
    "<tr><td>累积用气量 ：</td><td>45.6700 方气</td></tr>"
    "<tr><td>是否通气：</td><td>已通气</td></tr>"
    "<tr><td>操作时间：</td><td>2026-01-02 03:04:05</td></tr>"
    "</table>"
)

MAIN = (
    '<div id="myHouse"><table><tbody><tr>'
    '<td><input type="radio" name="uno" value="UNO1|13800000001"></td>'
    '<td><label>UNO1</label></td><td><label>张**三</label></td><td><label>查询</label></td>'
    '</tr><tr><td></td><td colspan="3"><label>地址: 某某路1号</label></td></tr></tbody></table></div>'
)


class Handler(BaseHTTPRequestHandler):
    session = False
    gzip_body = False

    def log_message(self, *args):  # silence
        pass

    def _send(self, text: str, status: int = 200):
        body = text.encode("utf-8")
        headers = {"Content-Type": "text/html;charset=utf-8"}
        if self.gzip_body:
            body = gzip.compress(body)
            headers["Content-Encoding"] = "gzip"
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/member/main.asp":
            if not Handler.session:
                self._send('<html><input id="inputPassword"></html>')
            else:
                self._send(MAIN)
        elif path == "/member/userrecharge.asp":
            self._send("<table><tbody></tbody></table>")
        else:
            self._send("not found", 404)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        if path == "/member/api/login.asp":
            ok = form.get("pws", [""])[0] == "secret"
            Handler.session = ok
            self._send("1" if ok else "0")
        elif path == "/member/api/gas.asp":
            if not Handler.session:
                self._send('<html><input id="inputPassword"></html>')
            else:
                self._send(BALANCE)
        else:
            self._send("not found", 404)


async def main() -> int:
    from custom_components.kuangyuan_gas.api import KuangyuanAPI, create_session

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    checks: list[tuple[str, bool]] = []

    # 1. plain HTTP, correct password
    session = create_session()
    api = KuangyuanAPI(session, "13800000001", "secret", base)
    accounts = await api.async_get_accounts()
    checks.append(("accounts parsed", len(accounts) == 1 and accounts[0].uno == "UNO1"))
    checks.append(("address parsed", accounts[0].address == "某某路1号"))
    info = await api.async_query_balance("UNO1")
    checks.append(("balance parsed", info.balance == 123.45))
    checks.append(("usage parsed", info.usage == 45.67))
    checks.append(("supply on", info.supplied))
    await api.async_close()
    checks.append(("session closed", session.closed))

    # 2. gzip encoded responses
    Handler.gzip_body = True
    Handler.session = False
    session = create_session()
    api = KuangyuanAPI(session, "13800000001", "secret", base)
    accounts = await api.async_get_accounts()
    info = await api.async_query_balance("UNO1")
    checks.append(("gzip accounts", len(accounts) == 1))
    checks.append(("gzip balance", info.balance == 123.45))
    await api.async_close()
    Handler.gzip_body = False

    # 3. wrong password -> auth error
    Handler.session = False
    session = create_session()
    api = KuangyuanAPI(session, "13800000001", "wrong", base)
    try:
        await api.async_get_accounts()
        checks.append(("wrong password rejected", False))
    except Exception as exc:  # noqa: BLE001
        from custom_components.kuangyuan_gas.api import KuangyuanAuthError

        checks.append(("wrong password rejected", isinstance(exc, KuangyuanAuthError)))
    await api.async_close()

    # 4. two independent sessions keep separate cookie jars
    Handler.session = False
    s1, s2 = create_session(), create_session()
    a1 = KuangyuanAPI(s1, "13800000001", "secret", base)
    a2 = KuangyuanAPI(s2, "13800000001", "secret", base)
    await a1.async_ensure_login()
    await a2.async_ensure_login()
    checks.append(("separate jars", s1.cookie_jar is not s2.cookie_jar))
    Handler.session = False  # a2's cookie must not log a1 in
    await a1.async_close()
    await a2.async_close()

    server.shutdown()

    failed = 0
    for name, ok in checks:
        print(("PASS " if ok else "FAIL ") + name)
        failed += 0 if ok else 1
    print(f"\n{len(checks) - failed}/{len(checks)} transport checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

