"""Repair text pulled from PDFs or pasted with broken line breaks, so it reads as a normal letter.

Two common breakages: a blank line after every word ("shredded" text) and lines wrapped in the
middle of sentences. Wording is never changed, only spacing and paragraph breaks. Idempotent.
"""
import re
import statistics

GREETING = re.compile(r"^((?:Dear|Hello|Hi|To whom)[^,\n]{0,70},)\s*", re.I)
SIGNOFF = re.compile(r"\b(Sincerely|Best regards|Kind regards|Warm regards|Regards|Respectfully|Yours truly|Yours sincerely|Thank you|Thanks|Best),?\s+"
                     r"((?:[A-Z][\w.'-]*\s?){1,4})$")
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")
MAX_PARAGRAPH_WORDS = 85


def _paragraphs_from_run(run: str) -> list[str]:
    """Rebuild greeting / body paragraphs / sign-off from one long run of words."""
    run = re.sub(r"\s+", " ", run).strip()
    parts: list[str] = []
    greeting = GREETING.search(run)
    if greeting:
        header = run[:greeting.start()].strip()
        if header:
            parts.append(header)   # date / address block before the greeting stays as one line
        parts.append(greeting.group(1)); run = run[greeting.end():]
    closing = SIGNOFF.search(run)
    tail = None
    if closing:
        tail = f"{closing.group(1)},\n{closing.group(2).strip()}"
        run = run[:closing.start()].strip()
    paragraph: list[str] = []; words = 0
    for sentence in SENTENCE_BREAK.split(run):
        paragraph.append(sentence); words += len(sentence.split())
        if words >= MAX_PARAGRAPH_WORDS or len(paragraph) >= 4:
            parts.append(" ".join(paragraph)); paragraph, words = [], 0
    if paragraph:
        parts.append(" ".join(paragraph))
    if tail:
        parts.append(tail)
    return parts


def reflow(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text).replace("\u00a0", " ").replace("\u200b", "").strip()
    blocks = [b for b in re.split(r"\n\s*\n", text) if b.strip()]
    if not blocks:
        return ""
    sizes = [len(b.split()) for b in blocks]
    if len(blocks) > 8 and statistics.median(sizes) <= 3:
        return "\n\n".join(_paragraphs_from_run(" ".join(b.strip() for b in blocks)))
    fixed = []
    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        # Join wrapped prose lines; keep short lines (addresses, sign-off) as they are.
        if len(lines) > 1 and statistics.mean(len(line) for line in lines) > 40:
            fixed.append(re.sub(r"\s{2,}", " ", " ".join(lines)))
        else:
            fixed.append("\n".join(lines))
    return "\n\n".join(fixed)
