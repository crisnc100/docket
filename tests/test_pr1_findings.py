import json
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from docket.cli import app
from docket.config import Signal, load_records
from docket.core.candidates import shortlist
from docket.core.extraction import (
    DocumentParseError,
    DocumentRejection,
    RejectionReason,
    extract_document,
)
from docket.core.ledger import transition
from docket.core.scoring import Tier, load_scoring_config, score_choice
from docket.corpus import Label, generate
from docket.db import Document, Event


def corpus_labels(seed=7):
    folder = Path(f"corpus/seed-{seed}")
    return folder, [
        Label.model_validate_json(line)
        for line in (folder / "labels.jsonl").read_text().splitlines()
    ]


@pytest.mark.parametrize("separator", ["-", "_", ".", " ", "—", "(", "/", "+"])
def test_f1_filename_separators_match_ids(separator):
    result = shortlist(
        load_records(), text="", filename=f"prefix{separator}UNIT-0001{separator}end.pdf"
    )
    assert result.candidates[0].record_id == "UNIT-0001"
    assert result.candidates[0].signals[Signal.FILENAME_ID] == 1


@pytest.mark.parametrize("seed", [7, 11])
def test_f1_corpus_filename_only_ids_are_real_candidates(seed):
    folder, labels = corpus_labels(seed)
    records = load_records()
    for label in labels:
        if label.case_type == "id_in_filename_only":
            text = extract_document((folder / label.filename).read_bytes()).text
            result = shortlist(records, text=text, filename=label.filename)
            assert label.record_id in {candidate.record_id for candidate in result.candidates}
            mismatch = shortlist(
                records,
                text="UNIT-0001" if label.record_id != "UNIT-0001" else "UNIT-0002",
                filename=label.filename,
            )
            assert mismatch.conflict


def test_f2_generator_labels_every_single_signal_as_review(tmp_path):
    labels = generate(7, tmp_path)
    single_signal = {"address_only", "tenant_only", "id_in_filename_only", "former_tenant"}
    selected = [label for label in labels if label.case_type in single_signal]
    assert len(selected) == 40
    assert all(label.expected == "review" for label in selected)


@pytest.mark.parametrize("seed", [7, 11])
def test_f2_committed_single_signal_labels_match_strict_scoring(seed):
    folder, labels = corpus_labels(seed)
    config = load_scoring_config()
    records = load_records()
    for label in labels:
        if label.case_type in {
            "address_only",
            "tenant_only",
            "id_in_filename_only",
            "former_tenant",
        }:
            assert label.expected == "review"
            text = extract_document((folder / label.filename).read_bytes()).text
            candidates = shortlist(records, text=text, filename=label.filename)
            result = score_choice(candidates, label.record_id, config)
            assert result.tier != Tier.STRONG and result.decision == "review"


def test_f3_config_load_rejects_auto_file_threshold_below_strong(tmp_path):
    policy = json.loads(Path("config/scoring.json").read_text())
    policy["auto_file_threshold"] = 0.30
    path = tmp_path / "invalid-scoring.json"
    path.write_text(json.dumps(policy))
    with pytest.raises(ValidationError, match="strong"):
        load_scoring_config(path)


def test_f3_filing_requires_strong_even_if_config_validation_is_bypassed():
    config = load_scoring_config().model_copy(update={"auto_file_threshold": Decimal(".30")})
    candidates = shortlist(load_records(), text="UNIT-0001")
    result = score_choice(candidates, "UNIT-0001", config)
    assert result.score >= config.auto_file_threshold
    assert result.tier == Tier.MEDIUM and result.decision == "review"


def test_f4_score_cli_runs_rules_only_pipeline_on_corpus_pdf(monkeypatch):
    from docket.llm.gateway import Gateway

    async def forbidden_call(*args, **kwargs):
        raise AssertionError("The score command must not call an LLM")

    monkeypatch.setattr(Gateway, "call", forbidden_call)
    folder, labels = corpus_labels()
    clean = next(label for label in labels if label.case_type == "clean")
    result = CliRunner().invoke(app, ["score", str(folder / clean.filename)])
    assert result.exit_code == 0, result.output
    output = json.loads(result.stdout)
    assert output["chosen_record_id"] == clean.record_id
    assert output["tier"] == "strong" and output["would_auto_file"] is True
    assert Decimal(output["score"]) == max(Decimal(item["score"]) for item in output["candidates"])
    chosen = next(item for item in output["candidates"] if item["record_id"] == clean.record_id)
    assert set(chosen["signals"]) == {signal.value for signal in Signal}
    assert chosen["signals"]["address"] == chosen["signals"]["explicit_id"] == 1
    assert chosen["tier"] == "strong" and chosen["would_auto_file"] is True


def test_f5_corrupt_pdf_raises_typed_rejection():
    folder, labels = corpus_labels()
    corrupt = next(label for label in labels if label.case_type == "corrupt")
    with pytest.raises(DocumentParseError) as error:
        extract_document((folder / corrupt.filename).read_bytes())
    assert isinstance(error.value, DocumentRejection)
    assert getattr(error.value, "status", None) == "rejected"
    reason = getattr(error.value, "reason", None)
    assert isinstance(reason, RejectionReason) and reason.value == "corrupt"
    assert error.value.non_retryable and error.value.__cause__ is not None


def test_f5_ledger_accepts_corrupt_as_terminal_rejection(postgres_engine):
    with Session(postgres_engine) as session, session.begin():
        document = Document(mode="act", run_id="live", content_hash="a" * 64)
        session.add(document)
        session.flush()
        assert transition(session, document.id, "rejected", "corrupt")
        assert document.status == "rejected" and document.status_reason == "corrupt"
        assert session.query(Event).one().detail["reason"] == "corrupt"


def test_f7_compose_healthcheck_probes_running_s3_gateway():
    compose = yaml.safe_load(Path("compose.yaml").read_text())
    probe = compose["services"]["seaweedfs"]["healthcheck"]["test"]
    assert probe[0] == "CMD"
    assert "http://localhost:8333/healthz" in probe
    assert all(":9333" not in value for value in probe)
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "seaweedfs", *probe[1:]], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "case_type,reason",
    [
        ("corrupt", "corrupt"),
        ("encrypted", "encrypted"),
        ("zero_byte", "zero_byte"),
        ("non_pdf", "unsupported_type"),
    ],
)
def test_f5_score_cli_reports_poison_documents_as_rejected(case_type, reason):
    folder, labels = corpus_labels()
    label = next(label for label in labels if label.case_type == case_type)
    result = CliRunner().invoke(app, ["score", str(folder / label.filename)])
    assert result.exit_code == 1
    output = json.loads(result.stdout)
    assert output["status"] == "rejected" and output["reason"] == reason
    assert output["candidates"] == [] and output["would_auto_file"] is False


@pytest.mark.parametrize("case_type", ["empty_text_layer", "id_in_filename_only"])
def test_f4_score_cli_reports_review_without_auto_filing(case_type):
    folder, labels = corpus_labels()
    label = next(label for label in labels if label.case_type == case_type)
    result = CliRunner().invoke(app, ["score", str(folder / label.filename)])
    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["tier"] == "none" and output["would_auto_file"] is False
    if case_type == "empty_text_layer":
        assert output["candidates"] == [] and output["chosen_record_id"] is None
    else:
        assert output["chosen_record_id"] == label.record_id


def test_f4_score_cli_missing_file_is_a_usage_error(tmp_path):
    result = CliRunner().invoke(app, ["score", str(tmp_path / "absent.pdf")])
    assert result.exit_code == 2
    assert "Invalid value" in result.output
