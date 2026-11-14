"""Verify every number in a summary against facts.json.

1. Normalise: Arabic-Indic / Persian digits -> 0-9, Arabic decimal/thousands/percent signs, unicode minus.
2. Mask strings that appear verbatim in facts.json (product names, SKUs, dates, ISO week) so digits inside
   them ("5x8ml", "2026-11-02") are not read as numbers. Dates / SKUs NOT in facts are reported.
3. Extract numbers (1,234.5 / 1.2k / 11.5 ألف / 12% / -3) and match each against the numeric facts:
   it matches when it equals the fact (sign ignored) rounded to the precision written.
4. Direction check: if a matched fact is a change (it has a precomputed up/down/flat direction), the
   direction claimed for that number (explicit +/- sign, else the nearest direction word in its clause)
   must not contradict it. A number passes if it matches a change with a consistent direction, or matches
   a non-change fact. Otherwise it fails ("not in facts" or "direction contradicts").

Known limitation: a wrong number can still pass if it happens to equal an unrelated fact.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal

DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
CHAR_MAP = {"٫": ".", "٬": ",", "٪": "%", "−": "-", " ": " ", " ": " ",
            "‎": "", "‏": "", "؜": "", "⁦": "", "⁧": "", "⁨": "", "⁩": ""}
MULTIPLIERS = {"k": 1000, "K": 1000, "ألف": 1000, "آلاف": 1000, "الف": 1000, "m": 1_000_000, "M": 1_000_000,
               "مليون": 1_000_000}

NUMBER_RE = re.compile(
    r"(?<![\d.,\w])(?P<sign>[+-])?(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<dec>\d+))?"
    r"(?:\s?(?P<suffix>k|K|m|M|ألف|آلاف|الف|مليون)(?!\w))?(?P<pct>\s?%)?"
)
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
ISO_WEEK_RE = re.compile(r"\b\d{4}-W\d{2}\b")
SKU_RE = re.compile(r"\b[A-Z]{3}-\d{3}\b")
BULLET_RE = re.compile(r"^\s*(?:[-•*·]|\d+[.)])\s+")
CLAUSE_SPLIT_RE = re.compile(r"[.;!?؛\n]|,\s|،|\bwhile\b|\bbut\b|\bwhereas\b|\band\b|\bwith\b|بينما|لكن|في حين|مقابل")

_AR_PREFIX = r"(?:[وفبل]|ال|بال|وال|و?ب?ال)?"
DIRECTION_WORDS = {
    "up": [r"\b(?:rose|rise|rises|risen|rising|increased|increase|increases|increasing|up|grew|grow|grows|growth|"
           r"higher|gained|gain|climbed|jumped|improved)\b",
           rf"(?<!\w){_AR_PREFIX}(?:ارتفع|ارتفعت|ارتفاع|ارتفاعا|زاد|زادت|زيادة|نما|نمت|نمو|صعد|صعدت|تحسن|تحسّن)(?!\w)"],
    "down": [r"\b(?:fell|fall|falls|fallen|falling|decreased|decrease|decreases|decreasing|decline|declined|declines|"
             r"down|dropped|drop|drops|lower|shrank|shrunk|slipped|slid|worsened)\b",
             rf"(?<!\w){_AR_PREFIX}(?:انخفض|انخفضت|انخفاض|انخفاضا|تراجع|تراجعت|قل|قلّ|قلت|قلّت|هبط|هبطت|هبوط|نقص|نقصت)(?!\w)"],
}
DIRECTION_RES = {d: [re.compile(p) for p in pats] for d, pats in DIRECTION_WORDS.items()}
TOLERANCE_EPS = Decimal("0.000001")


@dataclass(frozen=True)
class Fact:
    value: Decimal
    path: str
    is_pct: bool
    direction: str | None  # set when the fact is part of a change


@dataclass
class NumberCheck:
    text: str
    value: str
    ok: bool
    matched: str | None = None
    reason: str | None = None


@dataclass
class Verification:
    passed: bool
    numbers: list[NumberCheck] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def failures(self) -> list[NumberCheck]:
        return [n for n in self.numbers if not n.ok]

    def as_log(self) -> dict:
        return {
            "passed": self.passed,
            "numbers_checked": len(self.numbers),
            "unmatched_numbers": [{"text": n.text, "reason": n.reason} for n in self.failures],
            "problems": self.problems,
        }


def normalize(text: str) -> str:
    text = text.translate(DIGITS)
    for src, dst in CHAR_MAP.items():
        text = text.replace(src, dst)
    return text


def flatten_facts(facts: object) -> tuple[list[Fact], set[str]]:
    numbers: list[Fact] = []
    strings: set[str] = set()

    def walk(node: object, path: str, unit: str | None, direction: str | None) -> None:
        if isinstance(node, dict):
            unit = node.get("unit", unit) if isinstance(node.get("unit"), str) else unit
            own_direction = node.get("direction") if isinstance(node.get("direction"), str) else None
            for key, value in node.items():
                child_dir = own_direction if own_direction else None
                if isinstance(value, bool):
                    continue
                if isinstance(value, (int, float)):
                    is_pct = "pct" in key or (unit == "pct" and key in ("this_week", "previous_week", "abs"))
                    numbers.append(Fact(Decimal(str(value)), f"{path}.{key}".lstrip("."), is_pct, child_dir))
                else:
                    walk(value, f"{path}.{key}".lstrip("."), unit, None)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]", unit, direction)
        elif isinstance(node, str):
            strings.add(node)

    walk(facts, "", None, None)
    return numbers, strings


def parse_bullets(text: str) -> list[str]:
    lines = [ln.strip() for ln in normalize(text).splitlines() if ln.strip()]
    bullets = [BULLET_RE.sub("", ln, count=1).strip() for ln in lines if BULLET_RE.match(ln)]
    return bullets or lines


def _claimed_direction(clause: str, sign: str | None, pos: int) -> str | None:
    if sign == "-":
        return "down"
    if sign == "+":
        return "up"
    best: tuple[int, str] | None = None
    for direction, patterns in DIRECTION_RES.items():
        for pattern in patterns:
            for m in pattern.finditer(clause):
                distance = min(abs(m.start() - pos), abs(m.end() - pos))
                if best is None or distance < best[0]:
                    best = (distance, direction)
    return best[1] if best else None


def _clause_bounds(text: str, pos: int) -> tuple[int, int]:
    start, end = 0, len(text)
    for m in CLAUSE_SPLIT_RE.finditer(text):
        if m.end() <= pos:
            start = m.end()
        elif m.start() >= pos:
            end = m.start()
            break
    return start, end


def check_numbers(text: str, facts: dict) -> list[NumberCheck]:
    numbers, strings = flatten_facts(facts)
    text = normalize(text)
    checks: list[NumberCheck] = []

    # Mask fact strings that contain digits (product names, SKUs, dates, ISO week), longest first.
    masked = text
    for s in sorted((s for s in strings if any(c.isdigit() for c in s) and len(s) >= 3), key=len, reverse=True):
        masked = masked.replace(s, "§" * len(s))
    for regex, kind in ((DATE_RE, "date"), (ISO_WEEK_RE, "ISO week"), (SKU_RE, "SKU")):
        for m in regex.finditer(masked):
            checks.append(NumberCheck(m.group(0), m.group(0), False, reason=f"{kind} not in facts"))
            masked = masked[:m.start()] + "§" * len(m.group(0)) + masked[m.end():]

    for m in NUMBER_RE.finditer(masked):
        digits = m.group("int").replace(",", "")
        dec = m.group("dec") or ""
        written = Decimal(f"{digits}.{dec}" if dec else digits)
        mult = Decimal(MULTIPLIERS.get(m.group("suffix") or "", 1))
        target = written * mult
        half_step = (Decimal(1).scaleb(-len(dec)) * mult) / 2 + TOLERANCE_EPS
        is_pct = bool(m.group("pct"))
        candidates = [f for f in numbers if abs(abs(f.value) - target) <= half_step and (f.is_pct or not is_pct)]
        start, end = _clause_bounds(masked, m.start())
        claimed = _claimed_direction(masked[start:end], m.group("sign"), m.start() - start)
        consistent = [f for f in candidates if f.direction and (claimed is None or claimed == f.direction)]
        levels = [f for f in candidates if not f.direction]
        changes = [f for f in candidates if f.direction]
        token = m.group(0).strip()
        if consistent:
            checks.append(NumberCheck(token, str(target), True, matched=consistent[0].path))
        elif levels:
            checks.append(NumberCheck(token, str(target), True, matched=levels[0].path))
        elif changes:
            checks.append(NumberCheck(token, str(target), False, matched=changes[0].path,
                                      reason=f"direction contradicts facts ({changes[0].path} is "
                                             f"{changes[0].direction}, text says {claimed})"))
        else:
            checks.append(NumberCheck(token, str(target), False, reason="not found in facts.json"))
    return checks


def verify_summary(text: str, facts: dict, min_bullets: int = 4, max_bullets: int = 6,
                   action_label: str | None = None) -> Verification:
    bullets = parse_bullets(text)
    problems = []
    if not min_bullets <= len(bullets) <= max_bullets:
        problems.append(f"expected {min_bullets}-{max_bullets} bullets, got {len(bullets)}")
    if action_label and sum(1 for b in bullets if b.startswith(action_label)) != 1:
        problems.append(f"expected exactly one bullet starting with {action_label!r}")
    numbers = check_numbers("\n".join(bullets), facts)
    return Verification(passed=not problems and all(n.ok for n in numbers), numbers=numbers, problems=problems)
