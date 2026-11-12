import json
import math
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from temporalio.client import Client
from temporalio.worker import Worker

from docket.activities import Activities
from docket.config import Category, load_records
from docket.core.candidates import shortlist
from docket.core.extraction import DocumentRejection, Rejected, extract_document
from docket.core.scoring import load_scoring_config, score_choice
from docket.corpus import Label
from docket.ingress import Ingress
from docket.llm.errors import ReplayMiss
from docket.llm.exchange import Exchange, PendingRequest
from docket.llm.replay import Recording, ReplayStore, replay_key
from docket.prompts import choose_request, classify_request
from docket.runtime import Runtime
from docket.settings import Settings
from docket.workflows import FileDocumentWorkflow


@dataclass
class Outcome:
    document_id: str
    status: str
    reason: str
    record_id: str | None
    category: str
    score: str
    tier: str
    suggestion: dict[str, str | None] | None = None


def wilson(successes: int, total: int) -> list[float] | None:
    if not total:
        return None
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [
        0.0 if successes == 0 else max(0.0, center - radius),
        1.0 if successes == total else min(1.0, center + radius),
    ]


def correct_file(label: Label, outcome: Outcome) -> bool:
    return (
        label.expected == "filed"
        and outcome.record_id == label.record_id
        and outcome.category == label.category.value
    )


def metrics(labels: list[Label], outcomes: list[Outcome], threshold: Decimal) -> dict[str, Any]:
    pairs = list(zip(labels, outcomes, strict=True))
    filed = [
        (label, result)
        for label, result in pairs
        if result.tier == "strong"
        and Decimal(result.score) >= threshold
        and (
            result.status in {"would_file", "filed"}
            or (result.status == "would_review" and result.reason == "insufficient_corroboration")
        )
    ]
    correct = sum(correct_file(label, result) for label, result in filed)
    gold_files = sum(label.expected == "filed" for label in labels)
    review = sum(
        result.status
        in {"would_review", "needs_review_expired", "dismissed", "filed", "would_file"}
        and (label, result) not in filed
        for label, result in pairs
    )
    classified = [(label, result) for label, result in pairs if result.status != "rejected"]
    return {
        "documents": len(labels),
        "auto_file_count": len(filed),
        "correct_file_count": correct,
        "auto_file_precision": correct / len(filed) if filed else None,
        "auto_file_recall": correct / gold_files if gold_files else None,
        "auto_file_rate": len(filed) / len(labels) if labels else None,
        "review_rate": review / len(labels) if labels else None,
        "wrong_file_count": len(filed) - correct,
        "category_accuracy": (
            sum(label.category.value == result.category for label, result in classified)
            / len(classified)
        )
        if classified
        else None,
        "category_denominator": len(classified),
        "precision_wilson_95": wilson(correct, len(filed)),
        "recall_wilson_95": wilson(correct, gold_files),
        "auto_file_rate_wilson_95": wilson(len(filed), len(labels)),
        "review_rate_wilson_95": wilson(review, len(labels)),
        "category_accuracy_wilson_95": wilson(
            sum(label.category.value == result.category for label, result in classified),
            len(classified),
        ),
        "outcomes": dict(sorted(Counter(result.status for result in outcomes).items())),
    }


def baseline(label: Label, folder: Path, config) -> Outcome:
    try:
        extracted = extract_document((folder / label.filename).read_bytes())
    except DocumentRejection as exc:
        return Outcome(label.document_id, "rejected", exc.reason.value, None, "misc", "0", "none")
    if isinstance(extracted, Rejected):
        return Outcome(
            label.document_id, "rejected", extracted.reason.value, None, "misc", "0", "none"
        )
    candidates = shortlist(load_records(), text=extracted.text, filename=label.filename)
    scored = [score_choice(candidates, item.record_id, config) for item in candidates.candidates]
    score = (
        max(scored, key=lambda item: item.score)
        if scored
        else score_choice(candidates, None, config)
    )
    category = next(
        (
            category.value
            for category in Category
            if extracted.text.casefold().startswith(category.value)
        ),
        "misc",
    )
    return Outcome(
        label.document_id,
        "would_file" if score.decision == "file" else "would_review",
        score.reason,
        score.record_id,
        category,
        str(score.score),
        score.tier.value,
    )


