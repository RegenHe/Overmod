import asyncio
import hashlib
import hmac
import ipaddress
import json
import sqlite3
import threading
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import (
    ROOT,
    admin_token_hash,
    overrank_api_key,
    overrank_url,
    popular_cache_seconds,
)
from .database import connect, initialise, row_to_mod, row_to_submission
from .schemas import (
    ModResponse,
    ModWrite,
    SubmissionResponse,
    SubmissionWrite,
)


STATIC = ROOT / "static"
_popular_lock = threading.Lock()
_popular_cache: dict = {"expires_at": 0.0, "payload": None}
_verification_lock = threading.Lock()
SUBMISSION_DAILY_LIMIT = 50
SUBMISSION_WINDOW_SECONDS = 24 * 60 * 60
VERIFICATION_INTERVAL_SECONDS = 24 * 60 * 60
VERIFICATION_BATCH_SIZE = 500


def _invalidate_popular_cache() -> None:
    with _popular_lock:
        _popular_cache["expires_at"] = 0.0
        _popular_cache["payload"] = None


def _known_overrank_level_keys(level_keys: list[str]) -> set[str]:
    unique_keys = list(dict.fromkeys(key for key in level_keys if key))
    if not unique_keys:
        return set()
    body = json.dumps({"level_keys": unique_keys}, separators=(",", ":")).encode("utf-8")
    request = UrlRequest(
        overrank_url() + "/api/v1/levels/known",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "Overmod/0.1.0"},
        method="POST",
    )
    key = overrank_api_key()
    if key:
        request.add_header("X-Overrank-Key", key)
    with urlopen(request, timeout=4.0) as response:
        payload = json.loads(response.read().decode("utf-8"))
    returned = payload.get("level_keys", [])
    if not isinstance(returned, list):
        raise ValueError("Overrank returned an invalid known-level response")
    requested = set(unique_keys)
    return {str(level_key) for level_key in returned if str(level_key) in requested}


def _verification_state(level_key: str) -> tuple[int, int]:
    if not level_key:
        return 0, 0
    try:
        known = _known_overrank_level_keys([level_key])
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return 0, 0
    return (1, int(time.time())) if level_key in known else (0, 0)


def verify_unverified_maps() -> dict[str, int]:
    if not _verification_lock.acquire(blocking=False):
        return {"pending": 0, "verified": 0}
    try:
        with connect() as connection:
            rows = connection.execute(
                """
                SELECT id, level_key FROM mods
                WHERE mod_type = 'map' AND level_key <> '' AND overrank_verified = 0
                ORDER BY id ASC
                """
            ).fetchall()
        pending = [(int(row["id"]), str(row["level_key"])) for row in rows]
        verified_ids: list[int] = []
        for start in range(0, len(pending), VERIFICATION_BATCH_SIZE):
            batch = pending[start:start + VERIFICATION_BATCH_SIZE]
            try:
                known = _known_overrank_level_keys([level_key for _, level_key in batch])
            except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
                continue
            verified_ids.extend(mod_id for mod_id, level_key in batch if level_key in known)
        if verified_ids:
            placeholders = ",".join("?" for _ in verified_ids)
            with connect() as connection:
                connection.execute(
                    f"""
                    UPDATE mods
                    SET overrank_verified = 1, overrank_verified_at = ?
                    WHERE id IN ({placeholders}) AND overrank_verified = 0
                    """,
                    [int(time.time()), *verified_ids],
                )
        return {"pending": len(pending), "verified": len(verified_ids)}
    finally:
        _verification_lock.release()


