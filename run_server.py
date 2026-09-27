import os
from pathlib import Path

import uvicorn


ROOT = Path(__file__).resolve().parent


def load_environment(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


if __name__ == "__main__":
    load_environment(ROOT / ".env")
    uvicorn.run(
        "overmod_server.app:app",
        host=os.environ.get("OVERMOD_HOST", "127.0.0.1"),
        port=int(os.environ.get("OVERMOD_PORT", "3007")),
        reload=False,
        access_log=False,
        server_header=False,
        date_header=False,
        timeout_keep_alive=25,
    )

