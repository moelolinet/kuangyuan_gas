"""HTTP client for the Kuangyuan Energy customer portal (www.kynyyyt.com).

Only read-only endpoints are used:

* ``POST /member/api/login.asp``  -> ``1`` on success
* ``GET  /member/main.asp``       -> bound gas accounts (uno / owner / address)
* ``POST /member/api/gas.asp``    -> balance block for one uno
* ``GET  /member/userrecharge.asp`` -> recharge history

The portal is an ASP application behind a cloud WAF: requests must look like
the browser ones it serves, the body may come back gzip encoded, and frequent
logins are rate limited.  Everything here therefore keeps a private cookie jar,
reuses an existing session, and retries a login only once.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from typing import Any
from urllib.parse import urljoin

import aiohttp

from .const import DEFAULT_BASE_URL, REQUEST_GAP_SECONDS

_LOGGER = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
)

LOGIN_PATH = "/member/api/login.asp"
MAIN_PATH = "/member/main.asp"
GAS_PATH = "/member/api/gas.asp"
RECHARGE_PATH = "/member/userrecharge.asp"

_TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_TD_RE = re.compile(r"<td\b[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
_LABEL_RE = re.compile(r"<label\b[^>]*>(.*?)</label>", re.IGNORECASE | re.DOTALL)
_UNO_INPUT_RE = re.compile(
    r"""<input\b[^>]*name\s*=\s*["']uno["'][^>]*>""", re.IGNORECASE
)
_VALUE_ATTR_RE = re.compile(
    r"""\bvalue\s*=\s*["']([^"']*)["']""", re.IGNORECASE
)
_GAS_ROW_RE = re.compile(
    r"<td\b[^>]*>(.*?)</td>\s*<td\b[^>]*>(.*?)</td>",
    re.IGNORECASE | re.DOTALL,
)
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")

CATEGORY_HOME = "我家"
CATEGORY_FRIEND = "亲友家"


class KuangyuanError(Exception):
    """Base error for this integration."""


class KuangyuanAuthError(KuangyuanError):
    """The username/password combination was rejected."""


class KuangyuanConnectionError(KuangyuanError):
    """The portal could not be reached."""


class KuangyuanSessionExpired(KuangyuanError):
    """The cookie session is no longer accepted by the portal."""


class KuangyuanWafError(KuangyuanError):
    """The portal blocked the request (rate limit / WAF challenge)."""


class KuangyuanDataError(KuangyuanError):
    """The portal answered, but the payload could not be understood."""


@dataclass(slots=True)
class GasAccount:
    """One bound gas meter (用户编号) of the signed-in customer."""

    uno: str
    tel: str = ""
    owner: str = ""
    address: str = ""
    category: str = ""


@dataclass(slots=True)
class BalanceInfo:
    """Result of one balance query."""

    uno: str
    balance: float | None = None
    balance_text: str = ""
    usage: float | None = None
    usage_text: str = ""
    status: str = ""
    operated_at: datetime | None = None
    raw: dict[str, str] = field(default_factory=dict)

    @property
    def supplied(self) -> bool:
        """Whether the meter is connected to gas."""
        return "通气" in self.status and "未通气" not in self.status


@dataclass(slots=True)
class RechargeRecord:
    """One row of the recharge history."""

    uno: str
    tel: str
    order_id: str
    amount: float | None
    balance_after: float | None
    pay_method: str
    pay_result: str
    paid_at: datetime | None


def create_session() -> aiohttp.ClientSession:
    """Create a private HTTP session for one customer account.

    Home Assistant's ``async_create_clientsession`` shares the connector and
    the cookie jar with the whole instance.  That is wrong here: the portal
    authenticates with a cookie, so two config entries would exchange their
    session cookies and could read each other's meters.  A dedicated jar also
    keeps the login state after a Home Assistant restart is not needed - the
    entry simply logs in again.
    """
    connector = aiohttp.TCPConnector(
        # A threaded resolver avoids the optional aiodns dependency, which
        # only works with a selector event loop (not the default one on
        # Windows).  One DNS lookup per poll is cheap.
        resolver=aiohttp.ThreadedResolver(),
    )
    return aiohttp.ClientSession(
        connector=connector,
        cookie_jar=aiohttp.CookieJar(unsafe=True),
        timeout=aiohttp.ClientTimeout(total=40),
        headers={"User-Agent": USER_AGENT},
    )


def clean_text(value: str) -> str:
    """Strip tags/entities/whitespace from an HTML fragment."""
    value = re.sub(r"<[^>]+>", " ", value)
    value = value.replace("\xa0", " ")
    value = unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def _to_number(value: str) -> float | None:
    """Return the first number of a text, ignoring thousands separators."""
    match = _NUMBER_RE.search(value.replace(",", ""))
    return float(match.group(0)) if match else None