async def _verification_worker() -> None:
    while True:
        await asyncio.to_thread(verify_unverified_maps)
        await asyncio.sleep(VERIFICATION_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialise()
    verification_task = asyncio.create_task(_verification_worker())
    try:
        yield
    finally:
        verification_task.cancel()
        with suppress(asyncio.CancelledError):
            await verification_task


app = FastAPI(
    title="Overmod",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.mount("/assets", StaticFiles(directory=str(STATIC)), name="assets")


def require_admin(authorization: str = Header(default="")) -> None:
    expected = admin_token_hash()
    if not expected:
        raise HTTPException(status_code=503, detail="Administrator access is not configured")
    prefix = "Bearer "
    token = authorization[len(prefix):].strip() if authorization.startswith(prefix) else ""
    supplied = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if not token or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=401, detail="Invalid administrator token")


def _cache_headers(response: Response, seconds: int = 30) -> None:
    response.headers["Cache-Control"] = f"public, max-age={seconds}"


def _client_ip(request: Request) -> str:
    direct = request.client.host if request.client else "unknown"
    if direct in ("127.0.0.1", "::1"):
        forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        if forwarded:
            try:
                return str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
    try:
        return str(ipaddress.ip_address(direct))
    except ValueError:
        return direct or "unknown"


def _client_ip_hash(request: Request) -> str:
    secret = admin_token_hash().encode("ascii") or b"overmod-local-rate-limit"
    return hmac.new(secret, _client_ip(request).encode("utf-8"), hashlib.sha256).hexdigest()


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/admin", include_in_schema=False)
def admin_page() -> FileResponse:
    return FileResponse(STATIC / "admin.html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": app.version}


@app.get("/api/v1/mods", response_model=list[ModResponse])
def public_mods(response: Response) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM mods WHERE enabled = 1 ORDER BY updated_at DESC, id DESC"
        ).fetchall()
    _cache_headers(response)
    return [row_to_mod(row) for row in rows]


