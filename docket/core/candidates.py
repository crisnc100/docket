import re
import unicodedata
from dataclasses import dataclass, replace

from rapidfuzz import fuzz, process

from docket.config import Record, Signal

# Underscores are regex word characters but delimit record ids in attachment names.
ID_PATTERN = re.compile(r"(?<![^\W_])UNIT-[0-9]{4}(?![^\W_])", re.IGNORECASE)
UNIT_PATTERN = re.compile(
    r"(?<!\w)(?:(?:apartment|apt|unit|suite|ste)[\s._-]++|#[\s._-]*+|"
    r"(?P<street>street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln)"
    r"[\s._-]++(?!#|(?:apartment|apt|unit|suite|ste)(?=[\s._-]|$))(?=[^\s\d]*+\d))"
    r"(?P<value>\S++)"
)
ADDRESS_TOKEN_PATTERN = re.compile(r"(?<=unit )\S+|(?<![\w-])\d[\w-]*")
ADDRESS_SPAN_PATTERN = re.compile(
    r"(?<!unit )(?<!\S)\d[\w-]*(?:\s+\w+){1,8}?\s+"
    r"(?:(?:st|ave|rd|blvd|dr|ln|ct|court|pl|place|terrace|way)(?:\s+unit\s+\S+)?"
    r"|unit\s+\S+)(?!\S)"
)
LABEL_PATTERN = re.compile(r"\b(?P<name>[a-z]+)[^\S\r\n]*:", re.IGNORECASE)
FULL_SIGNAL_STRENGTH = 1.0
MAX_MATCH_TOKEN_CHARS = 64
MAX_MATCH_LINE_CHARS = 8192
MAX_BODY_CHARS = 64000
MAX_BODY_LINES = 2000
MAX_SUBJECT_CHARS = 1000
MAX_FILENAME_CHARS = 255
LINE_BREAKS = "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _line_count(text: str) -> int:
    count = 0
    previous = ""
    for character in text:
        if character in LINE_BREAKS and not (previous == "\r" and character == "\n"):
            count += 1
        previous = character
    return count + int(bool(text) and text[-1] not in LINE_BREAKS)


def _bounded_prefix(text: str, limit: int) -> str:
    # Keep an end marker so a cut unit cannot become a shorter valid unit.
    return text if len(text) <= limit else text[: limit - 1] + "_"


def bounded_matching_text(text: str) -> str:
    return "\n".join(
        _bounded_prefix(
            " ".join(_bounded_prefix(token, MAX_MATCH_TOKEN_CHARS) for token in line.split()),
            MAX_MATCH_LINE_CHARS,
        )
        for line in text.splitlines()
    )


