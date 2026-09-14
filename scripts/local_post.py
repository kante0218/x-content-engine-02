"""Run one local posting cycle under a process lock. No automatic retry."""
import fcntl
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    for folder in ("pending", "posted", "failed"):
        (ROOT / "drafts" / folder).mkdir(parents=True, exist_ok=True)
    with (logs / "local_post.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("投稿処理が実行中のため今回はスキップします")
            return 0
        return subprocess.run([sys.executable, str(ROOT / "scripts/pipeline.py"), *sys.argv[1:]], cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())
