import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .config import database_path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(str(database_path()), timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA busy_timeout = 10000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialise() -> None:
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS mods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                author TEXT NOT NULL,
                version TEXT NOT NULL DEFAULT '',
                level_key TEXT NOT NULL DEFAULT '',
                level_set_uid TEXT NOT NULL DEFAULT '',
                scene_name TEXT NOT NULL DEFAULT '',
                mod_type TEXT NOT NULL DEFAULT 'tool',
                description TEXT NOT NULL,
                download_label TEXT NOT NULL,
                download_url TEXT NOT NULL,
                download_instructions TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_mods_public_order
                ON mods(enabled, updated_at DESC, id DESC);

            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                author TEXT NOT NULL,
                version TEXT NOT NULL DEFAULT '',
                level_key TEXT NOT NULL DEFAULT '',
                level_set_uid TEXT NOT NULL DEFAULT '',
                scene_name TEXT NOT NULL DEFAULT '',
                mod_type TEXT NOT NULL,
                description TEXT NOT NULL,
                download_label TEXT NOT NULL,
                download_url TEXT NOT NULL,
                download_instructions TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected')),
                submitter_ip_hash TEXT NOT NULL,
                submitted_at INTEGER NOT NULL,
                reviewed_at INTEGER NOT NULL DEFAULT 0,
                approved_mod_id INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_submissions_review
                ON submissions(status, submitted_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_submissions_rate_limit
                ON submissions(submitter_ip_hash, submitted_at DESC);
            """
        )
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(mods)").fetchall()
        }
        if "version" not in columns:
            connection.execute(
                "ALTER TABLE mods ADD COLUMN version TEXT NOT NULL DEFAULT ''"
            )
        if "level_key" not in columns:
            connection.execute(
                "ALTER TABLE mods ADD COLUMN level_key TEXT NOT NULL DEFAULT ''"
            )
        if "level_set_uid" not in columns:
            connection.execute(
                "ALTER TABLE mods ADD COLUMN level_set_uid TEXT NOT NULL DEFAULT ''"
            )
        if "scene_name" not in columns:
            connection.execute(
                "ALTER TABLE mods ADD COLUMN scene_name TEXT NOT NULL DEFAULT ''"
            )
        if "mod_type" not in columns:
            connection.execute(
                "ALTER TABLE mods ADD COLUMN mod_type TEXT NOT NULL DEFAULT 'tool'"
            )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_mods_level_key
                ON mods(level_key) WHERE level_key <> ''
            """
        )


def row_to_mod(row: sqlite3.Row) -> dict:
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "author": row["author"],
        "version": row["version"],
        "level_key": row["level_key"],
        "level_set_uid": row["level_set_uid"],
        "scene_name": row["scene_name"],
        "mod_type": row["mod_type"],
        "description": row["description"],
        "download_label": row["download_label"],
        "download_url": row["download_url"],
        "download_instructions": row["download_instructions"],
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_submission(row: sqlite3.Row) -> dict:
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "author": row["author"],
        "version": row["version"],
        "level_key": row["level_key"],
        "level_set_uid": row["level_set_uid"],
        "scene_name": row["scene_name"],
        "mod_type": row["mod_type"],
        "description": row["description"],
        "download_label": row["download_label"],
        "download_url": row["download_url"],
        "download_instructions": row["download_instructions"],
        "status": row["status"],
        "submitted_at": int(row["submitted_at"]),
        "reviewed_at": int(row["reviewed_at"]),
        "approved_mod_id": (
            int(row["approved_mod_id"])
            if row["approved_mod_id"] is not None
            else None
        ),
    }