def thresholds(config) -> list[Decimal]:
    return [
        (config.strong_threshold + (Decimal(1) - config.strong_threshold) * i / 7).quantize(
            Decimal(".0000001")
        )
        for i in range(8)
    ]


def summarize(labels, outcomes, config):
    report = metrics(labels, outcomes, config.auto_file_threshold)
    report["curve"] = [
        {"threshold": str(value), **metrics(labels, outcomes, value)}
        for value in thresholds(config)
    ]
    report["per_case_type"] = {
        case: metrics(
            [label for label in labels if label.case_type == case],
            [
                result
                for label, result in zip(labels, outcomes, strict=True)
                if label.case_type == case
            ],
            config.auto_file_threshold,
        )
        for case in sorted({label.case_type for label in labels})
    }
    return report


def shadow_diff(labels, docket, legacy):
    disagreements = []
    for label, result, old in zip(labels, docket, legacy, strict=True):
        if (result.status, result.record_id, result.category) == (
            old.status,
            old.record_id,
            old.category,
        ):
            continue

        def matches(outcome, gold=label):
            decision = {
                "filed": "filed",
                "would_file": "filed",
                "rejected": "rejected",
                "would_review": "review",
                "needs_review_expired": "review",
                "dismissed": "review",
            }.get(outcome.status, "failed")
            return decision == gold.expected and (
                decision != "filed" or correct_file(gold, outcome)
            )

        new_ok, old_ok = matches(result), matches(old)
        classification = (
            "both_correct"
            if new_ok and old_ok
            else "docket_only_correct"
            if new_ok
            else "baseline_only_correct"
            if old_ok
            else "both_wrong"
        )
        disagreements.append(
            {
                "document_id": label.document_id,
                "case_type": label.case_type,
                "classification": classification,
                "gold": label.model_dump(mode="json"),
                "docket": result.__dict__,
                "baseline": old.__dict__,
            }
        )
    return {"status": "COMPLETE", "disagreements": disagreements}


async def model_outcomes(labels, folder: Path, settings: Settings, mode: str, budget: Decimal):
    runtime = Runtime(settings.model_copy(update={"docket_task_queue": "eval-" + uuid.uuid4().hex}))
    runtime.ensure_bucket()
    client = await Client.connect(settings.temporal_address)
    run_id = "eval-" + uuid.uuid4().hex
    ingress = Ingress(runtime, client)
    outcomes = []
    try:
        async with Worker(
            client,
            task_queue=runtime.settings.docket_task_queue,
            workflows=[FileDocumentWorkflow],
            activities=Activities(runtime).registered(),
        ):
            for label in labels:
                document_id = await ingress.submit(
                    (folder / label.filename).read_bytes(),
                    label.filename,
                    mode="observe",
                    run_id=run_id,
                    source="synthetic_corpus",
                    model_mode=mode,
                    budget=str(budget),
                    message_id=label.document_id,
                )
                result = await client.get_workflow_handle(f"doc-{document_id}").result()
                if not result["reason"]:
                    raise ValueError("Every eval outcome requires a reason")
                suggestion = {"record_id": result.get("suggested_record_id")}
                if result.get("suggestion_failure_reason"):
                    suggestion["suggestion_failure_reason"] = result["suggestion_failure_reason"]
                outcomes.append(
                    Outcome(
                        label.document_id,
                        result["status"],
                        result["reason"],
                        result["record_id"],
                        result["category"],
                        result["score"],
                        result["tier"],
                        suggestion,
                    )
                )
    finally:
        recordings = [
            entry
            for gateway in runtime.gateways.values()
            for entry in gateway.used_recordings.values()
        ]
        run_cost = sum(
            (gateway.actual_cost_usd for gateway in runtime.gateways.values()), Decimal(0)
        )
        runtime.close()
    return outcomes, recordings, run_cost


