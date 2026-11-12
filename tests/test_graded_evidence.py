import hashlib
import json
import shutil
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
from rapidfuzz import fuzz
from typer.testing import CliRunner

from docket.cli import app
from docket.config import Category, Signal, load_records
from docket.core.candidates import address_strength, name_strength, normalize_address, shortlist
from docket.core.extraction import extract_document
from docket.core.scoring import Tier, load_scoring_config, score_choice
from docket.corpus import Label, generate
from docket.evaluation import (
    Outcome,
    baseline,
    evaluate,
    export_requests,
    measurement_label,
    metrics,
    pending_requests,
    summarize,
    write_report,
)
from docket.llm.archive import load_archive
from docket.llm.replay import Recording, ReplayStore
from docket.settings import Settings
from scripts.tune_threshold import select_threshold

# The v0.1 simulated answers are keyed to the model they stood in for.
SIMULATED_TARGET = Settings(
    _env_file=None, docket_model_provider="openrouter", docket_model="openai/gpt-4o-mini"
)


def corpus_labels(seed):
    folder = Path(f"corpus/seed-{seed}")
    return folder, [
        Label.model_validate_json(line)
        for line in (folder / "labels.jsonl").read_text().splitlines()
    ]


@pytest.mark.parametrize("former", [False, True])
def test_name_strength_preserves_similarity_for_current_and_former_tenants(former):
    record = load_records()[0]
    name = (record.former_tenants if former else record.current_tenants)[0]
    typo = "z" + name[1:]
    prefix = "Former tenant" if former else "Tenant"
    result = shortlist([record], text=f"{record.record_id}\n{prefix}: {typo}")
    candidate = result.candidates[0]
    expected = (
        max(
            fuzz.ratio(name.lower(), typo.lower()),
            fuzz.token_sort_ratio(name.lower(), typo.lower()),
        )
        / 100
    )
    assert candidate.signals[Signal.TENANT_NAME] == expected
    assert 0.88 <= expected < 1
    assert candidate.former_tenant_score == (expected if former else 0)
    assert candidate.signals[Signal.EXPLICIT_ID] == 1
    assert name_strength([name], [name.lower()], 0.88) == 1
    assert name_strength([name], [" ".join(reversed(name.lower().split()))], 0.88) == 1


def test_address_strength_is_continuous_and_abbreviations_remain_exact():
    canonical = "1010 Cedar Street Apt 1"
    values = ["1010 Cedar Street Apt 1", "1010 Zedar Street Apt 1", "1010 Zedaz Street Apt 1"]
    scores = [address_strength([canonical], [value], 0.88) for value in values]
    assert scores[0] == 1 > scores[1] > scores[2] >= 0.88
    assert scores[1] == fuzz.ratio(normalize_address(canonical), normalize_address(values[1])) / 100
    for alias in ["1010 Cedar St #1", "1010 Cedar St Unit 1"]:
        assert address_strength([canonical], [alias], 0.88) == 1
    record = load_records()[0]
    candidate = shortlist([record], text=f"{record.record_id}\nAddress: {values[1]}").candidates[0]
    assert candidate.signals[Signal.ADDRESS] == scores[1]
    assert candidate.signals[Signal.EXPLICIT_ID] == 1


@pytest.mark.parametrize(
    "text", ["1010 Cedar Street", "1010 Cedar Street Apt 11", "1011 Cedar Street Apt 1", "nothing"]
)
def test_fuzzy_address_cannot_supply_missing_or_different_numbers(text):
    assert address_strength(["1010 Cedar Street Apt 1"], [text], 0.88) == 0


def test_unusable_aliases_and_names_supply_no_signal():
    assert address_strength(["", "Cedar Street"], ["Cedar Street"], 0.88) == 0
    assert address_strength(["1010 Cedar Street Apt 1"], ["1010 Other Building Apt 1"], 0.88) == 0
    assert name_strength([""], [""], 0.88) == 0
    assert name_strength(["Alexandra Henderson"], ["Another Person"], 0.88) == 0


@pytest.mark.parametrize("seed", [7, 11])
def test_original_payloads_labels_and_requests_remain_unchanged(seed):
    digests = json.loads(Path("corpus/original-digests.json").read_text())
    folder, labels = corpus_labels(seed)
    prefix = str(folder) + "/"
    for name, digest in digests.items():
        if not name.startswith(prefix):
            continue
        content = Path(name).read_bytes()
        if name.endswith("labels.jsonl"):
            content = b"\n".join(content.splitlines()[:170]) + b"\n"
        assert hashlib.sha256(content).hexdigest() == digest, name
    requests = pending_requests(labels[:170], folder, SIMULATED_TARGET)
    assert len(requests) == 200
    store = ReplayStore(Path("evals/recordings/calls.jsonl"))
    archived = load_archive(Path("evals/recordings/exchange.jsonl.gz"))
    for key, request in requests.items():
        saved = archived[key].request()
        saved.verify_key()
        assert saved == request
        assert store.get(key).key == key


