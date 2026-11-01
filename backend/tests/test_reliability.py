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
