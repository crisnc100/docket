"""The live pass's model cost, rebuilt from the recordings and the price snapshot.

Costs are recomputed from each answer's tokens, not copied from the recording, so the
summary checks the recorded costs as well as adding them up.
"""

from decimal import Decimal
from pathlib import Path
from typing import Any

from docket.core.extraction import DocumentRejection, Rejected, extract_document
from docket.corpus import Label
from docket.evaluation import pending_requests
from docket.llm.budget import PRICES_PATH, load_prices
from docket.llm.replay import ReplayStore
from docket.settings import REPLAY_PATHS, Settings

MODEL = "claude-haiku-5-5"
SEEDS = (7, 11)


def _labels(seed: int) -> list[Label]:
    folder = Path(f"corpus/seed-{seed}")
    return [
        Label.model_validate_json(line)
        for line in (folder / "labels.jsonl").read_text().splitlines()
    ]


def _per_document(spend: Decimal, count: int) -> str | None:
    return str(spend / count) if count else None


def build_summary(
    recordings: Path = Path(REPLAY_PATHS["anthropic"]), prices: Path = PRICES_PATH
) -> dict[str, Any]:
    snapshot = load_prices(prices)
    ceiling = snapshot.ceiling(MODEL)
    entries = {entry.key: entry for entry in ReplayStore(recordings).entries()}
    settings = Settings(
        docket_model_provider="anthropic",
        docket_model=MODEL,
    )
    seeds: dict[str, Any] = {}
    total = {
        "spend": Decimal(0),
        "input_tokens": 0,
        "output_tokens": 0,
        "requests": 0,
        "answered": 0,
        "documents": 0,
        "documents_not_rejected": 0,
        "documents_reaching_model": 0,
        "by_activity": {},
    }
    dates: set[str] = set()
    for seed in SEEDS:
        folder = Path(f"corpus/seed-{seed}")
        labels = _labels(seed)
        by_document = {
            label.document_id: pending_requests([label], folder, settings) for label in labels
        }
        requests = {key: request for keys in by_document.values() for key, request in keys.items()}
        answered = [entries[key] for key in sorted(requests) if key in entries]
        spend = Decimal(0)
        by_activity: dict[str, Decimal] = {}
        for entry in answered:
            if entry.input_tokens is None or entry.output_tokens is None:
                raise ValueError(f"A live answer has no token counts: {entry.key}")
            cost = ceiling.cost(entry.input_tokens, entry.output_tokens)
            if entry.cost_usd is None or Decimal(entry.cost_usd) != cost:
                raise ValueError(f"Recorded cost differs from tokens times prices: {entry.key}")
            spend += cost
            activity = requests[entry.key].activity
            by_activity[activity] = by_activity.get(activity, Decimal(0)) + cost
            dates.add((entry.provenance or {})["recorded"])
        rejected = sum(
            not keys and _rejected(folder, label)
            for label, keys in zip(labels, by_document.values(), strict=True)
        )
        reaching = sum(bool(keys) for keys in by_document.values())
        not_rejected = len(labels) - rejected
        seeds[str(seed)] = {
            "requests": len(requests),
            "answered": len(answered),
            "input_tokens": sum(entry.input_tokens or 0 for entry in answered),
            "output_tokens": sum(entry.output_tokens or 0 for entry in answered),
            "spend_usd": str(spend),
            "spend_by_activity_usd": {k: str(v) for k, v in sorted(by_activity.items())},
            "documents": len(labels),
            "documents_not_rejected": not_rejected,
            "documents_reaching_model": reaching,
            "cost_per_document_reaching_model_usd": _per_document(spend, reaching),
            "cost_per_document_not_rejected_usd": _per_document(spend, not_rejected),
            "cost_per_document_usd": _per_document(spend, len(labels)),
        }
        total["spend"] += spend
        total["input_tokens"] += seeds[str(seed)]["input_tokens"]
        total["output_tokens"] += seeds[str(seed)]["output_tokens"]
        total["requests"] += len(requests)
        total["answered"] += len(answered)
        total["documents"] += len(labels)
        total["documents_not_rejected"] += not_rejected
        total["documents_reaching_model"] += reaching
        for name, value in by_activity.items():
            total["by_activity"][name] = total["by_activity"].get(name, Decimal(0)) + value
    spend = total["spend"]
    return {
        "model": MODEL,
        "provider": "anthropic",
        "recorded": sorted(dates),
        "price_snapshot_date": snapshot.snapshot_date,
        "price_source": snapshot.source,
        "prices_per_million_usd": {
            "input": str(ceiling.prompt_per_million),
            "output": str(ceiling.completion_per_million),
        },
        "recordings": str(recordings),
        "seeds": seeds,
        "total": {
            "requests": total["requests"],
            "answered": total["answered"],
            "input_tokens": total["input_tokens"],
            "output_tokens": total["output_tokens"],
            "spend_usd": str(spend),
            "spend_by_activity_usd": {k: str(v) for k, v in sorted(total["by_activity"].items())},
            "documents": total["documents"],
            "documents_not_rejected": total["documents_not_rejected"],
            "documents_reaching_model": total["documents_reaching_model"],
            "cost_per_document_reaching_model_usd": _per_document(
                spend, total["documents_reaching_model"]
            ),
            "cost_per_document_not_rejected_usd": _per_document(
                spend, total["documents_not_rejected"]
            ),
            "cost_per_document_usd": _per_document(spend, total["documents"]),
        },
    }


def _rejected(folder: Path, label: Label) -> bool:
    try:
        return isinstance(extract_document((folder / label.filename).read_bytes()), Rejected)
    except DocumentRejection:
        return True
