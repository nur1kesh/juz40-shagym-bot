import json
import os
import sqlite3

from .config import DB_PATH

os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row

conn.executescript("""
CREATE TABLE IF NOT EXISTS bindings(
  role TEXT PRIMARY KEY, chat_id INTEGER, thread_id INTEGER);
CREATE TABLE IF NOT EXISTS users(
  user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, staff_role TEXT);
CREATE TABLE IF NOT EXISTS complaints(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  status TEXT,            -- new | awaiting_program | open | resolved | ignored
  source TEXT,            -- direct | external
  src_chat INTEGER, src_thread INTEGER, src_msg INTEGER,
  author_id INTEGER, author_name TEXT, author_role TEXT,   -- tech | internal | external
  program TEXT,           -- smart | junior
  target_chat INTEGER, target_thread INTEGER, target_msg INTEGER,
  text TEXT, parsed TEXT, responsible TEXT, notes TEXT,
  created_at TEXT, last_reminder_at TEXT, reminder_count INTEGER DEFAULT 0,
  resolved_at TEXT, resolved_by TEXT, final TEXT,
  echo_done INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS replies(
  id INTEGER PRIMARY KEY AUTOINCREMENT, complaint_id INTEGER,
  user_id INTEGER, user_name TEXT, text TEXT, kind TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS bot_msgs(
  chat_id INTEGER, msg_id INTEGER, complaint_id INTEGER,
  PRIMARY KEY(chat_id, msg_id));
CREATE TABLE IF NOT EXISTS notices(
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, chat_id INTEGER, thread_id INTEGER,
  reply_to INTEGER, text TEXT, sent INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS pending_res(
  id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, user_id INTEGER, user_name TEXT, text TEXT, done INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS media_msgs(
  chat_id INTEGER, group_id TEXT, msg_id INTEGER, PRIMARY KEY(chat_id, group_id, msg_id));
CREATE TABLE IF NOT EXISTS media_groups(
  chat_id INTEGER, group_id TEXT, thread INTEGER, first_msg INTEGER, user_id INTEGER,
  created_at TEXT, status TEXT, PRIMARY KEY(chat_id, group_id));
CREATE TABLE IF NOT EXISTS ephemeral(
  chat_id INTEGER, msg_id INTEGER, delete_at TEXT, PRIMARY KEY(chat_id, msg_id));
CREATE TABLE IF NOT EXISTS digest_msgs(
  chat_id INTEGER, msg_id INTEGER, complaint_id INTEGER, PRIMARY KEY(chat_id, msg_id, complaint_id));
CREATE TABLE IF NOT EXISTS deletions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, chat_id INTEGER, msg_id INTEGER, reason TEXT);
CREATE TABLE IF NOT EXISTS errors(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, source TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS decisions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, chat_id INTEGER, msg_id INTEGER, complaint_id INTEGER,
  who TEXT, outcome TEXT);
CREATE INDEX IF NOT EXISTS ix_c_status ON complaints(status);
""")
for col in ("review_needed", "review_asked"):
    try:
        conn.execute(f"ALTER TABLE complaints ADD COLUMN {col} INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
for col in ("progress_at TEXT", "progress_by TEXT"):
    try:
        conn.execute(f"ALTER TABLE complaints ADD COLUMN {col}")
    except sqlite3.OperationalError:
        pass
for col in ("msg_id INTEGER", "ttl INTEGER DEFAULT 15"):
    try:
        conn.execute(f"ALTER TABLE notices ADD COLUMN {col}")
    except sqlite3.OperationalError:
        pass
for col in ("draft TEXT", "draft_user INTEGER", "draft_at TEXT"):
    try:
        conn.execute(f"ALTER TABLE complaints ADD COLUMN {col}")
    except sqlite3.OperationalError:
        pass
try:
    conn.execute("ALTER TABLE complaints ADD COLUMN notified_at TEXT")
except sqlite3.OperationalError:
    pass
try:
    conn.execute("ALTER TABLE complaints ADD COLUMN src_group TEXT")
except sqlite3.OperationalError:
    pass
try:
    conn.execute("ALTER TABLE notices ADD COLUMN buttons TEXT")
except sqlite3.OperationalError:
    pass
try:
    conn.execute("ALTER TABLE complaints ADD COLUMN review_by TEXT")
except sqlite3.OperationalError:
    pass
conn.commit()


def q(sql, *args):
    return conn.execute(sql, args).fetchall()


def q1(sql, *args):
    return conn.execute(sql, args).fetchone()


def x(sql, *args) -> int:
    cur = conn.execute(sql, args)
    conn.commit()
    return cur.lastrowid


def update(table: str, id_: int, **fields):
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
            for v in fields.values()]
    x(f"UPDATE {table} SET {cols} WHERE id=?", *vals, id_)


# --- bindings
def bind(role, chat_id, thread_id):
    x("INSERT OR REPLACE INTO bindings VALUES(?,?,?)", role, chat_id, thread_id)


def binding(role):
    return q1("SELECT * FROM bindings WHERE role=?", role)


def role_of(chat_id, thread_id):
    r = q1("SELECT role FROM bindings WHERE chat_id=? AND thread_id=?", chat_id, thread_id)
    return r["role"] if r else None


# --- users
def remember_user(u):
    x("""INSERT INTO users(user_id, username, full_name) VALUES(?,?,?)
         ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name""",
      u.id, (u.username or "").lower() or None, u.full_name)


def staff_role(user_id):
    r = q1("SELECT staff_role FROM users WHERE user_id=?", user_id)
    return r["staff_role"] if r else None


def set_staff_role(user_id, role):
    x("UPDATE users SET staff_role=? WHERE user_id=?", role, user_id)
