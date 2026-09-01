"""CINQFLOW — a CINQCARE-owned, metadata-driven healthcare data platform.

The architecture is the chip metaphor, and five laws govern every change:

  1. core/ imports no vendor SDK, URL, path or credential.
  2. Every external touch crosses a port — real / dev stand-in / mock,
     sharing ONE contract suite.
  3. All environment difference lives in the connection profile. Nowhere else.
  4. Agents propose; humans dispose. R4 is human-always, not configurable.
  5. Acceptance criteria are the tests, written first.

See docs/architecture/ in the knowledge pack; cite plates, never paraphrase them.
"""

__version__ = "0.1.0"
