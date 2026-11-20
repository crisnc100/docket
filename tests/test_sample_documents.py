"""The beta's one-document generator (plan PR B, Builds 1 and 2)."""

import hashlib
import io
from pathlib import Path

import pytest
from pypdf import PdfReader

from docket.corpus import (
    CASE_TYPES,
    NOT_SAMPLES,
    SAMPLE_CASE_TYPES,
    generate,
    sample_document,
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("seed", [7, 11])
def test_committed_corpora_still_regenerate_byte_for_byte(seed, tmp_path):
    committed = Path(f"corpus/seed-{seed}")
    generate(seed, tmp_path)
    files = sorted(path.name for path in committed.iterdir())
    assert files == sorted(path.name for path in tmp_path.iterdir())
    # pypdf adds random bytes when it encrypts, so these differ on every run. test_corpus.py
    # compares their decrypted text instead.
    encrypted = [name for name in files if name.endswith("-encrypted.pdf")]
    assert len(encrypted) == 10
    compared = [name for name in files if name not in encrypted]
    assert len(compared) == 171
    assert [digest(committed / name) for name in compared] == [
        digest(tmp_path / name) for name in compared
    ]


def test_sample_case_types_are_the_case_types_minus_the_named_exclusions():
    assert set(NOT_SAMPLES) < set(CASE_TYPES)
    assert all(reason.strip() for reason in NOT_SAMPLES.values())
    assert SAMPLE_CASE_TYPES == tuple(item for item in CASE_TYPES if item not in NOT_SAMPLES)
    assert set(NOT_SAMPLES) == {
        "corrupt",
        "encrypted",
        "zero_byte",
        "non_pdf",
        "empty_text_layer",
        "exact_duplicate",
        "graded_evidence",
    }
    assert len(SAMPLE_CASE_TYPES) == 11


@pytest.mark.parametrize("case_type", sorted(NOT_SAMPLES) + ["", "CLEAN", "../clean"])
def test_a_case_type_outside_the_list_makes_no_sample(case_type):
    with pytest.raises(ValueError):
        sample_document(case_type, 1)


@pytest.mark.parametrize("case_type", SAMPLE_CASE_TYPES)
def test_each_sample_is_a_readable_pdf_that_differs_by_seed(case_type):
    first, second = sample_document(case_type, 101), sample_document(case_type, 202)
    assert first.content != second.content
    assert sample_document(case_type, 101) == first
    for sample in (first, second):
        assert sample.case_type == case_type
        assert sample.expected in {"filed", "review"}
        assert sample.filename.startswith("sample-") and sample.filename.endswith(".pdf")
        reader = PdfReader(io.BytesIO(sample.content))
        assert not reader.is_encrypted
        assert "".join(page.extract_text() for page in reader.pages).strip()