def _parse_datetime(value: str) -> datetime | None:
    """Parse the portal timestamps without inventing a timezone.

    The portal reports local wall-clock time in Asia/Shanghai.  ``None`` is
    returned for an unexpected format instead of guessing.
    """
    text = value.strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _decode(payload: bytes, charset: str | None) -> str:
    """Decode a portal response, which may be GBK on legacy pages."""
    candidates: list[str] = []
    if charset:
        candidates.append(charset)
    candidates.extend(["utf-8", "gb18030"])
    for encoding in candidates:
        try:
            return payload.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return payload.decode("utf-8", errors="replace")


def looks_like_login_page(html: str) -> bool:
    """Distinguish the login form from a signed-in page."""
    return (
        'id="inputPassword"' in html
        or 'name="ac" value="login"' in html
        or "会员登录" in html
    )


def parse_accounts(html: str) -> list[GasAccount]:
    """Extract the bound meters listed on ``main.asp``.

    Each meter is a row holding ``<input name="uno" value="UNO|PHONE">``; the
    address sits in the label of the row below it.  Rows are grouped by the
    "我家"/"亲友家" tab pane they belong to.
    """
    home_pos = html.find('id="myHouse"')
    friend_pos = html.find('id="friendsHouse"')
    panes: list[tuple[int, str]] = []
    if home_pos >= 0:
        panes.append((home_pos, CATEGORY_HOME))
    if friend_pos >= 0:
        panes.append((friend_pos, CATEGORY_FRIEND))
    panes.sort()

    def category_at(position: int) -> str:
        current = ""
        for start, name in panes:
            if position >= start:
                current = name
            else:
                break
        return current

    rows = _TR_RE.findall(html)
    accounts: dict[str, GasAccount] = {}
    pending_row: tuple[str, GasAccount] | None = None

    for row in rows:
        match = _UNO_INPUT_RE.search(row)
        if match:
            value_match = _VALUE_ATTR_RE.search(match.group(0))
            if not value_match:
                continue
            parts = value_match.group(1).split("|")
            uno = unescape(parts[0]).strip()
            if not uno or uno in accounts:
                continue
            cells = [_td_clean(cell) for cell in _TD_RE.findall(row)]
            owner = ""
            for cell in cells[1:]:
                if not cell or cell == uno or cell == "查询" or "地址" in cell:
                    continue
                owner = cell
                break
            account = GasAccount(
                uno=uno,
                tel=unescape(parts[1]).strip() if len(parts) > 1 else "",
                owner=owner,
                category=category_at(html.find(row)),
            )
            accounts[uno] = account
            pending_row = (uno, account)
            continue

        if pending_row is not None and "地址" in row:
            uno, account = pending_row
            for label in _LABEL_RE.findall(row):
                text = clean_text(label)
                if "地址" in text:
                    account.address = re.sub(r"^地址\s*[:：]\s*", "", text).strip()
                    break
            pending_row = None

    result = list(accounts.values())
    for account in result:
        if not account.address:
            account.address = _address_from_gas_page(html, account.uno)
    return result


def _address_from_gas_page(html: str, uno: str) -> str:
    """Fallback: ``gas.asp`` lists the address in its own table cell."""
    index = html.find(uno)
    if index < 0:
        return ""
    window = html[index : index + 4000]
    match = re.search(r"地址\s*[:：]\s*([^<\r\n]+)", window)
    return clean_text(match.group(1)) if match else ""


def _td_clean(fragment: str) -> str:
    return clean_text(fragment)


def parse_balance(html: str, uno: str) -> BalanceInfo:
    """Parse the ``uTable`` fragment returned by ``gas.asp?ac=query``."""
    raw: dict[str, str] = {}
    for label, value in _GAS_ROW_RE.findall(html):
        key = clean_text(label).rstrip(":： ").strip()
        if key:
            raw[key] = clean_text(value)
    if not raw:
        raise KuangyuanDataError("余额查询没有返回可识别的数据")

    def pick(*needles: str) -> str:
        for key, value in raw.items():
            if any(needle in key for needle in needles):
                return value
        return ""

    balance_text = pick("余额")
    if balance_text == "":
        raise KuangyuanDataError("余额查询结果中缺少余额字段")

    usage_text = pick("累积用气量", "用气量")
    operated = pick("操作时间", "时间")
    return BalanceInfo(
        uno=uno,
        balance=_to_number(balance_text),
        balance_text=balance_text,
        usage=_to_number(usage_text),
        usage_text=usage_text,
        status=pick("是否通气", "通气"),
        operated_at=_parse_datetime(operated),
        raw=raw,
    )


def parse_recharges(html: str) -> list[RechargeRecord]:
    """Parse the recharge history table (newest first)."""
    records: list[RechargeRecord] = []
    for row in _TR_RE.findall(html):
        cells = [_td_clean(cell) for cell in _TD_RE.findall(row)]
        if len(cells) < 8:
            continue
        uno = cells[0]
        if not uno or not re.fullmatch(r"[A-Za-z0-9]{4,32}", uno):
            continue
        records.append(
            RechargeRecord(
                uno=uno,
                tel=cells[1],
                order_id=cells[2],
                amount=_to_number(cells[3]),
                balance_after=_to_number(cells[4]),
                pay_method=cells[5],
                pay_result=cells[6],
                paid_at=_parse_datetime(cells[7]),
            )
        )
    return records


