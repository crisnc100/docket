import re
import unicodedata
from dataclasses import dataclass, replace

from rapidfuzz import fuzz

from docket.config import Record, Signal

# Underscores are regex word characters but delimit record ids in attachment names.
ID_PATTERN = re.compile(r"(?<![^\W_])UNIT-[0-9]{4}(?![^\W_])", re.IGNORECASE)
UNIT_PATTERN = re.compile(
    r"(?<!\w)(?:(?:apartment|apt|unit|suite|ste)[\s._-]+|#[\s._-]*|"
    r"(?P<street>street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln)"
    r"[\s._-]+(?!#|(?:apartment|apt|unit|suite|ste)(?=[\s._-]|$))(?=\S*\d))"
    r"(?P<value>\S+)(?:[ \t]+(?P<letter>[a-z])(?!\w))?"
)
ADDRESS_TOKEN_PATTERN = re.compile(r"(?<=unit )\S+|(?<![\w-])\d[\w-]*")
ADDRESS_SPAN_PATTERN = re.compile(
    r"(?<!unit )(?<!\S)\d[\w-]*(?:\s+\w+){1,8}?\s+"
    r"(?:(?:st|ave|rd|blvd|dr|ln|ct|court|pl|place|terrace|way)(?:\s+unit\s+\S+)?"
    r"|unit\s+\S+)(?!\S)"
)
LABEL_PATTERN = re.compile(r"\b(?P<name>[a-z]+)[^\S\r\n]*:", re.IGNORECASE)
FULL_SIGNAL_STRENGTH = 1.0