@pytest.mark.parametrize("seed", [7, 11])
def test_graded_profiles_have_deterministic_bytes_and_fixed_gold(seed, tmp_path):
    folder, labels = corpus_labels(seed)
    regenerated = generate(seed, tmp_path)
    assert regenerated == labels
    added = labels[170:]
    assert len(added) == 10
    assert {label.case_type for label in added} == {"graded_evidence"}
    assert [label.expected for label in added] == ["filed"] * 4 + ["review"] + ["filed"] * 5
    assert Counter(label.category for label in added) == dict.fromkeys(Category, 2)
    for profile, label in enumerate(added):
        payload = (folder / label.filename).read_bytes()
        assert payload == (tmp_path / label.filename).read_bytes()
        text = extract_document(payload).text
        assert label.record_id in text
        assert ("Building:" in text) == (profile in {0, 1, 6, 7, 9})
        assert ("Address:" in text) == (profile not in {6, 7})
        assert ("tenant:" in text.lower()) == (profile not in {4, 5})


def test_seed7_curve_has_at_least_three_distinct_points_and_reproduces_selection():
    folder, labels = corpus_labels(7)
    config = load_scoring_config()
    sweep = config.model_copy(update={"auto_file_threshold": config.strong_threshold})
    outcomes = [baseline(label, folder, sweep) for label in labels]
    curve = summarize(labels, outcomes, sweep)["curve"]
    assert len(curve) >= 8
    assert len({(point["auto_file_precision"], point["auto_file_rate"]) for point in curve}) >= 3
    selection = select_threshold(labels, outcomes, sweep)
    assert Decimal(selection["selected_threshold"]) == config.auto_file_threshold
    saved = json.loads(Path("evals/threshold-selection.json").read_text())
    assert saved["seed"] == 7 and selection["trials"] == saved["trials"]
    assert (
        saved["rule_sha256"]
        == hashlib.sha256(Path("builds/v01/graded-corpus-rule.md").read_bytes()).hexdigest()
    )
    rejected = outcomes[174]
    assert rejected.tier == "medium" and rejected.status == "would_review"


def test_continuous_scores_keep_conflict_and_independence_guards():
    record, other = load_records()[:2]
    config = load_scoring_config()
    typo = record.address.replace("Cedar", "Zedar")
    alone = shortlist([record], text=f"{record.record_id}\n{record.record_id}\n{record.record_id}")
    assert score_choice(alone, record.record_id, config).tier == Tier.MEDIUM
    conflicting = shortlist(
        [record, other], text=f"{other.record_id}\n{record.address}\nAddress: {typo}"
    )
    for candidate in conflicting.candidates:
        score = score_choice(conflicting, candidate.record_id, config)
        assert score.conflict and score.decision == "review" and score.tier == Tier.NONE


def test_production_threshold_routes_strong_evidence_below_it_to_review():
    record = load_records()[0]
    candidates = shortlist(
        [record], text=f"{record.record_id}\nTenant: {record.current_tenants[0]}"
    )
    config = load_scoring_config()
    result = score_choice(candidates, record.record_id, config)
    assert result.tier == Tier.STRONG and result.score == Decimal(".55")
    assert config.strong_threshold <= result.score < config.auto_file_threshold
    assert result.decision == "review"


async def test_export_never_rewrites_existing_requests_or_answers(tmp_path, monkeypatch):
    folder, labels = corpus_labels(7)
    requests = pending_requests(labels[170:], folder, Settings(_env_file=None))
    for label in labels[170:]:
        (tmp_path / label.filename).write_bytes((folder / label.filename).read_bytes())
    shutil.copytree("config", tmp_path / "config")
    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=None, docket_replay_path=str(tmp_path / "calls.jsonl"))
    assert len(await export_requests(labels[170:], tmp_path, settings)) == 20
    paths = list(Path("evals/pending").glob("*.json"))
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    await export_requests(labels[170:], tmp_path, settings)
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths} == before
    assert set(requests) == {path.stem for path in paths}
    assert not Path("evals/answers").exists() and not Path(settings.docket_replay_path).exists()


