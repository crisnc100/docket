import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from docket.core.scoring import load_scoring_config
from docket.corpus import Label
from docket.evaluation import baseline, metrics, summarize


def select_threshold(labels, outcomes, config) -> dict:
    thresholds = sorted(
        {config.strong_threshold, Decimal(1)}
        | {Decimal(item.score) for item in outcomes if item.tier == "strong"}
    )
    trials = []
    for threshold in thresholds:
        measured = metrics(labels, outcomes, threshold)
        clean_pairs = [
            (label, outcome)
            for label, outcome in zip(labels, outcomes, strict=True)
            if label.case_type == "clean"
        ]
        clean = metrics(
            [pair[0] for pair in clean_pairs], [pair[1] for pair in clean_pairs], threshold
        )
        trials.append(
            {
                "threshold": str(threshold),
                "correct_file_count": measured["correct_file_count"],
                "wrong_file_count": measured["wrong_file_count"],
                "clean_auto_file_rate": clean["auto_file_rate"],
            }
        )
    eligible = [
        trial
        for trial in trials
        if trial["wrong_file_count"] == 0
        and trial["clean_auto_file_rate"] is not None
        and trial["clean_auto_file_rate"] >= 0.9
    ]
    if not eligible:
        raise ValueError("Seed 7 has no threshold meeting the selection rule")
    selected = max(
        eligible, key=lambda trial: (trial["correct_file_count"], Decimal(trial["threshold"]))
    )
    return {"selected_threshold": selected["threshold"], "trials": trials}


def main() -> None:
    parser = argparse.ArgumentParser(description="Select a threshold using only seed-7 rules.")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    folder = Path("corpus/seed-7")
    labels = [
        Label.model_validate_json(line)
        for line in (folder / "labels.jsonl").read_text().splitlines()
    ]
    config = load_scoring_config()
    # A sweep needs scores before the chosen filing threshold removes any filings.
    sweep_config = config.model_copy(update={"auto_file_threshold": config.strong_threshold})
    outcomes = [baseline(label, folder, sweep_config) for label in labels]
    rule = Path("builds/v01/graded-corpus-rule.md")
    evidence = {
        "seed": 7,
        "source": "rules-only; model answers pending",
        "rule_sha256": hashlib.sha256(rule.read_bytes()).hexdigest(),
        "selection_rule": (
            "Zero wrong filings; clean rate >= 90%; "
            "most correct filings; largest threshold on ties."
        ),
        "weights_tiers_and_fuzzy_cutoffs": "untuned defaults",
        **select_threshold(labels, outcomes, sweep_config),
        "curve": summarize(labels, outcomes, sweep_config)["curve"],
    }
    if args.write:
        path = Path("config/scoring.json")
        values = json.loads(path.read_text())
        values["auto_file_threshold"] = float(evidence["selected_threshold"])
        values["calibration"] = "seed7_threshold_weights_and_tiers_untuned"
        path.write_text(json.dumps(values, indent=2) + "\n")
        body = json.dumps(evidence, indent=2, sort_keys=True)
        digest = evidence["rule_sha256"]
        body = body.replace(
            f'"rule_sha256": "{digest}",',
            f'"rule_sha256": "{digest}", '
            '"rule_digest_note": "public rule digest # pragma: allowlist secret",',
        )
        Path("evals/threshold-selection.json").write_text(body + "\n")
    print(
        json.dumps(
            {key: value for key, value in evidence.items() if key not in {"trials", "curve"}},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
