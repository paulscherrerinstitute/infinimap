"""Config layering: flag over environment over file over built-in default.

The precedence is the point. Getting it backwards is invisible until someone
sets a variable in a systemd unit and the file quietly wins, and the collector
half already shipped one bug of exactly that shape.
"""

from __future__ import annotations

import argparse

import pytest

from infinimap.api import config as api_config
from infinimap.collector import config as col_config
from infinimap.collector.__main__ import _config_from


# -- API ---------------------------------------------------------------------

def test_api_env_overrides_each_field():
    cfg = api_config.from_env(api_config.Config(), {
        "INFINIMAP_DSN": "postgresql://u@h/d",
        "INFINIMAP_FABRIC": "fab",
        "INFINIMAP_HOST": "0.0.0.0",
        "INFINIMAP_PORT": "9001",
        "INFINIMAP_WEB_ROOT": "/srv/ui",
    })
    assert cfg.dsn == "postgresql://u@h/d"
    assert cfg.default_fabric == "fab"
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 9001
    assert str(cfg.web_root).replace("\\", "/") == "/srv/ui"


def test_api_absent_and_empty_env_change_nothing():
    base = api_config.Config()
    assert api_config.from_env(base, {}) == base
    # An unset variable in a unit file arrives as "", which must not blank the
    # configured value.
    assert api_config.from_env(base, {"INFINIMAP_DSN": ""}) == base


def test_api_port_must_be_an_integer():
    with pytest.raises(ValueError, match="INFINIMAP_PORT"):
        api_config.from_env(api_config.Config(), {"INFINIMAP_PORT": "http"})


def test_api_env_leaves_unmentioned_fields_alone():
    base = api_config.Config(pool_max=99)
    assert api_config.from_env(base, {"INFINIMAP_DSN": "x"}).pool_max == 99


# -- collector ---------------------------------------------------------------

def test_collector_env_overrides():
    cfg = col_config.from_env(col_config.defaults(), {
        "INFINIMAP_DSN": "postgresql://u@h/d",
        "INFINIMAP_FABRIC": "fab",
        "INFINIMAP_INTERVAL": "60",
        "INFINIMAP_TRAFFIC_INTERVAL": "5",
    })
    assert (cfg.dsn, cfg.fabric_name) == ("postgresql://u@h/d", "fab")
    assert (cfg.interval_s, cfg.traffic_interval_s) == (60.0, 5.0)


def test_collector_interval_must_be_a_number():
    with pytest.raises(ValueError, match="INFINIMAP_INTERVAL"):
        col_config.from_env(col_config.defaults(), {"INFINIMAP_INTERVAL": "often"})


# -- the layering in __main__ ------------------------------------------------

def _args(**kw) -> argparse.Namespace:
    base = dict(config=None, dsn=None, fabric=None, subnet_prefix=None,
                interval=None, query_timeout=None, from_dir=None)
    return argparse.Namespace(**{**base, **kw})


def _write(tmp_path, body: str) -> str:
    p = tmp_path / "collector.toml"
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_file_supplies_values(tmp_path):
    cfg = _config_from(_args(config=_write(tmp_path, 'dsn = "postgresql:///fromfile"')))
    assert cfg.dsn == "postgresql:///fromfile"


def test_flag_beats_file(tmp_path):
    path = _write(tmp_path, 'dsn = "postgresql:///fromfile"')
    cfg = _config_from(_args(config=path, dsn="postgresql:///fromflag"))
    assert cfg.dsn == "postgresql:///fromflag"


def test_env_beats_file(tmp_path, monkeypatch):
    monkeypatch.setenv("INFINIMAP_DSN", "postgresql:///fromenv")
    path = _write(tmp_path, 'dsn = "postgresql:///fromfile"')
    assert _config_from(_args(config=path)).dsn == "postgresql:///fromenv"


def test_flag_beats_env(tmp_path, monkeypatch):
    monkeypatch.setenv("INFINIMAP_DSN", "postgresql:///fromenv")
    cfg = _config_from(_args(dsn="postgresql:///fromflag"))
    assert cfg.dsn == "postgresql:///fromflag"


def test_traffic_interval_from_file_survives(tmp_path):
    """Regression: `_config_from` used to rebuild Config field by field and
    omitted traffic_interval_s, so setting it in the file did nothing."""
    path = _write(tmp_path, "interval_s = 111\ntraffic_interval_s = 7\n")
    cfg = _config_from(_args(config=path))
    assert cfg.interval_s == 111.0
    assert cfg.traffic_interval_s == 7.0


def test_subnet_prefix_accepts_hex(tmp_path):
    cfg = _config_from(_args(subnet_prefix="0xfe80000000000000"))
    assert cfg.subnet_prefix == 0xFE80000000000000


# -- the full environment surface --------------------------------------------

