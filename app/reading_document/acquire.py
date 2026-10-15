"""Version-pinned, bounded, robots-aware acquisition; no automatic retry/crawl."""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urljoin
from urllib.robotparser import RobotFileParser

import httpx

AGENT = "knowledge-growth-RDR01"
ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})v[1-9]\d*\Z")


class AcquisitionError(Exception):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status


def official_url(url):
    try:
        parts = urlsplit(url)
        valid = (parts.scheme == "https" and parts.hostname == "arxiv.org"
                 and not parts.username and not parts.password and parts.port in (None, 443))
    except ValueError:
        valid = False
    if not valid:
        raise AcquisitionError("unsafe_url", f"Only https://arxiv.org is allowed: {url}")
    return url


def identity(value):
    value = value.strip()
    if "://" in value:
        official_url(value)
        parts = urlsplit(value)
        if parts.query or parts.fragment:
            raise AcquisitionError("invalid_input", "Input URL must not have a query/fragment")
        match = re.fullmatch(r"/(?:abs|html|pdf)/(.+?)(?:\.pdf)?", parts.path)
        value = match.group(1) if match else ""
    if not ID.fullmatch(value):
        raise AcquisitionError("version_unknown", "An explicit arXiv ID with vN is required")
    return value


def digest(data):
    return hashlib.sha256(data).hexdigest()


@dataclass
class Resource:
    requested_url: str
    resolved_url: str
    content: bytes
    content_type: str


class Fetcher:
    def __init__(self, client=None, interval=3.0):
        self.client = client or httpx.Client(timeout=30, follow_redirects=False,
                                             headers={"User-Agent": AGENT})
        self.interval = interval
        self.last_request = 0.0
        self.robots = None
        self.events = []
        self.total_bytes = 0

    def close(self):
        self.client.close()

    def _get(self, url, limit):
        requested = url
        for _ in range(6):
            official_url(url)  # validate BEFORE every request, including redirects
            pause = max(0, self.interval - (time.monotonic() - self.last_request))
            if pause:
                time.sleep(pause)
            self.last_request = time.monotonic()
            try:
                with self.client.stream("GET", url, follow_redirects=False,
                                        headers={"User-Agent": AGENT}) as response:
                    self.events.append({"url": url, "status": response.status_code})
                    if response.status_code in (301, 302, 303, 307, 308):
                        url = urljoin(url, response.headers.get("location", ""))
                        official_url(url)
                        if self.robots and not self.robots.can_fetch(AGENT, url):
                            raise AcquisitionError("robots_denied", url)
                        continue
                    code = response.status_code
                    if code != 200:
                        status = {403: "forbidden", 429: "rate_limited", 404: "html_unavailable",
                                  410: "html_unavailable"}.get(code, "temporary_failure" if code >= 500 else "http_error")
                        raise AcquisitionError(status, f"HTTP {code}: {url}; no retry")
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        self.total_bytes += len(chunk)
                        if size > limit or self.total_bytes > 150_000_000:
                            raise AcquisitionError("size_limit", "Snapshot download limit exceeded")
                        chunks.append(chunk)
                    return Resource(requested, str(response.url), b"".join(chunks),
                                    response.headers.get("content-type", "").split(";")[0].lower())
            except httpx.HTTPError as error:
                raise AcquisitionError("temporary_failure", type(error).__name__) from error
        raise AcquisitionError("redirect_limit", requested)

    def fetch(self, url, *, asset=False):
        official_url(url)
        if self.robots is None:
            try:
                resource = self._get("https://arxiv.org/robots.txt", 1_000_000)
                if resource.content_type != "text/plain":
                    raise AcquisitionError("robots_unavailable", "robots.txt is not plain text")
            except AcquisitionError as error:
                raise AcquisitionError("robots_unavailable", str(error)) from error
            self.robots = RobotFileParser()
            self.robots.parse(resource.content.decode("utf-8", errors="replace").splitlines())
            self.interval = max(self.interval, self.robots.crawl_delay(AGENT) or 0)
            if self.interval > 60:
                raise AcquisitionError("robots_delay", "Crawl delay exceeds runner wait budget")
        if not self.robots.can_fetch(AGENT, url):
            raise AcquisitionError("robots_denied", url)
        return self._get(url, 15_000_000 if asset else 20_000_000)
