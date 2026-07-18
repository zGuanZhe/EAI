from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DraftAttachmentRef(BaseModel):
    type: Literal["work", "document", "context_card"]
    id: str = Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9_.:-]+$")


class ThreadDraftPut(BaseModel):
    expected_revision: int = Field(default=0, ge=0)
    text: str = Field(default="", max_length=8000)
    agent_mode: Literal["auto", "chat", "local", "deep_research", "execute"] = "auto"
    attachment_refs: list[DraftAttachmentRef] = Field(default_factory=list, max_length=8)


class ThreadDraftDelete(BaseModel):
    expected_revision: int = Field(ge=0)
