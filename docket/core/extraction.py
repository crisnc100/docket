import io
from dataclasses import dataclass
from enum import StrEnum

from pypdf import PdfReader
from pypdf.errors import PyPdfError


class RejectionReason(StrEnum):
    NO_ATTACHMENT = "no_attachment"
    UNSUPPORTED_TYPE = "unsupported_type"
    ZERO_BYTE = "zero_byte"
    ENCRYPTED = "encrypted"
    TOO_LARGE = "too_large"


@dataclass(frozen=True)
class Extracted:
    text: str
    llm_text: str
    page_count: int


@dataclass(frozen=True)
class Rejected:
    reason: RejectionReason


class DocumentParseError(Exception):
    """A malformed PDF cannot be repaired by retrying the same bytes."""

    non_retryable = True


def truncate_for_llm(text: str) -> str:
    return text if len(text) <= 8000 else text[:6000] + text[-2000:]


def extract_document(
    content: bytes | None,
    *,
    content_type: str = "application/pdf",
    max_bytes: int = 10 * 1024 * 1024,
) -> Extracted | Rejected:
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    if content is None:
        return Rejected(RejectionReason.NO_ATTACHMENT)
    if not content:
        return Rejected(RejectionReason.ZERO_BYTE)
    if len(content) > max_bytes:
        return Rejected(RejectionReason.TOO_LARGE)
    if content_type.split(";", 1)[0].strip().lower() != "application/pdf":
        return Rejected(RejectionReason.UNSUPPORTED_TYPE)
    if not content.startswith(b"%PDF-"):
        return Rejected(RejectionReason.UNSUPPORTED_TYPE)
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            return Rejected(RejectionReason.ENCRYPTED)
        text = "\n".join(page.extract_text() for page in reader.pages)
        return Extracted(text=text, llm_text=truncate_for_llm(text), page_count=len(reader.pages))
    except (PyPdfError, ValueError, TypeError, KeyError, OSError, NotImplementedError) as exc:
        raise DocumentParseError("Unable to parse PDF bytes") from exc
