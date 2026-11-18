"""Parser tests that do not need a running Home Assistant instance."""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "kuangyuan_gas"


def _install_stubs() -> None:
    """Provide minimal stubs so api.py imports without Home Assistant."""
    if "aiohttp" not in sys.modules:
        aiohttp = types.ModuleType("aiohttp")

        class ClientError(Exception):
            pass

        class ClientSession:  # pragma: no cover - never used here
            pass

        class ClientTimeout:  # pragma: no cover - never used here
            def __init__(self, *args, **kwargs):
                pass

        aiohttp.ClientError = ClientError
        aiohttp.ClientSession = ClientSession
        aiohttp.ClientTimeout = ClientTimeout
        sys.modules["aiohttp"] = aiohttp


def _load_package():
    """Import the integration folder as a package named ``ky``."""
    _install_stubs()
    if "ky" in sys.modules:
        return importlib.import_module("ky.api")
    spec = importlib.util.spec_from_file_location(
        "ky",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    pkg = importlib.util.module_from_spec(spec)
    pkg.__path__ = [str(ROOT)]
    sys.modules["ky"] = pkg
    # Load const/api only: __init__.py itself needs Home Assistant.
    return importlib.import_module("ky.api")


api = _load_package()

BALANCE_HTML = (
    "<table class='uTable'>"
    "<tr><td>余额 ：</td><td>123.4500 元</td></tr>"
    "<tr><td>累积用气量 ：</td><td>45.6700 方气</td></tr>"
    "<tr><td>是否通气：</td><td>已通气</td></tr>"
    "<tr><td>操作时间：</td><td>2026-11-18 06:37:06</td></tr>"
    "</table>"
    "<div class=\"alert alert-info\" role=\"alert\">点击上面的阴影区域可关闭当前信息!</div>"
)

MAIN_HTML = """
<div id="myHouse">
<table><tbody>
<tr><td><input type="radio" name="uno" id="uno1" value="UNO000001|13800000001" checked></td>
<td><label for="uno1">UNO000001</label></td><td><label for="uno1">张**三</label></td>
<td><label for="uno1"><button type="button" class="btn btn-success btn-sm queryGas" data-uno="UNO000001">查询</button></label></td></tr>
<tr><td></td><td colspan="3"><label for="uno1" style="font-weight: 400;color: #078CD7;">地址: 某某区某某路1号101室</label></td></tr>
</tbody></table>
</div>
<div id="friendsHouse">
<table><tbody>
<tr><td><input type="radio" name="uno" id="unos1" value="UNO000002|13900000002"></td>
<td><label for="unos1">UNO000002</label></td><td><label for="unos1">李**四</label></td>
<td><label for="unos1"><button type="button" class="btn btn-success btn-sm queryGas" data-uno="UNO000002">查询</button></label></td></tr>
<tr><td></td><td colspan="3"><label for="unos1" style="font-weight: 400;color: #078CD7;">地址: 某某区某某路2号202室</label></td></tr>
</tbody></table>
</div>
"""

RECHARGE_HTML = """
<table class="table table-striped"><thead><tr><th>用户编号</th><th>用户手机号</th><th>订单号</th><th>充值金额</th><th>账户余额</th><th>支付方式</th><th>支付结果</th><th>充值时间</th></tr></thead>
<tbody>
<tr><td>UNO000001</td><td>13800000001</td><td>SYNTHETICORDER0000000001</td><td>￥200.00 元</td><td>￥980.1111 元</td><td>微信支付</td><td>交易完成</td><td>2026/11/6 19:50:06</td></tr>
<tr><td>UNO000002</td><td>13900000002</td><td>SYNTHETICORDER0000000002</td><td>￥50.00 元</td><td>￥70.3333 元</td><td>微信支付</td><td>交易完成</td><td>2026/8/3 17:02:08</td></tr>
<tr><td>UNO000001</td><td>13800000001</td><td>SYNTHETICORDER0000000003</td><td>￥100.00 元</td><td>￥880.2222 元</td><td>微信支付</td><td>交易完成</td><td>2026/5/3 15:16:01</td></tr>
</tbody></table>
"""


def test_parse_balance():
    info = api.parse_balance(BALANCE_HTML, "UNO000001")
    assert info.uno == "UNO000001"
    assert info.balance == 123.4500, info.balance
    assert info.usage == 45.67, info.usage
    assert info.status == "已通气"
    assert info.supplied is True
    assert info.operated_at is not None and info.operated_at.year == 2026
    assert set(info.raw) == {"余额", "累积用气量", "是否通气", "操作时间"}, info.raw


def test_parse_balance_without_balance_raises():
    try:
        api.parse_balance("<table class='uTable'><tr><td>错误</td><td>1</td></tr></table>", "X")
    except api.KuangyuanDataError:
        return
    raise AssertionError("expected KuangyuanDataError")


def test_parse_accounts():
    accounts = api.parse_accounts(MAIN_HTML)
    unos = [a.uno for a in accounts]
    assert unos == ["UNO000001", "UNO000002"], unos
    first, second = accounts
    assert first.tel == "13800000001"
    assert first.owner == "张**三", first.owner
    assert first.address == "某某区某某路1号101室", first.address
    assert first.category == "我家", first.category
    assert second.category == "亲友家", second.category
    assert second.address == "某某区某某路2号202室", second.address


def test_parse_accounts_dedup():
    accounts = api.parse_accounts(MAIN_HTML + MAIN_HTML)
    assert len(accounts) == 2, [a.uno for a in accounts]


def test_parse_recharges():
    rows = api.parse_recharges(RECHARGE_HTML)
    assert len(rows) == 3, len(rows)
    assert rows[0].uno == "UNO000001"
    assert rows[0].amount == 200.0
    assert rows[0].balance_after == 980.1111
    assert rows[0].pay_method == "微信支付"
    assert rows[0].pay_result == "交易完成"
    assert rows[0].paid_at is not None and rows[0].paid_at.month == 11


def test_parse_recharges_ignores_garbage():
    assert api.parse_recharges("<table><tbody><tr><td colspan='5'>请点击筛选</td></tr></tbody></table>") == []


def test_login_page_detection():
    assert api.looks_like_login_page('<input id="inputPassword" type="password">')
    assert api.looks_like_login_page("会员登录")
    assert not api.looks_like_login_page(MAIN_HTML)


def test_number_parsing():
    assert api._to_number("123.4500 元") == 123.4500
    assert api._to_number("1,234.50 元") == 1234.50
    assert api._to_number("无") is None


def test_decode_gb18030():
    raw = "余额 ：300.00 元".encode("gb18030")
    assert "余额" in api._decode(raw, None)