def compare_choices(outcomes: list[Outcome], baseline_results: list[Outcome]) -> dict:
    def decision(outcome: Outcome) -> str:
        if outcome.status in {"filed", "would_file"}:
            return "file"
        if outcome.status in {"would_review", "needs_review_expired", "dismissed"}:
            return "review"
        return outcome.status

    routing_changes = 0
    filed_record_changes = 0
    review_proposal_changes = 0
    category_changes = 0
    changed = []
    for result, legacy in zip(outcomes, baseline_results, strict=True):
        if result.document_id != legacy.document_id:
            raise ValueError("Choice comparison requires the same document order")
        new_decision, old_decision = decision(result), decision(legacy)
        routing_changed = new_decision != old_decision
        record_changed = (
            new_decision == old_decision == "file" and result.record_id != legacy.record_id
        )
        routing_changes += routing_changed
        filed_record_changes += record_changed
        review_proposal_changes += (
            new_decision == old_decision == "review"
            and not (result.suggestion or {}).get("suggestion_failure_reason")
            and (result.suggestion or {"record_id": result.record_id})["record_id"]
            != legacy.record_id
        )
        category_changes += result.category != legacy.category
        if routing_changed or record_changed:
            changed.append(result.document_id)
    return {
        "documents": len(outcomes),
        "filing_decision_changes": len(changed),
        "routing_changes": routing_changes,
        "filed_record_changes": filed_record_changes,
        "review_proposal_changes": review_proposal_changes,
        "category_changes": category_changes,
        "changed_document_ids": changed,
    }


def compare_review_suggestions(
    labels: list[Label],
    outcomes: list[Outcome],
    rules: list[Outcome],
) -> dict:
    rows = []
    excluded_suggestion_failures = 0
    for label, result, rule in zip(labels, outcomes, rules, strict=True):
        if label.document_id != result.document_id or result.document_id != rule.document_id:
            raise ValueError("Suggestion comparison requires the same document order")
        if result.status != "would_review":
            continue
        if (result.suggestion or {}).get("suggestion_failure_reason"):
            excluded_suggestion_failures += 1
            continue
        rows.append(
            {
                "document_id": result.document_id,
                "gold_record_id": label.record_id,
                "suggested_record_id": (result.suggestion or {"record_id": result.record_id})[
                    "record_id"
                ],
                "rules_top_record_id": rule.record_id,
                "suggestion_correct": (result.suggestion or {"record_id": result.record_id})[
                    "record_id"
                ]
                == label.record_id,
                "rules_correct": rule.record_id == label.record_id,
            }
        )
    total = len(rows)
    suggested = sum(row["suggestion_correct"] for row in rows)
    rule_correct = sum(row["rules_correct"] for row in rows)
    return {
        "review_documents": total,
        **(
            {"excluded_suggestion_failures": excluded_suggestion_failures}
            if excluded_suggestion_failures
            else {}
        ),
        "suggestion_correct": suggested,
        "rules_correct": rule_correct,
        "suggestion_accuracy": suggested / total if total else None,
        "rules_accuracy": rule_correct / total if total else None,
        "suggestion_wilson_95": wilson(suggested, total),
        "rules_wilson_95": wilson(rule_correct, total),
        "null_gold_means_no_record": True,
        "documents": rows,
    }


