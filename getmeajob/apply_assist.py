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
from urllib.parse import urlparse

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


def fill_page(page, applicant: dict, resume_path: Path, letter_text: str, letter_pdf: Path | None) -> dict:
    """Fill the plain fields on the current page. Returns {'filled': [...], 'needs': [...]}. Never submits."""
    values = values_for(applicant)
    filled, files = [], [f for f in page.evaluate(COLLECT) if f["type"] == "file"]
    only_one_file = len(files) == 1
    for field in page.evaluate(COLLECT):
        key = field_key(field)
        if key is None or field["value"] or (not field["visible"] and field["type"] != "file"):
            continue
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


def open_form_if_needed(page) -> None:
    """Some job pages show the description first; click only a plain 'Apply' link, never anything that submits."""
    if any(f["type"] in ("text", "email") and f["visible"] for f in page.evaluate(COLLECT)):
        return
    for role in ("link", "button"):
        candidate = page.get_by_role(role, name=APPLY_BUTTON)
        if candidate.count():
            candidate.first.click(); page.wait_for_load_state("domcontentloaded"); page.wait_for_timeout(1500)
            return


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


def assist(store, package_id: int, headless: bool = False, wait: bool = True, playwright_factory=None) -> dict:
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
            page = browser.new_page()
            page.goto(ready["url"], wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1500)
            open_form_if_needed(page)
            report = fill_page(page, applicant, resume_path, package["letter"], letter_pdf)
            banner(page, report)
            store.add_application(package_id, package["job_id"], "form_opened", report["filled"], report["needs"])
            if wait:
                try:
                    while not page.is_closed():
                        page.wait_for_timeout(1000)
                except Exception:
                    pass   # the window was closed
            browser.close()
    return report


def spawn(private_dir: Path, package_id: int, root: Path) -> None:
    """Start the assistant in its own process so the web page returns immediately."""
    log = open(root / "data" / "assist.log", "ab") if (root / "data").is_dir() else subprocess.DEVNULL
    subprocess.Popen([sys.executable, "-m", "getmeajob", "assist", "--package", str(int(package_id)), "--private", str(Path(private_dir).resolve())],
                     cwd=root, stdout=log, stderr=log, start_new_session=True)
