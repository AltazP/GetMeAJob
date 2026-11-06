"""Apply assistant: opens an application form in your own Chrome and fills the plain fields.

It NEVER submits. It fills only what you supplied (Applicant details page), attaches your resume
and cover letter, then leaves the window open so you can read everything, answer the remaining
questions and press Submit yourself. It does not touch CAPTCHAs, logins, demographic or
eligibility questions, and it only opens the public application forms of Greenhouse, Lever and Ashby.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse, urlsplit

ALLOWED_HOSTS = {"boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co", "jobs.eu.lever.co", "jobs.ashbyhq.com"}
APPLICANT_FIELDS = {"first_name": 60, "last_name": 60, "email": 120, "phone": 40, "city": 80, "linkedin": 200, "website": 200}
REQUIRED_FIELDS = ("first_name", "last_name", "email")

# First matching rule wins. Text is the lower-cased label + name + id + placeholder of the field.
RULES = (
    ("cover_letter", r"cover.?letter"),
    ("email", r"e-?mail"),
    ("phone", r"phone|mobile|telephone"),
    ("first_name", r"first.?name|given.?name"),
    ("last_name", r"last.?name|surname|family.?name"),
    ("full_name", r"^name\b|full.?name|your.?name"),
    ("linkedin", r"linkedin"),
    ("website", r"website|portfolio|personal.?site|github"),
    ("city", r"\b(city|location)\b"),
)
NEVER_FILL = re.compile(r"company|employer|school|university|salary|compensation|visa|sponsor|authori[sz]|gender|race|ethnic|veteran|disab|pronoun|preferred|referr|how did you hear|start date|notice", re.I)
APPLY_BUTTON = re.compile(r"^(apply|apply now|apply for this job|apply for this position)$", re.I)

COLLECT = """() => [...document.querySelectorAll('input,textarea,select')].map((el, i) => {
  el.dataset.gmj = String(i);
  const labels = [...(el.labels || [])].map(l => l.innerText).join(' ');
  const wrapper = el.closest('div,li,fieldset');
  const nearby = wrapper && wrapper.querySelector('label,legend') ? wrapper.querySelector('label,legend').innerText : '';
  const style = getComputedStyle(el);
  return {i, tag: el.tagName.toLowerCase(), type: (el.type || '').toLowerCase(),
    // A neighbouring label is used only when the field has no label of its own (avoids mixing up first/last name).
    text: (labels || el.getAttribute('aria-label') || el.placeholder
           ? [labels, el.getAttribute('aria-label'), el.placeholder, el.name, el.id] : [el.name, el.id, nearby]).filter(Boolean).join(' ').toLowerCase(),
    required: el.required || el.getAttribute('aria-required') === 'true',
    value: el.type === 'file' ? (el.files.length ? 'file' : '') : (el.value || ''),
    combobox: el.getAttribute('role') === 'combobox' || !!el.getAttribute('aria-autocomplete') || !!el.getAttribute('list'),
    visible: el.type === 'file' || (style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null)};
})"""


def check_url(url: object) -> str:
    """Only https pages on the supported application hosts; anything else is refused."""
    parsed = urlparse(url if isinstance(url, str) else "")
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("This posting's application page is not on a supported site (Greenhouse, Lever or Ashby), so open it yourself.")
    return url


def apply_url(job: dict) -> str:
    """The address of the real application form for a stored job (a dict from Job.to_dict()).

    Many companies show Greenhouse jobs on their own site. Greenhouse's official embed address
    serves the same form for the job's own board name and numeric ID, both taken from the
    source data. Anything else on an unsupported host is refused.
    """
    try:
        return check_url(job.get("application_url"))
    except ValueError:
        board, ident = str(job.get("board", "")), str(job.get("source_id", ""))
        if job.get("source") == "greenhouse" and re.fullmatch(r"[A-Za-z0-9_-]{1,60}", board) and re.fullmatch(r"\d{1,12}", ident):
            return f"https://job-boards.greenhouse.io/embed/job_app?for={board}&token={ident}"
        raise


def validate_applicant(data: dict) -> dict:
    clean = {}
    for key, limit in APPLICANT_FIELDS.items():
        value = re.sub(r"[\x00-\x1f\x7f]", " ", str(data.get(key, ""))).strip()
        if len(value) > limit:
            raise ValueError(f"{key.replace('_', ' ').title()} is too long.")
        clean[key] = value
    for key in REQUIRED_FIELDS:
        if not clean[key]:
            raise ValueError(f"{key.replace('_', ' ').title()} is required.")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", clean["email"]):
        raise ValueError("Enter a valid email address.")
    for key in ("linkedin", "website"):
        if clean[key] and not clean[key].startswith("https://"):
            raise ValueError(f"{key.title()} must start with https://")
    return clean


def field_key(field: dict) -> str | None:
    """Pure rule: which applicant value (if any) belongs in this field. Unknown fields stay empty."""
    text, kind = field["text"], field["type"]
    if kind in ("hidden", "checkbox", "radio", "submit", "button", "password", "search"):
        return None
    if kind == "file":
        if re.search(r"cover", text):
            return "cover_letter_file"
        return "resume_file" if re.search(r"resume|cv|curriculum", text) else "file_unlabeled"
    if NEVER_FILL.search(text) or field["tag"] == "select":
        return None
    if field["tag"] == "textarea":
        return "cover_letter" if re.search(r"cover.?letter", text) else None
    if kind == "email":
        return "email"
    if kind == "tel":
        return "phone"
    if field["combobox"]:
        return None   # custom dropdowns/autocompletes need a person
    for key, pattern in RULES:
        if key != "cover_letter" and re.search(pattern, text):
            return key
    return None


def values_for(applicant: dict) -> dict:
    values = dict(applicant)
    values["full_name"] = f"{applicant['first_name']} {applicant['last_name']}".strip()
    return values


def fill_page(page, applicant: dict, resume_path: Path, letter_text: str, letter_pdf: Path | None, url_ok=check_url) -> dict:
    """Fill the plain fields on the current page. Returns {'filled': [...], 'needs': [...]}. Never submits."""
    guard(page, url_ok)
    values = values_for(applicant)
    filled, files = [], [f for f in page.evaluate(COLLECT) if f["type"] == "file"]
    only_one_file = len(files) == 1
    for field in page.evaluate(COLLECT):
        key = field_key(field)
        if key is None or field["value"] or (not field["visible"] and field["type"] != "file"):
            continue
        guard(page, url_ok)   # a late redirect must never receive personal data
        target = page.locator(f'[data-gmj="{field["i"]}"]')
        try:
            if key in ("resume_file", "file_unlabeled"):
                if key == "file_unlabeled" and not only_one_file:
                    continue
                target.set_input_files(str(resume_path)); filled.append("Resume")
            elif key == "cover_letter_file":
                if letter_pdf:
                    target.set_input_files(str(letter_pdf)); filled.append("Cover letter (file)")
            elif key == "cover_letter":
                target.fill(letter_text[:30000]); filled.append("Cover letter (text)")
            elif values.get(key):
                target.fill(values[key]); filled.append(key.replace("_", " ").title())
        except Exception:
            continue   # a field that will not take input is simply left for the person
    needs = []
    for field in page.evaluate(COLLECT):
        if field["required"] and field["visible"] and not field["value"] and field["type"] not in ("hidden", "checkbox", "radio", "submit", "button"):
            label = re.sub(r"\s+", " ", field["text"]).strip()[:80] or "(unnamed required field)"
            if label not in needs:
                needs.append(label)
    return {"filled": filled, "needs": needs}


# While we fill, nothing on the page may submit the form: uploads and typing fire the page's own handlers
# (change/input), and a hostile or sloppy page could submit from one. The block is lifted at human handoff.
BLOCK_SUBMIT = """(() => {
  window.__gmjBlock = true; window.__gmjBlocked = 0;
  const hit = () => { window.__gmjBlocked += 1; };
  // The block ends only when a real person interacts (a trusted pointer/key event), never on a timer or a script event.
  const release = e => { if (e.isTrusted && window.__gmjBlock) { window.__gmjBlock = false; } };   // soft, page-visible layer only
  for (const type of ['pointerdown', 'keydown', 'touchstart']) window.addEventListener(type, release, true);
  window.addEventListener('submit', e => { if (window.__gmjBlock) { e.preventDefault(); e.stopImmediatePropagation(); hit(); } }, true);
  for (const name of ['submit', 'requestSubmit']) {
    const original = HTMLFormElement.prototype[name];
    HTMLFormElement.prototype[name] = function (...args) { if (window.__gmjBlock) { hit(); return; } return original.apply(this, args); };
  }
})()"""
SUBMIT_REQUEST = re.compile(r"(?i)(?:/|=)(?:apply|applications?|submit\w*|apisubmit\w*)(?:[/?&]|$)")
MUTATIONS = {"POST", "PUT", "PATCH", "DELETE"}


UPLOAD_HANDLE_OPERATION = re.compile(r"^(?:Api)?Create\w*(?:Upload|File)\w*Handle$")
SUBMIT_WORDS = re.compile(r"(?i)submit|createapplication|applyfor")


def graphql_read_or_upload(request) -> bool:
    """True only when EVERY operation in the request is a read, or an explicit 'create upload handle' mutation.

    Single-page sites such as Ashby load their forms with GraphQL reads sent as POST. Any other mutation, any
    submit-style operation name, an unparseable body that mentions a mutation, and any batch containing one bad
    operation are refused. (If a site names its upload operation differently, the file simply fails to attach and is
    left for the person; that is the safe direction.)
    """
    parts = urlsplit(request.url)
    if "graphql" not in parts.path.lower():
        return False
    body = request.post_data or ""
    url_names = parse_qs(parts.query).get("op", [])
    try:
        data = json.loads(body)
    except ValueError:
        return not re.search(r"(?i)\bmutation\b", body) and not any(SUBMIT_WORDS.search(n) for n in url_names)
    for item in data if isinstance(data, list) else [data]:
        if not isinstance(item, dict):
            return False
        name = str(item.get("operationName") or (url_names[0] if len(url_names) == 1 else ""))
        query = str(item.get("query") or "")
        if re.search(r"(?i)\bmutation\b", query):
            if not UPLOAD_HANDLE_OPERATION.fullmatch(name) or SUBMIT_WORDS.search(query):
                return False
        elif SUBMIT_WORDS.search(name):
            return False
    return True
# While filling, every write request is refused except these reviewed kinds: uploads on the same site,
# presigned uploads to cloud storage, and the CAPTCHA widget (which the person solves).
UPLOAD_PATH = re.compile(r"(?i)upload|presign|attachment|resume|\bparse\b")
STORAGE_HOSTS = (".amazonaws.com", "storage.googleapis.com")
CAPTCHA_HOSTS = {"www.google.com", "www.recaptcha.net"}


def captcha_traffic(request, same_site: bool) -> bool:
    """CAPTCHA and bot-check plumbing (reCAPTCHA, hCaptcha, Cloudflare). It carries no personal data and the person still
    solves the CAPTCHA, but blocking it can stop the widget from starting, so it passes through untouched."""
    target = urlsplit(request.url); host = target.hostname or ""
    return ((host in CAPTCHA_HOSTS and "/recaptcha/" in target.path) or host == "hcaptcha.com" or host.endswith(".hcaptcha.com")
            or host == "challenges.cloudflare.com" or (same_site and target.path.startswith("/cdn-cgi/")))


GESTURE_JS = """(() => {
  const go = e => { if (e.isTrusted && typeof gmjRelease === 'function') gmjRelease('1'); };
  for (const type of ['pointerdown', 'keydown', 'touchstart']) window.addEventListener(type, go, true);
})()"""


def watch_for_person(page, state: dict) -> None:
    """Tell Python when a real person first touches the page.

    The listener runs in a Chrome *isolated world*: it sees genuine (trusted) clicks and keys, but the page's own scripts can
    neither see it nor call the binding, so a page cannot lift the request guard itself. Lifting the page-visible submit
    block (BLOCK_SUBMIT) is only a soft layer; this is the hard one.
    """
    session = page.context.new_cdp_session(page)
    session.on("Runtime.bindingCalled", lambda event: state.update(released=True) if event.get("name") == "gmjRelease" else None)
    session.send("Page.enable"); session.send("Runtime.enable")
    session.send("Page.addScriptToEvaluateOnNewDocument", {"source": GESTURE_JS, "worldName": "gmj-isolated"})
    session.send("Runtime.addBinding", {"name": "gmjRelease", "executionContextName": "gmj-isolated"})


def block_submission(page) -> None:
    page.add_init_script(BLOCK_SUBMIT)


def stopped_submissions(page) -> int:
    """How many submission attempts the page made (and we stopped) while filling. Does not lift the block."""
    try:
        return int(page.evaluate("() => window.__gmjBlocked || 0"))
    except Exception:
        return 0


class UnsafeDestination(ValueError):
    """The browser ended up somewhere other than a supported application page; nothing was filled."""


FORM_CONTROLS = {"text", "email", "tel", "file", "textarea", "select", "url", "number", "search", "password"}
LINKS = """() => [...document.querySelectorAll('a[href]')].filter(a => !a.closest('form')).map(a => ({text: (a.innerText || a.getAttribute('aria-label') || '').trim(), href: a.href}))"""


def guard(page, url_ok=check_url) -> None:
    """Raise unless the page is currently on a supported application address (checked before every fill/upload)."""
    try:
        url_ok(page.url)
    except ValueError:
        raise UnsafeDestination("The browser left the supported application sites, so nothing was filled.") from None


def open_form_if_needed(page, url_ok=check_url) -> None:
    """Some job pages show the description first. Follow a plain 'Apply' LINK to a supported address.

    Nothing is ever clicked: a button (possibly a submit button) is never used, and a link is followed
    by navigating to its already-validated address. A page that already has form controls is left alone.
    """
    if any(f["type"] in FORM_CONTROLS or f["tag"] in ("textarea", "select") for f in page.evaluate(COLLECT)):
        return
    for link in page.evaluate(LINKS):
        if APPLY_BUTTON.match(link["text"] or ""):
            try:
                target = url_ok(link["href"])
            except ValueError:
                continue   # an Apply link to an unsupported site: leave it for the person
            page.goto(target, wait_until="domcontentloaded", timeout=45000); page.wait_for_timeout(1500)
            return


def navigation_guard(page, url_ok=check_url):
    """Stop the page from reaching an unsupported address, or submitting, while we work.

    - Top-level page loads are fetched without following redirects and every hop is validated before the browser follows it.
    - Every write request is refused unless it is a reviewed upload kind; permitted uploads are also fetched without
      following redirects, so an upload endpoint cannot forward data elsewhere.
    - The guard stays on until the person's first real click or keypress (seen from an isolated world the page cannot reach),
      so neither a delayed script nor a hostile page can lift it. `remove()` lifts it explicitly (tests, cleanup).
    """
    blocked: list[str] = []
    state = {"released": False}
    watch_for_person(page, state)

    def handle(route):
        request = route.request
        if state["released"]:
            route.continue_(); return
        permitted_write = False
        if request.method in MUTATIONS:
            target = urlsplit(request.url)
            where = f"{target.path}?{target.query}"
            same_site = target.netloc == urlsplit(page.url).netloc
            navigation = request.is_navigation_request() and request.frame == page.main_frame
            if not navigation and ((same_site and graphql_read_or_upload(request)) or captcha_traffic(request, same_site)):
                route.continue_(); return
            permitted_write = not navigation and not SUBMIT_REQUEST.search(where) and (
                (same_site and UPLOAD_PATH.search(where)) or (target.hostname or "").endswith(STORAGE_HOSTS))
            if not permitted_write:
                # Same-site or native-form attempts are reported to the person; third-party analytics blocks stay silent.
                blocked.append(f"{request.method} {target.path}" if same_site or navigation else f"third-party {request.method} {target.hostname}")
                route.abort(); return
        if not permitted_write and not (request.is_navigation_request() and request.frame == page.main_frame):
            route.continue_(); return
        try:
            if not permitted_write:
                url_ok(request.url)
            response = route.fetch(max_redirects=0)
            location = response.headers.get("location")
            if 300 <= response.status < 400 and location:
                if permitted_write:
                    raise ValueError("an upload endpoint answered with a redirect")
                url_ok(urljoin(request.url, location))
        except Exception:   # unsupported address, redirecting upload, network error or anything unexpected: do not load it
            blocked.append(request.url if not permitted_write else f"redirecting upload {urlsplit(request.url).path}")
            route.abort(); return
        route.fulfill(response=response)
    page.route("**/*", handle)

    def remove():
        state["released"] = True
        try:
            page.unroute("**/*", handle)
        except Exception:
            pass
    remove.blocked = blocked
    remove.state = state
    return remove


def banner(page, report: dict) -> None:
    message = f"GetMeAJob filled {len(report['filled'])} field(s). Review everything, answer the remaining questions, then press Submit yourself."
    page.evaluate("""(text) => { const d = document.createElement('div'); d.textContent = text;
        d.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;background:#125d65;color:#fff;padding:8px 14px;font:15px system-ui';
        document.body.appendChild(d); }""", message)


def prepare(store, package_id: int) -> dict:
    """Everything that must be true before a browser opens. Raises ValueError with a plain message."""
    package = store.get_package(package_id)
    if package["status"] != "reviewed":
        raise ValueError("Mark this package as Reviewed first. The assistant only works on packages you have approved.")
    applicant = store.get_setting("applicant")
    if not applicant:
        raise ValueError("Save your Applicant details first.")
    url = apply_url(package["payload"].get("job") or {"application_url": package["payload"].get("original_url")})
    return {"package": package, "applicant": validate_applicant(json.loads(applicant)), "url": url}


def assist(store, package_id: int, headless: bool = False, wait: bool = True, playwright_factory=None, url_ok=check_url) -> dict:
    ready = prepare(store, package_id)
    package, applicant = ready["package"], ready["applicant"]
    resume = store.resume(package["resume_id"])
    from getmeajob.resume.documents import export
    if playwright_factory is None:
        from playwright.sync_api import sync_playwright as playwright_factory
    with tempfile.TemporaryDirectory(prefix="getmeajob-") as folder:
        folder = Path(folder); folder.chmod(0o700)
        suffix = Path(resume["filename"]).suffix.lower() if Path(resume["filename"]).suffix.lower() in (".pdf", ".docx") else ".pdf"
        resume_path = folder / f"Resume{suffix}"; resume_path.write_bytes(resume["content"])
        letter_pdf = folder / "Cover Letter.pdf"; letter_pdf.write_bytes(export(package["letter"], "pdf")[0])
        with playwright_factory() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=headless)
            try:
                page = browser.new_page()
                block_submission(page)
                remove_guard = navigation_guard(page, url_ok)
                try:
                    page.goto(ready["url"], wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(1500)
                    guard(page, url_ok)
                    open_form_if_needed(page, url_ok)
                    guard(page, url_ok)
                    report = fill_page(page, applicant, resume_path, package["letter"], letter_pdf, url_ok)
                except UnsafeDestination as exc:
                    store.add_application(package_id, package["job_id"], "blocked", [], [str(exc)])
                    raise
                except Exception:
                    if any(item.startswith("http") for item in remove_guard.blocked):   # our guard stopped a navigation, which surfaces as a browser error
                        reason = "The page tried to leave the supported application sites, so nothing was filled."
                        store.add_application(package_id, package["job_id"], "blocked", [], [reason])
                        raise UnsafeDestination(reason) from None
                    raise
                stopped = stopped_submissions(page) + sum(1 for item in remove_guard.blocked if not item.startswith(("http", "third-party")))
                if stopped:
                    report["needs"].append("A script on the page tried to submit the form while it was being filled; that was blocked. Review the whole form before you submit.")
                banner(page, report)
                store.add_application(package_id, package["job_id"], "form_opened", report["filled"], report["needs"])
                if wait:
                    try:
                        while not page.is_closed():
                            page.wait_for_timeout(1000)
                    except Exception:
                        pass   # the window was closed
            finally:
                browser.close()
    return report


def spawn(private_dir: Path, package_id: int, root: Path) -> None:
    """Start the assistant in its own process so the web page returns immediately."""
    argv = [sys.executable, "-m", "getmeajob", "assist", "--package", str(int(package_id)), "--private", str(Path(private_dir).resolve())]
    if (root / "data").is_dir():
        with open(root / "data" / "assist.log", "ab") as log:   # the child keeps its own copy; the parent must not leak the handle
            subprocess.Popen(argv, cwd=root, stdout=log, stderr=log, start_new_session=True)
    else:
        subprocess.Popen(argv, cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
