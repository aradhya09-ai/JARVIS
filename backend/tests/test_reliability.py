"""Reliability scenarios from the brief that can be proven without a microphone.

TEST 13 (a restart keeps settings and approved memory) and TEST 15 (an app that
is not installed is reported as missing, never as opened) are pinned here against
the real database and the real launcher, so neither can be claimed on wording
alone.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from config import Settings
from database.db import Database
from database.repository import Repository
from tools.application_launcher import AppNameArgs, open_handler
from tools.base import ToolContext


@pytest.fixture(scope="module")
def services():
    import main
    return main._build_services()


def _fresh_env(tmp_path: Path) -> tuple[Settings, Repository]:
    settings = Settings(data_dir=tmp_path, output_dir=tmp_path / "out",
                        project_root=tmp_path / "projects")
    database = Database(settings.db_path)
    database.initialize()
    return settings, Repository(database)


def _context(services) -> ToolContext:
    return ToolContext(
        settings=services.settings,
        repo=services.repo,
        policy=services.policy,
        audit=services.audit,
        bus=services.bus,
    )


# ------------------------------------------------------------------- TEST 13
def test_settings_survive_a_restart(tmp_path):
    settings, repo = _fresh_env(tmp_path)
    repo.set_setting("voice.tts_enabled", False)
    repo.set_setting("theme.name", "amber")

    # Same data directory, brand new handles: what a restart actually does.
    reopened = Repository(Database(settings.db_path))
    assert reopened.get_setting("voice.tts_enabled") is False
    assert reopened.get_setting("theme.name") == "amber"


def test_approved_memory_survives_a_restart(tmp_path):
    settings, repo = _fresh_env(tmp_path)
    repo.remember("profile.name", "Aradhya Srivastava", category="profile",
                  source="user", verified=True)
    repo.remember("goal.gate_exam_date", "2028-02-06", category="goal",
                  source="user", verified=True)

    reopened = Repository(Database(settings.db_path))
    assert reopened.get_knowledge("profile.name")["value"] == "Aradhya Srivastava"
    assert reopened.get_knowledge("goal.gate_exam_date")["verified"] is True
    assert [row["key"] for row in reopened.list_knowledge(category="goal")] == \
        ["goal.gate_exam_date"]


def test_an_imported_fact_is_still_marked_unverified_after_a_restart(tmp_path):
    settings, repo = _fresh_env(tmp_path)
    repo.remember("imported.cf_my_cgpa", "My CGPA is 8.74.", category="education",
                  source="imported", verified=False)

    reopened = Repository(Database(settings.db_path))
    row = reopened.get_knowledge("imported.cf_my_cgpa")
    assert row["verified"] is False
    assert row["source"] == "imported", "an import must stay traceable after a restart"


# ------------------------------------------------------------------- TEST 15
def test_an_unknown_app_is_refused_in_one_short_line(services):
    """A miss must not turn into a lecture that reads the whole app list aloud."""
    result = asyncio.run(open_handler(
        AppNameArgs(name="Definitely Not Installed 9000"), _context(services)))
    assert result.ok is False
    assert "could not find" in result.summary
    assert "Opened" not in result.summary
    assert len(result.summary) < 70, result.summary
    listed = [app["name"] for app in services.repo.list_apps()]
    assert not [name for name in listed if name in result.summary]


@pytest.mark.parametrize("name", ["ZZ Photoshop Pro", "ZZ Figma Studio"])
def test_a_configured_but_absent_executable_never_reports_success(services, name):
    """The launcher may only say 'Opened' when it actually found the program."""
    spec = {"name": name, "aliases": [name.lower()], "kind": "exe",
            "executable": "Z:/definitely/not/here.exe"}
    services.repo.save_app(spec, builtin=False)
    try:
        result = asyncio.run(open_handler(AppNameArgs(name=name), _context(services)))
        assert result.ok is False, f"{name} was claimed open without an executable"
        assert "not installed" in result.summary or "could not find" in result.summary
    finally:
        services.repo.delete_app(name)


def test_a_refused_launch_is_not_recorded_as_a_completed_action(services):
    before = len([row for row in services.repo.list_audit(200)
                  if row["action"] == "app.open" and row["outcome"] == "ok"])
    asyncio.run(open_handler(AppNameArgs(name="ZZ Ghost App 42"), _context(services)))
    after = len([row for row in services.repo.list_audit(200)
                 if row["action"] == "app.open" and row["outcome"] == "ok"])
    assert after == before, "a launch that never happened must not appear in the audit trail"


def test_screenshot_reports_the_file_it_saved(tmp_path, monkeypatch):
    """The capture worked; only the reporting line after it raised NameError, because
    Path was imported inside capture() and used in the outer handler scope.

    Pillow is faked so no test run photographs the real desktop, and open_path is
    faked so no test run pops an image viewer on the developer's screen. The fake
    capture still writes a file: the regression sat after the save, not during it.
    """
    from PIL import ImageGrab

    import paths as paths_module
    from tools.system_monitor import ScreenshotArgs, _screenshot_handler

    class FakeImage:
        width, height = 8, 6

        def save(self, target, fmt):
            Path(target).write_bytes(b"\x89PNG\r\n\x1a\n fake")

    opened_targets: list[Path] = []
    monkeypatch.setattr(ImageGrab, "grab", lambda **kwargs: FakeImage())
    monkeypatch.setattr(
        paths_module, "open_path",
        lambda path: (opened_targets.append(path), (True, f"Opened {path.name}"))[1],
    )
    settings, repo = _fresh_env(tmp_path)
    repo.set_setting("paths.screenshots", str(tmp_path / "shots"))
    ctx = ToolContext(settings=settings, repo=repo, policy=None, audit=None, bus=None)

    result = asyncio.run(_screenshot_handler(ScreenshotArgs(), ctx))

    assert result.ok, result.error
    saved = Path(result.data["path"])
    assert saved.exists(), "the summary claimed a file that was never written"
    assert opened_targets == [saved], "a successful capture must open itself automatically"
    assert result.data["opened"] is True
    assert "\\" not in result.summary and str(tmp_path) not in result.summary, \
        "the reply must not read a path aloud"
    assert result.summary == "Executed.", result.summary
    assert repo.latest_artifact("screenshot")["path"] == str(saved)


def test_the_screenshot_confirmation_is_written_in_english():
    """The confirmation is read aloud, so "system.screenshot (moderate risk) with {}"
    was a tool id being spoken as though it were a sentence."""
    from executor import _describe

    assert _describe("system.screenshot", {}, "moderate") == \
        "Take a screenshot and open it on screen"


def test_no_confirmable_action_falls_through_to_the_args_dump():
    """The confirmation sentence is spoken aloud, so a tool without a label made
    JARVIS read "docs.create_presentation (moderate risk) with {'slides': 8, ...}"
    as though it were English. Every action that pauses for approval now owns a
    sentence, and this test fails when one is added without it."""
    from executor import _describe
    from tools import build_registry

    silent = [d.name for d in build_registry().all()
              if d.risk.value in ("moderate", "high")
              and ("{" in (text := _describe(d.name, {}, d.risk.value))
                   or "risk)" in text or "risk):" in text)]
    assert silent == [], f"no plain sentence for {silent}"


def test_a_search_by_name_alone_needs_no_extension(services):
    """files.search died on extension=None, so the common request "find my
    screenshots" crashed instead of answering. The reply also has to name the
    folder, not read an absolute path aloud."""
    from tools.file_manager import SearchArgs, search_handler

    result = asyncio.run(
        search_handler(SearchArgs(folder="Downloads", query="report"), _context(services))
    )

    assert result.ok, result.error
    assert str(Path.home()) not in result.summary, result.summary
