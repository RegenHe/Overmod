import hashlib
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator


RICH_TEXT_TAG = re.compile(r"</?[A-Za-z][^<>]*>")


def plain_text(value: object) -> str:
    text = "" if value is None else str(value)
    return RICH_TEXT_TAG.sub("", text).strip()


def oc2diy_level_key(level_set_uid: str, scene_name: str) -> str:
    seed = level_set_uid.lower() + "|" + scene_name.lower()
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return "oc2diy-" + digest[:32]


class ModFields(BaseModel):
    name: str = Field(min_length=1, max_length=96)
    author: str = Field(default="", max_length=64)
    version: str = Field(default="", max_length=32)
    level_key: str = Field(default="", max_length=96)
    level_set_uid: str = Field(default="", max_length=160)
    scene_name: str = Field(default="", max_length=160)
    mod_type: Literal["map", "tool"] = "tool"
    description: str = Field(default="", max_length=1600)
    download_label: str = Field(default="项目页面", max_length=48)
    download_url: str = Field(default="", max_length=500)
    download_instructions: str = Field(default="", max_length=800)
    @field_validator(
        "name",
        "author",
        "version",
        "level_key",
        "description",
        "download_label",
        "download_instructions",
        mode="before",
    )
    @classmethod
    def clean_text(cls, value: object) -> str:
        return plain_text(value)

    @field_validator("download_url", mode="before")
    @classmethod
    def valid_download_url(cls, value: object) -> str:
        url = str(value or "").strip()
        if not url:
            return ""
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("download_url must be an http or https URL")
        return url

    @model_validator(mode="after")
    def resolve_level_identity(self):
        if self.mod_type != "map":
            if self.level_key or self.level_set_uid or self.scene_name:
                raise ValueError("Only map entries can have a level identity")
            return self

        has_uid = bool(self.level_set_uid)
        has_scene = bool(self.scene_name)
        if has_uid != has_scene:
            raise ValueError("Both level set UID and scene name are required")
        if has_uid:
            generated = oc2diy_level_key(self.level_set_uid, self.scene_name)
            if self.level_key and self.level_key != generated:
                raise ValueError("The supplied level key does not match UID and sceneName")
            self.level_key = generated
        if not self.level_key:
            raise ValueError("A map needs either a full level key or UID and sceneName")
        return self


class ModWrite(ModFields):
    enabled: bool = True
    featured: bool = False


class SubmissionWrite(ModFields):
    target_mod_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_download_url(self):
        if not self.description:
            raise ValueError("A submission needs a description")
        if not self.download_url:
            raise ValueError("A submission needs a download or project URL")
        return self


class ModResponse(ModWrite):
    id: int
    overrank_verified: bool = False
    overrank_verified_at: int = 0
    created_at: str
    updated_at: str


class SubmissionResponse(SubmissionWrite):
    id: int
    status: Literal["pending", "approved", "rejected"]
    submitted_at: int
    reviewed_at: int
    approved_mod_id: int | None = None
    overrank_verified: bool = False
    overrank_verified_at: int = 0