@app.get("/api/v1/featured-mods", response_model=list[ModResponse])
def featured_mods(response: Response) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM mods
            WHERE enabled = 1 AND featured = 1
            ORDER BY updated_at DESC, id ASC
            LIMIT 8
            """
        ).fetchall()
    _cache_headers(response)
    return [row_to_mod(row) for row in rows]


@app.get("/api/v1/catalogue")
def public_catalogue(
    response: Response,
    page: int = Query(default=1, ge=1),
    query: str = Query(default="", max_length=100),
    sort: str = Query(
        default="updated",
        pattern="^(key|name|type|author|version|updated)$",
    ),
    direction: str = Query(default="desc", pattern="^(asc|desc)$"),
    mod_id: int | None = None,
) -> dict:
    page_size = 40
    where = "enabled = 1"
    parameters: list[object] = []
    cleaned_query = query.strip().lower()
    if mod_id is not None:
        where += " AND id = ?"
        parameters.append(mod_id)
    elif cleaned_query:
        escaped_query = (
            cleaned_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        where += """
            AND LOWER(
                name || ' ' || author || ' ' || description || ' ' ||
                CASE mod_type WHEN 'map' THEN '地图' ELSE '工具' END
            ) LIKE ? ESCAPE '\\'
        """
        parameters.append("%" + escaped_query + "%")

    order_columns = {
        "key": "CASE WHEN mod_type = 'map' AND level_key <> '' THEN SUBSTR(level_key, -6) ELSE printf('M%05d', id) END",
        "name": "name COLLATE NOCASE",
        "type": "mod_type COLLATE NOCASE",
        "author": "author COLLATE NOCASE",
        "version": "version COLLATE NOCASE",
        "updated": "updated_at",
    }
    order_direction = "ASC" if direction == "asc" else "DESC"
    with connect() as connection:
        counts = connection.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN mod_type = 'map' THEN 1 ELSE 0 END) AS maps
            FROM mods
            WHERE {where}
            """,
            parameters,
        ).fetchone()
        total = int(counts["total"] or 0)
        maps = int(counts["maps"] or 0)
        page_count = max(1, (total + page_size - 1) // page_size)
        selected_page = min(page, page_count)
        rows = connection.execute(
            f"""
            SELECT * FROM mods
            WHERE {where}
            ORDER BY {order_columns[sort]} {order_direction}, name COLLATE NOCASE ASC, id ASC
            LIMIT ? OFFSET ?
            """,
            [*parameters, page_size, (selected_page - 1) * page_size],
        ).fetchall()
    _cache_headers(response)
    return {
        "entries": [row_to_mod(row) for row in rows],
        "total": total,
        "maps": maps,
        "tools": total - maps,
        "page": selected_page,
        "page_size": page_size,
        "page_count": page_count,
    }


@app.post("/api/v1/submissions", response_model=SubmissionResponse)
def create_submission(payload: SubmissionWrite, request: Request) -> dict:
    values = payload.model_dump()
    target_mod_id = values.pop("target_mod_id")
    now = int(time.time())
    source_hash = _client_ip_hash(request)
    with connect() as connection:
        recent_count = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM submissions
                WHERE submitter_ip_hash = ? AND submitted_at >= ?
                """,
                (source_hash, now - SUBMISSION_WINDOW_SECONDS),
            ).fetchone()[0]
        )
        if recent_count >= SUBMISSION_DAILY_LIMIT:
            raise HTTPException(
                status_code=429,
                detail="同一网络地址 24 小时内最多提交 50 次，请稍后再试",
            )

        if target_mod_id is not None:
            target = connection.execute(
                """
                SELECT * FROM mods
                WHERE id = ? AND enabled = 1
                """,
                (target_mod_id,),
            ).fetchone()
            if target is None:
                raise HTTPException(status_code=404, detail="要修改的模组不存在或已隐藏")
            pending_update = connection.execute(
                """
                SELECT 1 FROM submissions
                WHERE status = 'pending' AND target_mod_id = ? LIMIT 1
                """,
                (target_mod_id,),
            ).fetchone()
            if pending_update:
                raise HTTPException(status_code=409, detail="这个模组已有修改申请正在等待审核")
            values["mod_type"] = target["mod_type"]
            values["level_key"] = target["level_key"]
            values["level_set_uid"] = target["level_set_uid"]
            values["scene_name"] = target["scene_name"]
        elif values["level_key"]:
            published = connection.execute(
                "SELECT 1 FROM mods WHERE level_key = ? LIMIT 1",
                (values["level_key"],),
            ).fetchone()
            pending = connection.execute(
                """
                SELECT 1 FROM submissions
                WHERE status = 'pending' AND level_key = ? LIMIT 1
                """,
                (values["level_key"],),
            ).fetchone()
            if published or pending:
                raise HTTPException(status_code=409, detail="这张地图已经收录或正在等待审核")
        verified, verified_at = (
            _verification_state(values["level_key"])
            if values["mod_type"] == "map"
            else (0, 0)
        )
        cursor = connection.execute(
            """
            INSERT INTO submissions (
                name, author, version, level_key, level_set_uid,
                scene_name, mod_type, description, download_label,
                download_url, download_instructions, status,
                submitter_ip_hash, submitted_at, target_mod_id,
                overrank_verified, overrank_verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
            """,
            (
                values["name"],
                values["author"],
                values["version"],
                values["level_key"],
                values["level_set_uid"],
                values["scene_name"],
                values["mod_type"],
                values["description"],
                values["download_label"],
                values["download_url"],
                values["download_instructions"],
                source_hash,
                now,
                target_mod_id,
                verified,
                verified_at,
            ),
        )
        row = connection.execute(
            "SELECT * FROM submissions WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    return row_to_submission(row)


@app.post("/api/v1/admin/session", dependencies=[Depends(require_admin)])
def admin_session() -> dict:
    return {"authenticated": True}


@app.get(
    "/api/v1/admin/mods",
    response_model=list[ModResponse],
    dependencies=[Depends(require_admin)],
)
def admin_mods() -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM mods ORDER BY updated_at DESC, id DESC"
        ).fetchall()
    return [row_to_mod(row) for row in rows]


@app.get(
    "/api/v1/admin/submissions",
    response_model=list[SubmissionResponse],
    dependencies=[Depends(require_admin)],
)
def admin_submissions() -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM submissions
            WHERE status = 'pending'
            ORDER BY submitted_at ASC, id ASC
            """
        ).fetchall()
    return [row_to_submission(row) for row in rows]


@app.post(
    "/api/v1/admin/submissions/{submission_id}/approve",
    response_model=ModResponse,
    dependencies=[Depends(require_admin)],
)
def approve_submission(submission_id: int, payload: ModWrite) -> dict:
    values = payload.model_dump()
    now = int(time.time())
    try:
        with connect() as connection:
            submission = connection.execute(
                "SELECT * FROM submissions WHERE id = ? AND status = 'pending'",
                (submission_id,),
            ).fetchone()
            if submission is None:
                raise HTTPException(status_code=404, detail="Pending submission not found")
            target_mod_id = submission["target_mod_id"]
            if values["mod_type"] == "map":
                same_level = values["level_key"] == submission["level_key"]
                if same_level and bool(submission["overrank_verified"]):
                    verified = 1
                    verified_at = int(submission["overrank_verified_at"])
                else:
                    verified, verified_at = _verification_state(values["level_key"])
            else:
                verified, verified_at = 0, 0
            if target_mod_id is None:
                cursor = connection.execute(
                    """
                    INSERT INTO mods (
                        name, author, version, level_key, level_set_uid,
                        scene_name, mod_type, description, download_label,
                        download_url, download_instructions, enabled, featured,
                        overrank_verified, overrank_verified_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        values["name"], values["author"], values["version"],
                        values["level_key"], values["level_set_uid"],
                        values["scene_name"], values["mod_type"],
                        values["description"], values["download_label"],
                        values["download_url"], values["download_instructions"],
                        1 if values["enabled"] else 0,
                        1 if values["featured"] else 0,
                        verified,
                        verified_at,
                    ),
                )
                approved_mod_id = cursor.lastrowid
            else:
                cursor = connection.execute(
                    """
                    UPDATE mods SET
                        name = ?, author = ?, version = ?, level_key = ?,
                        level_set_uid = ?, scene_name = ?, mod_type = ?,
                        description = ?, download_label = ?, download_url = ?,
                        download_instructions = ?, enabled = ?, featured = ?,
                        overrank_verified = ?, overrank_verified_at = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        values["name"], values["author"], values["version"],
                        values["level_key"], values["level_set_uid"],
                        values["scene_name"], values["mod_type"],
                        values["description"], values["download_label"],
                        values["download_url"], values["download_instructions"],
                        1 if values["enabled"] else 0,
                        1 if values["featured"] else 0,
                        verified,
                        verified_at,
                        int(target_mod_id),
                    ),
                )
                if cursor.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Target mod not found")
                approved_mod_id = int(target_mod_id)
            connection.execute(
                """
                UPDATE submissions
                SET status = 'approved', reviewed_at = ?, approved_mod_id = ?
                WHERE id = ?
                """,
                (now, approved_mod_id, submission_id),
            )
            row = connection.execute(
                "SELECT * FROM mods WHERE id = ?", (approved_mod_id,)
            ).fetchone()
    except sqlite3.IntegrityError as error:
        raise HTTPException(status_code=409, detail="This level key is already linked") from error
    _invalidate_popular_cache()
    return row_to_mod(row)


