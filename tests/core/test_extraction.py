from pathlib import Path

import pytest

from docket.core.extraction import (
    DocumentParseError,
    Extracted,
    Rejected,
    RejectionReason,
    extract_document,
    truncate_for_llm,
)
from docket.corpus import pdf


@pytest.mark.parametrize(
    "content,kwargs,reason",
    [
        (None, {}, "no_attachment"),
        (b"", {}, "zero_byte"),
        (b"text", {}, "unsupported_type"),
        (b"%PDF-1.7", {"content_type": "text/plain"}, "unsupported_type"),
        (b"12345", {"max_bytes": 4}, "too_large"),
    ],
)
def test_rejections_are_typed(content, kwargs, reason):
    result = extract_document(content, **kwargs)
    assert result == Rejected(RejectionReason(reason))


def test_extracts_actual_pdf_and_empty_text_layer():
    result = extract_document(pdf(["Lease agreement", "UNIT-0001"]))
    assert isinstance(result, Extracted)
    assert "Lease agreement" in result.text and "UNIT-0001" in result.llm_text
    assert result.page_count == 1
    empty = extract_document(pdf([], empty=True))
    assert isinstance(empty, Extracted) and empty.text == empty.llm_text == ""


def test_encrypted_and_corrupt_committed_fixtures():
    folder = Path("corpus/seed-7")
    encrypted = next(folder.glob("*-encrypted.pdf")).read_bytes()
    assert extract_document(encrypted) == Rejected(RejectionReason.ENCRYPTED)
    corrupt = next(folder.glob("*-corrupt.pdf")).read_bytes()
    with pytest.raises(DocumentParseError) as error:
        extract_document(corrupt)
    assert error.value.non_retryable and error.value.__cause__ is not None


@pytest.mark.parametrize("size", [0, 100, 7999, 8000, 8001, 20000])
def test_truncation_preserves_first_6k_and_last_2k(size):
    text = "".join(str(index % 10) for index in range(size))
    expected = text if size <= 8000 else text[:6000] + text[-2000:]
    assert truncate_for_llm(text) == expected


def test_pdf_limit_boundary_and_media_type_parameters():
    content = pdf(["Boundary test"])
    assert isinstance(
        extract_document(
            content, max_bytes=len(content), content_type="Application/PDF; charset=binary"
        ),
        Extracted,
    )
    assert extract_document(content, max_bytes=len(content) - 1) == Rejected(
        RejectionReason.TOO_LARGE
    )
    with pytest.raises(ValueError, match="positive"):
        extract_document(content, max_bytes=0)
