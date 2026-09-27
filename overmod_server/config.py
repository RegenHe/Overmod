import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


_local = _read_env(ROOT / ".env")
_overrank = _read_env(ROOT.parent / "Overrank" / ".env")


def setting(name: str, default: str = "") -> str:
    return os.environ.get(name, _local.get(name, default))


def database_path() -> Path:
    configured = setting("OVERMOD_DATABASE", "data/overmod.sqlite3")
    path = Path(configured)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def admin_token_hash() -> str:
    return setting("OVERMOD_ADMIN_TOKEN_SHA256").strip().lower()


def overrank_url() -> str:
    return setting("OVERMOD_OVERRANK_URL", "http://127.0.0.1:3005").rstrip("/")


def overrank_api_key() -> str:
    configured = setting("OVERMOD_OVERRANK_API_KEY")
    return configured or _overrank.get("OVERRANK_API_KEY", "")


def popular_cache_seconds() -> int:
    try:
        return max(10, min(600, int(setting("OVERMOD_POPULAR_CACHE_SECONDS", "60"))))
    except ValueError:
        return 60

