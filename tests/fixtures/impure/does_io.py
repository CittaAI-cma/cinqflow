"""A deliberately impure module. It exists to be REJECTED.

Law 1, operationally sharpened: core/ PERFORMS NO I/O. Parsers receive bytes
handed to them by the storage adapter; they never open a file themselves.
"""

import pathlib  # noqa: F401  — the violation under test


def read_roster() -> bytes:
    with open("roster.xlsx", "rb") as fh:  # noqa: PTH123 — the violation under test
        return fh.read()
