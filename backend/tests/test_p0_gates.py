"""The gates the 2026-11-02 audit asked for.

Each test describes something the assistant could do that the user never
approved: open or run an arbitrary path, read a private key, spend money, wipe
the memory store, or hand itself the token that confirms its own prompt. They
run against the real registry and the real permission policy, and none of them
may touch Windows.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from config import Settings
from database.db import Database
from database.repository import Repository
from events import EventBus
from executor import TaskExecutor
from security.audit_log import AuditLog
from security.permissions import PermissionError_, PermissionPolicy, Risk
from tools.base import ToolContext, ToolDefinition, ToolRegistry, ToolResult


class StubArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = "x"


@pytest.fixture(scope="module")
def services():
    import main
    return main._build_services()


class Sandbox:
    """A whole backend stack rooted in tmp_path, plus the real tool definitions."""

    def __init__(self, settings, repo, registry, policy, audit, bus):
        self.settings = settings
        self.repo = repo
        self.registry = registry
        self.policy = policy
        self.audit = audit
        self.bus = bus

    def context(self, task_id: str = "") -> ToolContext:
        return ToolContext(settings=self.settings, repo=self.repo, policy=self.policy,
                           audit=self.audit, bus=self.bus, task_id=task_id)

    def mount(self, module) -> None:
        for tool in module.build():
            if not self.registry.has(tool.name):
                self.registry.register(tool)

    def run(self, tool: str, args: BaseModel) -> ToolResult:
        task = self.repo.create_task(text=f"run {tool}", source="text", tool=tool,
                                     args={}, risk="low", status="pending",
                                     planner="rules")
        return asyncio.run(self.registry.execute(tool, args, self.context(task["id"])))


async def _noop(args: StubArgs, ctx) -> ToolResult:
    return ToolResult.success("done")


@pytest.fixture()
def sandbox():
    """A scratch backend, rooted where JARVIS is really allowed to write.

    pytest's own tmp_path sits under LOCALAPPDATA, which the write gate refuses
    on purpose, so the folder tests would never reach the rule under test.
    """
    scratch = Path(tempfile.mkdtemp(prefix="jarvis-p0-", dir=str(Path.home())))
    settings = Settings(data_dir=scratch / "data", output_dir=scratch / "out",
                        project_root=scratch / "projects")
    for leaf in (settings.data_dir, settings.output_dir, settings.project_root):
        leaf.mkdir(parents=True, exist_ok=True)
    database = Database(settings.db_path)
    database.initialize()
    repo = Repository(database)
    repo.seed()
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="probe.safe", description="t", args_model=StubArgs, risk=Risk.LOW,
        handler=_noop, category="probe"))
    registry.register(ToolDefinition(
        name="probe.spendy", description="t", args_model=StubArgs, risk=Risk.MODERATE,
        handler=_noop, category="probe",
        confirm_when=lambda args, cfg: cfg.image_provider == "openai"))
    policy = PermissionPolicy(repo, settings.read_roots, settings.write_roots)
    try:
        yield Sandbox(settings, repo, registry, policy,
                      AuditLog(repo, settings.audit_log_path), EventBus())
    finally:
        _release_audit_handler(scratch)
        shutil.rmtree(scratch, ignore_errors=True)


def _release_audit_handler(scratch: Path) -> None:
    """The rotating audit handler stays attached for the life of the process,
    and Windows refuses to delete a file an open handle still owns."""
    logger = logging.getLogger("jarvis.audit")
    root = scratch.resolve()
    for handler in list(logger.handlers):
        target = getattr(handler, "baseFilename", "")
        try:
            inside = Path(target).resolve().is_relative_to(root)
        except OSError:
            inside = False
        if inside:
            logger.removeHandler(handler)
            handler.close()


# --------------------------------------------------------------- 1. open folder
def test_open_folder_refuses_a_path_outside_the_boundaries(sandbox, monkeypatch):
    """The handler used to feed any existing path straight to os.startfile."""
    import paths
    from tools import file_manager

    opened: list[Path] = []
    monkeypatch.setattr(paths, "open_path", lambda p: opened.append(p) or (True, "ok"))
    sandbox.mount(file_manager)
    result = sandbox.run("files.open_folder",
                         file_manager.OpenFolderArgs(
                             folder=str(Path(os.environ["SystemRoot"]) / "System32")))
    assert result.ok is False
    assert opened == []


def test_open_folder_refuses_a_blocked_folder_inside_an_approved_one(sandbox, monkeypatch):
    """HOME is a read root, so only the blocklist keeps ~/.ssh shut."""
    import paths
    from tools import file_manager

    opened: list[Path] = []
    monkeypatch.setattr(paths, "open_path", lambda p: opened.append(p) or (True, "ok"))
    sandbox.mount(file_manager)
    result = sandbox.run("files.open_folder",
                         file_manager.OpenFolderArgs(folder=str(Path.home() / ".ssh")))
    assert result.ok is False
    assert opened == []


def test_open_folder_refuses_a_file_it_was_asked_to_open(sandbox, monkeypatch):
    """os.startfile on an .exe runs it, so a folder request must stay a folder."""
    import paths
    from tools import file_manager

    opened: list[Path] = []
    monkeypatch.setattr(paths, "open_path", lambda p: opened.append(p) or (True, "ok"))
    victim = sandbox.settings.output_dir / "installer.exe"
    victim.write_bytes(b"MZ")
    sandbox.mount(file_manager)
    result = sandbox.run("files.open_folder",
                         file_manager.OpenFolderArgs(folder=str(victim)))
    assert result.ok is False
    assert opened == []


def test_open_folder_still_opens_an_approved_folder(sandbox, monkeypatch):
    import paths
    from tools import file_manager

    opened: list[Path] = []
    monkeypatch.setattr(paths, "open_path", lambda p: opened.append(p) or (True, "ok"))
    wanted = sandbox.settings.output_dir
    sandbox.mount(file_manager)
    result = sandbox.run("files.open_folder",
                         file_manager.OpenFolderArgs(folder=str(wanted)))
    assert result.ok is True, result.error
    assert opened == [wanted.resolve()]


# ------------------------------------------------------------------- 2. reads
def test_reads_are_blocked_in_credential_folders(sandbox):
    """The write gate already blocked these; reads claimed the whole of HOME."""
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", home))
    for target in (home / ".ssh" / "id_rsa", home / ".aws" / "credentials",
                   home / ".config" / "tokens.json",
                   Path(os.environ.get("APPDATA", home)) / "secret.json",
                   local / "Google" / "Chrome" / "User Data" / "Login Data",
                   local / "Microsoft" / "Edge" / "User Data" / "Login Data"):
        with pytest.raises(PermissionError_):
            sandbox.policy.ensure_readable(target)


def test_jarvis_can_still_read_its_own_temp_folder(sandbox):
    """The read gate must not eat the assistant: speech renders into %TEMP%."""
    temp = Path(os.environ.get("TEMP", sandbox.settings.output_dir)) / "jarvis-tts"
    assert sandbox.policy.ensure_readable(temp) == temp.resolve()


def test_reads_still_work_in_an_approved_folder(sandbox):
    note = sandbox.settings.output_dir / "notes.md"
    note.write_text("hello", encoding="utf-8")
    assert sandbox.policy.ensure_readable(note) == note.resolve()


# ---------------------------------------------------------------- 3. secrets
def test_settings_endpoint_does_not_leak_the_hmac_salt(services):
    from api import settings_routes

    payload = asyncio.run(settings_routes.get_settings(services=services))
    assert "security.salt" not in payload["settings"]
    assert "security.device_id" not in payload["settings"]
    assert "salt" not in json.dumps(payload)
    # The stored secret is untouched: only the outward copy is redacted.
    assert services.repo.get_setting("security.salt")


def test_settings_patch_does_not_leak_the_hmac_salt(services):
    from api import settings_routes
    from api.schemas import SettingsPatch

    payload = SettingsPatch(values={"voice.style": "jarvis"})
    saved = asyncio.run(settings_routes.patch_settings(payload, services=services))
    assert "salt" not in json.dumps(saved)
    assert saved["settings"]["voice.style"] == "jarvis"


def test_websocket_snapshot_does_not_leak_the_hmac_salt(services):
    import main

    snapshot = asyncio.run(main._snapshot(services))
    assert "security.salt" not in snapshot["payload"]["settings"]
    assert "salt" not in json.dumps(snapshot)


# ------------------------------------------------- 4. the confirmation token
def _risky_plan():
    from ai.planner import Plan
    return Plan(kind="tool", tool="probe.risky", args={"label": "solo"},
                intent="probe", planner="rules")


def _confirm_env(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", output_dir=tmp_path / "out",
                        project_root=tmp_path / "projects")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    database = Database(settings.db_path)
    database.initialize()
    repo = Repository(database)
    repo.seed()
    registry = ToolRegistry()
    ran: list[str] = []

    async def risky(args: StubArgs, ctx) -> ToolResult:
        ran.append(args.label)
        return ToolResult.success("Approved step ran")

    registry.register(ToolDefinition(
        name="probe.risky", description="t", args_model=StubArgs, risk=Risk.HIGH,
        handler=risky, category="probe"))
    policy = PermissionPolicy(repo, settings.read_roots, settings.write_roots)
    executor = TaskExecutor(settings, repo, registry, EventBus(), policy,
                            AuditLog(repo, settings.audit_log_path))
    return executor, repo, ran


def _drive_confirmation(tmp_path, token_to_present: str | None = None) -> dict:
    """Submit a high-risk task and confirm it on one loop.

    It has to be one loop: confirm() starts the run with asyncio.create_task, so
    a second asyncio.run() would raise before the tool ever executed.
    """
    executor, repo, ran = _confirm_env(tmp_path)

    async def flow():
        out = await executor.submit("do the risky thing", _risky_plan())
        task = out["task"]
        before = list(ran)
        rows = {"submitted": dict(task),
                "stored": dict(repo.get_task(task["id"]) or {}),
                "listed": [dict(row) for row in repo.list_tasks()]}
        presented = (out["confirmation"]["token"] if token_to_present is None
                     else token_to_present)
        result = executor.confirm(task["id"], presented)
        for _ in range(200):
            if ran or "error" in result:
                break
            await asyncio.sleep(0.01)
        return {"out": out, "before": before, "rows": rows,
                "result": result, "after": list(ran)}

    return asyncio.run(flow())


def test_nothing_runs_until_the_token_is_presented(tmp_path):
    flow = _drive_confirmation(tmp_path)
    assert flow["out"]["requires_confirmation"] is True
    assert flow["out"]["confirmation"]["token"]
    assert flow["before"] == []
    assert "error" not in flow["result"]
    assert flow["after"] == ["solo"]


def test_a_wrong_token_runs_nothing(tmp_path):
    flow = _drive_confirmation(tmp_path, "guessed")
    assert "error" in flow["result"]
    assert flow["after"] == []


def test_task_rows_never_carry_the_confirmation_token(tmp_path):
    flow = _drive_confirmation(tmp_path)
    assert "confirmation_token" not in flow["out"]["task"]
    assert "confirmation_token" not in flow["rows"]["submitted"]
    assert "confirmation_token" not in flow["rows"]["stored"]
    assert all("confirmation_token" not in row for row in flow["rows"]["listed"])
    assert flow["after"] == ["solo"]


# ---------------------------------------------------- 5. the confirm_when gate
def test_bulk_memory_forget_is_confirmed_but_a_single_key_is_not(sandbox):
    from tools import assistant_tools
    from tools.assistant_tools import MemoryForgetArgs

    sandbox.mount(assistant_tools)
    assert sandbox.registry.requires_confirmation(
        "memory.forget", sandbox.policy, MemoryForgetArgs(key=None), sandbox.settings)
    assert not sandbox.registry.requires_confirmation(
        "memory.forget", sandbox.policy, MemoryForgetArgs(key="favourite-colour"),
        sandbox.settings)


def test_image_generation_is_confirmed_only_when_it_costs_money(sandbox):
    from tools import image_tool
    from tools.image_tool import ImageArgs

    sandbox.mount(image_tool)
    sandbox.settings.image_provider = "none"
    assert not sandbox.registry.requires_confirmation(
        "image.generate", sandbox.policy, ImageArgs(prompt="a fox"), sandbox.settings)
    sandbox.settings.image_provider = "openai"
    assert sandbox.registry.requires_confirmation(
        "image.generate", sandbox.policy, ImageArgs(prompt="a fox"), sandbox.settings)


def test_confirm_when_decides_even_when_moderate_is_auto_confirmed(sandbox):
    sandbox.repo.set_setting("permissions.auto_confirm_moderate", True)
    assert sandbox.registry.requires_confirmation("probe.spendy", sandbox.policy,
                                                  StubArgs(), sandbox.settings) is False
    sandbox.settings.image_provider = "openai"
    assert sandbox.registry.requires_confirmation("probe.spendy", sandbox.policy,
                                                  StubArgs(), sandbox.settings) is True


# ---------------------------------------------------------------- 6. run tests
def test_run_tests_refuses_a_directory_outside_the_project_root(sandbox, monkeypatch):
    """`unittest discover` imports every test_*.py it finds, so the folder it
    searches cannot be whichever one a model names or a file gets dropped in."""
    import subprocess

    from tools import coding_tools

    dropper = sandbox.settings.output_dir / "someone-elses-folder"
    dropper.mkdir()
    sentinel = dropper / "RAN-HERE"
    (dropper / "test_planted.py").write_text(
        f"open(r'{sentinel}', 'w').write('ran')\n", encoding="utf-8")
    calls: list[list] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(list(a)) or type(
        "R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    sandbox.mount(coding_tools)
    result = sandbox.run("code.run_tests",
                         coding_tools.RunTestsArgs(project_dir=str(dropper)))
    assert result.ok is False
    assert "project" in (result.error or "").lower()
    assert not sentinel.exists()
    assert calls == []


def test_run_tests_still_runs_inside_the_project_root(sandbox):
    from tools import coding_tools

    project = sandbox.settings.project_root / "my-app"
    project.mkdir()
    (project / "test_ok.py").write_text(
        "import unittest\n\n\nclass T(unittest.TestCase):\n"
        "    def test_a(self):\n        self.assertTrue(True)\n", encoding="utf-8")
    sandbox.mount(coding_tools)
    result = sandbox.run("code.run_tests",
                         coding_tools.RunTestsArgs(project_dir=str(project)))
    assert result.ok is True, result.error
