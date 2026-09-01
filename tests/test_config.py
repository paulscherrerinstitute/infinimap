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
                interval=None, from_dir=None)
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
