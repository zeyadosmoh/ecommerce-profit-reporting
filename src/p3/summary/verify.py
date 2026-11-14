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
5. Unit check: margin changes are percentage points (facts unit "pp"). A number written with % / percent /
   ٪ / بالمئة must match a percent fact; one written with pp / percentage points / نقطة مئوية must match a
   pp fact. Writing a pp change as % fails.
6. Per bullet: a causal claim (helped, due to, بسبب, ساهم, ...) passes only if it cites a profit driver
   (a number matched to facts.profit_drivers with a consistent direction, and a consistent helped/hurt word
   if one is used) or is hedged (possible, may, محتمل, ربما, قد). A definitional statement about the
   missing-cost exclusion ("excluded because SKUs have no cost row") is not a causal claim. The summary must also mention the top insight's key number, and the action bullet must name a SKU
   from facts or a concrete lever.

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
CLAUSE_SPLIT_RE = re.compile(r"\.(?!\d)|[;!?؛\n]|,\s|،|\bwhile\b|\bbut\b|\bwhereas\b|\band\b|\bwith\b|بينما|لكن|في حين|مقابل")

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

# Unit written right after a number (searched at the end of the number match).
PCT_WORDS_RE = re.compile(r"\s*(?:%|percent\b|per cent\b|بالمئة|بالمائة|في المئة|في المائة)")
CURRENCY_AFTER_RE = re.compile(r"\s*(?:SAR\b|ريال|ر\.س)")
CURRENCY_BEFORE_RE = re.compile(r"(?:\bSAR|ريال|ر\.س\.?)\s*$")
PP_WORDS_RE = re.compile(r"\s*(?:pp\b|p\.p\.|percentage[- ]points?\b|points?\b|pts\b|"
                         r"نقطة مئوية|نقاط مئوية|نقطتين مئويتين|نقطة|نقاط)")

_AR_P = r"(?:[وف])?"
CAUSE_RES = [
    re.compile(r"\b(?:helped|helps|help|hurt|hurts|due to|because|driven by|drove|caused|causes|led to|leads to|"
               r"thanks to|resulted in|results in|as a result of)\b", re.IGNORECASE),
    re.compile(rf"(?<!\w){_AR_P}(?:بسبب|نتيجة|أدى|أدت|ساهم|ساهمت|بفضل|أثر|أثّر|أثرت|أسهم|أسهمت)(?!\w)"),
]
HEDGE_RES = [
    re.compile(r"\b(?:possible|possibly|may|might|likely)\b", re.IGNORECASE),
    re.compile(rf"(?<!\w){_AR_P}(?:محتمل|محتملة|محتملاً|ربما|قد)(?!\w)"),
]
# Polarity of a cause word, checked against a cited driver's effect on net profit.
HELP_RES = [re.compile(r"\b(?:helped|helps|help|boosted|boost|supported|lifted|offset|added|thanks to)\b", re.I),
            re.compile(rf"(?<!\w){_AR_P}(?:بفضل|دعم|دعمت|عزز|عززت|خفف|خففت)(?!\w)")]
HURT_RES = [re.compile(r"\b(?:hurt|hurts|dragged|weighed|squeezed|eroded|cut into)\b", re.I),
            re.compile(rf"(?<!\w){_AR_P}(?:أضر|أضرت|ضغط|ضغطت)(?!\w)|أثر سلب|أثرت سلب")]
# "Revenue was excluded because SKUs have no cost row": a definition of the data gap, not a cause.
EXCLUSION_RES = [re.compile(r"\bexclu\w*", re.I), re.compile(r"مستبعد|المستبعدة|استبعد|استُبعد|استبعاد")]
COST_GAP_RES = [re.compile(r"no cost|missing[- ]cost|cost rows?|without (?:a )?cost", re.I),
                re.compile(r"تكلفة|التكلفة|تكاليف|التكاليف")]
DRIVER_PATH = re.compile(r"^profit_drivers\.drivers\[(\d+)\]\.")
SKIP_KEYS = {"effect_on_net_profit"}  # same magnitude as the driver's change; matched through `change` instead

# Concrete levers an action may name instead of a SKU.
LEVERS = {
    "en": ["free-shipping threshold", "free shipping threshold", "cost sheet", "shipping fee", "shipping rate",
           "discount", "coupon", "price", "carrier"],
    "ar": ["الشحن المجاني", "ورقة التكاليف", "رسوم الشحن", "سعر الشحن", "الخصم", "كوبون", "السعر", "شركة الشحن"],
}


