"""Data for the public front page's demo, built from committed evidence only.

The page never runs the pipeline. It plays back what the held-out report recorded for a few
seed-11 documents, beside the rules' candidate scores recomputed from the same PDFs.
`tests/test_front_demo.py` fails when web/public/home/demo.json drifts from this output.

Regenerate with: uv run python -m docket.front_demo
"""

import json
from decimal import Decimal
from pathlib import Path

from docket.config import Record, Signal, load_records
from docket.core.candidates import shortlist
from docket.core.extraction import Extracted, extract_document
from docket.core.scoring import load_scoring_config, score_choice, weighted_evidence

HELD_OUT = Path("evals/reports/2026-11-13-seed-11.json")
TUNING = Path("evals/reports/2026-11-13-seed-7.json")
CORPUS = Path("corpus/seed-11")
OUTPUT = Path("web/public/home/demo.json")

# Order is the order of the page's document tabs. doc-0176 sits behind "one more".
DOCUMENTS = ("doc-0001", "doc-0075", "doc-0012", "doc-0176")


def _labels() -> dict[str, dict]:
    rows = (json.loads(line) for line in (CORPUS / "labels.jsonl").read_text().splitlines())
    return {row["document_id"]: row for row in rows if row}


def _by_id(rows: list[dict]) -> dict[str, dict]:
    return {row["document_id"]: row for row in rows}


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


def _gates(report: dict, tuning: dict) -> dict:
    """The four live-pass gates as ADR 008 defines them, measured from both seeds' reports."""
    seeds = (report["live"], tuning["live"])
    requests = sum(live["requests"] for live in seeds)
    unreadable = sum(live["unreadable_count"] for live in seeds)
    no_answer = sum(live["no_answer_count"] for live in seeds)
    spend = sum(Decimal(live["spend_usd"]) for live in seeds)
    gates = {
        "unreadable_at_most_2_percent": unreadable <= requests * Decimal("0.02"),
        "no_answer_at_most_2_percent": no_answer <= requests * Decimal("0.02"),
        "held_out_floors": all(report["floors"].values()),
        "spend_under_5_dollars": spend < 5,
    }
    return {
        "passed": sum(gates.values()),
        "missed": len(gates) - sum(gates.values()),
        "total": len(gates),
        "results": gates,
        "requests": requests,
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
            "review_documents": suggestions["review_documents"],
            "model_suggestions_correct": suggestions["suggestion_correct"],
            "rules_top_pick_correct": suggestions["rules_correct"],
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
