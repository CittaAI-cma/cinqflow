"""A scripted parser. The `document_parse` pin's mock seat.

    document_parse: {mock: canned}
    — docs/architecture/plates/04-pin-out-map.md

Returns whatever a test scripts for a given `(content, media_type)` pair, or —
for `text/plain` and `text/csv`, where "parse" is honestly just "decode" —
produces a real one-page `ParsedDocument` with no scripting required at all.
That default is not a shortcut around the port: a plain-text or CSV upload
genuinely has no layout to preserve, so the mock's real behaviour and the real
adapter's real behaviour AGREE on those two media types, and only the two
formats that need actual parsing libraries (PDF, DOCX) need scripting here.
"""

from __future__ import annotations

from cinqflow.ports import port
from cinqflow.ports.document_parse import (
    DocumentParseError,
    ParsedDocument,
    ParsedPage,
)

__all__ = ["ScriptedDocumentParser"]


@port("document_parse", "mock")
class ScriptedDocumentParser:
    def __init__(self) -> None:
        self._scripted: dict[tuple[bytes, str], ParsedDocument] = {}
        self._refuse: dict[tuple[bytes, str], str] = {}

    # ── scripting ────────────────────────────────────────────────────────────
    def script(self, content: bytes, *, media_type: str, result: ParsedDocument) -> None:
        """Say what parsing `content` (of `media_type`) should produce."""
        self._scripted[(content, media_type)] = result

    def refuse(self, content: bytes, *, media_type: str, reason: str) -> None:
        """Say that parsing `content` should fail, and why."""
        self._refuse[(content, media_type)] = reason

    # ── the port ─────────────────────────────────────────────────────────────
    def parse(self, content: bytes, *, media_type: str, filename: str = "") -> ParsedDocument:
        key = (content, media_type)
        if key in self._refuse:
            raise DocumentParseError(self._refuse[key])
        if key in self._scripted:
            return self._scripted[key]
        if media_type in ("text/plain", "text/markdown"):
            text = content.decode("utf-8", errors="replace")
            if not text.strip():
                raise DocumentParseError(f"{filename or '(unnamed)'}: empty document")
            return ParsedDocument(media_type=media_type, pages=(ParsedPage(number=1, text=text),))
        if media_type == "text/csv":
            text = content.decode("utf-8", errors="replace")
            if not text.strip():
                raise DocumentParseError(f"{filename or '(unnamed)'}: empty document")
            return ParsedDocument(media_type=media_type, pages=(ParsedPage(number=1, text=text),))
        raise DocumentParseError(
            f"{filename or '(unnamed)'}: no scripted result for this {media_type} content — "
            "script one with .script(), or use text/plain or text/csv for the unscripted default"
        )
