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


MIGRATIONS = [
    "ALTER TABLE metrics ADD COLUMN curated INTEGER DEFAULT 0",
    "ALTER TABLE metrics ADD COLUMN template_id TEXT",
    "ALTER TABLE metrics ADD COLUMN skill TEXT",
    "ALTER TABLE metrics ADD COLUMN company TEXT",
    "ALTER TABLE changes ADD COLUMN status TEXT DEFAULT 'accepted'",
    "ALTER TABLE changes ADD COLUMN reason TEXT",
    "ALTER TABLE entries ADD COLUMN gone_at TEXT",
    "ALTER TABLE metrics ADD COLUMN reducer TEXT",  # count | sum | avg | max over the ledger
    "ALTER TABLE metrics ADD COLUMN field TEXT",    # the number read from each entry, for sum/avg/max
    "ALTER TABLE entries ADD COLUMN value REAL",
    "ALTER TABLE entries ADD COLUMN date TEXT",  # the item's own date (posted, launched, announced)  # for removing closed postings later
    "ALTER TABLE metrics ADD COLUMN paced INTEGER DEFAULT 0",       # 1 = this server paces the monitor's ticks
    "ALTER TABLE metrics ADD COLUMN check_interval_s INTEGER",    # current pace; shrinks after a change, grows when quiet
    "ALTER TABLE metrics ADD COLUMN next_tick_at TEXT",
    "ALTER TABLE metrics ADD COLUMN last_tick_at TEXT",
    "ALTER TABLE metrics ADD COLUMN baselined INTEGER DEFAULT 0",  # a paced monitor's first run is its baseline, not a change
    "ALTER TABLE users ADD COLUMN share_token TEXT",  # set = the board is readable at /b/<token>
    "ALTER TABLE users ADD COLUMN webhook_url TEXT",  # a Slack or Discord incoming webhook: changes on the board are posted there
    "CREATE TABLE IF NOT EXISTS visits(day TEXT, path TEXT, ref TEXT, n INTEGER DEFAULT 0, PRIMARY KEY(day, path, ref))",  # page views per day; no IPs, no cookies
    "CREATE TABLE IF NOT EXISTS recipes(id INTEGER PRIMARY KEY, metric_id INTEGER NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL, author TEXT, note TEXT, "
    "status TEXT DEFAULT 'live', created_at TEXT, trial_json TEXT)",  # the wiki: every revision of every number's recipe, with the trial that decided it
    "ALTER TABLE metrics ADD COLUMN instructions TEXT",     # reading instructions from the live recipe; they replace the stock ones
    "ALTER TABLE metrics ADD COLUMN recipe_version INTEGER DEFAULT 1",
    "ALTER TABLE observations ADD COLUMN recipe_version INTEGER",
    "ALTER TABLE metrics ADD COLUMN period TEXT",     # 'month' or 'quarter': a lasting number whose value is counted inside the current period
    "ALTER TABLE metrics ADD COLUMN period_at TEXT",  # first day of the period its question and search currently name
    "CREATE TABLE IF NOT EXISTS gone(id INTEGER PRIMARY KEY, title TEXT, to_path TEXT, at TEXT)",  # numbers taken out of the record: their old address leads somewhere that still exists
    "CREATE TABLE IF NOT EXISTS asks(day TEXT, q TEXT, via TEXT, found INTEGER DEFAULT 0, n INTEGER DEFAULT 0, PRIMARY KEY(day, q, via))",  # what people and agents searched for, and whether the record had it; words only, nothing about who asked
    "CREATE TABLE IF NOT EXISTS fetches(day TEXT, kind TEXT, metric_id INTEGER DEFAULT 0, n INTEGER DEFAULT 0, PRIMARY KEY(day, kind, metric_id))",  # data addresses read by scripts and agents (json, md, csv, svg, embed, llms, search)
    "ALTER TABLE metrics ADD COLUMN read_failures INTEGER DEFAULT 0",  # daily reads in a row that produced no usable value; any good reading resets it
    "ALTER TABLE metrics ADD COLUMN repaired_at TEXT",                 # when an agent last tried to repair this number's recipe
    "ALTER TABLE metrics ADD COLUMN update_json TEXT",  # how to keep the number current, as the creating agent wrote it: method, address, how often
    "CREATE TABLE IF NOT EXISTS drafts(id INTEGER PRIMARY KEY, user TEXT, question TEXT, title TEXT, status TEXT, payload TEXT, created_at TEXT, metric_id INTEGER)",  # a requested number before it is published: what was asked, what the agent found
    "ALTER TABLE users ADD COLUMN email_alerts INTEGER DEFAULT 1",  # 1 = email this person when a number they follow moves
    "ALTER TABLE users ADD COLUMN alerted_at TEXT",                 # when the last alert email went out
    "CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT)",
    "CREATE TABLE IF NOT EXISTS views(day TEXT, metric_id INTEGER, n INTEGER DEFAULT 0, PRIMARY KEY(day, metric_id))",  # people opening a number's page, per day; no IPs, no cookies
    "CREATE TABLE IF NOT EXISTS interest(subject TEXT PRIMARY KEY, article TEXT, week REAL, base REAL, at TEXT)",  # how much each subject is being read about this week (Wikipedia page views)  # the recipe version that produced this reading (empty for readings older than this column)
    "CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY, metric_id INTEGER, change_id INTEGER, user TEXT, action TEXT, at TEXT)",
]


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        for m in MIGRATIONS:
            try:
                c.execute(m)
            except sqlite3.OperationalError:
                pass  # already applied


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
