"""SQLite storage: call log, allow/block lists, hashed known contacts, pending decisions."""
import hashlib
import re
import sqlite3
import threading
import time

from . import config

_lock = threading.Lock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    number TEXT,
    flow TEXT,            -- screen | voicemail
    outcome TEXT,         -- see CallFlow for values
    transcript TEXT,
    verdict TEXT,
    reason TEXT,
    voicemail TEXT,
    tg_msg_id INTEGER
);
CREATE TABLE IF NOT EXISTS lists (
    number TEXT NOT NULL,
    kind TEXT NOT NULL,   -- allow | block
    ts REAL NOT NULL,
    PRIMARY KEY (number, kind)
);
CREATE TABLE IF NOT EXISTS known (
    hash TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS decisions (
    call_id INTEGER PRIMARY KEY,
    action TEXT NOT NULL,
    ts REAL NOT NULL
);
"""


def conn():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, isolation_level=None)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
    return _conn


def q(sql, args=()):
    with _lock:
        return conn().execute(sql, args).fetchall()


def x(sql, args=()):
    with _lock:
        cur = conn().execute(sql, args)
        return cur.lastrowid


# ---------- phone numbers ----------

def normalize(number):
    """Reduce any US number format to its last 10 digits ('+1 (405) 555-0100' -> '4055550100').
    Non-US / short numbers keep all digits. Empty string means anonymous."""
    digits = re.sub(r"\D", "", number or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def number_hash(number):
    return hashlib.sha256(normalize(number).encode()).hexdigest()


def pretty(number):
    n = normalize(number)
    if len(n) == 10:
        return f"({n[:3]}) {n[3:6]}-{n[6:]}"
    return n or "No caller ID"


# ---------- lists ----------

def in_list(number, kind):
    n = normalize(number)
    return bool(n) and bool(q("SELECT 1 FROM lists WHERE number=? AND kind=?", (n, kind)))


def set_list(number, kind):
    n = normalize(number)
    if not n:
        return False
    other = "block" if kind == "allow" else "allow"
    x("DELETE FROM lists WHERE number=? AND kind=?", (n, other))
    x("INSERT OR REPLACE INTO lists(number, kind, ts) VALUES (?,?,?)", (n, kind, time.time()))
    return True


def remove_from_lists(number):
    x("DELETE FROM lists WHERE number=?", (normalize(number),))


def get_list(kind):
    return [r[0] for r in q("SELECT number FROM lists WHERE kind=? ORDER BY ts", (kind,))]


def is_known(number):
    """Saved contact (hash uploaded by the phone app) or on the allow list."""
    if not normalize(number):
        return False
    if in_list(number, "allow"):
        return True
    return bool(q("SELECT 1 FROM known WHERE hash=?", (number_hash(number),)))


def replace_known(hashes):
    clean = [h.lower() for h in hashes if re.fullmatch(r"[0-9a-fA-F]{64}", h or "")]
    with _lock:
        c = conn()
        c.execute("BEGIN")
        c.execute("DELETE FROM known")
        c.executemany("INSERT OR IGNORE INTO known(hash) VALUES (?)", [(h,) for h in clean])
        c.execute("COMMIT")
    return len(clean)


# ---------- calls & decisions ----------

def new_call(number, flow):
    return x("INSERT INTO calls(ts, number, flow, outcome) VALUES (?,?,?,?)",
             (time.time(), normalize(number), flow, "in_progress"))


def update_call(call_id, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    x(f"UPDATE calls SET {cols} WHERE id=?", (*fields.values(), call_id))


def get_call(call_id):
    rows = q("SELECT id, ts, number, flow, outcome, transcript, verdict, reason, voicemail, tg_msg_id "
             "FROM calls WHERE id=?", (call_id,))
    if not rows:
        return None
    keys = ["id", "ts", "number", "flow", "outcome", "transcript", "verdict", "reason", "voicemail", "tg_msg_id"]
    return dict(zip(keys, rows[0]))


def recent_calls(limit=10):
    keys = ["id", "ts", "number", "flow", "outcome", "verdict"]
    rows = q("SELECT id, ts, number, flow, outcome, verdict FROM calls ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(zip(keys, r)) for r in rows]


def set_decision(call_id, action):
    """First tap wins; later taps on the same call are ignored."""
    x("INSERT OR IGNORE INTO decisions(call_id, action, ts) VALUES (?,?,?)", (call_id, action, time.time()))
    return q("SELECT action FROM decisions WHERE call_id=?", (call_id,))[0][0]


def get_decision(call_id):
    rows = q("SELECT action FROM decisions WHERE call_id=?", (call_id,))
    return rows[0][0] if rows else None
