"""Email-alert presets: a digest of new, best-matching stored jobs per saved filter preset.

Privacy and safety rules
- Only job postings, scores and your resume version label are ever emailed; never resume text
  or personal facts.
- Mail goes only from your own Gmail address to that same address.
- The Gmail app password lives in macOS Keychain, never in a file, setting or log.
- Every posting is emailed at most once per preset. Job text is escaped; links must be http(s).
- Nothing here applies to a job or contacts anyone else.
"""
from dataclasses import dataclass, field, replace
from email.message import EmailMessage
from html import escape
import fcntl
import json
from contextlib import contextmanager
from pathlib import Path
import re
import smtplib
import subprocess
from urllib.parse import urlparse

from getmeajob import jobfilters, jobsquery, openness
from getmeajob.sources.http import BudgetExceeded
from getmeajob.resume.intelligence import match, stored_weights
from getmeajob.sources.leadlists import LABELS as LEAD_LABELS

KEYCHAIN_SERVICE = "getmeajob-gmail"
DIGEST_SIZE = 30            # postings per email; the rest roll over to the next digest
ADDRESS_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@(?:gmail|googlemail)\.com$")
PLACEHOLDERS = {"you", "your_name", "yourname", "your.name", "youremail", "your_email", "name", "example", "user"}


def valid_address(address: str) -> bool:
    """A plausible own Gmail address; example text such as YOU@gmail.com is rejected."""
    return bool(ADDRESS_PATTERN.match(address)) and address.split("@")[0].casefold() not in PLACEHOLDERS


@dataclass
class Digest:
    preset: dict
    items: list = field(default_factory=list)   # dicts: row, score, summary, resume
    total_new: int = 0
    notes: list = field(default_factory=list)
    dropped_closed: int = 0


def safe_link(*urls):
    for url in urls:
        if url and urlparse(url).scheme in ("http", "https"):
            return url
    return None


