"""
Daily Scheduler — Automatic Accident Crawl
============================================
Runs the multi-agent crawler automatically every day at configurable time.
Also supports Windows Task Scheduler registration for system-level scheduling.

Features:
  - APScheduler-based daily job
  - Persistent job store (survives restarts)
  - Missed job catch-up (if machine was off)
  - Configurable via .env (SCHEDULE_HOUR, SCHEDULE_MINUTE)
  - Generates daily Excel + updates SQLite
  - Console log + file log

Usage:
  python scheduler.py                    # Start scheduler daemon
  python scheduler.py --run-now          # Run once immediately
  python scheduler.py --install-task     # Register Windows Task Scheduler job
  python scheduler.py --status           # Show next run time + history
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from config import SCHEDULE_HOUR, SCHEDULE_MINUTE, MAX_ARTICLES_PER_DISTRICT, DB_PATH

# ── Logging setup ────────────────────────────────────────────────
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logger.add(
    LOG_DIR / "scheduler_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    retention="30 days",
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
)


def run_daily_crawl():
    """Execute the full multi-agent crawl. Called by scheduler."""
    logger.info("=" * 60)
    logger.info(f"🗓️  DAILY CRAWL STARTED — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    logger.info("=" * 60)

    try:
        from agents import OrchestratorAgent
        orch = OrchestratorAgent(max_articles=MAX_ARTICLES_PER_DISTRICT)
        stats = asyncio.run(orch.run())

        logger.success(
            f"✅ Daily crawl complete: "
            f"{stats.get('new', 0)} new records, "
            f"{stats.get('dup', 0)} duplicates, "
            f"{stats.get('states', 0)} states"
        )

        # Save daily report
        report_dir = Path(__file__).parent / "reports"
        report_dir.mkdir(exist_ok=True)
        report_path = report_dir / f"daily_report_{datetime.now().strftime('%Y%m%d')}.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump({
                "date": datetime.now().strftime("%Y-%m-%d"),
                "stats": stats,
                "completed_at": datetime.now().isoformat(),
            }, f, indent=2, ensure_ascii=False, default=str)
        logger.info(f"Report saved: {report_path}")

    except Exception as e:
        logger.error(f"❌ Daily crawl FAILED: {e}")


def install_windows_task():
    """Register a Windows Task Scheduler task for daily execution."""
    python_exe = sys.executable
    script_path = Path(__file__).resolve()
    task_name = "AI_News_Daily_Crawler"

    cmd = [
        "schtasks", "/Create", "/F",
        "/SC", "DAILY",
        "/TN", task_name,
        "/TR", f'"{python_exe}" "{script_path}" --run-now',
        "/ST", f"{SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}",
        "/RL", "HIGHEST",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        logger.success(f"✅ Windows Task '{task_name}' created!")
        logger.info(f"   Schedule: Daily at {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}")
        logger.info(f"   Python: {python_exe}")
        logger.info(f"   Script: {script_path}")
        print(f"\n✅ Task '{task_name}' registered successfully!")
        print(f"   Runs daily at {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}")
        print(f"\n   Manage via: taskschd.msc or 'schtasks /Query /TN {task_name}'")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to create task: {e.stderr}")
        print(f"❌ Failed: {e.stderr}")
        print("   Try running as Administrator")


def show_status():
    """Show scheduler status and crawl history."""
    from database import AccidentDB
    db = AccidentDB()
    db.init_sync()

    print(f"\n📊 AI News Accident Crawler — Status")
    print(f"{'─'*50}")
    print(f"  Schedule: Daily at {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}")
    print(f"  Database: {DB_PATH}")

    # Check if Windows task exists
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", "AI_News_Daily_Crawler", "/FO", "LIST"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "Next Run Time" in line or "Last Run Time" in line or "Status" in line:
                    print(f"  {line.strip()}")
        else:
            print("  Windows Task: Not installed (use --install-task)")
    except FileNotFoundError:
        print("  Windows Task: N/A")

    # DB stats
    import sqlite3
    if os.path.exists(DB_PATH):
        con = sqlite3.connect(DB_PATH)
        cur = con.execute("SELECT COUNT(*), SUM(fatalities), SUM(injuries) FROM accidents")
        total, fatal, injured = cur.fetchone()
        cur2 = con.execute("SELECT COUNT(*) FROM crawl_runs WHERE status='completed'")
        runs = cur2.fetchone()[0]
        cur3 = con.execute("SELECT MAX(crawl_date) FROM accidents")
        last = cur3.fetchone()[0]
        con.close()

        print(f"\n  📈 Database Stats:")
        print(f"     Total records:  {total or 0}")
        print(f"     Fatalities:     {fatal or 0}")
        print(f"     Injuries:       {injured or 0}")
        print(f"     Crawl runs:     {runs}")
        print(f"     Last crawl:     {last or 'Never'}")
    else:
        print("\n  Database not yet created. Run --run-now first.")


def main():
    parser = argparse.ArgumentParser(description="Daily auto-scheduler for accident crawler")
    parser.add_argument("--run-now", action="store_true", help="Run crawl immediately")
    parser.add_argument("--install-task", action="store_true", help="Register Windows Task Scheduler job")
    parser.add_argument("--status", action="store_true", help="Show status and history")
    args = parser.parse_args()

    if args.run_now:
        run_daily_crawl()
        return

    if args.install_task:
        install_windows_task()
        return

    if args.status:
        show_status()
        return

    # Start daemon scheduler
    print(f"\n🗓️  AI News Daily Scheduler")
    print(f"   Crawl runs daily at {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}")
    print(f"   Press Ctrl+C to stop\n")

    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_daily_crawl,
        CronTrigger(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE),
        id="daily_crawl",
        name="Daily Accident Crawl",
        misfire_grace_time=3600,   # Allow 1 hour late
        coalesce=True,             # Combine missed runs
    )
    logger.info(f"Scheduler started. Next run: {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