def test_api_every_file_key_has_an_env_var():
    """Every key api.toml accepts must be reachable from the environment.

    Two spellings differ deliberately -- `fabric` is `default_fabric` in the
    dataclass -- so the comparison is over FIELDS, not over key names.
    """
    from dataclasses import fields
    covered = set(api_config.ENV_VARS.values())
    missing = {f.name for f in fields(api_config.Config())} - covered
    assert missing == set(), f"unreachable from the environment: {missing}"


def test_collector_every_file_key_has_an_env_var():
    from dataclasses import fields
    covered = set(col_config.ENV_VARS.values())
    missing = {f.name for f in fields(col_config.defaults())} - covered
    assert missing == set(), f"unreachable from the environment: {missing}"


def test_api_new_env_vars():
    cfg = api_config.from_env(api_config.Config(), {
        "INFINIMAP_POOL_MIN": "3",
        "INFINIMAP_POOL_MAX": "40",
        "INFINIMAP_ID_TO_NAME": "/etc/infinimap/id_to_name.json",
        "INFINIMAP_LOG_LEVEL": "warning",
    })
    assert cfg.pool_min == 3
    assert cfg.pool_max == 40
    assert str(cfg.id_to_name_path).replace("\\", "/") \
        == "/etc/infinimap/id_to_name.json"
    assert cfg.log_level == "warning"


def test_api_pool_bounds_must_be_integers():
    with pytest.raises(ValueError, match="INFINIMAP_POOL_MAX"):
        api_config.from_env(api_config.Config(), {"INFINIMAP_POOL_MAX": "lots"})


def test_api_cors_origins_from_env_is_comma_separated():
    """An environment variable has no arrays, so the list is comma-separated.
    A trailing comma is not an origin."""
    cfg = api_config.from_env(api_config.Config(), {
        "INFINIMAP_CORS_ORIGINS": "https://a.example, https://b.example,",
    })
    assert cfg.cors_origins == ("https://a.example", "https://b.example")


def test_collector_new_env_vars():
    cfg = col_config.from_env(col_config.defaults(), {
        "INFINIMAP_QUERY_TIMEOUT": "300",
        "INFINIMAP_SUBNET_PREFIX": "0xfe80000000000000",
        "INFINIMAP_FROM_DIR": "/tmp/fixtures",
        "INFINIMAP_LOG_LEVEL": "debug",
    })
    assert cfg.query_timeout_s == 300.0
    assert cfg.subnet_prefix == 0xFE80000000000000
    assert cfg.from_dir == "/tmp/fixtures"
    assert cfg.log_level == "debug"


def test_collector_subnet_prefix_env_rejects_nonsense():
    with pytest.raises(ValueError, match="INFINIMAP_SUBNET_PREFIX"):
        col_config.from_env(col_config.defaults(),
                            {"INFINIMAP_SUBNET_PREFIX": "the-default-one"})


# -- unknown keys ------------------------------------------------------------

def test_collector_warns_about_an_unknown_key(caplog):
    with caplog.at_level("WARNING"):
        cfg = col_config.from_mapping({"dsn": "x", "interval": 60})
    assert "interval" in caplog.text
    assert cfg.dsn == "x"
    assert cfg.interval_s == 300.0


def test_api_warns_about_an_unknown_key(caplog):
    with caplog.at_level("WARNING"):
        cfg = api_config.from_mapping({"prot": 8080})
    assert "prot" in caplog.text
    assert cfg.port == 8000


def test_a_clean_file_warns_about_nothing(caplog):
    with caplog.at_level("WARNING"):
        api_config.from_mapping({k: "x" for k in api_config.KNOWN_KEYS
                                 if k not in {"port", "pool_min", "pool_max",
                                              "cors_origins"}})
        col_config.from_mapping({"dsn": "x", "fabric": "f", "interval_s": 1,
                                 "traffic_interval_s": 1, "log_level": "info",
                                 "max_clock_skew_s": 0, "query_timeout_s": 1})
    assert caplog.text == ""


# -- query timeout -----------------------------------------------------------

def test_query_timeout_layers(tmp_path, monkeypatch):
    """File, then environment, then flag -- the same order as everything else."""
    path = _write(tmp_path, "query_timeout_s = 200\n")
    assert _config_from(_args(config=path)).query_timeout_s == 200.0

    monkeypatch.setenv("INFINIMAP_QUERY_TIMEOUT", "300")
    assert _config_from(_args(config=path)).query_timeout_s == 300.0

    assert _config_from(_args(config=path, query_timeout=400)).query_timeout_s == 400.0


def test_query_timeout_defaults_unchanged():
    """The default must not have moved: changing it silently would change how
    every existing deployment behaves on a slow fabric."""
    from infinimap.collector.acquire.result import DEFAULT_TIMEOUT_S
    assert col_config.defaults().query_timeout_s == 120.0
    assert DEFAULT_TIMEOUT_S == 120.0
