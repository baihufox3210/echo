import ipaddress
import re
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urlsplit, urlunsplit

USER_AGENT = "Mozilla/5.0 (compatible; EchoBot/1.0; +https://github.com/baihufox3210/echo)"
CACHE_MAX_ENTRIES = 256


class TTLCache:
    def __init__(self, ttl: float, max_entries: int = CACHE_MAX_ENTRIES):
        self.ttl = ttl
        self.max_entries = max_entries
        self._data: OrderedDict[Any, tuple[float, Any]] = OrderedDict()

    def get(self, key: Any) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        if time.monotonic() - item[0] > self.ttl:
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return item[1]

    def set(self, key: Any, value: Any) -> None:
        if self.ttl <= 0:
            return
        self._data[key] = (time.monotonic(), value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)


def normalize_url(url: str) -> str:
    """去除 fragment、追蹤參數與結尾斜線，用於結果去重。"""
    parts = urlsplit(url)
    query = "&".join(
        p for p in parts.query.split("&")
        if p and not p.lower().startswith(("utm_", "fbclid=", "gclid="))
    )
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), query, ""))


def truncate(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def clean_domain(domain: str) -> str:
    domain = domain.strip().lower()
    domain = re.sub(r"^[a-z]+://", "", domain)
    return domain.split("/")[0].removeprefix("www.")


def hostname(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def host_matches(url: str, domain: str) -> bool:
    host = hostname(url).removeprefix("www.")
    return host == domain or host.endswith("." + domain)


def same_site(url: str, other: str) -> bool:
    return hostname(url).removeprefix("www.") == hostname(other).removeprefix("www.")