@app.post(
    "/api/v1/admin/submissions/{submission_id}/reject",
    response_model=SubmissionResponse,
    dependencies=[Depends(require_admin)],
)
def reject_submission(submission_id: int) -> dict:
    now = int(time.time())
    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE submissions SET status = 'rejected', reviewed_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (now, submission_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Pending submission not found")
        row = connection.execute(
            "SELECT * FROM submissions WHERE id = ?", (submission_id,)
        ).fetchone()
    return row_to_submission(row)


@app.post(
    "/api/v1/admin/mods",
    response_model=ModResponse,
    dependencies=[Depends(require_admin)],
)
def create_mod(payload: ModWrite) -> dict:
    values = payload.model_dump()
    verified, verified_at = (
        _verification_state(values["level_key"])
        if values["mod_type"] == "map"
        else (0, 0)
    )
    try:
        with connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO mods (
                    name, author, version, level_key, level_set_uid,
                    scene_name, mod_type,
                    description, download_label, download_url,
                    download_instructions, enabled, featured,
                    overrank_verified, overrank_verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values["name"],
                    values["author"],
                    values["version"],
                    values["level_key"],
                    values["level_set_uid"],
                    values["scene_name"],
                    values["mod_type"],
                    values["description"],
                    values["download_label"],
                    values["download_url"],
                    values["download_instructions"],
                    1 if values["enabled"] else 0,
                    1 if values["featured"] else 0,
                    verified,
                    verified_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM mods WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
    except sqlite3.IntegrityError as error:
        raise HTTPException(status_code=409, detail="This level key is already linked") from error
    _invalidate_popular_cache()
    return row_to_mod(row)


@app.put(
    "/api/v1/admin/mods/{mod_id}",
    response_model=ModResponse,
    dependencies=[Depends(require_admin)],
)
def update_mod(mod_id: int, payload: ModWrite) -> dict:
    values = payload.model_dump()
    verified, verified_at = (
        _verification_state(values["level_key"])
        if values["mod_type"] == "map"
        else (0, 0)
    )
    try:
        with connect() as connection:
            cursor = connection.execute(
                """
                UPDATE mods SET
                    name = ?, author = ?, version = ?, level_key = ?,
                    level_set_uid = ?, scene_name = ?, mod_type = ?,
                    description = ?, download_label = ?,
                    download_url = ?, download_instructions = ?, enabled = ?, featured = ?,
                    overrank_verified = ?, overrank_verified_at = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    values["name"],
                    values["author"],
                    values["version"],
                    values["level_key"],
                    values["level_set_uid"],
                    values["scene_name"],
                    values["mod_type"],
                    values["description"],
                    values["download_label"],
                    values["download_url"],
                    values["download_instructions"],
                    1 if values["enabled"] else 0,
                    1 if values["featured"] else 0,
                    verified,
                    verified_at,
                    mod_id,
                ),
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Mod not found")
            row = connection.execute("SELECT * FROM mods WHERE id = ?", (mod_id,)).fetchone()
    except sqlite3.IntegrityError as error:
        raise HTTPException(status_code=409, detail="This level key is already linked") from error
    _invalidate_popular_cache()
    return row_to_mod(row)


@app.delete(
    "/api/v1/admin/mods/{mod_id}",
    dependencies=[Depends(require_admin)],
)
def delete_mod(mod_id: int) -> dict:
    with connect() as connection:
        cursor = connection.execute("DELETE FROM mods WHERE id = ?", (mod_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Mod not found")
    _invalidate_popular_cache()
    return {"deleted": True, "id": mod_id}


def _load_popular_custom_levels() -> dict:
    query = urlencode(
        {
            "days": 7,
            "limit": 50,
            "kind": "custom",
            "client_id": "overmod-web",
        }
    )
    request = UrlRequest(
        overrank_url() + "/api/v1/statistics/popular-levels?" + query,
        headers={"User-Agent": "Overmod/0.1.0"},
    )
    key = overrank_api_key()
    if key:
        request.add_header("X-Overrank-Key", key)
    with urlopen(request, timeout=4.0) as response:
        payload = json.loads(response.read().decode("utf-8"))
    entries = [
        entry
        for entry in payload.get("entries", [])
        if not str(entry.get("level_key", "")).startswith("official-")
    ]
    entries.sort(
        key=lambda entry: (
            -int(entry.get("player_count", entry.get("play_count", 0))),
            str(entry.get("level_key", "")),
        )
    )
    with connect() as connection:
        catalogued = {
            str(row["level_key"]): row_to_mod(row)
            for row in connection.execute(
                "SELECT * FROM mods WHERE enabled = 1 AND level_key <> ''"
            ).fetchall()
        }
    for index, entry in enumerate(entries[:20], start=1):
        entry["rank"] = index
        metadata = catalogued.get(str(entry.get("level_key", "")))
        if metadata:
            entry["catalogue"] = metadata
    return {"days": 7, "entries": entries[:20]}


@app.get("/api/v1/popular-levels")
def popular_levels(response: Response) -> dict:
    now = time.time()
    with _popular_lock:
        cached = _popular_cache.get("payload")
        if cached is not None and float(_popular_cache["expires_at"]) > now:
            _cache_headers(response, popular_cache_seconds())
            return cached
        try:
            payload = _load_popular_custom_levels()
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
            if cached is not None:
                stale = dict(cached)
                stale["stale"] = True
                stale["error"] = "Overrank is temporarily unavailable"
                return stale
            raise HTTPException(
                status_code=503,
                detail="Overrank popularity data is temporarily unavailable",
            ) from error
        _popular_cache["payload"] = payload
        _popular_cache["expires_at"] = now + popular_cache_seconds()
        _cache_headers(response, popular_cache_seconds())
        return payload