def normalize_text(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", _normalize(text)).split())


def unit_parts(value: str) -> tuple[str, str]:
    value = value.rstrip(".,;:!?)]}\"'”’»")
    tail = ""
    if value.endswith(".pdf"):
        value, tail = value[:-4], "pdf"
        # Filename words follow the unit; one-letter suffixes and ranges belong to it.
        word = re.search(r"-[a-z]{2,}(?=-|$)", value)
        if word:
            value, tail = value[: word.start()], value[word.start() :] + " " + tail
    return value.rstrip(".,;:!?)]}\"'”’»"), tail


def normalize_address(text: str) -> str:
    text = _normalize(text)
    parts: list[str] = []
    end = 0
    for match in UNIT_PATTERN.finditer(text):
        # Keep unit tokens such as 4b and 1-2 whole; normalize_text would split 1-2 into two words.
        value, tail = unit_parts(match.group("value"))
        if not any(character.isdigit() for character in value) and len(value) != 1:
            continue
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
    line = _normalize(line)
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
            best = max(best, fuzz.ratio(normalized, chunk, score_cutoff=cutoff * 100) / 100)
    return best if best >= cutoff else 0.0


def name_strength(names: list[str], chunks: list[str], cutoff: float) -> float:
    return _name_strength(
        names, chunks, cutoff, [" ".join(sorted(chunk.split())) for chunk in chunks]
    )


def _name_strength(
    names: list[str], chunks: list[str], cutoff: float, sorted_chunks: list[str]
) -> float:
    best = tenant_score(names, chunks, cutoff)
    for name in names:
        normalized = normalize_text(name)
        if normalized:
            sorted_name = " ".join(sorted(normalized.split()))
            for chunk in sorted_chunks:
                best = max(best, fuzz.ratio(sorted_name, chunk, score_cutoff=cutoff * 100) / 100)
    return best if best >= cutoff else 0.0


def address_strength(aliases: list[str], chunks: list[str], cutoff: float) -> float:
    addresses = [address for chunk in chunks for address in _normalized_address_chunks(chunk)]
    return _address_strength(
        aliases,
        [(address, ADDRESS_TOKEN_PATTERN.findall(address)) for address in addresses],
        cutoff,
    )


def _normalized_address_chunks(text: str) -> list[str]:
    text = _normalize(text)
    chunks: list[str] = []
    start = 0
    for match in UNIT_PATTERN.finditer(text):
        if match.group("value")[-1] in ".,;:!?)]}\"'”’»":
            continue
        following = match.end()
        while following < len(text) and text[following] in " \t":
            following += 1
        if following == match.end() or following == len(text) or not text[following].isalpha():
            continue
        if following + 1 < len(text) and (
            text[following + 1].isalnum() or text[following + 1] == "_"
        ):
            continue
        # A separate bare letter makes the unit ambiguous and cannot support an address match.
        chunks.append(normalize_address(text[start : match.start()]))
        start = following + 1
    chunks.append(normalize_address(text[start:]))
    return chunks


def _address_strength(
    aliases: list[str], chunks: list[tuple[str, list[str]]], cutoff: float
) -> float:
    best = 0.0
    for alias in aliases:
        normalized = normalize_address(alias)
        tokens = ADDRESS_TOKEN_PATTERN.findall(normalized)
        if not tokens:
            continue
        for address, address_tokens in chunks:
            if contains_phrase(normalized, address):
                return 1.0
            # Similar street names cannot supply a missing or different house or unit token.
            if address_tokens == tokens:
                best = max(best, fuzz.ratio(normalized, address) / 100)
    return best if best >= cutoff else 0.0


def _best_ratio(query: str, choices: list[str], cutoff: float) -> float:
    matches = process.extract(query, choices, scorer=fuzz.ratio, score_cutoff=cutoff * 100, limit=1)
    best = matches[0][1] / 100 if matches else 0.0
    return best if best >= cutoff else 0.0


def _batch_name_scores(
    names: list[str], chunks: list[str], cutoff: float
) -> dict[str, tuple[float, float]]:
    text = " " + " \n ".join(chunks) + " "
    sorted_chunks = list(dict.fromkeys(" ".join(sorted(chunk.split())) for chunk in chunks))
    scores: dict[str, tuple[float, float]] = {}
    for name in names:
        if not name:
            scores[name] = (0.0, 0.0)
            continue
        ordinary = 1.0 if f" {name} " in text else _best_ratio(name, chunks, cutoff)
        graded = max(ordinary, _best_ratio(" ".join(sorted(name.split())), sorted_chunks, cutoff))
        scores[name] = (ordinary, graded)
    return scores


def _batch_address_scores(aliases: list[str], chunks: list[str], cutoff: float) -> dict[str, float]:
    text = " " + " \n ".join(chunks) + " "
    by_tokens: dict[tuple[str, ...], list[str]] = {}
    for chunk in chunks:
        tokens = tuple(ADDRESS_TOKEN_PATTERN.findall(chunk))
        by_tokens.setdefault(tokens, []).append(chunk)
    scores: dict[str, float] = {}
    for alias in aliases:
        tokens = tuple(ADDRESS_TOKEN_PATTERN.findall(alias))
        if not tokens:
            scores[alias] = 0.0
        elif f" {alias} " in text:
            scores[alias] = 1.0
        else:
            scores[alias] = _best_ratio(alias, by_tokens.get(tokens, []), cutoff)
    return scores


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
    over_budget: bool = False


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
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    text, subject, filename = (_normalize(value) for value in (text, subject, filename))
    # Untrusted PDF text gets one budget per field so matching time stays bounded. An
    # over-budget field is left out and the document goes to review: cutting the field
    # could hide a contradicting address. Inside the budget, long tokens and lines in the
    # body and subject are capped so regex work stays linear; the 255-character filename is
    # not, so slugged names keep their .pdf. Ids from every field still count toward a conflict.
    body_over_budget = len(text) > MAX_BODY_CHARS or _line_count(text) > MAX_BODY_LINES
    subject_over_budget = len(subject) > MAX_SUBJECT_CHARS
    filename_over_budget = len(filename) > MAX_FILENAME_CHARS
    explicit = {value.upper() for value in ID_PATTERN.findall(text + "\n" + subject)}
    all_filename_ids = {value.upper() for value in ID_PATTERN.findall(filename)}
    all_ids = explicit | all_filename_ids
    filename_ids: set[str] = set() if filename_over_budget else all_filename_ids
    text = "" if body_over_budget else bounded_matching_text(text)
    subject = "" if subject_over_budget else bounded_matching_text(subject)
    filename = "" if filename_over_budget else filename
    address_text = " \n ".join(_normalized_address_chunks("\n".join([text, subject, filename])))
    normalized_text = normalize_text("\n".join([text, subject, filename]))
    building_text = independent_building_context("\n".join([text, subject, filename]))
    # Repeated lines cannot change a maximum match strength.
    lines = list(dict.fromkeys((text + "\n" + subject + "\n" + filename).splitlines()))
    chunks = [
        normalize_text(
            re.sub(r"^(?:former tenant|tenant|resident|name)\s*:\s*", "", line, flags=re.IGNORECASE)
        )
        for line in lines
        if line.strip()
    ]
    address_chunks = [
        address
        for line in lines
        for address in _normalized_address_chunks(
            re.sub(r"^address\s*:\s*", "", line, flags=re.IGNORECASE)
        )
    ]
    normalized_names = {
        name: normalize_text(name)
        for record in records
        for name in [*record.current_tenants, *record.former_tenants]
    }
    normalized_aliases = {
        alias: normalize_address(alias)
        for record in records
        for alias in [record.address, *record.aliases]
        if alias.strip()
    }
    name_scores = _batch_name_scores(
        list(dict.fromkeys(normalized_names.values())), chunks, tenant_cutoff
    )
    address_scores = _batch_address_scores(
        list(dict.fromkeys(normalized_aliases.values())), address_chunks, tenant_cutoff
    )
    candidates: list[Candidate] = []
    graded: dict[str, Candidate] = {}
    corroborated_ids = set(all_ids)
    for record in records:
        aliases = [
            normalized_aliases[alias]
            for alias in [record.address, *record.aliases]
            if alias.strip()
        ]
        current_scores = [name_scores[normalized_names[name]] for name in record.current_tenants]
        former_scores = [name_scores[normalized_names[name]] for name in record.former_tenants]
        address = any(contains_phrase(alias, address_text) for alias in aliases)
        current_name = max((score[0] for score in current_scores), default=0.0)
        former_name = max((score[0] for score in former_scores), default=0.0)
        signals = {
            Signal.EXPLICIT_ID: float(record.record_id in explicit),
            Signal.FILENAME_ID: float(record.record_id in filename_ids),
            Signal.ADDRESS: float(address),
            Signal.TENANT_NAME: max(current_name, former_name),
            Signal.BUILDING: float(
                contains_phrase(normalize_text(record.building), normalized_text)
            ),
        }
        former = max((score[1] for score in former_scores), default=0.0)
        graded_signals = signals | {
            Signal.ADDRESS: max(
                float(address),
                max((address_scores[alias] for alias in aliases), default=0.0),
            ),
            Signal.TENANT_NAME: max(
                max((score[1] for score in current_scores), default=0.0),
                former,
            ),
        }
        # Full matches outside the shortlist can still contradict the selected record.
        if (
            max(graded_signals[Signal.ADDRESS], graded_signals[Signal.TENANT_NAME])
            >= FULL_SIGNAL_STRENGTH
        ):
            corroborated_ids.add(record.record_id)
        if any(signals.values()):
            building_is_explicit = False
            if signals[Signal.BUILDING] > 0:
                building_context = building_text
                for alias in aliases:
                    building_context = re.sub(
                        rf"(?<!\S){re.escape(alias)}(?!\S)",
                        "",
                        building_context,
                    )
                building_is_explicit = contains_phrase(
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
        body_over_budget or subject_over_budget or filename_over_budget,
    )
