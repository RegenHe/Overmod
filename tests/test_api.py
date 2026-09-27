import hashlib
import os
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
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
    _popular_cache,
    admin_submissions,
    approve_submission,
    admin_mods,
    create_submission,
    create_mod,
    delete_mod,
    popular_levels,
    public_mods,
    require_admin,
    reject_submission,
    update_mod,
)
from overmod_server.database import initialise
from overmod_server.schemas import ModWrite, SubmissionWrite, oc2diy_level_key
from import_overrank_levels import import_levels


class OvermodApiTests(unittest.TestCase):
    def setUp(self):
        for suffix in ("", "-shm", "-wal"):
            path = Path(str(TEST_DATABASE) + suffix)
            if path.exists():
                path.unlink()
        initialise()
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
                ("a", "official-map", "official", "官方关卡", "2026-03-01"),
            ],
        )
        source.commit()
        source.close()

        inserted, skipped = import_levels(source_path)
        self.assertEqual((inserted, skipped), (1, 0))
        imported = public_mods(Response())
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]["name"], "合集 / 新名称")
        self.assertEqual(imported[0]["author"], "合集")
        self.assertEqual(imported[0]["download_url"], "")

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


if __name__ == "__main__":
    unittest.main()
