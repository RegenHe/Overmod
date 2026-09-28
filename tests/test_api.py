import hashlib
import os
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request as UrlRequest
from starlette.requests import Request


ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE = ROOT / "tmp" / "overmod-test.sqlite3"
TOKEN = "test-overmod-administrator-token"
os.environ["OVERMOD_DATABASE"] = str(TEST_DATABASE)
os.environ["OVERMOD_ADMIN_TOKEN_SHA256"] = hashlib.sha256(TOKEN.encode("utf-8")).hexdigest()
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException, Response
from pydantic import ValidationError

from overmod_server.app import (
    _load_popular_custom_levels,
    _popular_cache,
    admin_submissions,
    approve_submission,
    admin_mods,
    create_submission,
    create_mod,
    delete_mod,
    featured_mods,
    popular_levels,
    public_catalogue,
    public_mods,
    require_admin,
    reject_submission,
    update_mod,
)
from overmod_server.database import connect, initialise
from overmod_server.schemas import ModWrite, SubmissionWrite, oc2diy_level_key
from import_overrank_levels import IMPORTED_DESCRIPTION, import_levels


class OvermodApiTests(unittest.TestCase):
    def setUp(self):
        for suffix in ("", "-shm", "-wal"):
            path = Path(str(TEST_DATABASE) + suffix)
            if path.exists():
                path.unlink()
        initialise()
        with connect() as connection:
            connection.execute("DELETE FROM mods")
        _popular_cache["payload"] = None
        _popular_cache["expires_at"] = 0.0

    def test_admin_crud_and_public_visibility(self):
        with self.assertRaises(HTTPException) as denied:
            require_admin("Bearer wrong-token")
        self.assertEqual(denied.exception.status_code, 401)
        require_admin("Bearer " + TOKEN)

        created = create_mod(
            ModWrite(
                name="<color=red>Overwashed</color>",
                author="Chef",
                version="1.2.3",
                level_set_uid="Dinner-Pack-UID",
                scene_name="Dinner_Scene_01",
                mod_type="map",
                description="自动洗碗机器人",
                download_label="GitHub",
                download_url="https://example.com/overwashed",
                download_instructions="下载 DLL 后放入 BepInEx/plugins。",
                enabled=True,
            )
        )
        self.assertEqual(created["name"], "Overwashed")
        self.assertEqual(created["version"], "1.2.3")
        self.assertEqual(
            created["level_key"],
            oc2diy_level_key("Dinner-Pack-UID", "Dinner_Scene_01"),
        )
        self.assertEqual(created["level_set_uid"], "Dinner-Pack-UID")
        self.assertEqual(created["scene_name"], "Dinner_Scene_01")
        self.assertEqual(created["mod_type"], "map")
        self.assertEqual(len(public_mods(Response())), 1)

        updated_payload = ModWrite(
            name=created["name"],
            author=created["author"],
            version=created["version"],
            level_key=created["level_key"],
            level_set_uid=created["level_set_uid"],
            scene_name=created["scene_name"],
            mod_type=created["mod_type"],
            description=created["description"],
            download_label=created["download_label"],
            download_url=created["download_url"],
            download_instructions=created["download_instructions"],
            enabled=False,
        )
        update_mod(created["id"], updated_payload)
        self.assertEqual(public_mods(Response()), [])
        self.assertEqual(len(admin_mods()), 1)

        delete_mod(created["id"])
        self.assertEqual(admin_mods(), [])

    def test_recommended_mods_are_seeded(self):
        for suffix in ("", "-shm", "-wal"):
            path = Path(str(TEST_DATABASE) + suffix)
            if path.exists():
                path.unlink()
        initialise()
        recommended = featured_mods(Response())
        self.assertEqual({mod["name"] for mod in recommended}, {"Overrank", "Overwashed"})
        self.assertTrue(all(mod["featured"] for mod in recommended))
        self.assertTrue(all(mod["description"] == "" for mod in recommended))

    def test_uid_and_scene_name_must_match_supplied_key(self):
        with self.assertRaises(ValidationError):
            ModWrite(
                name="Map",
                mod_type="map",
                level_set_uid="set-one",
                scene_name="scene-one",
                level_key="oc2diy-wrong",
                description="Map description",
                download_url="https://example.com/map",
            )

    def test_catalogue_is_paginated_and_searchable(self):
        for index in range(45):
            create_mod(
                ModWrite(
                    name=f"Mod {index:03d}",
                    author="Catalogue Author",
                    mod_type="tool",
                    description="Pagination test",
                    download_url=f"https://example.com/mod-{index}",
                    enabled=True,
                )
            )

        first = public_catalogue(
            Response(), page=1, query="", sort="name", direction="asc"
        )
        second = public_catalogue(
            Response(), page=2, query="", sort="name", direction="asc"
        )
        filtered = public_catalogue(
            Response(), page=1, query="Mod 044", sort="name", direction="asc"
        )

        self.assertEqual(first["total"], 45)
        self.assertEqual(first["page_count"], 2)
        self.assertEqual(len(first["entries"]), 40)
        self.assertEqual(first["entries"][0]["name"], "Mod 000")
        self.assertEqual(len(second["entries"]), 5)
        self.assertEqual(second["entries"][0]["name"], "Mod 040")
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["entries"][0]["name"], "Mod 044")

    @staticmethod
    def request(ip: str = "198.51.100.10") -> Request:
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/submissions",
                "headers": [],
                "client": (ip, 12345),
                "server": ("testserver", 80),
                "scheme": "http",
                "query_string": b"",
            }
        )

    @staticmethod
    def submission(name: str, url: str) -> SubmissionWrite:
        return SubmissionWrite(
            name=name,
            author="Community",
            mod_type="tool",
            description="Community submission",
            download_url=url,
        )

    def test_public_submission_can_be_approved_or_rejected(self):
        payload = self.submission("Submitted tool", "https://example.com/submitted")
        created = create_submission(payload, self.request())
        self.assertEqual(created["status"], "pending")
        self.assertEqual(len(admin_submissions()), 1)

        approved = approve_submission(
            created["id"],
            ModWrite(**payload.model_dump(), enabled=True),
        )
        self.assertEqual(approved["name"], "Submitted tool")
        self.assertEqual(admin_submissions(), [])
        self.assertEqual(len(public_mods(Response())), 1)

        rejected = create_submission(
            self.submission("Rejected tool", "https://example.com/rejected"),
            self.request("198.51.100.11"),
        )
        result = reject_submission(rejected["id"])
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(admin_submissions(), [])

    def test_submission_rate_limit_uses_rolling_day(self):
        with patch("overmod_server.app.SUBMISSION_DAILY_LIMIT", 1):
            create_submission(
                self.submission("First", "https://example.com/first"),
                self.request(),
            )
            with self.assertRaises(HTTPException) as limited:
                create_submission(
                    self.submission("Second", "https://example.com/second"),
                    self.request(),
                )
        self.assertEqual(limited.exception.status_code, 429)

    def test_shared_download_links_are_allowed(self):
        shared_url = "https://www.xiaohongshu.com/explore/example"
        first = create_submission(
            self.submission("Collection map one", shared_url),
            self.request(),
        )
        second = create_submission(
            self.submission("Collection map two", shared_url),
            self.request(),
        )
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(admin_submissions()), 2)

        bilibili = self.submission("Bilibili share", "https://b23.tv/example")
        self.assertEqual(bilibili.download_url, "https://b23.tv/example")

    def test_imports_latest_custom_levels_without_fake_links(self):
        source_path = ROOT / "tmp" / "import-source.sqlite3"
        if source_path.exists():
            source_path.unlink()
        source = sqlite3.connect(str(source_path))
        source.execute(
            """
            CREATE TABLE player_levels (
                player_id TEXT NOT NULL,
                level_key TEXT NOT NULL,
                level_name TEXT NOT NULL,
                level_label TEXT NOT NULL,
                last_played TEXT NOT NULL
            )
            """
        )
        source.executemany(
            "INSERT INTO player_levels VALUES (?, ?, ?, ?, ?)",
            [
                ("a", "oc2diy-map-one", "old_scene", "合集 / 旧名称", "2026-01-01"),
                ("b", "oc2diy-map-one", "new_scene", "合集 / 新名称", "2026-02-01"),
                ("a", "oc2diy-story", "s_oc1_story_1_2", "胡闹厨房 1 - 主线 / 1-2", "2026-02-02"),
                ("a", "official-map", "official", "官方关卡", "2026-03-01"),
            ],
        )
        source.commit()
        source.close()

        create_mod(
            ModWrite(
                name="胡闹厨房 1 - 主线 / 1-2",
                level_key="oc2diy-story",
                mod_type="map",
                description=IMPORTED_DESCRIPTION,
            )
        )
        inserted, skipped, removed = import_levels(source_path)
        self.assertEqual((inserted, skipped, removed), (1, 0, 1))
        imported = [
            mod for mod in public_mods(Response()) if mod["mod_type"] == "map"
        ]
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]["name"], "合集 / 新名称")
        self.assertEqual(imported[0]["author"], "合集")
        self.assertEqual(imported[0]["download_url"], "")

    def test_map_change_request_updates_existing_entry_after_approval(self):
        original = create_mod(
            ModWrite(
                name="合集 / 第一关",
                author="作者",
                level_set_uid="set-uid",
                scene_name="scene-one",
                mod_type="map",
                description="原介绍",
                download_url="https://example.com/original",
            )
        )
        request_payload = SubmissionWrite(
            target_mod_id=original["id"],
            name="合集 / 第一关",
            author="新作者说明",
            level_key=original["level_key"],
            mod_type="map",
            description="补充后的介绍",
            download_url="https://b23.tv/updated",
        )
        submitted = create_submission(request_payload, self.request())
        self.assertEqual(submitted["target_mod_id"], original["id"])

        with self.assertRaises(HTTPException) as duplicate:
            create_submission(request_payload, self.request("198.51.100.12"))
        self.assertEqual(duplicate.exception.status_code, 409)

        approved = approve_submission(
            submitted["id"],
            ModWrite(
                name=submitted["name"],
                author=submitted["author"],
                version=submitted["version"],
                level_key=submitted["level_key"],
                level_set_uid=submitted["level_set_uid"],
                scene_name=submitted["scene_name"],
                mod_type=submitted["mod_type"],
                description=submitted["description"],
                download_label=submitted["download_label"],
                download_url=submitted["download_url"],
                download_instructions=submitted["download_instructions"],
                enabled=True,
            ),
        )
        self.assertEqual(approved["id"], original["id"])
        self.assertEqual(approved["description"], "补充后的介绍")
        self.assertEqual(len(public_mods(Response())), 1)

    def test_tool_change_request_updates_a_featured_entry(self):
        original = create_mod(
            ModWrite(name="Overrank", mod_type="tool", description="", featured=True)
        )
        submitted = create_submission(
            SubmissionWrite(
                target_mod_id=original["id"],
                name="Overrank",
                author="RegenHe",
                mod_type="tool",
                description="排行榜与房间工具",
                download_url="https://example.com/overrank",
            ),
            self.request(),
        )
        approved = approve_submission(
            submitted["id"],
            ModWrite(
                name=submitted["name"],
                author=submitted["author"],
                mod_type=submitted["mod_type"],
                description=submitted["description"],
                download_url=submitted["download_url"],
                enabled=True,
                featured=True,
            ),
        )
        self.assertEqual(approved["id"], original["id"])
        self.assertTrue(approved["featured"])
        self.assertEqual(approved["author"], "RegenHe")

    def test_rejects_non_http_download_urls(self):
        with self.assertRaises(ValidationError):
            ModWrite(
                name="Unsafe",
                description="Unsafe link",
                download_url="javascript:alert(1)",
            )

    def test_popular_levels_are_cached(self):
        payload = {
            "days": 7,
            "entries": [{"rank": 1, "level_key": "oc2diy-test", "play_count": 5}],
        }
        with patch("overmod_server.app._load_popular_custom_levels", return_value=payload) as loader:
            first = popular_levels(Response())
            second = popular_levels(Response())
        self.assertEqual(first, payload)
        self.assertEqual(second, payload)
        self.assertEqual(loader.call_count, 1)

    def test_popular_levels_use_an_http_request(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return b'{"days":7,"entries":[]}'

        with patch("overmod_server.app.urlopen", return_value=FakeResponse()) as opener:
            payload = _load_popular_custom_levels()

        self.assertEqual(payload, {"days": 7, "entries": []})
        self.assertIsInstance(opener.call_args.args[0], UrlRequest)


if __name__ == "__main__":
    unittest.main()
