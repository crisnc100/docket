import io
import json
from collections import Counter
from pathlib import Path

import pytest
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from typer.testing import CliRunner

from docket.cli import app
from docket.config import load_records
from docket.corpus import CASE_TYPES, ENCRYPTION_PASSWORD, Label, generate


def labels(path):
    return [
        Label.model_validate_json(line) for line in (path / "labels.jsonl").read_text().splitlines()
    ]


def extracted(path, label):
    content = (path / label.filename).read_bytes()
    if label.case_type in {"zero_byte", "non_pdf"}:
        return label.case_type, content.decode()
    if label.case_type == "corrupt":
        with pytest.raises(PdfReadError):
            PdfReader(io.BytesIO(content))
        return "corrupt", "unreadable"
    reader = PdfReader(io.BytesIO(content))
    if reader.is_encrypted:
        assert reader.decrypt(ENCRYPTION_PASSWORD)
    return "encrypted" if reader.is_encrypted else "pdf", "\n".join(
        page.extract_text() for page in reader.pages
    )


@pytest.mark.parametrize("seed", [7, 11])
def test_committed_corpus_regenerates_extracted_text_and_labels(seed, tmp_path):
    committed = Path(f"corpus/seed-{seed}")
    gold = labels(committed)
    regenerated = generate(seed, tmp_path)
    assert len(gold) >= 150
    assert Counter(item.case_type for item in gold) == dict.fromkeys(CASE_TYPES, 10)
    assert regenerated == gold == labels(tmp_path)
    assert len({item.document_id for item in gold}) == len(gold)
    assert len({item.filename for item in gold}) == len(gold)
    assert {item.filename for item in gold} == {
        p.name for p in committed.iterdir() if p.name != "labels.jsonl"
    }
    for label in gold:
        assert extracted(committed, label) == extracted(tmp_path, label)


@pytest.mark.parametrize("seed", [7, 11])
def test_corpus_edge_cases_and_gold_integrity(seed):
    path = Path(f"corpus/seed-{seed}")
    gold = labels(path)
    by_id = {label.document_id: label for label in gold}
    records = {record.record_id: record for record in load_records()}
    for label in gold:
        content = (path / label.filename).read_bytes()
        kind, text = extracted(path, label)
        assert label.record_id is None or label.record_id in records
        if label.case_type == "injection":
            assert label.injected_record_id in records
            assert label.injected_record_id != label.record_id
            assert label.injected_record_id in text
            assert records[label.record_id].address in text
            assert label.record_id not in text
            assert label.expected == "review"
        elif label.case_type == "exact_duplicate":
            original = by_id[label.duplicate_of]
            assert original.case_type == "clean"
            assert content == (path / original.filename).read_bytes()
            assert (label.record_id, label.category, label.expected) == (
                original.record_id,
                original.category,
                original.expected,
            )
        elif label.case_type == "empty_text_layer":
            assert kind == "pdf" and not text.strip()
            assert label.expected == "review"
        elif label.case_type == "encrypted":
            assert kind == "encrypted" and label.expected == "rejected"
        elif label.case_type == "zero_byte":
            assert content == b"" and label.expected == "rejected"
        elif label.case_type == "non_pdf":
            assert not content.startswith(b"%PDF") and label.expected == "rejected"
        elif label.case_type == "corrupt":
            assert content.startswith(b"%PDF") and label.expected == "rejected"
        elif label.case_type == "very_long":
            assert len(text) > 16000
            assert text.count("End of report.") == 1
        elif label.case_type == "id_in_filename_only":
            assert label.record_id in label.filename
            assert label.record_id not in text + label.subject
        elif label.case_type == "tenant_only":
            record = records[label.record_id]
            assert record.current_tenants[0] in text
            assert record.record_id not in text and record.address not in text
        elif label.case_type == "address_only":
            record = records[label.record_id]
            assert record.address in text
            assert record.record_id not in text and record.current_tenants[0] not in text
        elif label.case_type == "former_tenant":
            record = records[label.record_id]
            assert record.former_tenants[0] in text
            assert record.current_tenants[0] not in text
        elif label.case_type in {"building_level", "no_record", "two_record_conflict"}:
            assert label.record_id is None and label.expected == "review"


def test_seed_split_has_different_labels_and_text():
    seed7, seed11 = Path("corpus/seed-7"), Path("corpus/seed-11")
    assert labels(seed7) != labels(seed11)
    first7, first11 = labels(seed7)[0], labels(seed11)[0]
    assert extracted(seed7, first7)[1] != extracted(seed11, first11)[1]


def test_corpus_cli_generates_labels(tmp_path):
    result = CliRunner().invoke(
        app, ["corpus", "generate", "--seed", "7", "--output", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "Generated 180 documents" in result.stdout
    assert (
        len([json.loads(line) for line in (tmp_path / "labels.jsonl").read_text().splitlines()])
        == 180
    )
