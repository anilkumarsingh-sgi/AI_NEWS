"""
SQLite database layer for accident records.
Async-first with sync fallback. Stores all crawl results, supports
deduplication, daily diffs, and historical queries.
"""

import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import aiosqlite
from loguru import logger

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS accidents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url      TEXT NOT NULL,
    headline        TEXT,
    state           TEXT,
    district        TEXT,
    location        TEXT,
    city            TEXT,
    police_station  TEXT,
    vehicle_type    TEXT,
    vehicle_number  TEXT,
    persons         TEXT,
    fatalities      INTEGER DEFAULT 0,
    injuries        INTEGER DEFAULT 0,
    date            TEXT,
    time            TEXT,
    language_detected TEXT,
    confidence_score REAL DEFAULT 0.0,
    raw_text        TEXT,
    crawl_date      TEXT NOT NULL,
    crawl_timestamp TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(source_url, headline, state, district)
);

CREATE TABLE IF NOT EXISTS crawl_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date        TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    states_crawled  INTEGER DEFAULT 0,
    districts_crawled INTEGER DEFAULT 0,
    articles_found  INTEGER DEFAULT 0,
    records_new     INTEGER DEFAULT 0,
    records_dup     INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'running',
    error_log       TEXT
);

CREATE INDEX IF NOT EXISTS idx_accidents_state ON accidents(state);
CREATE INDEX IF NOT EXISTS idx_accidents_district ON accidents(district);
CREATE INDEX IF NOT EXISTS idx_accidents_crawl_date ON accidents(crawl_date);
CREATE INDEX IF NOT EXISTS idx_accidents_source ON accidents(source_url);
"""


def _serialize_list(val):
    if isinstance(val, list):
        return json.dumps(val, ensure_ascii=False)
    return val


class AccidentDB:
    """Async SQLite wrapper for accident data."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or DB_PATH

    # ── Lifecycle ────────────────────────────────────────────────

    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA)
            await db.commit()
        logger.info(f"Database initialized at {self.db_path}")

    def init_sync(self):
        con = sqlite3.connect(self.db_path)
        con.executescript(SCHEMA)
        con.commit()
        con.close()

    # ── Insert ───────────────────────────────────────────────────

    async def insert_record(self, rec: dict) -> bool:
        """Insert a record. Returns True if new, False if duplicate."""
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute(
                    """INSERT INTO accidents
                       (source_url, headline, state, district, location, city,
                        police_station, vehicle_type, vehicle_number, persons,
                        fatalities, injuries, date, time, language_detected,
                        confidence_score, raw_text, crawl_date, crawl_timestamp)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        rec.get("source_url", ""),
                        rec.get("headline"),
                        rec.get("state"),
                        rec.get("district"),
                        rec.get("location"),
                        rec.get("city"),
                        rec.get("police_station"),
                        _serialize_list(rec.get("vehicle_type")),
                        _serialize_list(rec.get("vehicle_number")),
                        _serialize_list(rec.get("persons")),
                        rec.get("fatalities", 0),
                        rec.get("injuries", 0),
                        rec.get("date"),
                        rec.get("time"),
                        rec.get("language_detected"),
                        rec.get("confidence_score", 0.0),
                        rec.get("raw_text"),
                        rec.get("crawl_date", datetime.now().strftime("%Y-%m-%d")),
                        rec.get("crawl_timestamp", datetime.now().isoformat()),
                    ),
                )
                await db.commit()
                return True
            except aiosqlite.IntegrityError:
                return False

    async def insert_records(self, records: list[dict]) -> tuple[int, int]:
        """Bulk insert. Returns (new_count, dup_count)."""
        new, dup = 0, 0
        for rec in records:
            if await self.insert_record(rec):
                new += 1
            else:
                dup += 1
        return new, dup

    def insert_records_sync(self, records: list[dict]) -> tuple[int, int]:
        con = sqlite3.connect(self.db_path)
        new, dup = 0, 0
        for rec in records:
            try:
                con.execute(
                    """INSERT INTO accidents
                       (source_url, headline, state, district, location, city,
                        police_station, vehicle_type, vehicle_number, persons,
                        fatalities, injuries, date, time, language_detected,
                        confidence_score, raw_text, crawl_date, crawl_timestamp)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        rec.get("source_url", ""),
                        rec.get("headline"),
                        rec.get("state"),
                        rec.get("district"),
                        rec.get("location"),
                        rec.get("city"),
                        rec.get("police_station"),
                        _serialize_list(rec.get("vehicle_type")),
                        _serialize_list(rec.get("vehicle_number")),
                        _serialize_list(rec.get("persons")),
                        rec.get("fatalities", 0),
                        rec.get("injuries", 0),
                        rec.get("date"),
                        rec.get("time"),
                        rec.get("language_detected"),
                        rec.get("confidence_score", 0.0),
                        rec.get("raw_text"),
                        rec.get("crawl_date", datetime.now().strftime("%Y-%m-%d")),
                        rec.get("crawl_timestamp", datetime.now().isoformat()),
                    ),
                )
                new += 1
            except sqlite3.IntegrityError:
                dup += 1
        con.commit()
        con.close()
        return new, dup

    # ── Crawl runs ───────────────────────────────────────────────

    async def start_run(self) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "INSERT INTO crawl_runs (run_date, started_at) VALUES (?,?)",
                (datetime.now().strftime("%Y-%m-%d"), datetime.now().isoformat()),
            )
            await db.commit()
            return cur.lastrowid

    async def finish_run(self, run_id: int, stats: dict):
        errors = stats.get("errors")
        if isinstance(errors, list):
            errors = json.dumps(errors, ensure_ascii=False, default=str)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """UPDATE crawl_runs SET finished_at=?, states_crawled=?,
                   districts_crawled=?, articles_found=?, records_new=?,
                   records_dup=?, status=?, error_log=? WHERE id=?""",
                (
                    datetime.now().isoformat(),
                    stats.get("states", 0),
                    stats.get("districts", 0),
                    stats.get("articles", 0),
                    stats.get("new", 0),
                    stats.get("dup", 0),
                    stats.get("status", "completed"),
                    errors,
                    run_id,
                ),
            )
            await db.commit()

    # ── Queries ──────────────────────────────────────────────────

    async def get_today_records(self) -> list[dict]:
        return await self._query(
            "SELECT * FROM accidents WHERE crawl_date = ? ORDER BY state, district",
            (datetime.now().strftime("%Y-%m-%d"),),
        )

    async def get_state_records(self, state: str) -> list[dict]:
        return await self._query(
            "SELECT * FROM accidents WHERE state = ? ORDER BY district, crawl_date DESC",
            (state,),
        )

    async def get_stats(self) -> dict:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            row = await db.execute_fetchall(
                """SELECT
                     COUNT(*) as total,
                     SUM(fatalities) as fatalities,
                     SUM(injuries) as injuries,
                     COUNT(DISTINCT state) as states,
                     COUNT(DISTINCT district) as districts,
                     COUNT(DISTINCT crawl_date) as days_crawled,
                     MIN(crawl_date) as first_crawl,
                     MAX(crawl_date) as last_crawl
                   FROM accidents"""
            )
            r = row[0]
            return dict(r) if r else {}

    async def get_daily_summary(self, date: str | None = None) -> list[dict]:
        date = date or datetime.now().strftime("%Y-%m-%d")
        return await self._query(
            """SELECT state, district, COUNT(*) as count,
                      SUM(fatalities) as fatalities, SUM(injuries) as injuries
               FROM accidents WHERE crawl_date = ?
               GROUP BY state, district ORDER BY state, district""",
            (date,),
        )

    async def get_crawl_history(self, limit: int = 30) -> list[dict]:
        return await self._query(
            "SELECT * FROM crawl_runs ORDER BY id DESC LIMIT ?", (limit,)
        )

    async def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(sql, params)
            return [dict(r) for r in rows]
