from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str | None = Field(default=None, max_length=2000)
    tool: str | None = Field(default=None, max_length=80)
    args: dict[str, Any] = Field(default_factory=dict)
    source: Literal["text", "voice", "phone", "ui", "quick_action"] = "text"


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=8, max_length=200)


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, Any]


class MemoryWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=80)
    value: Any
    kind: str = Field(default="preference", max_length=40)


class TTSRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=1200)
    language: str | None = Field(default=None, max_length=16)
    voice: str | None = Field(default=None, max_length=200)
    rate: int | None = Field(default=None, ge=-10, le=10)
    pitch: int | None = Field(default=None, ge=-10, le=10)


class PhoneNotifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(default="", max_length=500)


class IntegrationWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = False


class KnowledgeWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=120)
    value: Any
    category: str = Field(default="preference", max_length=40)
    source: str = Field(default="user", max_length=20)
    verified: bool = True
    as_of: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=500)


class KnowledgeUpload(BaseModel):
    """A document the user hands over to be remembered.

    The browser reads the file and posts its text, so no multipart parser
    dependency is needed and binary formats are rejected in one honest place.
    """

    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=400_000)
    category: str = Field(default="unverified", max_length=40)


class KnowledgePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Any = None
    category: str | None = Field(default=None, max_length=40)
    source: str | None = Field(default=None, max_length=20)
    verified: bool | None = None
    as_of: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=500)


class SiteWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=4, max_length=2048)
    aliases: list[str] = Field(default_factory=list, max_length=12)


class ReminderWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=300)
    due_at: str = Field(default="", max_length=40)
    minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 365)
    seconds: int | None = Field(default=None, ge=1, le=60 * 60 * 24 * 30)
    speak: bool = True
    source: str = Field(default="manual", max_length=40)


class ImportPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw: str = Field(min_length=2, max_length=20_000_000,
                     description="The text of a ChatGPT conversations.json export.")


class ImportApprove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    import_id: str = Field(min_length=1, max_length=80)
    fact_ids: list[str] = Field(default_factory=list, max_length=2000)
    excluded: list[str] = Field(default_factory=list, max_length=2000,
                                description="Conversation ids to leave out.")
