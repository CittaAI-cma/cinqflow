"""The die. No I/O happens here — that is Law 1, operationally sharpened.

Everything in this package is synchronous and pure: it receives values and
returns values. Bytes arrive from the storage adapter; rows leave through the
compute adapter. Keeping the domain synchronous stops async from leaking into
the compiler and the reconciler, where it buys nothing and costs testability.

Enforced by .importlinter (imports) and conformance/lint_core_purity.py
(URLs, paths, regions, credentials, and any call that performs I/O).
"""