@contextmanager
def send_lock(directory: Path):
    """One notification send at a time (scheduled refresh, `notify send`, `search --notify`). Yields False if another is running."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(directory / "notify.lock", "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False; return
        yield True


def preset_filters(preset: dict) -> jobfilters.JobFilters:
    filters = jobfilters.load_preferences(preset["filters"])
    return replace(filters, sort="match")


def build_digests(job_db: Path, store, scorer=match, limit: int = DIGEST_SIZE, only: int | None = None) -> list[Digest]:
    """Compute the unsent top matches for every enabled preset. Writes nothing."""
    weights = stored_weights(store.get_setting("weights"))
    digests = []
    for preset in store.presets():
        if not preset["enabled"] or (only is not None and preset["id"] != only):
            continue
        digest = Digest(preset)
        filters = preset_filters(preset)
        if not jobfilters.is_narrow(filters):   # a preset with no real filter would email everything
            digest.notes.append(jobfilters.NARROW_MESSAGE)
            digests.append(digest); continue
        sent = store.sent_ids(preset["id"])
        try:
            result = jobsquery.run(job_db, store, filters, weights, scorer, 0, limit=limit, exclude=frozenset(sent))
        except jobfilters.FilterError as exc:
            digest.notes.append(f"Preset skipped: {exc}")
            digests.append(digest); continue
        digest.total_new = result.total
        if not any("No resume has verified" in note for note in result.notices):
            for row in result.rows:
                resume = result.best.get(row.id)
                found = result.matches.get((row.id, resume["id"])) if resume else None
                if row.score is None or not found:
                    continue
                digest.items.append({"row": row, "score": row.score, "summary": found["summary"], "resume": resume["label"]})
        else:
            digest.notes.append("No resume version has verified facts, so matches cannot be ranked. Verify a resume first.")
        if result.notices and not digest.notes:
            digest.notes.extend(result.notices)
        digests.append(digest)
    return digests


API_SOURCES = {"greenhouse", "lever", "ashby"}


def screen_closed(store, digests: list[Digest], checker) -> None:
    """Check list-sourced postings (not just refreshed from an employer's own board) and drop the ones found closed."""
    live = None   # `checker` may be a Checker or a function that builds one; it is only built if a check is needed
    for digest in digests:
        kept = []
        stopped = False
        for item in digest.items:
            job = item["row"].job
            if stopped or job.source in API_SOURCES:
                kept.append(item); continue
            if live is None:
                live = checker if hasattr(checker, "check") else checker()
            try:
                result = openness.ensure_check(store, live, item["row"].id, job.application_url)
            except BudgetExceeded:
                stopped = True; kept.append(item); continue
            item["open"] = result
            if result.status == "CLOSED":
                digest.dropped_closed += 1
            else:
                kept.append(item)
        digest.items = kept
        if digest.dropped_closed:
            digest.notes.append(f"{digest.dropped_closed} posting(s) left out because they appear to be closed.")


def render(digest: Digest) -> tuple[str, str, str]:
    """Return (subject, plain text, html). Everything from a posting is untrusted and escaped."""
    name = digest.preset["name"]
    count = len(digest.items)
    more = digest.total_new - count
    subject = f"GetMeAJob: {count} new match{'es' if count != 1 else ''} for {name}"
    text = [f"{count} new posting(s) for your preset \"{name}\", best match first.",
            "These are stored postings, not live listings: verify each is still open before applying.", ""]
    body = [f"<h2>{escape(name)}</h2><p>{count} new posting(s), best match first. Stored postings, not live listings: "
            "verify each is still open before applying.</p>"]
    for number, item in enumerate(digest.items, 1):
        row, job = item["row"], item["row"].job
        link = safe_link(job.original_url, job.application_url)
        where = "; ".join(job.locations) or "Unknown location"
        seen = item.get("open")
        status = ("Confirmed open when checked" if seen and seen.status == "OPEN" else "Could not confirm it is still open" if seen else "")
        text += [f"{number}. {item['score']}%  {job.role} — {job.employer}", f"   {where} | {row.field} | {LEAD_LABELS.get(job.source, job.source.title())}" + (f" | {status}" if status else ""),
                 f"   {item['summary']}", f"   {link or '(no link available)'}", ""]
        title = escape(job.role)
        title = f'<a href="{escape(link, quote=True)}">{title}</a>' if link else title
        body.append(f"<p><b>{item['score']}%</b> · {title} — {escape(job.employer)}<br>"
                    f"<small>{escape(where)} · {escape(row.field)} · {escape(LEAD_LABELS.get(job.source, job.source.title()))}{(' · ' + status) if status else ''}<br>{escape(item['summary'])}</small></p>")
    if more > 0:
        text.append(f"{more} more matching posting(s) will be in the next digest.")
        body.append(f"<p>{more} more matching posting(s) will be in the next digest.</p>")
    text.append("Best resume version used for scoring: " + (digest.items[0]["resume"] if digest.items else "none"))
    return subject, "\n".join(text), "<html><body style=\"font-family:system-ui\">" + "".join(body) + "</body></html>"


def keychain_password(address: str, runner=subprocess.run) -> str:
    done = runner(["security", "find-generic-password", "-a", address, "-s", KEYCHAIN_SERVICE, "-w"],
                  capture_output=True, text=True, timeout=20)
    if done.returncode != 0 or not done.stdout.strip():
        raise RuntimeError("No Gmail app password in Keychain. Run: .venv/bin/python -m getmeajob notify setup --address YOU@gmail.com")
    return done.stdout.strip().replace(" ", "")   # Google shows app passwords in groups of four


def gmail_sender(address: str, password_provider=keychain_password, smtp=smtplib.SMTP_SSL):
    """Return send(subject, text, html). Sends only from and to the configured Gmail address."""
    if not valid_address(address):
        raise ValueError("Use your own real @gmail.com address (not the example text).")

    def send(subject: str, text: str, html: str | None = None) -> None:
        message = EmailMessage()
        message["From"] = message["To"] = address
        message["Subject"] = re.sub(r"[\r\n]+", " ", subject)[:200]
        message.set_content(text)
        if html:
            message.add_alternative(html, subtype="html")
        with smtp("smtp.gmail.com", 465, timeout=30) as server:
            server.login(address, password_provider(address))
            server.send_message(message)
    return send


def write_previews(digests: list[Digest], directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    paths = []
    for digest in digests:
        if not digest.items:
            continue
        subject, text, html = render(digest)
        path = directory / (re.sub(r"[^A-Za-z0-9_-]+", "-", digest.preset["name"]).strip("-") or "preset")
        path = path.with_suffix(".html")
        path.write_text(html, encoding="utf-8"); path.chmod(0o600)
        paths.append(path)
    return paths


def send_digests(store, digests: list[Digest], send) -> dict:
    """Send one email per digest with new postings; mark them sent only after a successful send."""
    report = {"emails": 0, "postings": 0, "errors": []}
    for digest in digests:
        if not digest.items:
            continue
        subject, text, html = render(digest)
        try:
            send(subject, text, html)
        except Exception as exc:   # network/login problems must not lose or repeat postings
            report["errors"].append(f"{digest.preset['name']}: {type(exc).__name__}")
            continue
        store.mark_sent(digest.preset["id"], [item["row"].id for item in digest.items])
        report["emails"] += 1; report["postings"] += len(digest.items)
    return report
