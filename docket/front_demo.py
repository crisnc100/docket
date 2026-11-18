"""Data for the public front page's demo, built from committed evidence only.

The page never runs the pipeline. It plays back what the held-out report recorded for a few
seed-11 documents, beside the rules' candidate scores recomputed from the same PDFs.
`tests/test_front_demo.py` fails when web/public/home/demo.json drifts from this output.

Regenerate with: uv run python -m docket.front_demo
"""

import json
import re
from decimal import Decimal
from pathlib import Path

from docket.config import Record, Signal, load_records
from docket.core.candidates import address_strength, name_strength, normalize_text, shortlist
from docket.core.extraction import Extracted, extract_document
from docket.core.scoring import load_scoring_config, score_choice, weighted_evidence

HELD_OUT = Path("evals/reports/2026-11-13-seed-11.json")
TUNING = Path("evals/reports/2026-11-13-seed-7.json")
# ADR 008's live-pass gates: at most 2% unreadable or unanswered, spend under $5.
ANSWER_LIMIT_PERCENT = 2
SPEND_CAP_USD = 5
# The report's intervals use z = 1.96 (docket/evaluation.py).
CONFIDENCE_PERCENT = 95
# The matcher's fuzzy cutoff for names and addresses (shortlist's tenant_cutoff default).
MATCH_CUTOFF = 0.88
CORPUS = Path("corpus/seed-11")
OUTPUT = Path("web/public/home/demo.json")

# Order is the order of the page's document tabs.
DOCUMENTS = ("doc-0001", "doc-0075", "doc-0012", "doc-0176")


def _labels() -> dict[str, dict]:
    rows = (json.loads(line) for line in (CORPUS / "labels.jsonl").read_text().splitlines())
    return {row["document_id"]: row for row in rows if row}


def _by_id(rows: list[dict]) -> dict[str, dict]:
    return {row["document_id"]: row for row in rows}


# The matcher strips these labels before comparing (docket/core/candidates.py shortlist).
ADDRESS_LABEL = re.compile(r"^\s*address\s*:\s*", re.IGNORECASE)
NAME_LABEL = re.compile(r"^\s*(?:former tenant|tenant|resident|name)\s*:\s*", re.IGNORECASE)


def _spans(text: str, candidate, record: Record) -> list[dict]:
    """The text each clue came from. An exact phrase is marked where it appears; a close
    spelling is found by re-running the matcher one line at a time, so "Streeet" is marked
    where the matcher actually found it."""
    lines = [line for line in text.splitlines() if line.strip()]
    spans = []

    def add(signal: Signal, found: str | None):
        if found:
            spans.append({"text": found, "signal": signal.value, "record_id": record.record_id})

    def exact(phrases: list[str]) -> str | None:
        for phrase in phrases:
            match = re.search(re.escape(phrase), text, re.IGNORECASE)
            if match:
                return match.group(0)
        return None

    def line_with(strength: float, label: re.Pattern, measure) -> str | None:
        for line in lines:
            rest = label.sub("", line).strip()
            if abs(measure(rest) - strength) < 1e-9:
                return rest
        return None

    for signal, strength in candidate.signals.items():
        if strength <= 0:
            continue
        if signal == Signal.EXPLICIT_ID:
            add(signal, exact([record.record_id]))
        elif signal == Signal.BUILDING and candidate.building_is_explicit:
            add(signal, exact([record.building]))
        elif signal == Signal.ADDRESS:
            aliases = [record.address, *record.aliases]
            add(
                signal,
                (strength == 1.0 and exact(aliases))
                or line_with(
                    strength,
                    ADDRESS_LABEL,
                    lambda rest, aliases=aliases: address_strength(aliases, [rest], MATCH_CUTOFF),
                ),
            )
        elif signal == Signal.TENANT_NAME:
            names = [*record.current_tenants, *record.former_tenants]
            add(
                signal,
                (strength == 1.0 and exact(names))
                or line_with(
                    strength,
                    NAME_LABEL,
                    lambda rest, names=names: name_strength(
                        names, [normalize_text(rest)], MATCH_CUTOFF
                    ),
                ),
            )
    return spans


