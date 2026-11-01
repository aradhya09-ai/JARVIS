"""Regressions for the "remember this" path and the model-latency guards.

A bare "remember this" used to store the literal word "this" as a lifetime
fact, an uploaded document had nowhere to go, and every planner call asked the
local model for the full answer budget even when it only had to route.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from fastapi import HTTPException

from ai.model_router import ModelUnavailable, clock_context
from ai.planner import Planner
from api.personal_routes import upload_knowledge
from api.schemas import KnowledgeUpload


@pytest.fixture(scope="session")
def services():
    import main
    return main._build_services()


class DeadRouter:
    """Rules must answer these without reaching for a model."""

    def __init__(self, detail: str = "no model in tests") -> None:
        self.detail = detail

    async def complete(self, messages, json_mode=False):  # noqa: ANN001, ARG002
        raise ModelUnavailable(self.detail)

    def active_name(self) -> str:
        return "none"


def plan_for(services, text: str):
    planner = Planner(services.settings, services.repo, services.registry, DeadRouter())
    return asyncio.run(planner.plan(text))


def test_bare_remember_asks_instead_of_storing_the_word_this(services):
    before = len(services.repo.list_knowledge())
    plan = plan_for(services, "remember this")
    assert plan.intent == "memory_capture"
    assert plan.tool is None
    assert "?" in (plan.reply or "")
    assert len(services.repo.list_knowledge()) == before, "nothing may be written"


def test_pointer_with_a_colon_stores_the_detail_not_the_pointer(services):
    plan = plan_for(services, "remember this: my hostel room is 312")
    assert plan.tool == "memory.set"
    assert plan.args["key"] == "hostel room"
    assert plan.args["value"] == "312"


def test_reply_responses_carry_the_intent_to_the_frontend(services):
    plan = plan_for(services, "remember that")
    result = asyncio.run(services.executor.submit("remember that", plan, source="voice"))
    assert result["type"] == "reply"
    assert result["intent"] == "memory_capture"


def test_upload_remembers_a_text_file_for_good(services):
    result = asyncio.run(upload_knowledge(
        KnowledgeUpload(filename="DBMS notes.txt", content="Normalization up to 3NF.",
                        category="education"),
        services,
    ))
    item = result["item"]
    assert item["key"] == "doc.dbms-notes-txt"
    assert result["truncated"] is False
    stored = services.repo.get_knowledge(item["key"])
    assert stored["value"] == "Normalization up to 3NF."
    assert stored["source"] == "imported"
    assert "DBMS notes.txt" in stored["note"]


def test_upload_refuses_a_pdf_instead_of_storing_binary_noise(services):
    with pytest.raises(HTTPException) as refused:
        asyncio.run(upload_knowledge(
            KnowledgeUpload(filename="syllabus.pdf", content="%PDF-1.7 garbage",
                            category="education"),
            services,
        ))
    assert refused.value.status_code == 415
    assert services.repo.get_knowledge("doc.syllabus-pdf") is None


def test_upload_refuses_an_empty_file(services):
    with pytest.raises(HTTPException) as refused:
        asyncio.run(upload_knowledge(
            KnowledgeUpload(filename="blank.txt", content="   \n  ", category="unverified"),
            services,
        ))
    assert refused.value.status_code == 400


def test_system_prompt_states_today_so_dates_are_not_guessed():
    prompt = clock_context("Aradhya")
    assert datetime.now().strftime("%d %B %Y") in prompt
    assert "Aradhya" in prompt


def test_the_catalog_prefix_never_carries_the_clock(services):
    # Ollama re-reads the whole prompt unless its prefix is byte-identical to the
    # last one, and this catalog is ~3,600 tokens (measured 82s vs 9.7s). Anything
    # that changes per request has to sit behind it, not in front of it.
    planner = Planner(services.settings, services.repo, services.registry,
                      DeadRouter("offline"))
    head = planner._stable_head()
    assert head == planner._stable_head()
    assert "You are JARVIS" in head
    assert datetime.now().strftime("%d %B %Y") not in head


def test_the_keep_warm_ping_primes_the_prompt_a_command_sends(services):
    # Warming with an unrelated "ok" keeps the weights resident but throws away
    # the catalog reading, so the next command pays the cold cost again.
    planner = Planner(services.settings, services.repo, services.registry,
                      DeadRouter("offline"))
    warmed = planner.warm_messages()[0]["content"]
    assert warmed.startswith(planner._stable_head())
    assert "Right now it is" in warmed


def test_routing_calls_get_a_smaller_token_budget_than_answers(services):
    router = services.router
    assert router._max_tokens(True) == services.settings.llm_route_max_tokens
    assert router._max_tokens(True) < router._max_tokens(False)


def test_the_model_is_only_kept_warm_during_a_live_conversation(services):
    # The keep-warm loop releases the model once the conversation goes quiet;
    # an idle assistant must not hold 2 GB of RAM overnight.
    assert 0 < services.settings.model_keep_warm_seconds < 3600


def test_the_planner_head_stays_small_enough_to_prefill_quickly(services):
    """A cache miss re-reads the whole head, and on this CPU qwen2.5:3b needs
    ~22 ms per token to do it. The JSON schema round-trip measured 3,826 tokens
    and cost 84.6s, so the compact catalog has a hard budget."""
    planner = Planner(services.settings, services.repo, services.registry,
                      DeadRouter("offline"))
    head = planner._stable_head()
    assert len(head) < 9_000, f"planner head grew to {len(head)} chars"
    for tool in services.registry.all():
        if tool.name in Planner.MODEL_HIDDEN_TOOLS:
            continue
        assert f"{tool.name}(" in head, f"{tool.name} vanished from the catalog"


def test_optional_and_typed_arguments_survive_the_compact_catalog(services):
    planner = Planner(services.settings, services.repo, services.registry,
                      DeadRouter("offline"))
    catalog = planner._catalog_lines()
    line = next(row for row in catalog.splitlines() if row.startswith("system.brightness("))
    # A nullable int used to be listed as a string, and the required flag must
    # still be visible or the model invents arguments the executor rejects.
    assert "percent?:integer" in line
    assert "action?:get|set|up|down|max|min" in line