@dataclass(frozen=True)
class Fact:
    value: Decimal
    path: str
    kind: str  # pct (percent) | pp (percentage points) | count (whole-number counts) | amount (SAR, other)
    direction: str | None  # set when the fact is part of a change


@dataclass
class NumberCheck:
    text: str
    value: str
    ok: bool
    matched: str | None = None
    reason: str | None = None
    tolerance: str = "0"
    paths: list[str] = field(default_factory=list)  # every fact the number consistently matches


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
                if isinstance(value, bool) or key in SKIP_KEYS:
                    continue
                if isinstance(value, (int, float)):
                    numbers.append(Fact(Decimal(str(value)), f"{path}.{key}".lstrip("."),
                                        _kind(key, unit, isinstance(value, int)), child_dir))
                else:
                    walk(value, f"{path}.{key}".lstrip("."), unit, None)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]", unit, direction)
        elif isinstance(node, str):
            strings.add(node)

    walk(facts, "", None, None)
    return numbers, strings


def _kind(key: str, unit: str | None, is_int: bool = False) -> str:
    if is_int and "threshold" not in key:
        return "count"  # orders, lines, units, ranks... (money is always a float with 2 dp)
    if "pct" in key:
        return "pct"
    if unit == "pp" and key in ("abs", "key_number", "this_week", "previous_week"):
        return "pp"
    if unit == "pct" and key in ("this_week", "previous_week", "key_number"):
        return "pct"
    return "amount"


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
        written_unit = "pct" if m.group("pct") or PCT_WORDS_RE.match(masked, m.end()) else (
            "pp" if PP_WORDS_RE.match(masked, m.end()) else None)
        if written_unit is None and (CURRENCY_AFTER_RE.match(masked, m.end())
                                     or CURRENCY_BEFORE_RE.search(masked[max(0, m.start() - 8):m.start()])):
            written_unit = "amount"  # written as money: must not match a count
        token = m.group(0).strip()
        by_value = [f for f in numbers if abs(abs(f.value) - target) <= half_step]
        candidates = [f for f in by_value if written_unit is None or f.kind == written_unit]
        if by_value and not candidates:
            kinds = sorted({f.kind for f in by_value})
            written = {"pct": "percent", "pp": "percentage points", "amount": "SAR"}[written_unit]
            facts_unit = " / ".join({"pct": "percent", "pp": "percentage points", "amount": "an amount",
                                     "count": "a count"}[k] for k in kinds)
            checks.append(NumberCheck(token, str(target), False, matched=by_value[0].path, tolerance=str(half_step),
                                      reason=f"unit: written as {written} but the fact is {facts_unit}"))
            continue
        start, end = _clause_bounds(masked, m.start())
        claimed = _claimed_direction(masked[start:end], m.group("sign"), m.start() - start)
        consistent = [f for f in candidates if f.direction and (claimed is None or claimed == f.direction)]
        levels = [f for f in candidates if not f.direction]
        changes = [f for f in candidates if f.direction]
        tol = str(half_step)
        if consistent:
            paths = [f.path for f in consistent]
            contradiction = _effect_contradiction(masked, m.start(), paths, facts)
            if contradiction:
                checks.append(NumberCheck(token, str(target), False, matched=paths[0], tolerance=tol, reason=contradiction))
            else:
                checks.append(NumberCheck(token, str(target), True, matched=paths[0], tolerance=tol, paths=paths))
        elif levels:
            checks.append(NumberCheck(token, str(target), True, matched=levels[0].path, tolerance=tol))
        elif changes:
            checks.append(NumberCheck(token, str(target), False, matched=changes[0].path, tolerance=tol,
                                      reason=f"direction contradicts facts ({changes[0].path} is "
                                             f"{changes[0].direction}, text says {claimed})"))
        else:
            checks.append(NumberCheck(token, str(target), False, tolerance=tol, reason="not found in facts.json"))
    return checks


def _nearest_polarity(text: str, pos: int) -> str | None:
    best: tuple[int, str] | None = None
    for polarity, patterns in (("helped", HELP_RES), ("hurt", HURT_RES)):
        for pattern in patterns:
            for m in pattern.finditer(text):
                distance = min(abs(m.start() - pos), abs(m.end() - pos))
                if best is None or distance < best[0]:
                    best = (distance, polarity)
    return best[1] if best else None


SOFT_SPLIT_RE = re.compile(r",\s|،|\band\b|\bwith\b|\bwhich\b|مما")


