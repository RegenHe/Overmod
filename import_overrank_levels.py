import argparse
import sqlite3
import time
from pathlib import Path

from overmod_server.database import connect, initialise
from overmod_server.schemas import plain_text


ROOT = Path(__file__).resolve().parent
DEFAULT_OVERRANK_DATABASE = ROOT.parent / "Overrank" / "server" / "data" / "overrank.sqlite3"
IMPORTED_DESCRIPTION = "该地图由 Overrank 游玩记录导入，详细介绍与下载地址待补充。"


def latest_custom_levels(database: Path) -> list[sqlite3.Row]:
    if not database.is_file():
        raise FileNotFoundError(f"Overrank database not found: {database}")
    source = sqlite3.connect(str(database))
    source.row_factory = sqlite3.Row
    try:
        return source.execute(
            """
            WITH latest AS (
                SELECT
                    level_key,
                    level_name,
                    level_label,
                    last_played,
                    ROW_NUMBER() OVER (
                        PARTITION BY level_key
                        ORDER BY last_played DESC, player_id ASC
                    ) AS metadata_row
                FROM player_levels
                WHERE level_key LIKE 'oc2diy-%'
                    AND LOWER(COALESCE(level_name, '')) NOT LIKE 's_oc1_story_%'
                    AND COALESCE(level_label, '') NOT LIKE '%主线%'
            )
            SELECT level_key, level_name, level_label
            FROM latest
            WHERE metadata_row = 1
            ORDER BY last_played DESC, level_key ASC
            """
        ).fetchall()
    finally:
        source.close()


def excluded_mainline_keys(database: Path) -> list[str]:
    source = sqlite3.connect(str(database))
    try:
        return [
            str(row[0])
            for row in source.execute(
                """
                SELECT DISTINCT level_key
                FROM player_levels
                WHERE level_key LIKE 'oc2diy-%'
                    AND (
                        LOWER(COALESCE(level_name, '')) LIKE 's_oc1_story_%'
                        OR COALESCE(level_label, '') LIKE '%主线%'
                    )
                """
            ).fetchall()
        ]
    finally:
        source.close()


def display_name(row: sqlite3.Row) -> str:
    label = plain_text(row["level_label"])
    name = plain_text(row["level_name"])
    return (label or name or str(row["level_key"]))[:96]


def inferred_author(name: str) -> str:
    if "/" not in name:
        return ""
    return name.split("/", 1)[0].strip()[:64]


def import_levels(overrank_database: Path) -> tuple[int, int, int]:
    levels = latest_custom_levels(overrank_database)
    excluded_keys = excluded_mainline_keys(overrank_database)
    initialise()
    inserted = 0
    skipped = 0
    removed = 0
    verified_at = int(time.time())
    with connect() as destination:
        if excluded_keys:
            placeholders = ",".join("?" for _ in excluded_keys)
            cursor = destination.execute(
                f"""
                DELETE FROM mods
                WHERE level_key IN ({placeholders})
                    AND mod_type = 'map'
                    AND description = ?
                    AND download_url = ''
                    AND level_set_uid = ''
                    AND scene_name = ''
                """,
                [*excluded_keys, IMPORTED_DESCRIPTION],
            )
            removed = max(0, cursor.rowcount)
        for level in levels:
            level_key = str(level["level_key"] or "").strip()
            if not level_key:
                skipped += 1
                continue
            name = display_name(level)
            cursor = destination.execute(
                """
                INSERT OR IGNORE INTO mods (
                    name, author, version, level_key, level_set_uid,
                    scene_name, mod_type, description, download_label,
                    download_url, download_instructions, enabled,
                    overrank_verified, overrank_verified_at
                ) VALUES (?, ?, '', ?, '', '', 'map', ?, '下载页面', '', '', 1, 1, ?)
                """,
                (
                    name,
                    inferred_author(name),
                    level_key,
                    IMPORTED_DESCRIPTION,
                    verified_at,
                ),
            )
            if cursor.rowcount:
                inserted += 1
            else:
                destination.execute(
                    """
                    UPDATE mods
                    SET overrank_verified = 1, overrank_verified_at = ?
                    WHERE level_key = ? AND mod_type = 'map'
                    """,
                    (verified_at, level_key),
                )
                skipped += 1
    return inserted, skipped, removed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import known custom levels from an Overrank database into Overmod."
    )
    parser.add_argument(
        "database",
        nargs="?",
        type=Path,
        default=DEFAULT_OVERRANK_DATABASE,
        help="Path to overrank.sqlite3",
    )
    arguments = parser.parse_args()
    inserted, skipped, removed = import_levels(arguments.database.resolve())
    print(
        f"Imported {inserted} custom level(s); skipped {skipped} existing or invalid "
        f"level(s); removed {removed} automatically imported mainline level(s)."
    )


if __name__ == "__main__":
    main()