def test_curve_can_lower_threshold_without_counting_medium_or_conflicted_reviews():
    _, labels = corpus_labels(7)
    label = labels[0]
    result = Outcome(
        label.document_id,
        "would_review",
        "insufficient_corroboration",
        label.record_id,
        label.category.value,
        ".57",
        "strong",
    )
    assert metrics([label], [result], Decimal(".55"))["auto_file_count"] == 1
    assert metrics([label], [result], Decimal(".55"))["review_rate"] == 0
    assert metrics([label], [result], Decimal(".58"))["auto_file_count"] == 0
    assert metrics([label], [result], Decimal(".58"))["review_rate"] == 1
    for tier, reason in [("medium", "insufficient_corroboration"), ("none", "conflicting_records")]:
        result.tier, result.reason = tier, reason
        assert metrics([label], [result], Decimal(".01"))["auto_file_count"] == 0


async def test_pending_export_and_replay_do_not_measure_held_out_scores(tmp_path, monkeypatch):
    folder, labels = corpus_labels(11)
    target = tmp_path / folder
    target.mkdir(parents=True)
    shutil.copytree("config", tmp_path / "config")
    for label in labels[170:]:
        (target / label.filename).write_bytes((folder / label.filename).read_bytes())
    (target / "labels.jsonl").write_text(
        "\n".join(label.model_dump_json() for label in labels[170:]) + "\n"
    )
    monkeypatch.chdir(tmp_path)

    def forbidden_scoring(*args):
        raise AssertionError("Held-out scoring must wait for answers")

    monkeypatch.setattr("docket.evaluation.baseline", forbidden_scoring)
    for mode in ["export", "replay"]:
        report = await evaluate(
            11, mode, Decimal(0), settings=Settings(_env_file=None), date="test"
        )
        assert report["status"] == "PENDING-SIMULATION" and report["pending_requests"] == 20
        assert (
            report["baseline"]
            is report["baseline_results"]
            is report["docket"]
            is report["floors"]
            is None
        )
        assert report["measurement"] == "no model calls" and report["model_cost_usd"] is None
        path = write_report(report)
        assert "Scoring is deferred" in path.with_suffix(".md").read_text()


def recording(simulator=None):
    return Recording(
        key=hashlib.sha256((simulator or "provider").encode()).hexdigest(),
        output={},
        cost_usd=None if simulator else ".01",
        source="synthetic_corpus",
        provenance={"simulated_by": simulator, "standing_in_for": "target"} if simulator else None,
    )


@pytest.mark.parametrize(
    "kinds,expected",
    [
        ([], "no model calls"),
        ([None], "openrouter"),
        (["small"], "simulated"),
        (["small", "large"], "simulated"),
        ([None, "small"], "mixed"),
        (["small", None], "mixed"),
    ],
)
async def test_measurement_label_and_report_use_actual_response_sources(
    kinds, expected, tmp_path, monkeypatch
):
    entries = [recording(kind) for kind in kinds]
    assert measurement_label(entries) == expected
    folder, labels = corpus_labels(7)
    original = labels[0]
    shutil.copytree("config", tmp_path / "config")
    target = tmp_path / folder
    target.mkdir(parents=True)
    (target / original.filename).write_bytes((folder / original.filename).read_bytes())
    (target / "labels.jsonl").write_text(original.model_dump_json() + "\n")
    result = baseline(original, folder, load_scoring_config())
    monkeypatch.chdir(tmp_path)
    Path("calls.jsonl").touch()
    monkeypatch.setattr("docket.evaluation.pending_requests", lambda *args: {})

    async def supplied_outcomes(*args):
        return [result], entries, Decimal(0)

    monkeypatch.setattr("docket.evaluation.model_outcomes", supplied_outcomes)
    report = await evaluate(
        7,
        "replay",
        Decimal(0),
        settings=Settings(_env_file=None, docket_replay_path="calls.jsonl"),
        date="test",
    )
    assert report["measurement"] == expected
    assert report["model_cost_usd"] == ("0.01" if expected == "openrouter" else None)
    path = write_report(report)
    text = path.with_suffix(".md").read_text()
    if expected == "mixed":
        assert text.startswith("Mixed model sources:") and "simulated" in text.splitlines()[0]
    elif expected == "no model calls":
        assert text.startswith("No model calls.")
    elif expected == "simulated":
        assert text.startswith("Model responses simulated by")


@pytest.mark.parametrize("measurement", ["mixed", "no model calls"])
def test_shadow_report_keeps_measurement_label(measurement, tmp_path):
    path = tmp_path / "report.json"
    report = {"measurement": measurement, "shadow": {"status": "COMPLETE", "disagreements": []}}
    if measurement == "mixed":
        report.update(
            banner="Mixed model sources: simulated and OpenRouter responses.", provenance=[]
        )
    path.write_text(json.dumps(report))
    result = CliRunner().invoke(app, ["shadow-diff", "--report", str(path)])
    assert result.exit_code == 0, result.output
    saved = json.loads(path.with_name("report-shadow.json").read_text())
    assert saved["measurement"] == measurement
