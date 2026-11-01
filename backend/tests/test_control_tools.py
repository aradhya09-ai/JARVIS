"""Regressions for "just do it, and say it in a few words".

The user's complaint was specific: opening an app read out the whole executable
path, an unknown app read out the entire approved list, and screen brightness was
not something JARVIS could do at all.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai.planner import Planner
from tools import system_monitor
from tools.application_launcher import (
    AppNameArgs,
    find_settings_page,
    open_handler,
    scan_shortcuts,
)
from tools.system_monitor import (
    BrightnessArgs,
    VolumeArgs,
    _brightness_handler,
    _volume_handler,
)


@pytest.fixture(scope="module")
def services():
    import main
    return main._build_services()


def _context(services):
    from tools.base import ToolContext
    return ToolContext(
        settings=services.settings,
        repo=services.repo,
        policy=services.policy,
        audit=services.audit,
        bus=services.bus,
    )


# --------------------------------------------------------------- spoken wording
def test_opening_an_app_never_reads_out_its_path(services, monkeypatch):
    """The absolute path belongs in the payload, not in the sentence."""
    from tools import application_launcher

    monkeypatch.setattr(
        application_launcher, "_launch",
        lambda spec: {"method": "exe", "target": "C:\\Program Files\\Zed\\zed.exe",
                      "pid": 4242, "alive": True, "exit_code": None},
    )
    result = asyncio.run(open_handler(AppNameArgs(name="Calculator"), _context(services)))
    assert result.ok is True
    assert "\\" not in result.summary
    assert "Program Files" not in result.summary
    assert result.summary == f"Opening {result.data['app']}."
    # Still available for the UI, which can show it without speaking it.
    assert result.data["launch"]["target"].endswith("zed.exe")


def test_a_handed_off_launcher_process_still_counts_as_opened(services, monkeypatch):
    """Packaged apps hand off to an installer stub that exits with code 0."""
    from tools import application_launcher

    monkeypatch.setattr(
        application_launcher, "_launch",
        lambda spec: {"method": "exe", "target": "C:\\x\\y.exe", "pid": 1,
                      "alive": False, "exit_code": 0},
    )
    result = asyncio.run(open_handler(AppNameArgs(name="Calculator"), _context(services)))
    assert result.ok is True


def test_a_launcher_that_dies_with_an_error_is_reported_as_failure(
    services, monkeypatch
):
    from tools import application_launcher

    monkeypatch.setattr(
        application_launcher, "_launch",
        lambda spec: {"method": "exe", "target": "C:\\x\\y.exe", "pid": 1,
                      "alive": False, "exit_code": 3},
    )
    result = asyncio.run(open_handler(AppNameArgs(name="Calculator"), _context(services)))
    assert result.ok is False
    assert "code 3" in result.summary


# ------------------------------------------------------------- settings pages
@pytest.mark.parametrize(
    ("spoken", "uri"),
    [
        ("bluetooth settings", "ms-settings:bluetooth"),
        ("open my display setting", "ms-settings:display"),
        ("wifi", "ms-settings:network-wifi"),
        ("windows update", "ms-settings:windowsupdate"),
        ("night light", "ms-settings:nightlight"),
    ],
)
def test_settings_pages_resolve_to_their_uri(spoken: str, uri: str):
    page = find_settings_page(spoken)
    assert page is not None
    assert page[0] == uri


def test_an_unlisted_name_is_not_guessed_into_a_settings_page():
    assert find_settings_page("obs studio") is None
    assert find_settings_page("spotify") is None


# ------------------------------------------------------------- shortcut safety
def test_uninstallers_are_never_offered_as_apps(tmp_path, monkeypatch):
    """'open foo' must never reach the 'Uninstall foo' entry in the same folder."""
    import paths

    (tmp_path / "Foo.lnk").write_bytes(b"x")
    (tmp_path / "Uninstall Foo.lnk").write_bytes(b"x")
    monkeypatch.setattr(paths, "shortcut_roots", lambda: [tmp_path])
    hits = scan_shortcuts("foo")
    assert [hit.name for hit in hits] == ["Foo.lnk"]


# -------------------------------------------------------------------- planner
@pytest.mark.parametrize(
    ("spoken", "args"),
    [
        ("brighten my screen", {"action": "up", "step": 20}),
        ("can you please brighten my laptop", {"action": "up", "step": 20}),
        ("brightness badha do", {"action": "up", "step": 20}),
        ("set brightness to 70 percent", {"action": "set", "percent": 70, "step": 20}),
        ("brightness kam karo", {"action": "down", "step": 20}),
        ("too dim", {"action": "down", "step": 20}),
        ("full brightness", {"action": "max", "step": 20}),
        ("minimum brightness", {"action": "min", "step": 20}),
        ("what is my brightness", {"action": "get", "step": 20}),
    ],
)
def test_brightness_phrasing_reaches_the_tool(services, spoken: str, args: dict):
    planner = Planner(services.settings, services.repo, services.registry, None)
    plan = planner._rule_brightness(spoken)
    assert plan is not None
    assert plan.tool == "system.brightness"
    assert plan.args == args


@pytest.mark.parametrize(
    "spoken",
    ["turn on night light", "open display settings", "lock my laptop", "battery status"],
)
def test_neighbouring_commands_are_not_stolen_by_brightness(services, spoken: str):
    planner = Planner(services.settings, services.repo, services.registry, None)
    assert planner._rule_brightness(spoken) is None


# ------------------------------------------------------------- brightness tool
@pytest.fixture
def no_real_powershell(monkeypatch):
    """Swap the PowerShell call for a canned reply, so the suite never dims a screen."""

    def install(raw: str) -> None:
        monkeypatch.setattr(system_monitor, "_run_brightness", lambda script: raw)

    return install


def test_get_reads_the_current_level(services, no_real_powershell):
    no_real_powershell("OK|62|62")
    result = asyncio.run(_brightness_handler(BrightnessArgs(action="get"), _context(services)))
    assert result.summary == "Brightness is at 62 percent."
    assert result.data["current"] == 62


def test_a_change_is_confirmed_by_re_reading_the_panel(services, no_real_powershell):
    no_real_powershell("OK|40|60")
    result = asyncio.run(
        _brightness_handler(BrightnessArgs(action="set", percent=60), _context(services))
    )
    assert result.summary == "Brightness 40 to 60 percent."
    assert result.data["changed"] is True


def test_no_movement_is_reported_as_already_there(services, no_real_powershell):
    no_real_powershell("OK|40|40")
    result = asyncio.run(
        _brightness_handler(BrightnessArgs(action="set", percent=40), _context(services))
    )
    assert result.summary == "It is already at 40 percent."
    assert result.data["changed"] is False


def test_a_missing_wmi_class_is_not_reported_as_success(services, no_real_powershell, monkeypatch):
    opened: list[bool] = []
    monkeypatch.setattr(system_monitor, "_open_display_settings", lambda: opened.append(True) or True)
    no_real_powershell("ERR|Cannot find the class")
    result = asyncio.run(_brightness_handler(BrightnessArgs(action="up"), _context(services)))
    assert result.ok is False
    assert "no hardware brightness control" in result.summary
    assert opened == [True], "he should get the slider, not just a refusal"


def test_a_refusal_from_windows_keeps_its_reason(services, no_real_powershell):
    no_real_powershell("ERR|Access denied")
    result = asyncio.run(_brightness_handler(BrightnessArgs(action="set", percent=5), _context(services)))
    assert result.ok is False
    assert "Access denied" in result.summary


def test_only_the_validated_integer_reaches_the_script(services, monkeypatch):
    """The level is an int by the time it is formatted into the PowerShell text."""
    captured: list[str] = []
    monkeypatch.setattr(
        system_monitor, "_run_brightness",
        lambda script: captured.append(script) or "OK|10|20",
    )
    asyncio.run(_brightness_handler(BrightnessArgs(action="set", percent=20), _context(services)))
    assert "Brightness = $target" in captured[0]
    assert "$target = 20" in captured[0]


def test_brightness_is_registered_and_needs_no_confirmation(services):
    definition = services.registry.get("system.brightness")
    assert definition.risk.value == "low"
    assert services.registry.requires_confirmation("system.brightness", services.policy) is False


# -------------------------------------------------------------------- volume
@pytest.fixture
def fake_endpoint(monkeypatch):
    """Canned master volume, so the suite never changes what he is hearing."""
    from tools import volume_control

    state = {"percent": 45, "muted": False, "written": []}

    def read():
        return state["percent"], state["muted"]

    def write(percent=None, muted=None):
        state["written"].append((percent, muted))
        if percent is not None:
            state["percent"] = percent
        if muted is not None:
            state["muted"] = muted
        return state["percent"], state["muted"]

    monkeypatch.setattr(volume_control, "read", read)
    monkeypatch.setattr(volume_control, "write", write)
    return state


def test_volume_get_states_the_level(services, fake_endpoint):
    result = asyncio.run(_volume_handler(VolumeArgs(action="get"), _context(services)))
    assert result.summary == "Volume is at 45 percent."
    assert fake_endpoint["written"] == [], "a reading must not touch the speakers"


def test_a_muted_get_says_so(services, fake_endpoint):
    fake_endpoint.update(muted=True)
    result = asyncio.run(_volume_handler(VolumeArgs(action="get"), _context(services)))
    assert "45 percent" in result.summary and "muted" in result.summary


def test_volume_set_moves_the_endpoint(services, fake_endpoint):
    result = asyncio.run(
        _volume_handler(VolumeArgs(action="set", percent=70), _context(services))
    )
    assert result.summary == "Volume 45 to 70 percent."
    assert fake_endpoint["written"] == [(70, None)]


def test_volume_already_there_is_not_called_a_change(services, fake_endpoint):
    result = asyncio.run(
        _volume_handler(VolumeArgs(action="set", percent=45), _context(services))
    )
    assert result.summary == "It is already at 45 percent."
    assert fake_endpoint["written"] == []


def test_mute_and_unmute_are_one_word_answers(services, fake_endpoint):
    muted = asyncio.run(_volume_handler(VolumeArgs(action="mute"), _context(services)))
    assert muted.summary == "Muted."
    again = asyncio.run(_volume_handler(VolumeArgs(action="mute"), _context(services)))
    assert again.summary == "It is already muted."
    audible = asyncio.run(_volume_handler(VolumeArgs(action="unmute"), _context(services)))
    assert audible.summary == "Unmuted."


def test_an_unreachable_endpoint_is_not_reported_as_success(services, monkeypatch):
    from tools import volume_control

    def refuse():
        raise volume_control.VolumeUnavailable("no playback device is present")

    monkeypatch.setattr(volume_control, "read", refuse)
    result = asyncio.run(_volume_handler(VolumeArgs(action="get"), _context(services)))
    assert result.ok is False
    assert "no playback device is present" in result.summary


@pytest.mark.parametrize("spoken,action", [
    ("Increase volume to 50%", "set"),
    ("set the volume to 80 percent", "set"),
    ("volume up", "up"),
    ("volume down", "down"),
    ("mute my computer", "mute"),
    ("unmute my computer", "unmute"),
    ("what is my volume", "get"),
    ("check my volume", "get"),
    ("awaaz badha do", "up"),
    ("aawaz kam karo", "down"),
    ("speaker volume kam karo", "down"),
    ("JARVIS, lower your voice", "down"),
])
def test_volume_phrases_reach_the_volume_tool(services, spoken: str, action: str):
    planner = Planner(services.settings, services.repo, services.registry, None)
    plan = planner._rule_volume(spoken)
    assert plan is not None and plan.tool == "system.volume"
    assert plan.args["action"] == action
    if action == "set":
        assert plan.args["percent"] in (50, 80)


@pytest.mark.parametrize("spoken", [
    "roshni kam karo",
    "brightness up",
    "what is the battery level",
    "turn up the microphone",
    "open youtube",
])
def test_neighbouring_commands_are_not_stolen_by_volume(services, spoken: str):
    planner = Planner(services.settings, services.repo, services.registry, None)
    assert planner._rule_volume(spoken) is None


def test_volume_is_registered_without_a_confirmation_step(services):
    definition = services.registry.get("system.volume")
    assert definition.confirm is False or not services.registry.requires_confirmation(
        "system.volume", services.policy
    )