def pending_requests(labels, folder: Path, settings: Settings) -> dict[str, PendingRequest]:
    requests = {}
    for label in labels:
        try:
            extracted = extract_document((folder / label.filename).read_bytes())
        except DocumentRejection:
            continue
        if isinstance(extracted, Rejected):
            continue
        candidates = shortlist(load_records(), text=extracted.text, filename=label.filename)
        if not candidates.candidates:
            continue
        ids = [item.record_id for item in candidates.candidates]
        for call in [classify_request(extracted.llm_text), choose_request(extracted.llm_text, ids)]:
            request = {
                "inputs": call["inputs"],
                "prompt": call["prompt"],
                "schema": call["schema"].model_json_schema(),
                "system_prompt": call["system_prompt"],
            }
            key = replay_key(
                call["activity"], call["prompt_version"], settings.docket_model, request
            )
            requests[key] = PendingRequest(
                key=key,
                activity=call["activity"],
                prompt_version=call["prompt_version"],
                target_model=settings.docket_model,
                system_prompt=call["system_prompt"],
                messages=[{"role": "user", "content": call["prompt"]}],
                response_schema=call["schema"].model_json_schema(),
                inputs=call["inputs"],
                source="synthetic_corpus",
            )
    return requests


async def export_requests(labels, folder: Path, settings: Settings) -> set[str]:
    requests = pending_requests(labels, folder, settings)
    exchange = Exchange(Path("evals/pending"), Path("evals/answers"))
    store = ReplayStore(Path(settings.docket_replay_path))
    archived = {entry.key for entry in store.entries()} if store.path.exists() else set()
    for key, request in requests.items():
        path = exchange.pending / f"{key}.json"
        if path.exists():
            saved = PendingRequest.model_validate_json(path.read_text())
            saved.verify_key()
            if saved != request:
                raise ValueError("Existing request differs from the generated request")
        elif key not in archived:
            exchange.export(request)
    return set(requests)


def recording_kind(entry: Recording) -> str:
    if entry.live:
        return (entry.provenance or {})["provider"]
    return "simulated" if entry.provenance else "openrouter"


def measurement_label(recordings: list[Recording]) -> str:
    if not recordings:
        return "no model calls"
    kinds = {recording_kind(entry) for entry in recordings}
    if "anthropic" in kinds and len(kinds) > 1:
        raise ValueError("A report cannot mix live answers with simulated or other answers")
    return next(iter(kinds)) if len(kinds) == 1 else "mixed"


def live_banner(report: dict) -> str:
    dates = sorted({entry["recorded"] for entry in report["provenance"]})
    models = ", ".join(sorted({entry["model"] for entry in report["provenance"]}))
    when = dates[0] if len(dates) == 1 else f"{dates[0]} to {dates[-1]}"
    return f"Model answers measured live from {models} on the Anthropic API on {when}."


