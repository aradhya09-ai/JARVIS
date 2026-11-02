"""Regressions for reaching the dashboard from a private tunnel host.

The blocker was "I cannot always open it on localhost". The fix has to stay
narrow: only origins the user lists get in, and a wildcard must never become an
open CORS policy on a service that controls a real desktop.
"""
from __future__ import annotations

import config
import main

BASE_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8765",
    "http://127.0.0.1:8765",
]


class _Request:
    def __init__(self, origin: str | None):
        self.headers = {"origin": origin} if origin else {}


def test_tunnel_origins_are_parsed_and_trailing_slashes_dropped():
    assert config._split_origins(
        "https://laptop.tailnet.ts.net, http://192.168.1.9:8765/"
    ) == ["https://laptop.tailnet.ts.net", "http://192.168.1.9:8765"]


def test_a_wildcard_is_never_an_open_cors_policy():
    assert config._split_origins("* ; https://laptop.tailnet.ts.net") == [
        "https://laptop.tailnet.ts.net"
    ]
    assert config._split_origins("") == []


def test_settings_feed_the_extra_origins_into_the_enforcement_lists():
    assert main.ALLOWED_ORIGINS[:4] == BASE_ORIGINS
    assert main.ALLOWED_ORIGINS[4:] == config.settings.extra_origins


def test_the_loopback_origins_survive_whatever_the_env_says():
    for origin in BASE_ORIGINS:
        assert main._origin_ok(_Request(origin))
    assert main._origin_ok(_Request(BASE_ORIGINS[0] + "/"))


def test_an_unlisted_origin_is_refused():
    assert not main._origin_ok(_Request("https://some-website.example"))
    assert not main._origin_ok(_Request("http://localhost:9999"))


def test_a_tunnel_ip_is_not_loopback_for_the_rest_guard():
    # Tailscale's carrier range. Serve forwards the real peer address, so the
    # guard judges the device itself instead of the local proxy.
    assert not main._is_loopback("100.84.19.7")
    assert main._is_loopback("127.0.0.1")


def test_an_unnamed_device_is_rejected_even_when_a_range_is_empty(monkeypatch):
    monkeypatch.setattr(main, "REMOTE_NETWORKS", [])
    assert main._is_allowed_client("127.0.0.1")
    assert not main._is_allowed_client("100.84.19.7")
    assert not main._is_allowed_client("")
    assert not main._is_allowed_client(None)


def test_a_named_device_gets_in_and_its_neighbour_does_not(monkeypatch):
    monkeypatch.setattr(main, "REMOTE_NETWORKS", main._named_networks(["100.84.19.7"]))
    assert main._is_allowed_client("100.84.19.7")
    assert not main._is_allowed_client("100.84.19.8")


def test_a_named_range_covers_only_what_it_covers(monkeypatch):
    monkeypatch.setattr(main, "REMOTE_NETWORKS", main._named_networks(["100.99.0.0/24"]))
    assert main._is_allowed_client("100.99.0.42")
    assert not main._is_allowed_client("100.99.1.42")


def test_nonsense_entries_are_skipped_not_applied():
    assert main._named_networks(["not-an-ip", "*"]) == []
    assert main._named_networks(["100.84.19.7"])


def test_a_garbled_client_string_cannot_sneak_past():
    assert not main._is_allowed_client("100.84.19.7; 0.0.0.0")
