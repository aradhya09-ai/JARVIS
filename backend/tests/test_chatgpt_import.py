"""The ChatGPT export importer: parse locally, preview, approve only what is chosen.

These tests pin the three promises the brief makes about an import: nothing is
written before approval, an assistant reply is never treated as a fact about the
user, and deleting the import leaves typed facts alone.
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from ai.chatgpt_import import (
    ImportFormatError,
    cross_check,
    dedupe,
    parse_export,
)


def _conversation(conv_id: str, title: str, turns: list[tuple[str, str]]) -> dict:
    """ChatGPT exports nest messages under a 'mapping' of nodes."""
    mapping = {}
    parent = None
    for index, (role, text) in enumerate(turns):
        node_id = f"node-{index}"
        mapping[node_id] = {
            "id": node_id,
            "parent": parent,
            "message": {
                "role": role,
                "author": role,
                "create_time": 1_760_000_000 + index,
                "content": {"content_type": "text", "parts": [text]},
            },
        }
        parent = node_id
    return {"id": conv_id, "title": title, "create_time": 1_760_000_000,
            "update_time": 1_760_009_000, "mapping": mapping}


EXPORT = [
    _conversation("c1", "GATE planning", [
        ("user", "I am Aradhya Srivastava. I study at IIT Patna."),
        ("assistant", "Nice! I can help you plan your GATE preparation."),
        ("user", "My CGPA is 8.74. The semester ends in November."),
    ]),
    _conversation("c2", "Project help", [
        ("user", "My project ScanVerse detects deepfakes."),
        # Same sentence again: one candidate, marked as a repeat.
        ("user", "My CGPA is 8.74."),
        # Disagrees with the first CGPA claim.
        ("user", "My CGPA is 6.2."),
    ]),
    _conversation("c3", "Recipe talk", [
        ("user", "Tell me how to make tea."),
    ]),
]


@pytest.fixture()
def services():
    import main

    return main._build_services()


@pytest.fixture()
def client(services):
    import main

    main.app.state.services = services
    return TestClient(main.app, client=("127.0.0.1", 54321),
                      headers={"X-JARVIS-Client": "desktop"})


# ------------------------------------------------------------------ parsing
def test_parse_reads_only_the_users_own_turns():
    conversations, facts = parse_export(json.dumps(EXPORT))
    assert {row.id for row in conversations} == {"c1", "c2", "c3"}
    text = " ".join(fact.text for fact in facts)
    assert "Nice! I can help you plan" not in text, "an assistant reply became a fact"
    assert any("Aradhya Srivastava" in fact.text for fact in facts)


def test_a_request_for_help_is_not_a_fact_about_the_user():
    _, facts = parse_export(json.dumps(EXPORT[:3]))
    assert not any("how to make tea" in fact.text for fact in facts)


def test_categories_come_from_the_words_used():
    _, facts = parse_export(json.dumps(EXPORT))
    cgpa = [fact for fact in facts if "8.74" in fact.text][0]
    assert cgpa.category in ("education", "goal", "progress")
    scanverse = [fact for fact in facts if "ScanVerse" in fact.text][0]
    assert scanverse.category == "project"


def test_bad_json_is_reported_not_stored():
    with pytest.raises(ImportFormatError) as caught:
        parse_export("{not json")
    assert "conversations.json" in str(caught.value)


def test_a_plain_string_export_is_not_a_conversation_list():
    with pytest.raises(ImportFormatError):
        parse_export('"just a string"')


def test_the_same_sentence_twins_into_one_candidate():
    _, facts = parse_export(json.dumps(EXPORT))
    ids = [fact.id for fact in facts if "8.74" in fact.text]
    assert len(ids) == 1
    assert dedupe(facts) == facts


def test_wrapped_conversations_key_is_accepted():
    conversations, facts = parse_export(json.dumps({"conversations": EXPORT}))
    assert len(conversations) == 3
    assert facts


# ------------------------------------------------------- duplicates and clashes
def test_an_already_stored_fact_is_flagged_as_a_duplicate():
    _, facts = parse_export(json.dumps(EXPORT))
    stored = [{"key": "cgpa.latest", "value": "My CGPA is 8.74."}]
    cross_check(facts, stored)
    cgpa = [fact for fact in facts if "8.74" in fact.text][0]
    assert cgpa.duplicate_of == "cgpa.latest"


def test_a_clashing_number_is_flagged_never_silently_merged():
    _, facts = parse_export(json.dumps(EXPORT))
    cross_check(facts, [])
    low = [fact for fact in facts if "6.2" in fact.text][0]
    assert low.conflicts_with, "8.74 and 6.2 must not both pass as unmarked facts"
    high = [fact for fact in facts if "8.74" in fact.text][0]
    assert high.conflicts_with is None, "the earlier statement stays as it was said"


def test_the_same_number_written_two_ways_is_not_a_conflict():
    _, facts = parse_export(json.dumps([_conversation("c", "x", [
        ("user", "My CGPA is 8.74."),
        ("user", "My CGPA is 8.74 out of 10."),
    ])]))
    cross_check(facts, [])
    assert all(fact.conflicts_with is None for fact in facts)


# ------------------------------------------------------------------ the API
def test_preview_writes_nothing_until_approval(client, services):
    body = client.post("/api/import/chatgpt/preview", json={"raw": json.dumps(EXPORT)}).json()
    assert body["candidates"]
    assert services.repo.list_knowledge(source="imported") == []
    assert any("Nothing has been stored yet" in note for note in body["notes"])


def test_approve_stores_only_the_ids_that_were_chosen(client, services):
    body = client.post("/api/import/chatgpt/preview", json={"raw": json.dumps(EXPORT)}).json()
    first = body["candidates"][0]
    result = client.post("/api/import/chatgpt/approve",
                         json={"import_id": body["import_id"], "fact_ids": [first["id"]]}).json()
    assert result["stored"] == 1
    rows = services.repo.list_knowledge(source="imported")
    assert len(rows) == 1
    assert rows[0]["verified"] is False, "an imported fact is never auto-verified"
    assert rows[0]["category"] == first["category"]
    assert rows[0]["key"].startswith("imported.")


def test_approving_a_duplicate_leaves_the_original_alone(client, services):
    services.repo.remember("cgpa.latest", "My CGPA is 8.74.", category="education",
                           source="user", verified=True)
    body = client.post("/api/import/chatgpt/preview", json={"raw": json.dumps(EXPORT)}).json()
    duplicate = [row for row in body["candidates"] if "8.74" in row["text"]][0]
    assert duplicate["duplicate_of"] == "cgpa.latest"
    result = client.post("/api/import/chatgpt/approve",
                         json={"import_id": body["import_id"], "fact_ids": [duplicate["id"]]}).json()
    assert result["stored"] == 0 and result["duplicates_not_written"] == 1
    assert services.repo.get_knowledge("cgpa.latest")["verified"] is True


def test_excluded_conversations_are_never_written(client, services):
    body = client.post("/api/import/chatgpt/preview", json={"raw": json.dumps(EXPORT)}).json()
    from_c1 = [row for row in body["candidates"] if row["conversation_id"] == "c1"]
    result = client.post("/api/import/chatgpt/approve", json={
        "import_id": body["import_id"],
        "fact_ids": [row["id"] for row in from_c1],
        "excluded": ["c1"],
    }).json()
    assert result["stored"] == 0


def test_an_expired_or_unknown_preview_is_refused(client):
    result = client.post("/api/import/chatgpt/approve",
                         json={"import_id": "imp_nope", "fact_ids": ["cf_x"]})
    assert result.status_code == 404
    assert "preview" in result.json()["detail"].lower()


def test_a_second_approve_of_the_same_preview_cannot_double_write(client):
    body = client.post("/api/import/chatgpt/preview", json={"raw": json.dumps(EXPORT)}).json()
    payload = {"import_id": body["import_id"], "fact_ids": [body["candidates"][0]["id"]]}
    assert client.post("/api/import/chatgpt/approve", json=payload).status_code == 200
    assert client.post("/api/import/chatgpt/approve", json=payload).status_code == 404


def test_delete_removes_imported_rows_and_keeps_typed_ones(client, services):
    services.repo.remember("pref.language", "Hinglish", category="preference", source="user")
    body = client.post("/api/import/chatgpt/preview", json={"raw": json.dumps(EXPORT)}).json()
    client.post("/api/import/chatgpt/approve",
                json={"import_id": body["import_id"],
                      "fact_ids": [row["id"] for row in body["candidates"][:2]]})
    assert services.repo.list_knowledge(source="imported")
    result = client.delete("/api/import/chatgpt").json()
    assert result["deleted"] >= 1
    assert services.repo.list_knowledge(source="imported") == []
    assert services.repo.get_knowledge("pref.language") is not None


def test_a_bad_upload_is_a_clear_400(client):
    result = client.post("/api/import/chatgpt/preview", json={"raw": "<html>"})
    assert result.status_code == 400
    assert "JSON" in result.json()["detail"]


def test_previews_do_not_accumulate_without_limit(client):
    for _ in range(10):
        client.post("/api/import/chatgpt/preview", json={"raw": json.dumps(EXPORT)})
    time.sleep(0.01)
    body = client.post("/api/import/chatgpt/preview", json={"raw": json.dumps(EXPORT)}).json()
    assert body["import_id"]