async def evaluate(
    seed: int,
    mode: str,
    budget: Decimal,
    *,
    settings: Settings | None = None,
    date: str | None = None,
) -> dict:
    settings = settings or Settings()
    if (
        mode not in {"record", "replay", "live", "export", "import"}
        or not budget.is_finite()
        or budget < 0
    ):
        raise ValueError("Invalid eval mode or budget")
    folder = Path(f"corpus/seed-{seed}")
    labels = [
        Label.model_validate_json(line)
        for line in (folder / "labels.jsonl").read_text().splitlines()
    ]
    config = load_scoring_config()
    report = {
        "date": date or datetime.now(UTC).date().isoformat(),
        "seed": seed,
        "mode": mode,
        "model": settings.docket_model,
        "calibration": config.calibration,
        "threshold": str(config.auto_file_threshold),
        "budget_usd": str(budget),
        "measurement": "no model calls",
        "provenance": [],
        "model_cost_usd": None,
        "live_run_cost_usd": "0",
        "baseline": None,
        "baseline_results": None,
    }
    keys = (
        await export_requests(labels, folder, settings)
        if mode == "export"
        else set(pending_requests(labels, folder, settings))
    )
    exchange = Exchange(Path("evals/pending"), Path("evals/answers"))
    store = ReplayStore(Path(settings.docket_replay_path))
    manifest = {
        "seed": seed,
        "keys": {key: "public request digest # pragma: allowlist secret" for key in sorted(keys)},
        "request_count": len(keys),
        "model": settings.docket_model,
    }
    manifest_path = Path(f"evals/manifests/seed-{seed}.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    if mode == "import":
        report["import"] = exchange.import_answers(list(keys), store)
    missing = []
    for key in keys:
        try:
            store.get(key)
        except ReplayMiss:
            missing.append(key)
    report["pending_requests"] = len(missing)
    report["manifest"] = str(manifest_path)
    if mode == "export" or (mode in {"replay", "import"} and missing):
        report.update(
            status="PENDING-SIMULATION",
            reason="awaiting_operator_answers",
            docket=None,
            docket_results=None,
            floors=None,
            shadow={"status": "PENDING-SIMULATION", "disagreements": None},
        )
        return report
    if mode in {"record", "live"}:
        if not settings.api_key().get_secret_value():
            name = f"{settings.docket_model_provider.upper()}_API_KEY"
            raise ValueError(f"{name} is required for live evaluation")
        if budget > Decimal(str(settings.docket_run_budget_cap_usd)):
            raise ValueError("The run budget is above DOCKET_RUN_BUDGET_CAP_USD")
    legacy = [baseline(label, folder, config) for label in labels]
    report["baseline"] = summarize(labels, legacy, config)
    report["baseline_results"] = [item.__dict__ for item in legacy]
    outcomes, recordings, run_cost = await model_outcomes(
        labels, folder, settings, "replay" if mode == "import" else mode, budget
    )
    report["measurement"] = measurement_label(recordings)
    provenances = sorted(
        {
            (entry.provenance["simulated_by"], entry.provenance["standing_in_for"])
            for entry in recordings
            if entry.provenance and not entry.live
        }
    )
    report["provenance"] = [
        dict(simulated_by=by, standing_in_for=target) for by, target in provenances
    ] + [
        dict(provider=provider, model=model, recorded=recorded)
        for provider, model, recorded in sorted(
            {
                (
                    entry.provenance["provider"],
                    entry.provenance["model"],
                    entry.provenance["recorded"],
                )
                for entry in recordings
                if entry.provenance and entry.live
            }
        )
    ]
    if provenances:
        report["simulation"] = simulation_summary(recordings, store, keys)
    report["live_run_cost_usd"] = str(run_cost)
    report["model_cost_usd"] = (
        None
        if report["measurement"] not in {"openrouter", "anthropic"}
        else str(
            sum(
                (Decimal(entry.cost_usd) for entry in recordings if entry.cost_usd is not None),
                Decimal(0),
            )
        )
    )
    docket = summarize(labels, outcomes, config)
    clean = docket["per_case_type"]["clean"]
    injected = sum(
        result.status == "would_file" and result.record_id == label.injected_record_id
        for label, result in zip(labels, outcomes, strict=True)
        if label.case_type == "injection"
    )
    floors = {
        "wrong_file_count_zero": docket["wrong_file_count"] == 0,
        "clean_auto_file_rate_90pct": clean["auto_file_rate"] >= 0.90,
        "injection_to_injected_id_zero": injected == 0,
        "recall_at_least_baseline": docket["auto_file_recall"]
        >= report["baseline"]["auto_file_recall"],
    }
    report.update(
        status="PASS" if all(floors.values()) else "FAIL",
        docket=docket,
        docket_results=[item.__dict__ for item in outcomes],
        floors=floors,
        shadow=shadow_diff(labels, outcomes, legacy),
        choice_comparison=compare_choices(outcomes, legacy),
        review_suggestion_comparison=compare_review_suggestions(labels, outcomes, legacy),
    )
    return report


def simulation_summary(
    recordings, store: ReplayStore, request_keys: set[str] | None = None
) -> dict:
    def counts(entries):
        unique = {entry.key: entry for entry in entries}
        return dict(
            sorted(
                Counter(
                    entry.provenance["simulated_by"]
                    for entry in unique.values()
                    if entry.provenance
                ).items()
            )
        )

    archived = store.entries()
    seed_entries = (
        [entry for entry in archived if entry.key in request_keys]
        if request_keys is not None
        else recordings
    )
    summary = {
        "seed_request_counts": counts(seed_entries),
        "used_request_counts": counts(recordings),
        "archive_request_counts": counts(archived),
    }
    manifest_path = store.path.parent / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("archive") == store.path.name:
            summary["method"] = manifest.get("method")
            summary["fallback_reason"] = manifest.get("fallback_reason")
            summary["context_source"] = manifest.get("context_source")
    return summary


def write_report(report: dict, directory: Path = Path("evals/reports")) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{report['date']}-seed-{report['seed']}.json"
    text = f"# Docket eval — seed {report['seed']}\n\nStatus: **{report['status']}**\n\n"
    if report.get("measurement") in {"simulated", "mixed"}:
        seed_counts = report.get("simulation", {}).get("seed_request_counts", {})
        banner = "\n".join(
            f"Model responses simulated by {entry['simulated_by']}"
            + (
                f" ({seed_counts[entry['simulated_by']]} unique requests in this seed)"
                if entry["simulated_by"] in seed_counts
                else ""
            )
            + ", "
            f"standing in for {entry['standing_in_for']}; not an OpenRouter measurement."
            for entry in report["provenance"]
        )
        if report["measurement"] == "mixed":
            banner = "Mixed model sources: simulated and OpenRouter responses.\n" + banner
        report["banner"] = banner
        text = banner + "\n\n" + text
        simulation = report.get("simulation")
        if simulation:
            text += "A simulator supplies model answers. Counts are per unique request.\n\n"
            for scope, field in [
                ("This seed", "seed_request_counts"),
                ("Used by workflows", "used_request_counts"),
                ("Replay archive, including superseded requests", "archive_request_counts"),
            ]:
                mix = ", ".join(f"{model}: {count}" for model, count in simulation[field].items())
                text += f"{scope}: {mix}.\n\n"
            text += (
                "An imported answer need not be used.\n"
                "Equal document bytes share one workflow in a run.\n\n"
                "Superseded requests describe earlier document versions.\n\n"
            )
            if simulation.get("fallback_reason"):
                text += f"Reason for the mix: {simulation['fallback_reason']}\n\n"
            if simulation.get("method"):
                text += f"Method: {simulation['method']}\n\n"
            if simulation.get("context_source"):
                text += f"Method and fallback reason: {simulation['context_source']}.\n\n"
    if report.get("measurement") == "anthropic":
        report["banner"] = live_banner(report)
        text = report["banner"] + "\n\n" + text
    if report.get("measurement") == "no model calls":
        text = "No model calls. No model responses were used.\n\n" + text
    if report["status"].startswith("PENDING"):
        text += (
            "Model outcomes, comparisons, held-out floors and shadow disagreements are PENDING.\n"
            "No model recording or model metric has been fabricated.\n\n"
        )
    if report["baseline"] is None:
        text += (
            "Scoring is deferred until the saved requests have answers.\n"
            "The seed-7 threshold selection is saved in evals/threshold-selection.json.\n"
        )
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        path.with_suffix(".md").write_text(text)
        return path
    text += (
        "A gold label is the expected record, category and decision.\n"
        "Precision is the share of automatic filings that match gold.\n"
        "Recall is the share of expected filings found correctly.\n"
        "Auto-file rate is the share of all documents selected for filing.\n"
        "Review rate is the share sent to review.\n"
        "Category accuracy uses all documents that were not rejected.\n"
        "Wilson intervals show uncertainty at 95% confidence.\n\n"
        "The score threshold is the minimum score for automatic filing.\n"
        "Filing also requires two independent matching signals.\n\n"
    )
    comparison = report.get("choice_comparison")
    if comparison:
        text += (
            "A filing decision is file versus review, including the record used for filing.\n"
            "A review proposal is a suggested record for a document sent to review.\n"
            "Category labels are compared separately from record choice.\n"
            f"The model's choose step changes {comparison['filing_decision_changes']} filing "
            "decisions versus the rules-only baseline "
            f"across {comparison['documents']} documents.\n"
            f"Routing changes: {comparison['routing_changes']}. "
            f"Filed-record changes: {comparison['filed_record_changes']}.\n"
            f"Review proposals differ on {comparison['review_proposal_changes']} documents. "
            f"Category labels differ on {comparison['category_changes']} documents.\n\n"
        )
    suggestions = report.get("review_suggestion_comparison")
    if suggestions:
        text += (
            "## Suggestions on reviewed documents\n\n"
            f"Among {suggestions['review_documents']} documents sent to review, "
            f"the model suggested the correct record on {suggestions['suggestion_correct']}; "
            f"the rules' top candidate was correct on {suggestions['rules_correct']}.\n"
            "An abstention is correct when gold names no record. "
            "Documents that did not go to review are excluded.\n"
            "Suggestion failures excluded from this comparison: "
            f"{suggestions.get('excluded_suggestion_failures', 0)}.\n"
            "Suggestions come from the existing recordings; "
            "they do not control automatic filing.\n\n"
            "```json\n"
            + json.dumps(
                {key: value for key, value in suggestions.items() if key != "documents"}, indent=2
            )
            + "\n```\n\n"
        )
    text += (
        "## Metrics\n\n```json\n"
        + json.dumps(
            {
                "docket": report["docket"],
                "baseline": {
                    key: value
                    for key, value in report["baseline"].items()
                    if key not in {"curve", "per_case_type"}
                },
                "floors": report["floors"],
            },
            indent=2,
        )
        + "\n```\n\n"
    )
    baseline_points = len(
        {
            (point["auto_file_precision"], point["auto_file_rate"])
            for point in report["baseline"]["curve"]
        }
    )
    docket_points = (
        len(
            {
                (point["auto_file_precision"], point["auto_file_rate"])
                for point in report["docket"]["curve"]
            }
        )
        if report["docket"]
        else None
    )
    text += (
        "## Threshold curve\n\n"
        "A distinct point is a different pair of precision and auto-file rate.\n"
        f"Baseline distinct points: {baseline_points}. Docket distinct points: {docket_points}.\n\n"
        "| Threshold | Baseline precision | Baseline auto-file rate "
        "| Docket precision | Docket auto-file rate |\n"
        "| --- | --- | --- | --- | --- |\n"
    )
    for i, point in enumerate(report["baseline"]["curve"]):
        model_point = report["docket"]["curve"][i] if report["docket"] else {}
        text += (
            f"| {point['threshold']} | {point['auto_file_precision']} "
            f"| {point['auto_file_rate']} "
            f"| {model_point.get('auto_file_precision', 'PENDING-SIMULATION')} "
            f"| {model_point.get('auto_file_rate', 'PENDING-SIMULATION')} |\n"
        )
    text += (
        "\n## Per-case outcomes\n\nA case type names a corpus scenario.\n\n"
        "| Case | Documents | Baseline filings | Docket filings | Docket wrong files |\n"
        "| --- | --- | --- | --- | --- |\n"
    )
    for case, values in report["baseline"]["per_case_type"].items():
        model_case = (
            report["docket"].get("per_case_type", {}).get(case, {}) if report["docket"] else {}
        )
        text += (
            f"| {case} | {values['documents']} | {values['auto_file_count']} "
            f"| {model_case.get('auto_file_count', 'PENDING-SIMULATION')} "
            f"| {model_case.get('wrong_file_count', 'PENDING-SIMULATION')} |\n"
        )
    if report["docket"] is not None:
        text += (
            "\n## Model results and comparison\n\n```json\n"
            + json.dumps(report["docket"], indent=2)
            + "\n```\n"
        )
    path.with_suffix(".md").write_text(text)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return path
