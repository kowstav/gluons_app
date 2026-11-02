"""Bounded, local-only document extraction behind a Gluons-owned interface.

Upstreams: MarkItDown 0.1.9 (MIT), pypdf 6.21.4 (BSD-3-Clause). No URL,
plugin, OCR, model endpoint or external process is enabled by this adapter. Callers
must supply bytes from an authorized tenant artifact; extraction grants no authority.
"""
from __future__ import annotations

import io
import stat
import zipfile
from dataclasses import dataclass
from importlib.metadata import version
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from gluons.core import GluonsError, sha256_hex
from gluons.security import scan_untrusted


@dataclass(frozen=True)
class ExtractionLimits:
    max_bytes: int = 8 * 1024 * 1024
    max_pages: int = 100
    max_characters: int = 250000
    max_archive_bytes: int = 32 * 1024 * 1024
    max_archive_entries: int = 1000
    max_pdf_content_bytes_per_page: int = 2 * 1024 * 1024


def _limit(message: str) -> GluonsError:
    return GluonsError("DOCUMENT_LIMIT_EXCEEDED", message, status=422)


def _archive_check(data: bytes, limits: ExtractionLimits) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > limits.max_archive_entries or sum(entry.file_size for entry in entries) > limits.max_archive_bytes:
                raise _limit("The document archive exceeds extraction limits.")
            if any(entry.file_size > max(1, entry.compress_size) * 200 for entry in entries):
                raise _limit("The document has an unsafe archive compression ratio.")
            if any(entry.flag_bits & 0x1 for entry in entries):
                raise GluonsError("DOCUMENT_ENCRYPTED", "Unlock this document through an approved local process first.", status=422)
            if any("vbaproject" in entry.filename.lower() for entry in entries):
                raise GluonsError("DOCUMENT_FORMAT_UNSUPPORTED", "Macro-bearing Office files are not supported.", status=422)
    except zipfile.BadZipFile:
        raise GluonsError("DOCUMENT_INVALID", "The Office document is not a valid archive.", status=422) from None


def extract_bytes(data: bytes, *, filename: str, limits: ExtractionLimits | None = None) -> dict[str, Any]:
    """Convert authorized bytes to untrusted text with hashes and page provenance."""
    limits = limits or ExtractionLimits()
    if any(getattr(limits, name) <= 0 for name in limits.__dataclass_fields__):
        raise ValueError("extraction limits must be positive")
    if len(data) > limits.max_bytes:
        raise _limit("The document exceeds the permitted input size.")
    extension = Path(filename).suffix.lower()
    if extension not in {".txt", ".md", ".csv", ".json", ".pdf", ".html", ".docx", ".xlsx", ".pptx"}:
        raise GluonsError("DOCUMENT_FORMAT_UNSUPPORTED", "Use text, PDF or a supported non-macro Office document.", status=422)
    pages: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        if extension == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise GluonsError("DOCUMENT_ENCRYPTED", "Unlock this PDF through an approved local process first.", status=422)
            if len(reader.pages) > limits.max_pages:
                raise _limit("The PDF exceeds the permitted page count.")
            texts: list[str] = []
            length = 0
            for index, page in enumerate(reader.pages):
                contents = page.get_contents()
                if contents is not None and len(contents.get_data()) > limits.max_pdf_content_bytes_per_page:
                    raise _limit("A PDF page's decoded content exceeds extraction limits.")
                text = page.extract_text() or ""
                if length + len(text) > limits.max_characters:
                    raise _limit("The extracted text exceeds the permitted character count.")
                pages.append({"page": index + 1, "start": length, "end": length + len(text)})
                texts.append(text)
                length += len(text) + 1
                if not text.strip():
                    warnings.append(f"Page {index + 1} has no extractable text; OCR may be required.")
            text = "\n".join(texts)
            extractor = f"pypdf:{version('pypdf')}"
        elif extension in {".txt", ".md", ".csv", ".json"}:
            text = data.decode("utf-8-sig", errors="strict")
            extractor = "utf8"
        else:
            if extension != ".html":
                _archive_check(data, limits)
                package = {".docx": "mammoth", ".xlsx": "openpyxl", ".pptx": "pptx"}[extension]
                if find_spec(package) is None:
                    raise GluonsError("CONFIGURATION_REQUIRED", f"Install the MarkItDown {extension[1:]} optional extra to process this format.", status=409)
            from markitdown import MarkItDown, StreamInfo

            converter = MarkItDown(enable_plugins=False)
            result = converter.convert_stream(io.BytesIO(data), stream_info=StreamInfo(extension=extension))
            text = result.text_content
            extractor = f"markitdown:{version('markitdown')}"
            warnings.append("Source page coordinates are not preserved by this extractor.")
    except GluonsError:
        raise
    except ImportError:
        raise GluonsError("CONFIGURATION_REQUIRED", "Install the pinned Gluons documents extra to process this format.", status=409) from None
    except Exception:  # noqa: BLE001 - parser exceptions may include private document contents
        raise GluonsError("DOCUMENT_INVALID", "This document could not be parsed safely. No extracted result was retained.", status=422) from None
    if len(text) > limits.max_characters:
        raise _limit("The extracted text exceeds the permitted character count.")
    return {
        "text": text, "source_hash": sha256_hex(data), "text_hash": sha256_hex(text),
        "extractor": extractor, "page_spans": pages, "warnings": warnings,
        "untrusted": True, "injection_flags": scan_untrusted(text), "external_calls": 0,
        "limitations": ["Extraction does not verify business facts, document authenticity, signatures or OCR accuracy."],
    }


def extract_from_root(relative_path: str, *, approved_root: Path, limits: ExtractionLimits | None = None) -> dict[str, Any]:
    """Only read files under a server-owned private artifact root, never arbitrary paths.

    `approved_root` is deployment configuration, never an MCP input or model output.
    Symlinks/junction escapes, absolute paths, URLs and Windows alternate streams fail.
    """
    limits = limits or ExtractionLimits()
    path = Path(relative_path)
    if path.is_absolute() or ":" in relative_path or relative_path.startswith(("\\", "/")) or ".." in path.parts:
        raise GluonsError("ARTIFACT_PATH_FORBIDDEN", "Choose a relative file inside the authorized artifact store.", status=403)
    try:
        root = approved_root.resolve(strict=True)
        candidate = (root / path).resolve(strict=True)
        candidate.relative_to(root)
        if not stat.S_ISREG(candidate.stat().st_mode) or any((root / Path(*path.parts[:index])).is_symlink() for index in range(1, len(path.parts) + 1)):
            raise ValueError("not a regular private artifact")
        with candidate.open("rb") as stream:
            data = stream.read(limits.max_bytes + 1)
    except (OSError, ValueError):
        raise GluonsError("ARTIFACT_PATH_FORBIDDEN", "That file is unavailable inside the authorized artifact store.", status=403) from None
    return extract_bytes(data, filename=candidate.name, limits=limits)
