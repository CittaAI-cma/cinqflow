"""The Law-1 gate — the half that import-linter cannot see.

    "core imports no vendor SDK, no URL, no path, no credential"
    — docs/architecture/plates/03-chip-anatomy.md, core_forbidden

`.importlinter` handles the imports between our own packages and the named
vendor SDKs. Three of the four things Law 1 forbids are not imports at all:
a URL, a filesystem path, a region name or a credential written into core/ is
environment difference living somewhere other than the connection profile.

So this walks the AST and rejects, in core/:

  vendor-import   a top-level module outside the core allowlist
  io-call         open() / input() — core/ performs NO I/O
  hardcoded-url   a URL scheme in a string literal
  hardcoded-path  an absolute filesystem path
  region-name     a cloud region name
  credential      a credential-shaped literal

The allowlist is requirements/core.txt plus the pure standard library. That is
deliberate: "not on the list" is a mechanical question, whereas "is this a
vendor SDK?" is a judgement call, and a gate that needs judgement is not a gate.

Run standalone:   python -m conformance.lint_core_purity src/cinqflow/core
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# ── the allowlist ────────────────────────────────────────────────────────────
# requirements/core.txt, by import name. Nothing here opens a socket, a file
# handle or a database connection.
CORE_THIRD_PARTY: frozenset[str] = frozenset(
    {
        "pydantic",
        "pyarrow",
        "python_calamine",
        "openpyxl",
        "orjson",
        "jinja2",
        "dateutil",
    }
)

# Standard-library modules that perform or enable I/O. Everything else in the
# stdlib is pure enough for core/ (dataclasses, typing, enum, datetime, re,
# hashlib, decimal, uuid, fnmatch, textwrap, collections, functools, …).
STDLIB_IO: frozenset[str] = frozenset(
    {
        "os",
        "io",
        "sys",
        "pathlib",
        "socket",
        "socketserver",
        "subprocess",
        "shutil",
        "tempfile",
        "glob",
        "fileinput",
        "sqlite3",
        "dbm",
        "shelve",
        "pickle",
        "marshal",
        "urllib",
        "http",
        "ftplib",
        "smtplib",
        "poplib",
        "imaplib",
        "telnetlib",
        "ssl",
        "asyncio",
        "selectors",
        "signal",
        "multiprocessing",
        "threading",
        "concurrent",
        "ctypes",
        "mmap",
        "webbrowser",
        "logging",
        "importlib",
        "pkgutil",
        "runpy",
        "venv",
        "zipfile",
        "tarfile",
        "gzip",
        "bz2",
        "lzma",
        "configparser",
        "netrc",
        "platform",
        "getpass",
        "pty",
        "tty",
        "termios",
        "resource",
        "curses",
        "argparse",
        "secrets",
        "random",
        "time",
    }
)

IO_BUILTINS: frozenset[str] = frozenset({"open", "input", "eval", "exec", "compile", "__import__"})

# Specific names that may be imported FROM an otherwise-denied stdlib module,
# because they perform no I/O despite their module's name.
#
# `io.StringIO` and `io.BytesIO` are in-memory buffers. A parser receives bytes
# from the storage adapter and wraps them to hand to a reader — no descriptor
# is ever opened. `io.open` DOES open one, which is why the module itself stays
# denied and only these two names are allowed through: the precise form keeps
# the guarantee that `import io` would quietly surrender.
NAME_ALLOWLIST: dict[str, frozenset[str]] = {
    "io": frozenset({"StringIO", "BytesIO"}),
}

# ── the literal patterns ─────────────────────────────────────────────────────
URL_SCHEME = re.compile(
    r"\b(?:https?|ftps?|sftp|s3|abfss?|gs|wasbs?|postgresql|postgres"
    r"|mysql|mongodb|redis|jdbc|grpc|wss?)://",
    re.IGNORECASE,
)
# Filesystem roots only. Deliberately NOT a generic "starts with /" rule: core/
# legitimately carries UI routes ("/data/intake/feed/..."), which are domain
# logic and identical at every rung, whereas "/mnt/adls/raw" is a mount point
# that differs per environment and therefore belongs in the connection profile.
# The distinguishing feature is the ROOT, so the root is what is matched.
ABSOLUTE_PATH = re.compile(
    r"(?:^|[\s\"'=(,])(?:/(?:mnt|var|etc|opt|usr|home|Users|dbfs|tmp|srv|proc|dev|"
    r"Volumes|volumes|databricks|lakehouse)/|[A-Za-z]:\\\\|\\\\\\\\[A-Za-z])"
)
# Azure regions the deployment plate names, plus the families they belong to.
REGION_NAME = re.compile(
    r"\b(?:eastus2?|westus[23]?|centralus|northeurope|westeurope|uksouth|ukwest|"
    r"eastasia|southeastasia|australiaeast|canadacentral|"
    r"us-east-[12]|us-west-[12]|eu-west-[123]|ap-south-1)\b",
    re.IGNORECASE,
)
CREDENTIAL = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{12,}"  # OpenAI-shaped
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"  # Slack-shaped
    r"|AKIA[0-9A-Z]{12,}"  # AWS-shaped
    r"|ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."  # JWT-shaped
    r"|AccountKey=|SharedAccessSignature=|password=|pwd=|api[_-]?key=)",
    re.IGNORECASE,
)

# `secret://name` is the ONLY reference form core/ may carry: it names a secret
# without holding one. Resolution is the secrets adapter's job.
SECRET_REFERENCE = re.compile(r"^secret://[A-Za-z0-9._-]+$")

LITERAL_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("hardcoded-url", URL_SCHEME),
    ("hardcoded-path", ABSOLUTE_PATH),
    ("region-name", REGION_NAME),
    ("credential", CREDENTIAL),
)


@dataclass(frozen=True, order=True)
class Violation:
    """One refusal, addressed precisely enough to fix without a search."""

    path: str
    line: int
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}  {self.kind}  {self.detail}"


def _root_module(name: str) -> str:
    return name.split(".", 1)[0]


def _is_allowed_import(root: str) -> bool:
    if root in {"cinqflow", "__future__"}:
        return True
    if root in CORE_THIRD_PARTY:
        return True
    if root in STDLIB_IO:
        return False
    return root in sys.stdlib_module_names


def lint_source(source: str, path: str) -> list[Violation]:
    """Lint one module's text. Pure: no file access, which is the point."""
    violations: list[Violation] = []
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:  # a module that will not parse cannot be certified
        return [Violation(path, exc.lineno or 0, "syntax-error", str(exc.msg))]

    for node in ast.walk(tree):
        # ── imports ──────────────────────────────────────────────────────────
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = _root_module(alias.name)
                if not _is_allowed_import(root):
                    violations.append(
                        Violation(path, node.lineno, "vendor-import", f"import {alias.name}")
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                root = _root_module(node.module)
                allowed_names = NAME_ALLOWLIST.get(node.module, frozenset())
                imported = {alias.name for alias in node.names}
                if not _is_allowed_import(root) and not imported <= allowed_names:
                    violations.append(
                        Violation(
                            path, node.lineno, "vendor-import", f"from {node.module} import …"
                        )
                    )

        # ── I/O calls ────────────────────────────────────────────────────────
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in IO_BUILTINS:
                violations.append(Violation(path, node.lineno, "io-call", f"{func.id}()"))

        # ── string literals ──────────────────────────────────────────────────
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if SECRET_REFERENCE.match(text.strip()):
                continue  # naming a secret is not holding one
            for kind, pattern in LITERAL_RULES:
                if match := pattern.search(text):
                    violations.append(
                        Violation(path, node.lineno, kind, _redact(text, match.group(0), kind))
                    )

    return sorted(violations)


def _redact(text: str, matched: str, kind: str) -> str:
    """Report the violation without reprinting a credential into the CI log."""
    if kind == "credential":
        return f"credential-shaped literal near {matched[:8]}…"
    snippet = text if len(text) <= 80 else text[:77] + "…"
    return repr(snippet)


def lint_path(target: Path) -> list[Violation]:
    """Lint a module or a tree. Returns every violation, sorted, never raises."""
    if not target.exists():
        return []
    files = sorted(target.rglob("*.py")) if target.is_dir() else [target]
    violations: list[Violation] = []
    for file in files:
        if "__pycache__" in file.parts:
            continue
        violations.extend(lint_source(file.read_text(encoding="utf-8"), str(file)))
    return violations


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:]] or [Path("src/cinqflow/core")]
    violations = [v for t in targets for v in lint_path(t)]
    if not violations:
        print(f"core-purity: GREEN — {', '.join(str(t) for t in targets)}")
        return 0
    print("core-purity: RED")
    print('  INVARIANT: "core imports no vendor SDK, no URL, no path, no credential"')
    print("             — docs/architecture/plates/03-chip-anatomy.md\n")
    for v in violations:
        print(f"  {v}")
    print(
        f"\n{len(violations)} violation(s). Vendor code lives in adapters/; "
        "environment difference lives in the connection profile."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