class KuangyuanAPI:
    """Minimal read-only client for the Kuangyuan customer portal."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._base_url = base_url.rstrip("/")
        self._logged_in = False
        self._lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        return self._base_url

    def _url(self, path: str) -> str:
        return urljoin(self._base_url + "/", path.lstrip("/"))

    def _headers(self, referer: str) -> dict[str, str]:
        return {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Cache-Control": "no-cache",
            "Origin": self._base_url,
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, str] | None = None,
        referer: str | None = None,
    ) -> str:
        url = self._url(path)
        headers = self._headers(referer or self._url(MAIN_PATH))
        try:
            async with self._session.request(
                method,
                url,
                data=data,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
                allow_redirects=True,
            ) as response:
                payload = await response.read()
                status = response.status
                charset = response.charset
        except asyncio.TimeoutError as err:
            raise KuangyuanConnectionError("连接营业厅超时") from err
        except aiohttp.ClientError as err:
            raise KuangyuanConnectionError(f"连接营业厅失败: {err}") from err

        text = _decode(payload, charset)
        if status in (403, 429) or "CloudWAF" in text[:2000]:
            raise KuangyuanWafError("营业厅风控拦截了请求，请稍后再试")
        if status >= 400:
            raise KuangyuanConnectionError(f"营业厅返回 HTTP {status}")
        return text

    async def async_login(self) -> None:
        """Sign in and keep the session cookies in the private jar."""
        text = await self._request(
            "POST",
            LOGIN_PATH,
            data={
                "utel": self._username,
                "pws": self._password,
                "rdm": "true",
                "ac": "login",
            },
            referer=self._url("/member/"),
        )
        result = text.strip()
        if result == "1":
            self._logged_in = True
            return
        if result in ("0", ""):
            raise KuangyuanAuthError("手机号或密码不正确")
        if "<html" in result.lower() or "login" in result.lower():
            raise KuangyuanWafError("登录被营业厅拦截，请稍后重试")
        raise KuangyuanAuthError(f"登录返回了未知内容: {result[:120]}")

    async def async_ensure_login(self) -> None:
        """Log in unless a session is already established."""
        async with self._lock:
            if self._logged_in:
                return
            await self.async_login()

    async def async_get_accounts(self) -> list[GasAccount]:
        """Return every gas meter bound to the signed-in customer."""
        async with self._lock:
            if not self._logged_in:
                await self.async_login()
            html = await self._request("GET", MAIN_PATH)
            if looks_like_login_page(html):
                self._logged_in = False
                await self.async_login()
                html = await self._request("GET", MAIN_PATH)
                if looks_like_login_page(html):
                    raise KuangyuanAuthError("登录后仍然返回登录页")
            accounts = parse_accounts(html)
            if not accounts:
                raise KuangyuanDataError("账号下没有查询到任何燃气户号")
            return accounts

    async def async_query_balance(self, uno: str) -> BalanceInfo:
        """Query the balance of one meter (read-only operation)."""
        await self.async_ensure_login()
        text = await self._request(
            "POST",
            GAS_PATH,
            data={"ac": "query", "uno": uno},
            referer=self._url(MAIN_PATH),
        )
        if looks_like_login_page(text):
            self._logged_in = False
            raise KuangyuanSessionExpired("登录状态已失效")
        await self._gap()
        return parse_balance(text, uno)

    async def async_get_recharges(
        self, uno: str, pages: int = 1
    ) -> list[RechargeRecord]:
        """Return the recharge history of one meter, newest first."""
        await self.async_ensure_login()
        records: list[RechargeRecord] = []
        seen: set[str] = set()
        for page in range(1, max(1, pages) + 1):
            text = await self._request(
                "GET",
                f"{RECHARGE_PATH}?uno={uno}&page={page}",
                referer=self._url(RECHARGE_PATH),
            )
            if looks_like_login_page(text):
                self._logged_in = False
                raise KuangyuanSessionExpired("登录状态已失效")
            page_records = [row for row in parse_recharges(text) if row.uno == uno]
            fresh = [
                row
                for row in page_records
                if not row.order_id or row.order_id not in seen
            ]
            for row in fresh:
                if row.order_id:
                    seen.add(row.order_id)
            records.extend(fresh)
            if len(page_records) < 8 or not fresh:
                break
            await self._gap()
        records.sort(key=lambda row: row.paid_at or datetime.min, reverse=True)
        return records

    async def async_invalidate(self) -> None:
        """Forget the cached login state (used after an auth failure)."""
        self._logged_in = False

    async def async_close(self) -> None:
        """Release the private HTTP session owned by this client."""
        if not self._session.closed:
            await self._session.close()

    @staticmethod
    async def _gap() -> None:
        """Small pause between portal calls to stay below WAF limits."""
        await asyncio.sleep(REQUEST_GAP_SECONDS)



