"""Permitted public discovery leads. Candidate extraction never verifies a source."""

from html import unescape
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from getmeajob.registry import Registry, elapsed
from getmeajob.sources import SourceError
from getmeajob.sources.http import BudgetExceeded
from getmeajob.sources.normalize import plain
from getmeajob.urls import PROVIDERS, board_url, detect, public_url


class Links(HTMLParser):
    def __init__(self, base: str, respect_nofollow: bool = True):
        super().__init__(convert_charrefs=True)
        self.base, self.urls, self.nofollow = base, [], False
        self.respect_nofollow = respect_nofollow

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and (attrs.get("name") or "").casefold() in {"robots", "getmeajob"}:
            self.nofollow |= "nofollow" in (attrs.get("content") or "").casefold()
        if tag not in {"a", "iframe"} or (self.respect_nofollow and "nofollow" in (attrs.get("rel") or "").casefold()):
            return
        href = attrs.get("href" if tag == "a" else "src")
        if href and len(self.urls) < 100:
            try:
                url = public_url(urljoin(self.base, href))
            except SourceError:
                return
            if url not in self.urls:
                self.urls.append(url)

    def extract(self, text: str) -> list[str]:
        self.feed(text)
        return [] if self.nofollow else self.urls


class PublicPages:
    def __init__(self, client, allowed_hosts: tuple[str, ...]):
        self.client, self.allowed_hosts = client, allowed_hosts
        self.robots = {}

    def read(self, url: str) -> str:
        url = public_url(url)
        p = urlsplit(url)
        if p.hostname not in self.allowed_hosts:
            raise SourceError("Career page hostname is not explicitly permitted in discovery.page_hosts.")
        if p.hostname not in self.robots:
            parser = RobotFileParser()
            robots_url = f"https://{p.hostname}/robots.txt"
            try:
                raw = self.client.read(robots_url, page_hosts=self.allowed_hosts, max_bytes=100000)
            except SourceError as exc:
                if getattr(exc.__cause__, "code", None) == 404:
                    raw = b""  # Explicit missing robots file; other failures remain fail-closed.
                else:
                    raise
            parser.parse(raw.decode("utf-8", errors="replace").splitlines())
            self.robots[p.hostname] = parser
        parser = self.robots[p.hostname]
        if not parser.can_fetch("GetMeAJob", url):
            raise SourceError("Career page disallowed by robots.txt.")
        delay = parser.crawl_delay("GetMeAJob") or 0
        rate = parser.request_rate("GetMeAJob")
        self.client.interval = max(self.client.interval, delay, rate.seconds / rate.requests if rate and rate.requests else 0)
        raw = self.client.read(url, page_hosts=self.allowed_hosts, max_bytes=1024 * 1024)
        return raw.decode("utf-8", errors="replace")


def discover_hn(registry: Registry, client, options, recheck: bool = False) -> dict:
    """Official HN API: latest whoishiring thread, bounded top-level employer comments."""
    client.source = "discovery:hn"
    base = "https://hacker-news.firebaseio.com/v0"
    story_id = options.hn_story_id
    if story_id is None:
        user = client.get(f"{base}/user/whoishiring.json")
        if not isinstance(user, dict) or not isinstance(user.get("submitted"), list):
            raise SourceError("Unexpected HN public-user schema.")
        for candidate in user["submitted"][:6]:
            if type(candidate) is not int or candidate <= 0:
                continue
            story = client.get(f"{base}/item/{candidate}.json")
            if isinstance(story, dict) and story.get("by") == "whoishiring" and isinstance(story.get("title"), str) and re.match(r"^Ask HN: Who is hiring\?", story.get("title", ""), re.I):
                story_id = candidate
                break
        if story_id is None:
            raise SourceError("No recent official Who is hiring thread found within the bounded lookup.")
    story = client.get(f"{base}/item/{story_id}.json")
    if (not isinstance(story, dict) or story.get("type") != "story" or story.get("by") != "whoishiring"
            or not isinstance(story.get("title"), str) or not re.match(r"^Ask HN: Who is hiring\?", story.get("title", ""), re.I)
            or story.get("dead") or story.get("deleted") or not isinstance(story.get("kids", []), list)):
        raise SourceError("HN story is not a public official Who is hiring thread.")
    processed, added, errors = 0, 0, []
    headers_found, links_found, ats_found = 0, 0, 0
    for item_id in story.get("kids", []):
        if type(item_id) is not int or item_id <= 0 or (not recheck and registry.seen(f"hn:{item_id}")):
            continue
        if processed >= options.max_items:
            break
        processed += 1
        try:
            item = client.get(f"{base}/item/{item_id}.json")
            if not isinstance(item, dict):
                raise SourceError("Unexpected HN item schema.")
            if item.get("type") == "comment" and item.get("parent") == story_id and not item.get("dead") and not item.get("deleted"):
                text = item.get("text")
                if isinstance(text, str):
                    # Only explicit pipe-delimited employer headers; no names invented from ATS tokens.
                    blocks = re.split(r"<p(?:\s[^>]*)?>|<br\s*/?>", text, flags=re.I)
                    header = next((plain(block) for block in blocks if plain(block).strip()), "")
                    name = header.split("|", 1)[0].strip()
                    if "|" in header and 1 <= len(name) <= 120 and not any(char in name for char in ("@", "\n")) and "http" not in name.casefold():
                        headers_found += 1
                        for url in Links(f"https://news.ycombinator.com/item?id={item_id}", respect_nofollow=False).extract(text):
                            links_found += 1
                            ats_found += bool(detect(url))
                            if detect(url) or re.search(r"career|/jobs(?:/|$)|myworkdayjobs", url, re.I):
                                before = registry.source_count()
                                registry.add(name, careers_url=url, method="hn-hiring-api", evidence=f"https://news.ycombinator.com/item?id={item_id}")
                                added += registry.source_count() - before
            registry.mark_seen(f"hn:{item_id}")
        except BudgetExceeded as exc:
            processed -= 1  # No comment/page request was issued once the budget was exhausted.
            errors.append(str(exc))
            break
        except SourceError as exc:
            errors.append(f"HN item {item_id}: {exc}")
    return {"thread": story_id, "comments_checked": processed, "employer_headers": headers_found, "links_examined": links_found, "ats_links_detected": ats_found, "new_sources": added, "errors": errors}


