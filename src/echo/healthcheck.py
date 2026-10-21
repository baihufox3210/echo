"""Docker HEALTHCHECK：Bot 連線正常時會定期更新心跳檔，超過時限未更新即視為異常。"""

import os
import sys
import time
from pathlib import Path

MAX_AGE = 120


def main() -> int:
    path = Path(os.environ.get("HEALTHCHECK_FILE", "/tmp/echo-heartbeat"))
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        print("heartbeat file missing")
        return 1
    if age > MAX_AGE:
        print(f"heartbeat stale: {age:.0f}s")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