def _candidate(candidate, records: dict[str, Record], config) -> dict:
    raw, independent = weighted_evidence(candidate, config)
    record = records[candidate.record_id]
    return {
        "record_id": candidate.record_id,
        "address": record.address,
        "tenant": record.current_tenants[0],
        "signals": {
            signal.value: round(candidate.signals[signal], 4)
            for signal in Signal
            if candidate.signals[signal] > 0
            and (signal != Signal.BUILDING or candidate.building_is_explicit)
        },
        "evidence_score": str(raw),
        "clue_count": len(independent),
    }


def _document(
    document_id: str,
    label: dict,
    recorded: dict,
    baseline: dict,
    review: dict | None,
    records: dict[str, Record],
    config,
) -> dict:
    path = CORPUS / label["filename"]
    extracted = extract_document(path.read_bytes())
    if not isinstance(extracted, Extracted):
        raise ValueError(f"{document_id} is rejected; the demo shows readable PDFs only")
    found = shortlist(list(records.values()), text=extracted.text, filename=path.name)
    scored = sorted(
        (score_choice(found, item.record_id, config) for item in found.candidates),
        key=lambda result: result.score,
        reverse=True,
    )
    top = scored[0] if scored else score_choice(found, None, config)
    by_id = {item.record_id: item for item in found.candidates}
    ranked = sorted(found.candidates, key=lambda item: item.rank_score, reverse=True)
    suggestion = recorded.get("suggestion") or {}
    return {
        "id": document_id,
        "filename": label["filename"],
        "case_type": label["case_type"],
        "expected": {"decision": label["expected"], "record_id": label["record_id"]},
        "text": extracted.text.strip(),
        "pages": extracted.page_count,
        "candidates": [_candidate(by_id[item.record_id], records, config) for item in ranked],
        "clue_spans": [
            span
            for item in ranked
            for span in _spans(extracted.text, by_id[item.record_id], records[item.record_id])
        ],
        "conflict": found.conflict,
        "rules": {
            "record_id": top.record_id,
            "score": str(top.score),
            "tier": top.tier.value,
            "decision": top.decision,
            "reason": top.reason,
        },
        "recorded": {
            "status": recorded["status"],
            "reason": recorded["reason"],
            "record_id": recorded["record_id"],
            "score": recorded["score"],
            "tier": recorded["tier"],
            "category": recorded["category"],
            "model_suggestion": suggestion.get("record_id"),
            "model_called": bool(found.candidates),
        },
        "rules_only_baseline": {
            "status": baseline["status"],
            "record_id": baseline["record_id"],
        },
        "review_comparison": review,
    }


def _held_breakdown(report: dict) -> dict:
    """Every held document in exactly one row, so each suggestion figure has its own
    denominator. The report's own comparison counts documents the model was never asked
    about (no candidate) as correct abstentions; these rows keep them apart."""
    compared = _by_id(report["review_suggestion_comparison"]["documents"])
    held = [row for row in report["docket_results"] if row["status"] == "would_review"]
    failed = [row for row in held if (row["suggestion"] or {}).get("suggestion_failure_reason")]
    answered = [compared[row["document_id"]] for row in held if row not in failed]
    not_asked = [row for row in answered if row["rules_top_record_id"] is None]
    asked = [row for row in answered if row["rules_top_record_id"] is not None]
    with_unit = [row for row in asked if row["gold_record_id"] is not None]
    no_unit = [row for row in asked if row["gold_record_id"] is None]
    return {
        "held": len(held),
        "held_failed": len(failed),
        "held_not_asked": len(not_asked),
        "asked_with_unit": len(with_unit),
        "model_right_unit": sum(row["suggestion_correct"] for row in with_unit),
        "rules_right_unit": sum(row["rules_correct"] for row in with_unit),
        "asked_without_unit": len(no_unit),
        "model_right_no_unit": sum(row["suggestion_correct"] for row in no_unit),
        "rules_right_no_unit": sum(row["rules_correct"] for row in no_unit),
        "not_asked_rules_right": sum(row["rules_correct"] for row in not_asked),
        "not_asked_model_right": sum(row["suggestion_correct"] for row in not_asked),
    }


