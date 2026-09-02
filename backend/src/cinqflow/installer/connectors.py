"""Profile in, connectors out. CF-V1-E8-09's "zero code per source".

    "Configure every connector entirely from the feed registry's delivery
     section plus profile secrets — zero code per source."
    — CF-V1-E8-09, acceptance criteria

Mirrors `intelligence.wiring.llm_from`: every value an adapter needs is read
HERE, from the profile, and there is no second way to build one in the
running system. What is new here, and what LLM routing does not need, is that
a connector is chosen PER FEED, not per deployment — `payer-a` and `payer-b`
can arrive by different methods on the same socket. `FeedOperations.
endpoint_ref` is the registry's half of that ("a NAME the connection profile
resolves"); `connector.routes` in the profile is the other half, and this
function is the join.

ONBOARDING A NEW SFTP FEED IS A REGISTRY ROW AND A PROFILE ENTRY. Give the
feed an `endpoint_ref` and add a route under that name — `{adapter:
folder-drop, drop_root: ...}` at rung 0.5, later `{adapter: sftp-poller,
host: ..., path: ...}` at rung 3 with a real client behind it. No engine code
moves either time, which is the whole claim ADR-0023 makes about connectors.
"""

from __future__ import annotations

from typing import Any

from cinqflow.adapters.local.folder_connector import FolderDropConnector
from cinqflow.adapters.local.localfs_storage import LocalFsStorage
from cinqflow.adapters.local.upload_connector import UploadConnector
from cinqflow.adapters.mock.connector import ScriptedConnector
from cinqflow.adapters.sftp.connector import SftpPollerConnector
from cinqflow.core.model.profile import Profile, ProfileError
from cinqflow.ports.connector import ConnectorPort
from cinqflow.ports.secrets import SecretsPort

__all__ = ["connectors_from"]

#: The adapters a profile may name for a connector route. `api-puller`,
#: `fhir-puller` and `db-extractor` join this set later — plate 09 has named
#: them since Wave 0. `sftp-poller` is fitted (ADR-0023): a real `asyncssh`
#: client behind `adapters/sftp/connector.py`; `folder-drop` remains its
#: local dev stand-in for a source with no real SFTP endpoint to point at.
_ADAPTERS = frozenset({"none", "mock", "scripted", "upload", "folder-drop", "sftp-poller"})


def connectors_from(
    profile: Profile, *, storage: LocalFsStorage | None, secrets: SecretsPort | None = None
) -> dict[str, ConnectorPort]:
    """Every fitted connector route, keyed by the name `endpoint_ref` names.

    A route whose adapter is `none` (or absent entirely) is OMITTED from the
    result rather than mapped to a stub — the same honest-`None` shape
    `create_app`'s own `connector` parameter uses, so a caller resolving a
    route that was never fitted gets "not configured", never a connector that
    silently accepts into nowhere.

    An empty `connector` pin (no `routes` at all) returns `{}` rather than
    raising: `profiles/ci.yaml` fits nothing on purpose ("nothing is
    delivered in CI"), and that is a legitimate, tested socket — not a
    malformed profile.
    """
    config = profile.pins.get("connector", {})
    routes: dict[str, Any] = config.get("routes", {})
    if not isinstance(routes, dict):
        raise ProfileError(
            f"{profile.source}: connector.routes is a mapping of name -> adapter config"
        )
    built: dict[str, ConnectorPort] = {}
    for name, route in routes.items():
        if not isinstance(route, dict):
            raise ProfileError(
                f"{profile.source}: connector.routes.{name} is a mapping, not {route!r}"
            )
        connector = _build(profile, storage, secrets, name=str(name), route=route)
        if connector is not None:
            built[str(name)] = connector
    return built


def _build(
    profile: Profile,
    storage: LocalFsStorage | None,
    secrets: SecretsPort | None,
    *,
    name: str,
    route: dict[str, Any],
) -> ConnectorPort | None:
    adapter = str(route.get("adapter", "none"))
    if adapter not in _ADAPTERS:
        raise ProfileError(
            f"{profile.source}: connector.routes.{name}: {adapter!r} is not a connector "
            f"adapter. Fitted seats are {', '.join(sorted(_ADAPTERS - {'none'}))}; a real "
            "protocol client (api-puller, fhir-puller, db-extractor) joins the pin at rung "
            "3, as configuration, not as new engine code."
        )
    if adapter == "none":
        return None
    if adapter in ("mock", "scripted"):
        return ScriptedConnector(source=str(route.get("source", name)))
    if storage is None:
        raise ProfileError(
            f"{profile.source}: connector.routes.{name} needs the `storage` pin fitted — a "
            f"{adapter} connector lands into a landing zone that has to already exist."
        )
    if adapter == "upload":
        return UploadConnector(storage, source=str(route.get("source", "upload-endpoint")))
    if adapter == "folder-drop":
        drop_root = route.get("drop_root")
        if not drop_root:
            raise ProfileError(
                f"{profile.source}: connector.routes.{name} is folder-drop with no "
                "`drop_root` — there is nowhere to poll."
            )
        return FolderDropConnector(
            storage, drop_root=str(drop_root), source=str(route.get("source", name))
        )
    # adapter == "sftp-poller"
    if secrets is None:
        raise ProfileError(
            f"{profile.source}: connector.routes.{name} is sftp-poller with no `secrets` "
            "pin available — a real client needs somewhere to resolve its password from."
        )
    host = secrets.resolve(str(route.get("host", "")))
    username = secrets.resolve(str(route.get("username", "")))
    if not host or not username:
        raise ProfileError(
            f"{profile.source}: connector.routes.{name} is sftp-poller with no `host` or "
            "no `username` — there is nowhere to connect and nobody to connect as."
        )
    return SftpPollerConnector(
        storage,
        host=host,
        port=int(route.get("port", 22)),
        username=username,
        password=secrets.resolve(str(route.get("password", ""))) or None,
        remote_root=str(route.get("remote_root", ".")),
        source=str(route.get("source", name)),
        known_hosts=route.get("known_hosts"),
    )
