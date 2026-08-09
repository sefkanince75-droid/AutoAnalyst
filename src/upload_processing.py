"""Single-read capture of Streamlit uploads for deterministic downstream use."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.dataset_combination import content_digest


class UploadedFileLike(Protocol):
    """Minimal interface used from Streamlit's UploadedFile."""

    name: str

    def getvalue(self) -> bytes: ...


class UploadCaptureError(ValueError):
    """Raised when an uploaded object cannot be captured as immutable bytes."""


@dataclass(frozen=True)
class CapturedUpload:
    """Immutable upload snapshot used by metadata, hashing, and parsers."""

    name: str
    content: bytes
    digest: str

    @property
    def size_bytes(self) -> int:
        return len(self.content)


def capture_upload(upload: UploadedFileLike) -> CapturedUpload:
    """Call ``getvalue`` once and normalize its result to immutable bytes."""
    try:
        value = upload.getvalue()
    except (AttributeError, TypeError, ValueError, OSError) as exc:
        raise UploadCaptureError("The uploaded file content could not be captured.") from exc

    if isinstance(value, bytes):
        content = value
    elif isinstance(value, (bytearray, memoryview)):
        content = bytes(value)
    else:
        raise UploadCaptureError(
            f"Uploaded content must be bytes-like, not {type(value).__name__}."
        )
    if not content:
        raise UploadCaptureError("The uploaded file is empty.")
    return CapturedUpload(str(upload.name), content, content_digest(content))


def capture_uploads(uploads: list[UploadedFileLike]) -> list[CapturedUpload]:
    """Capture each uploaded file exactly once, preserving uploader order."""
    return [capture_upload(upload) for upload in uploads]
