"""SQLite storage for Tally. One file, one connection per call."""
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.environ.get("TALLY_DB", os.path.join(os.path.dirname(__file__), "tally.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics(
  id INTEGER PRIMARY KEY, question TEXT NOT NULL, title TEXT, kind TEXT NOT NULL,
  unit TEXT, source_url TEXT, search_query TEXT, monitor_query TEXT,
  telem_monitor_id TEXT, monitor_cursor INTEGER DEFAULT 0,
  last_checked_at TEXT, refreshes INTEGER DEFAULT 0,
  created_by TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS entries(
  id INTEGER PRIMARY KEY, metric_id INTEGER NOT NULL, key TEXT NOT NULL,
  title TEXT, url TEXT, first_seen_at TEXT, seeded INTEGER DEFAULT 0,
  held_out INTEGER DEFAULT 0, found_via TEXT,
  UNIQUE(metric_id, key));
CREATE TABLE IF NOT EXISTS observations(
  id INTEGER PRIMARY KEY, metric_id INTEGER NOT NULL, value REAL, as_of TEXT,
  evidence_url TEXT, origin TEXT, seeded INTEGER DEFAULT 0, quote TEXT);
CREATE TABLE IF NOT EXISTS changes(
  id INTEGER PRIMARY KEY, metric_id INTEGER NOT NULL, before TEXT, after TEXT,
  entry_id INTEGER, at TEXT, origin TEXT, kind TEXT, note TEXT, url TEXT);
CREATE TABLE IF NOT EXISTS board_items(
  user TEXT NOT NULL, metric_id INTEGER NOT NULL, muted INTEGER DEFAULT 0,
  PRIMARY KEY(user, metric_id));
CREATE TABLE IF NOT EXISTS users(
  id TEXT PRIMARY KEY, name TEXT, picture TEXT, plan TEXT DEFAULT 'free',
  stripe_customer_id TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT);
CREATE TABLE IF NOT EXISTS telem_calls(
  id INTEGER PRIMARY KEY, metric_id INTEGER, endpoint TEXT, providers_json TEXT,
  latency_ms INTEGER, est_cost REAL, at TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init():
    with conn() as c:
        c.executescript(SCHEMA)


def q(sql, args=()):
    with conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def run(sql, args=()):
    with conn() as c:
        cur = c.execute(sql, args)
        return cur.lastrowid, cur.rowcount
