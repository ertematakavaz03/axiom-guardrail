from __future__ import annotations

import io
import re
from dataclasses import dataclass

from pypdf import PdfReader

from services.rag.errors import DocumentParsingError
from services.rag.models import ChunkDraft, ParsedSection

SUPPORTED_MIME_TYPES = {
    "text/plain": "text",
    "text/markdown": "markdown",
    "application/pdf": "pdf",
}


@dataclass(frozen=True)
class ChunkingConfig:
    chunk_size_tokens: int = 600
    overlap_tokens: int = 75

    def __post_init__(self) -> None:
        if self.chunk_size_tokens < 1:
            raise ValueError("chunk_size_tokens must be positive")
        if self.overlap_tokens < 0 or self.overlap_tokens >= self.chunk_size_tokens:
            raise ValueError("overlap_tokens must be non-negative and smaller than chunk size")


def normalize_text(value: str) -> str:
    value = value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def parse_document(content: bytes, source_type: str, mime_type: str | None = None) -> list[ParsedSection]:
    if not content:
        raise DocumentParsingError("Document content is empty")
    if mime_type and mime_type not in SUPPORTED_MIME_TYPES:
        raise DocumentParsingError(
            "Unsupported document MIME type", details={"mime_type": mime_type}
        )
    if source_type == "pdf":
        return _parse_pdf(content)
    if source_type not in {"text", "markdown"}:
        raise DocumentParsingError(
            "Unsupported document source type", details={"source_type": source_type}
        )
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentParsingError("Text document must be valid UTF-8") from exc
    normalized = normalize_text(text)
    if not normalized:
        raise DocumentParsingError("Document contains no usable text")
    if source_type == "markdown":
        return _markdown_sections(normalized)
    return [ParsedSection(text=normalized)]


def chunk_sections(sections: list[ParsedSection], config: ChunkingConfig) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = []
    step = config.chunk_size_tokens - config.overlap_tokens
    for section in sections:
        tokens = section.text.split()
        for start in range(0, len(tokens), step):
            window = tokens[start : start + config.chunk_size_tokens]
            if not window:
                continue
            drafts.append(
                ChunkDraft(
                    text=" ".join(window),
                    token_count=len(window),
                    chunk_index=len(drafts),
                    page_number=section.page_number,
                    section_title=section.section_title,
                )
            )
            if start + config.chunk_size_tokens >= len(tokens):
                break
    if not drafts:
        raise DocumentParsingError("Document produced no usable chunks")
    return drafts


def _parse_pdf(content: bytes) -> list[ParsedSection]:
    try:
        reader = PdfReader(io.BytesIO(content))
        sections = [
            ParsedSection(text=normalize_text(page.extract_text() or ""), page_number=index + 1)
            for index, page in enumerate(reader.pages)
        ]
    except Exception as exc:
        raise DocumentParsingError("PDF could not be parsed") from exc
    usable = [section for section in sections if section.text]
    if not usable:
        raise DocumentParsingError("PDF contains no extractable text; OCR is not enabled")
    return usable


def _markdown_sections(text: str) -> list[ParsedSection]:
    sections: list[ParsedSection] = []
    title: str | None = None
    lines: list[str] = []
    for line in text.splitlines():
        if re.match(r"^#{1,6}\s+", line):
            if lines:
                sections.append(ParsedSection(text="\n".join(lines).strip(), section_title=title))
            title = re.sub(r"^#{1,6}\s+", "", line).strip()
            lines = []
        else:
            lines.append(line)
    if lines:
        sections.append(ParsedSection(text="\n".join(lines).strip(), section_title=title))
    return [section for section in sections if section.text] or [ParsedSection(text=text)]
