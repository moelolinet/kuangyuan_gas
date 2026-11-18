from __future__ import annotations

import asyncio
import sys

import pytest

# Windows' ProactorEventLoop builds an internal socketpair, which the Home
# Assistant test plugin blocks.  Select the selector loop before pytest-asyncio
# creates any loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(scope="session")
def event_loop_policy():
    """Return the selector policy so aiodns can build its resolver."""
    return asyncio.WindowsSelectorEventLoopPolicy()

pytest_plugins = "pytest_homeassistant_custom_component"

BALANCE_OK = (
    "<table class='uTable'>"
    "<tr><td>余额 ：</td><td>%.4f 元</td></tr>"
    "<tr><td>累积用气量 ：</td><td>%.4f 方气</td></tr>"
    "<tr><td>是否通气：</td><td>%s</td></tr>"
    "<tr><td>操作时间：</td><td>2026-11-18 06:37:06</td></tr>"
    "</table>"
)

MAIN_HTML = """
<div id="myHouse"><table><tbody>
<tr><td><input type="radio" name="uno" id="uno1" value="UNO000001|13800000001" checked></td>
<td><label for="uno1">UNO000001</label></td><td><label for="uno1">张**三</label></td>
<td><label for="uno1"><button class="queryGas" data-uno="UNO000001">查询</button></label></td></tr>
<tr><td></td><td colspan="3"><label for="uno1">地址: 某某区某某路1号101室</label></td></tr>
</tbody></table></div>
<div id="friendsHouse"><table><tbody>
<tr><td><input type="radio" name="uno" id="unos1" value="UNO000002|13900000002"></td>
<td><label for="unos1">UNO000002</label></td><td><label for="unos1">李**四</label></td>
<td><label for="unos1"><button class="queryGas" data-uno="UNO000002">查询</button></label></td></tr>
<tr><td></td><td colspan="3"><label for="unos1">地址: 某某区某某路2号202室</label></td></tr>
</tbody></table></div>
"""

RECHARGE_HTML = """
<table><thead><tr><th>用户编号</th><th>用户手机号</th><th>订单号</th><th>充值金额</th>
<th>账户余额</th><th>支付方式</th><th>支付结果</th><th>充值时间</th></tr></thead><tbody>
<tr><td>UNO000001</td><td>13800000001</td><td>ORDER1</td><td>￥200.00 元</td><td>￥980.1111 元</td>
<td>微信支付</td><td>交易完成</td><td>2026/11/6 19:50:06</td></tr>
<tr><td>UNO000001</td><td>13800000001</td><td>ORDER2</td><td>￥100.00 元</td><td>￥880.2222 元</td>
<td>微信支付</td><td>交易完成</td><td>2026/5/3 15:16:01</td></tr>
</tbody></table>
"""

LOGIN_PAGE = '<html><input id="inputPassword" type="password"></html>'
BASE_URL = "https://portal.invalid"


class FakePortal:
    """Table-driven stand-in for www.kynyyyt.com."""

    def __init__(self) -> None:
        self.password = "secret"
        self.login_ok = True
        self.logged_in = False
        self.logged_in_once = False
        self.fail_uno: str | None = None
        self.everything_fails = False
        self.waf_block = False
        self.balances = {
            "UNO000001": (123.4500, 45.67, "已通气"),
            "UNO000002": (67.8900, 12.34, "已通气"),
        }
        self.calls: list[tuple[str, str]] = []

    # -- helpers ---------------------------------------------------------
    def expire(self) -> None:
        """Drop the server-side session (cookie no longer valid)."""
        self.logged_in = False

    def login_calls(self) -> int:
        return sum(1 for method, path in self.calls if path == "/member/api/login.asp")

    def gas_calls(self) -> int:
        return sum(1 for method, path in self.calls if path == "/member/api/gas.asp")

    # -- request handling ------------------------------------------------
    async def request(self, method, path, *, data=None, referer=None):
        from custom_components.kuangyuan_gas.api import (
            KuangyuanAuthError,
            KuangyuanConnectionError,
            KuangyuanWafError,
        )

        self.calls.append((method, path))

        if self.waf_block:
            raise KuangyuanWafError("blocked by WAF")

        if path == "/member/api/login.asp":
            if not self.login_ok or (data or {}).get("pws") != self.password:
                return "0"
            self.logged_in = True
            self.logged_in_once = True
            return "1"

        if self.everything_fails:
            raise KuangyuanConnectionError("portal down")

        if not self.logged_in:
            return LOGIN_PAGE

        if path == "/member/main.asp":
            if not self.logged_in_once:
                return LOGIN_PAGE
            return MAIN_HTML

        if path == "/member/api/gas.asp":
            uno = (data or {}).get("uno", "")
            if uno == self.fail_uno:
                raise KuangyuanConnectionError("HTTP 500")
            balance, usage, status = self.balances[uno]
            return BALANCE_OK % (balance, usage, status)

        if path.startswith("/member/userrecharge.asp"):
            if "uno=UNO000001" in path:
                return RECHARGE_HTML
            return "<table><tbody></tbody></table>"

        raise KuangyuanConnectionError(f"unexpected path {path}")


@pytest.fixture
def portal():
    """A fresh fake portal per test."""
    return FakePortal()


def _patch_api(monkeypatch, portal: FakePortal) -> None:
    from custom_components.kuangyuan_gas.api import KuangyuanAPI

    async def fake_request(self, method, path, *, data=None, referer=None):
        # Raw recharge paths carry query strings; normalise for dispatch.
        target = path.split("?", 1)[0]
        query = path.split("?", 1)[1] if "?" in path else ""
        full = target + (f"?{query}" if query else "")
        if target == "/member/userrecharge.asp":
            return await portal.request(method, full, data=data, referer=referer)
        return await portal.request(method, target, data=data, referer=referer)

    monkeypatch.setattr(KuangyuanAPI, "_request", fake_request)


@pytest.fixture
def fake_portal(monkeypatch, portal):
    """Patch the integration HTTP layer and return the fake portal."""
    _patch_api(monkeypatch, portal)
    yield portal




# The Home Assistant pytest plugin blanket-bans sockets, which also breaks
# asyncio's own event-loop self-pipe on Windows.  These tests only use an
# in-process fake or 127.0.0.1, so the ban is neutralised here.
import contextlib  # noqa: E402

import pytest_socket  # noqa: E402


def _allow_sockets(*_args, **_kwargs):
    return contextlib.nullcontext()


pytest_socket.disable_socket = _allow_sockets