def normalize_text(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", text).casefold()).split())


def unit_parts(value: str) -> tuple[str, str]:
    value = value.rstrip(".,;:!?)]}\"'”’»")
    tail = ""
    if value.endswith(".pdf"):
        value, tail = value[:-4], "pdf"
        # Filename words follow the unit; one-letter suffixes and ranges belong to it.
        word = re.search(r"-[a-z]{2,}(?=-|$)", value)
        if word:
            value, tail = value[: word.start()], value[word.start() :] + " " + tail
    return value, tail


def normalize_address(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    parts: list[str] = []
    end = 0
    for match in UNIT_PATTERN.finditer(text):
        value, tail = unit_parts(match.group("value"))
        if not any(character.isdigit() for character in value) and len(value) != 1:
            continue
        if match.group("letter"):
            value += "-" + match.group("letter")
        parts.extend(
            [
                normalize_text(text[end : match.start()]),
                match.group("street") or "",
                "unit",
                value,
                normalize_text(tail),
            ]
        )
        end = match.end()
    parts.append(normalize_text(text[end:]))
    normalized = " ".join(" ".join(parts).split())
    replacements = {
        "street": "st",
        "avenue": "ave",
        "road": "rd",
        "boulevard": "blvd",
        "drive": "dr",
        "lane": "ln",
        "apartment": "unit",
        "apt": "unit",
        "suite": "unit",
        "ste": "unit",
    }
    return " ".join(replacements.get(word, word) for word in normalized.split())


def strip_address_labels(line: str) -> str:
    # Scan labels once so long whitespace is not rescanned at each position.
    labels = list(LABEL_PATTERN.finditer(line))
    parts: list[str] = []
    start = 0
    for index, label in enumerate(labels):
        if label.group("name").casefold() == "address":
            parts.append(line[start : label.start()])
            start = labels[index + 1].start() if index + 1 < len(labels) else len(line)
    parts.append(line[start:])
    return " ".join(parts)


def independent_building_context(text: str) -> str:
    return " ".join(
        ADDRESS_SPAN_PATTERN.sub(" ", normalize_address(strip_address_labels(line)))
        for line in text.splitlines()
    )


def contains_phrase(phrase: str, text: str) -> bool:
    return f" {phrase} " in f" {text} "


def tenant_score(names: list[str], chunks: list[str], cutoff: float) -> float:
    best = 0.0
    for name in names:
        normalized = normalize_text(name)
        if not normalized:
            continue
        for chunk in chunks:
            if contains_phrase(normalized, chunk):
                return 1.0
            best = max(best, fuzz.ratio(normalized, chunk) / 100)
    return best if best >= cutoff else 0.0


def name_strength(names: list[str], chunks: list[str], cutoff: float) -> float:
    best = tenant_score(names, chunks, cutoff)
    for name in names:
        normalized = normalize_text(name)
        if normalized:
            for chunk in chunks:
                best = max(best, fuzz.token_sort_ratio(normalized, chunk) / 100)
    return best if best >= cutoff else 0.0


def address_strength(aliases: list[str], chunks: list[str], cutoff: float) -> float:
    return _address_strength(aliases, [normalize_address(chunk) for chunk in chunks], cutoff)


def _address_strength(aliases: list[str], chunks: list[str], cutoff: float) -> float:
    best = 0.0
    for alias in aliases:
        normalized = normalize_address(alias)
        tokens = ADDRESS_TOKEN_PATTERN.findall(normalized)
        if not tokens:
            continue
        for address in chunks:
            if contains_phrase(normalized, address):
                return 1.0
            # Similar street names cannot supply a missing or different house or unit token.
            if ADDRESS_TOKEN_PATTERN.findall(address) == tokens:
                best = max(best, fuzz.ratio(normalized, address) / 100)
    return best if best >= cutoff else 0.0


@dataclass(frozen=True)
class Candidate:
    record_id: str
    signals: dict[Signal, float]
    former_tenant_score: float
    building_is_explicit: bool = False

    @property
    def rank_score(self) -> tuple[float, float, float, float]:
        return (
            max(self.signals[Signal.EXPLICIT_ID], self.signals[Signal.FILENAME_ID]),
            self.signals[Signal.ADDRESS],
            self.signals[Signal.TENANT_NAME],
            self.signals[Signal.BUILDING],
        )


@dataclass(frozen=True)
class CandidateResult:
    candidates: tuple[Candidate, ...]
    conflict: bool
    explicit_ids: tuple[str, ...]
    conflict_record_ids: tuple[str, ...]


def shortlist(
    records: list[Record],
    *,
    text: str,
    subject: str = "",
    filename: str = "",
    k: int = 5,
    tenant_cutoff: float = 0.88,
) -> CandidateResult:
    if k < 1 or k > 5:
        raise ValueError("k must be between 1 and 5")
    if not 0 < tenant_cutoff <= 1:
        raise ValueError("tenant_cutoff must be in (0, 1]")
    if len({record.record_id for record in records}) != len(records):
        raise ValueError("Record ids must be unique")
    explicit = {value.upper() for value in ID_PATTERN.findall(text + "\n" + subject)}
    filename_ids = {value.upper() for value in ID_PATTERN.findall(filename)}
    all_ids = explicit | filename_ids
    address_text = normalize_address("\n".join([text, subject, filename]))
    normalized_text = normalize_text("\n".join([text, subject, filename]))
    building_text = independent_building_context("\n".join([text, subject, filename]))
    chunks = [
        normalize_text(
            re.sub(r"^(?:former tenant|tenant|resident|name)\s*:\s*", "", line, flags=re.IGNORECASE)
        )
        for line in (text + "\n" + subject + "\n" + filename).splitlines()
        if line.strip()
    ]
    address_chunks = [
        normalize_address(re.sub(r"^address\s*:\s*", "", line, flags=re.IGNORECASE))
        for line in (text + "\n" + subject + "\n" + filename).splitlines()
    ]
    candidates: list[Candidate] = []
    graded: dict[str, Candidate] = {}
    corroborated_ids = set(all_ids)
    for record in records:
        address = any(
            contains_phrase(normalize_address(alias), address_text)
            for alias in [record.address, *record.aliases]
            if alias.strip()
        )
        current_name = tenant_score(record.current_tenants, chunks, tenant_cutoff)
        former_name = tenant_score(record.former_tenants, chunks, tenant_cutoff)
        signals = {
            Signal.EXPLICIT_ID: float(record.record_id in explicit),
            Signal.FILENAME_ID: float(record.record_id in filename_ids),
            Signal.ADDRESS: float(address),
            Signal.TENANT_NAME: max(current_name, former_name),
            Signal.BUILDING: float(
                contains_phrase(normalize_text(record.building), normalized_text)
            ),
        }
        former = name_strength(record.former_tenants, chunks, tenant_cutoff)
        graded_signals = signals | {
            Signal.ADDRESS: max(
                float(address),
                _address_strength([record.address, *record.aliases], address_chunks, tenant_cutoff),
            ),
            Signal.TENANT_NAME: max(
                name_strength(record.current_tenants, chunks, tenant_cutoff), former
            ),
        }
        # Full matches outside the shortlist can still contradict the selected record.
        if (
            max(graded_signals[Signal.ADDRESS], graded_signals[Signal.TENANT_NAME])
            >= FULL_SIGNAL_STRENGTH
        ):
            corroborated_ids.add(record.record_id)
        if any(signals.values()):
            building_context = building_text
            for alias in [record.address, *record.aliases]:
                if alias.strip():
                    building_context = re.sub(
                        rf"(?<!\S){re.escape(normalize_address(alias))}(?!\S)",
                        "",
                        building_context,
                    )
            building_is_explicit = signals[Signal.BUILDING] > 0 and contains_phrase(
                normalize_address(record.building), " ".join(building_context.split())
            )
            candidate = Candidate(record.record_id, signals, former_name, building_is_explicit)
            candidates.append(candidate)
            graded[record.record_id] = replace(
                candidate, signals=graded_signals, former_tenant_score=former
            )
    candidates.sort(key=lambda candidate: candidate.record_id)
    candidates.sort(key=lambda candidate: candidate.rank_score, reverse=True)
    conflict = len(corroborated_ids) > 1
    return CandidateResult(
        tuple(graded[candidate.record_id] for candidate in candidates[:k]),
        conflict,
        tuple(sorted(all_ids)),
        tuple(sorted(corroborated_ids)) if conflict else (),
    )