def discover(registry: Registry, settings, client, offline: bool = False, recheck: bool = False) -> dict:
    added, errors, hn_report = 0, [], None
    # Existing explicit configuration is seed evidence, never implicit verification.
    for board in settings.boards:
        before = registry.source_count()
        registry.add(board.employer, ats_url=board_url(board.source, board.token, board.region), method="configured-board")
        added += registry.source_count() - before
    if offline:
        return {"new_sources": added, "network": "disabled", "planned": "HN feed and permitted registry pages", "errors": []}
    if settings.discovery.hn_enabled:
        try:
            report = discover_hn(registry, client, settings.discovery, recheck=recheck)
            hn_report = report
            added += report["new_sources"]
            errors.extend(report["errors"])
        except SourceError as exc:
            errors.append(f"HN discovery: {exc}")
    pages = PublicPages(client, settings.discovery.page_hosts)
    processed = 0
    for row in registry.rows():
        if not registry.enabled(row) or row["provider"] in PROVIDERS or not row["careers_page"]:
            continue
        if row["next_check"] and not elapsed(row["next_check"], 0):
            continue
        if urlsplit(row["careers_page"]).hostname not in settings.discovery.page_hosts:
            if row["status"] != "UNSUPPORTED":
                registry.failure(row["id"], "Custom page recorded; hostname not permitted for fetching. Configure discovery.page_hosts after checking public access.", unsupported=True)
            continue
        if not recheck and registry.seen(f"page:{row['careers_page']}", settings.discovery.refresh_minutes * 60):
            continue
        if processed >= settings.discovery.max_items:
            break
        processed += 1
        client.source = f"page:{row['id']}"
        try:
            text = pages.read(row["careers_page"])
            found = 0
            for url in Links(row["careers_page"]).extract(text):
                is_ats = detect(url)
                if is_ats or (urlsplit(url).hostname == urlsplit(row["careers_page"]).hostname and re.search(r"career|/jobs(?:/|$)", url, re.I)):
                    before = registry.source_count()
                    registry.add(row["canonical_name"], careers_url=url, method="career-page-link", evidence=row["careers_page"])
                    added += registry.source_count() - before
                    found += bool(is_ats)
            registry.mark_seen(f"page:{row['careers_page']}")
            registry.failure(row["id"], "Custom page retained; " + ("ATS candidates extracted." if found else "no supported ATS link found."), unsupported=True)
        except BudgetExceeded as exc:
            processed -= 1  # No comment/page request was issued once the budget was exhausted.
            errors.append(str(exc))
            break
        except SourceError as exc:
            registry.failure(row["id"], str(exc), unsupported="not explicitly permitted" in str(exc) or "robots" in str(exc))
            errors.append(f"Page source {row['id']}: {exc}")
    return {"new_sources": added, "pages_checked": processed, "hn": hn_report, "errors": errors}