def _gates(report: dict, tuning: dict) -> dict:
    """The four live-pass gates as ADR 008 defines them, measured from both seeds' reports."""
    seeds = (report["live"], tuning["live"])
    requests = sum(live["requests"] for live in seeds)
    unreadable = sum(live["unreadable_count"] for live in seeds)
    no_answer = sum(live["no_answer_count"] for live in seeds)
    spend = sum(Decimal(live["spend_usd"]) for live in seeds)
    gates = {
        "unreadable_at_most_2_percent": unreadable * 100 <= requests * ANSWER_LIMIT_PERCENT,
        "no_answer_at_most_2_percent": no_answer * 100 <= requests * ANSWER_LIMIT_PERCENT,
        "held_out_floors": all(report["floors"].values()),
        "spend_under_5_dollars": spend < SPEND_CAP_USD,
    }
    return {
        "passed": sum(gates.values()),
        "missed": len(gates) - sum(gates.values()),
        "total": len(gates),
        "results": gates,
        "requests": requests,
        "answer_limit_percent": ANSWER_LIMIT_PERCENT,
        "unreadable_answers": unreadable,
        "spend_usd": str(spend),
        "missed_floor": [name for name, met in report["floors"].items() if not met],
    }


def build() -> dict:
    report = json.loads(HELD_OUT.read_text())
    tuning = json.loads(TUNING.read_text())
    config = load_scoring_config()
    records = {record.record_id: record for record in load_records()}
    labels = _labels()
    docket, baseline = _by_id(report["docket_results"]), _by_id(report["baseline_results"])
    reviews = _by_id(report["review_suggestion_comparison"]["documents"])
    suggestions = report["review_suggestion_comparison"]
    return {
        "source": {
            "report": str(HELD_OUT),
            "tuning_report": str(TUNING),
            "model": report["model"],
            "recorded_on": report["date"],
            "seed": report["seed"],
            "tuning_documents": tuning["docket"]["documents"],
            "confidence_percent": CONFIDENCE_PERCENT,
        },
        "policy": {
            "weights": {
                signal.value: str(weight) for signal, weight in config.weights.by_signal().items()
            },
            "strong_threshold": str(config.strong_threshold),
            "auto_file_threshold": str(config.auto_file_threshold),
            "auto_file_threshold_short": f"{config.auto_file_threshold:.3f}",
            "clues_required": 2,
        },
        "results": {
            "documents": report["docket"]["documents"],
            "outcomes": report["docket"]["outcomes"],
            "auto_filed": report["docket"]["auto_file_count"],
            "auto_filed_correct": report["docket"]["correct_file_count"],
            "auto_filed_wrong": report["docket"]["wrong_file_count"],
            "expected_filings": round(
                report["docket"]["correct_file_count"] / report["docket"]["auto_file_recall"]
            ),
            "rules_only_filed": report["baseline"]["auto_file_count"],
            "rules_only_filed_correct": report["baseline"]["correct_file_count"],
            "rules_only_filed_wrong": report["baseline"]["wrong_file_count"],
            "rejected": report["docket"]["outcomes"]["rejected"],
            "suggestion_failures": suggestions.get("excluded_suggestion_failures", 0),
            "review_documents": suggestions["review_documents"],
            "model_suggestions_correct": suggestions["suggestion_correct"],
            "rules_top_pick_correct": suggestions["rules_correct"],
            **_held_breakdown(report),
            "units": len(records),
        },
        "gates": _gates(report, tuning),
        "documents": [
            _document(
                document_id,
                labels[document_id],
                docket[document_id],
                baseline[document_id],
                reviews.get(document_id),
                records,
                config,
            )
            for document_id in DOCUMENTS
        ],
    }


def render() -> str:
    return json.dumps(build(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render())
