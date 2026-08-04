"""SQLite 数据库自动备份脚本。

用法:
  python backup_db.py                # 手动备份
  python backup_db.py --schedule     # 打印 cron 配置提示

定时备份 (Linux crontab):
  0 3 * * * cd /path/to/backend && .venv/bin/python app/backup_db.py

定时备份 (Windows 任务计划程序):
  程序: D:\top\finance\backend\.venv\Scripts\python.exe
  参数: D:\top\finance\backend\app\backup_db.py
  触发: 每天 03:00
"""
from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "financecrew.db"
BACKUP_DIR = Path(__file__).resolve().parent.parent / "data" / "backups"
MAX_BACKUPS = 30  # 保留最近30天备份


def backup() -> Path:
    """备份数据库，返回备份文件路径。"""
    if not DB_PATH.exists():
        print(f"数据库不存在: {DB_PATH}")
        sys.exit(1)

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"financecrew_{timestamp}.db"

    # 使用 SQLite 的 backup API（在线备份，不锁库）
    import sqlite3
    src = sqlite3.connect(str(DB_PATH))
    dst = sqlite3.connect(str(backup_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    print(f"备份成功: {backup_path} ({backup_path.stat().st_size / 1024 / 1024:.1f} MB)")

    # 清理旧备份
    backups = sorted(BACKUP_DIR.glob("financecrew_*.db"))
    if len(backups) > MAX_BACKUPS:
        for old in backups[:-MAX_BACKUPS]:
            old.unlink()
            print(f"清理旧备份: {old.name}")

    return backup_path


if __name__ == "__main__":
    if "--schedule" in sys.argv:
        print("Linux crontab (每天3点):")
        print("  0 3 * * * cd /path/to/backend && .venv/bin/python app/backup_db.py >> data/logs/backup.log 2>&1")
        print("\nWindows 任务计划程序:")
        print("  程序: python.exe")
        print("  参数: app\\backup_db.py")
        print("  起始位置: D:\\top\\finance\\backend")
    else:
        backup()
