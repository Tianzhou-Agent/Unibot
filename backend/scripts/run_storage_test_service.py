from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

import uvicorn

BACKEND_DIR = Path(__file__).resolve().parents[1]
LOG_PATH = BACKEND_DIR / "storage-test-service.log"
sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault(
    "TZ_STORAGE_MYSQL_DSN",
    "mysql+aiomysql://unibot:unibot@127.0.0.1:13306/unibot_storage_e2e",
)
os.environ.setdefault("TZ_STORAGE_REDIS_DSN", "redis://127.0.0.1:16379/0")
os.environ.setdefault("TZ_STORAGE_NAS_ROOT_PATH", str(BACKEND_DIR / ".docker" / "nas"))


def main() -> None:
    with LOG_PATH.open("a", encoding="utf-8", buffering=1) as log_file:
        sys.stdout = log_file
        sys.stderr = log_file
        try:
            print("Starting storage test service on http://127.0.0.1:18081")
            uvicorn.run(
                "tests.support.storage_test_service:app",
                host="127.0.0.1",
                port=18081,
                log_level="info",
            )
        except Exception:
            traceback.print_exc()
            raise


if __name__ == "__main__":
    main()
