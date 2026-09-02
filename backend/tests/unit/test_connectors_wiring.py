"""CF-V1-E8-09's "zero code per source" — the profile-to-connector join.

    "Configure every connector entirely from the feed registry's delivery
     section plus profile secrets — zero code per source."
    — CF-V1-E8-09, acceptance criteria

`connectors_from` is the ONLY way a connector is built in the running system,
mirroring `intelligence.wiring.llm_from`. This asserts that in isolation, with
a hand-built `Profile`, rather than through a whole running app — the same
reason `test_profile.py` tests the profile loader on its own.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cinqflow.adapters.local.folder_connector import FolderDropConnector
from cinqflow.adapters.local.localfs_storage import LocalFsStorage
from cinqflow.adapters.local.upload_connector import UploadConnector
from cinqflow.adapters.mock.connector import ScriptedConnector
from cinqflow.adapters.mock.secrets import MemSecrets
from cinqflow.adapters.sftp.connector import SftpPollerConnector
from cinqflow.core.model.profile import Profile, ProfileError
from cinqflow.core.model.vocabulary import Mode
from cinqflow.installer.connectors import connectors_from

pytestmark = pytest.mark.unit


def _profile(pins: dict[str, dict[str, object]]) -> Profile:
    return Profile(source="test.yaml", rung=0.5, socket="test", mode=Mode.FULL, pins=pins)


def test_no_connector_pin_at_all_fits_nothing() -> None:
    assert connectors_from(_profile({}), storage=None) == {}


def test_an_empty_routes_mapping_fits_nothing() -> None:
    assert connectors_from(_profile({"connector": {"routes": {}}}), storage=None) == {}


def test_a_none_adapter_route_is_omitted_not_stubbed() -> None:
    """The same honest-`None` shape `create_app`'s own `connector` param uses
    — a route naming `none` is absent, never a connector that accepts into
    nowhere."""
    profile = _profile({"connector": {"routes": {"default": {"adapter": "none"}}}})
    assert connectors_from(profile, storage=None) == {}


def test_a_mock_route_needs_no_storage() -> None:
    profile = _profile({"connector": {"routes": {"default": {"adapter": "mock"}}}})
    connectors = connectors_from(profile, storage=None)
    assert isinstance(connectors["default"], ScriptedConnector)


def test_two_named_routes_build_two_distinct_adapters(tmp_path: Path) -> None:
    """The multi-route shape this story adds: `default` for manual upload,
    a NAMED route for a feed whose `endpoint_ref` points at it — the SFTP
    seat's dev stand-in, per ADR-0023."""
    storage = LocalFsStorage(root=str(tmp_path / "landing"))
    profile = _profile(
        {
            "connector": {
                "routes": {
                    "default": {"adapter": "upload"},
                    "fidelis-sftp": {
                        "adapter": "folder-drop",
                        "drop_root": str(tmp_path / "drop"),
                    },
                }
            }
        }
    )
    connectors = connectors_from(profile, storage=storage)
    assert isinstance(connectors["default"], UploadConnector)
    assert isinstance(connectors["fidelis-sftp"], FolderDropConnector)
    assert connectors["fidelis-sftp"].drop_root == str(tmp_path / "drop")


def test_an_upload_route_with_no_storage_pin_is_refused() -> None:
    profile = _profile({"connector": {"routes": {"default": {"adapter": "upload"}}}})
    with pytest.raises(ProfileError, match="storage"):
        connectors_from(profile, storage=None)


def test_a_folder_drop_route_with_no_drop_root_is_refused(tmp_path: Path) -> None:
    storage = LocalFsStorage(root=str(tmp_path / "landing"))
    profile = _profile({"connector": {"routes": {"default": {"adapter": "folder-drop"}}}})
    with pytest.raises(ProfileError, match="drop_root"):
        connectors_from(profile, storage=storage)


def test_an_unknown_adapter_name_is_refused_with_the_fitted_list() -> None:
    """`api-puller` names a real seat plate 09 reserves, but nothing is
    fitted to it yet — unlike `sftp-poller`, which now is (see below)."""
    profile = _profile({"connector": {"routes": {"default": {"adapter": "api-puller"}}}})
    with pytest.raises(ProfileError, match="api-puller"):
        connectors_from(profile, storage=None)


def test_an_sftp_poller_route_builds_a_real_client(tmp_path: Path) -> None:
    """ADR-0023: a real `asyncssh` client, configured entirely from the
    profile plus a resolved secret — zero code per source."""
    storage = LocalFsStorage(root=str(tmp_path / "landing"))
    secrets = MemSecrets({"sftp-password": "s3cret"})
    profile = _profile(
        {
            "connector": {
                "routes": {
                    "fidelis-sftp": {
                        "adapter": "sftp-poller",
                        "host": "simulator",
                        "username": "cinqflow",
                        "password": "secret://sftp-password",
                        "remote_root": "incoming",
                    }
                }
            }
        }
    )
    connectors = connectors_from(profile, storage=storage, secrets=secrets)
    connector = connectors["fidelis-sftp"]
    assert isinstance(connector, SftpPollerConnector)
    assert connector.source == "fidelis-sftp"


def test_an_sftp_poller_route_with_no_secrets_pin_is_refused(tmp_path: Path) -> None:
    storage = LocalFsStorage(root=str(tmp_path / "landing"))
    profile = _profile(
        {
            "connector": {
                "routes": {
                    "fidelis-sftp": {
                        "adapter": "sftp-poller",
                        "host": "simulator",
                        "username": "cinqflow",
                    }
                }
            }
        }
    )
    with pytest.raises(ProfileError, match="secrets"):
        connectors_from(profile, storage=storage)


def test_an_sftp_poller_route_with_no_host_is_refused(tmp_path: Path) -> None:
    storage = LocalFsStorage(root=str(tmp_path / "landing"))
    secrets = MemSecrets({})
    profile = _profile(
        {
            "connector": {
                "routes": {"fidelis-sftp": {"adapter": "sftp-poller", "username": "cinqflow"}}
            }
        }
    )
    with pytest.raises(ProfileError, match="host"):
        connectors_from(profile, storage=storage, secrets=secrets)


def test_routes_that_is_not_a_mapping_is_refused() -> None:
    profile = _profile({"connector": {"routes": ["upload"]}})
    with pytest.raises(ProfileError, match="mapping"):
        connectors_from(profile, storage=None)


def test_a_route_that_is_not_a_mapping_is_refused() -> None:
    profile = _profile({"connector": {"routes": {"default": "upload"}}})
    with pytest.raises(ProfileError, match="mapping"):
        connectors_from(profile, storage=None)
