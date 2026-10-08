import json

import pytest

from morx.config import ConfigError, DeviceConfig, Settings


# -- single device from env -------------------------------------------------


def test_single_device_from_env(clean_env):
    clean_env.setenv("MORX_DEVICE_HOST", "10.0.0.5")
    clean_env.setenv("MORX_DEVICE_PORT", "5000")

    settings = Settings.load()
    assert len(settings.devices) == 1
    assert (settings.devices[0].host, settings.devices[0].port) == ("10.0.0.5", 5000)


def test_device_name_defaults_to_host(clean_env):
    clean_env.setenv("MORX_DEVICE_HOST", "10.0.0.5")
    assert Settings.load().devices[0].label == "10.0.0.5"


def test_no_device_configured_is_an_actionable_error(clean_env):
    with pytest.raises(ConfigError, match="MORX_DEVICE_HOST"):
        Settings.load()


@pytest.mark.parametrize("value,expected", [("true", True), ("no", False), ("1", True), ("off", False)])
def test_bool_env_accepts_common_spellings(clean_env, value, expected):
    clean_env.setenv("MORX_DEVICE_HOST", "10.0.0.5")
    clean_env.setenv("MORX_DEVICE_FORCE_UDP", value)
    assert Settings.load().devices[0].force_udp is expected


def test_bad_int_env_names_the_variable(clean_env):
    clean_env.setenv("MORX_DEVICE_HOST", "10.0.0.5")
    clean_env.setenv("MORX_DEVICE_PORT", "not-a-port")
    with pytest.raises(ConfigError, match="MORX_DEVICE_PORT"):
        Settings.load()


def test_bad_storage_backend_rejected(clean_env):
    clean_env.setenv("MORX_DEVICE_HOST", "10.0.0.5")
    clean_env.setenv("MORX_STORAGE_BACKEND", "mongodb")
    with pytest.raises(ConfigError, match="csv"):
        Settings.load()


# -- device validation ------------------------------------------------------


@pytest.mark.parametrize("port", [0, -1, 70000])
def test_port_out_of_range_rejected(port):
    with pytest.raises(ConfigError, match="port"):
        DeviceConfig(host="10.0.0.5", port=port)


def test_empty_host_rejected():
    with pytest.raises(ConfigError, match="host"):
        DeviceConfig(host="")


def test_non_positive_timeout_rejected():
    with pytest.raises(ConfigError, match="timeout"):
        DeviceConfig(host="10.0.0.5", timeout=0)


# -- multi-device inventory -------------------------------------------------


def test_inventory_file_with_defaults(clean_env, tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(
        json.dumps(
            {
                "defaults": {"port": 4370, "timeout": 15},
                "devices": [
                    {"name": "gate", "host": "10.0.0.1"},
                    {"name": "warehouse", "host": "10.0.0.2", "force_udp": True, "timeout": 30},
                ],
            }
        )
    )
    clean_env.setenv("MORX_DEVICES_FILE", str(path))

    devices = Settings.load().devices
    assert [d.label for d in devices] == ["gate", "warehouse"]
    assert devices[0].timeout == 15  # inherited from defaults
    assert devices[1].timeout == 30  # per-device override wins
    assert devices[1].force_udp is True


def test_inventory_file_accepts_a_bare_list(clean_env, tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps([{"host": "10.0.0.1"}, {"host": "10.0.0.2"}]))
    clean_env.setenv("MORX_DEVICES_FILE", str(path))
    assert len(Settings.load().devices) == 2


def test_inventory_file_overrides_single_device_env(clean_env, tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps([{"host": "10.0.0.9"}]))
    clean_env.setenv("MORX_DEVICE_HOST", "192.168.1.1")
    clean_env.setenv("MORX_DEVICES_FILE", str(path))
    assert [d.host for d in Settings.load().devices] == ["10.0.0.9"]


def test_duplicate_device_names_rejected(clean_env, tmp_path):
    """Device name is part of every dedup key — a collision would silently
    merge two terminals' data."""
    path = tmp_path / "devices.json"
    path.write_text(json.dumps([{"name": "gate", "host": "10.0.0.1"}, {"name": "gate", "host": "10.0.0.2"}]))
    clean_env.setenv("MORX_DEVICES_FILE", str(path))
    with pytest.raises(ConfigError, match="duplicate"):
        Settings.load()


def test_typo_in_device_option_is_caught(clean_env, tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps([{"host": "10.0.0.1", "porrt": 4370}]))
    clean_env.setenv("MORX_DEVICES_FILE", str(path))
    with pytest.raises(ConfigError, match="porrt"):
        Settings.load()


def test_missing_inventory_file_reported(clean_env, tmp_path):
    clean_env.setenv("MORX_DEVICES_FILE", str(tmp_path / "nope.json"))
    with pytest.raises(ConfigError, match="missing file"):
        Settings.load()


def test_malformed_inventory_file_reported(clean_env, tmp_path):
    path = tmp_path / "devices.json"
    path.write_text("{not json")
    clean_env.setenv("MORX_DEVICES_FILE", str(path))
    with pytest.raises(ConfigError, match="not valid JSON"):
        Settings.load()
