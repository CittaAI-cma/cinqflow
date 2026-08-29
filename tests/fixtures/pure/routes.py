"""A pure module carrying UI routes. It must PASS.

A route is domain logic — the same string at rung 0 and rung 4 — so it is not
environment difference and does not belong in the connection profile. The
core-purity lint must tell a route apart from a mount point, and this fixture
is what holds it to that.
"""

from __future__ import annotations

HOME = "/"
FEED = "/data/intake/feed"
BATCH = "/operations/control/batch"
GLOSSARY = "/data/intake/glossary"
ASK = "/ai/ask"