def _effect_window(text: str, pos: int) -> tuple[int, int]:
    """The number's clause plus directly attached soft clauses (", which hurt profit", "and helped", "مما دعم"),
    stopping at a contrast (while, but, بينما, ...) or the end of a sentence."""
    lo, hi = _clause_bounds(text, pos)
    while hi < len(text):
        sep = CLAUSE_SPLIT_RE.match(text, hi)
        if not sep or not SOFT_SPLIT_RE.fullmatch(sep.group(0)):
            break
        _, nxt = _clause_bounds(text, sep.end())
        if nxt <= hi:
            break
        hi = nxt
    return lo, hi


def _effect_contradiction(text: str, pos: int, paths: list[str], facts: dict) -> str | None:
    """If the number cites profit drivers and a helped/hurt word is used, it must match a driver's effect."""
    drivers = (facts.get("profit_drivers") or {}).get("drivers") or []
    cited = [drivers[int(m.group(1))] for p in paths if (m := DRIVER_PATH.match(p)) and int(m.group(1)) < len(drivers)]
    if not cited:
        return None
    lo, hi = _effect_window(text, pos)
    polarity = _nearest_polarity(text[lo:hi], pos - lo)
    if polarity and all(d["effect"] != polarity for d in cited):
        return f"effect contradicts facts ({cited[0]['label']} {cited[0]['effect']} net profit, text says {polarity})"
    return None


def _is_definitional(bullet: str, start: int) -> bool:
    lo, hi = _clause_bounds(bullet, start)
    clause = bullet[lo:hi]
    return any(r.search(clause) for r in EXCLUSION_RES) and any(r.search(clause) for r in COST_GAP_RES)


def unhedged_causal_claims(bullets: list[str], checks: list[list[NumberCheck]] | None = None) -> list[str]:
    """One problem per bullet that states a cause without citing a profit driver and without a hedge.
    `checks[i]` are the number checks of bullet i (needed to recognise cited drivers)."""
    problems = []
    for i, bullet in enumerate(bullets):
        causes = [m for r in CAUSE_RES for m in r.finditer(bullet) if not _is_definitional(bullet, m.start())]
        if not causes or any(r.search(bullet) for r in HEDGE_RES):
            continue
        bullet_checks = checks[i] if checks else []
        if any(c.ok and any(DRIVER_PATH.match(p) for p in c.paths) for c in bullet_checks):
            continue  # the cause is a profit driver, cited with a consistent number and direction
        words = ", ".join(m.group(0) for m in causes)
        problems.append(f"bullet {i + 1}: causal claim without a cited profit driver or a hedge ({words}): {bullet}")
    return problems


def mentions_key_number(checks: list[NumberCheck], insight: dict) -> bool:
    """True if a verified number in the text equals the insight's key number (or its relative change in %)."""
    wanted = [Decimal(str(insight[k])) for k in ("key_number", "key_number_pct") if insight.get(k) is not None]
    return any(c.ok and abs(Decimal(c.value) - abs(w)) <= Decimal(c.tolerance) for c in checks for w in wanted)


def action_is_specific(action: str, facts: dict, lang: str) -> bool:
    _, strings = flatten_facts(facts)
    skus = {s for s in strings if SKU_RE.fullmatch(s)}
    text = normalize(action)
    return any(s in text for s in skus) or any(lever in text.lower() for lever in LEVERS.get(lang, LEVERS["en"]))


def verify_summary(text: str, facts: dict, min_bullets: int = 4, max_bullets: int = 6,
                   action_label: str | None = None, lang: str = "en") -> Verification:
    bullets = parse_bullets(text)
    problems = []
    if not min_bullets <= len(bullets) <= max_bullets:
        problems.append(f"expected {min_bullets}-{max_bullets} bullets, got {len(bullets)}")
    actions = [b for b in bullets if action_label and b.startswith(action_label)]
    if action_label and len(actions) != 1:
        problems.append(f"expected exactly one bullet starting with {action_label!r}")
    elif actions and not action_is_specific(actions[0], facts, lang):
        problems.append("the recommended action must name a SKU from facts.json or a concrete lever "
                        "(e.g. the free-shipping threshold or the cost sheet)")
    per_bullet = [check_numbers(b, facts) for b in bullets]
    numbers = [c for checks in per_bullet for c in checks]
    problems += unhedged_causal_claims(bullets, per_bullet)
    insights = facts.get("insights") or []
    if insights and not mentions_key_number(numbers, insights[0]):
        top = insights[0]
        problems.append(f"the top insight ({top['id']}) is not covered: mention its key number {top['key_number']}"
                        + (f" or {top['key_number_pct']}%" if top.get("key_number_pct") is not None else ""))
    return Verification(passed=not problems and all(n.ok for n in numbers), numbers=numbers, problems=problems)
