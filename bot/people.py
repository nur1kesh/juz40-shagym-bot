"""Roster адамдарын Telegram пайдаланушыларымен сәйкестендіру және mention жасау."""
import html
import re

from . import db
from .roster import ALL_PEOPLE, Person


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^\wәіңғүұқөһ]+", s.lower()) if len(t) > 1}


def find_user_id(p: Person) -> int | None:
    if p.username:
        r = db.q1("SELECT user_id FROM users WHERE username=?", p.username.lower())
        if r:
            return r["user_id"]
    want = _tokens(p.name)
    if len(want) < 2:
        return None
    for r in db.q("SELECT user_id, full_name FROM users"):
        if want <= _tokens(r["full_name"] or ""):
            return r["user_id"]
    return None


def mention(p: Person) -> str:
    if p.username:
        return f"@{p.username}"
    uid = find_user_id(p)
    if uid:
        return f'<a href="tg://user?id={uid}">{html.escape(p.name)}</a>'
    return f"<b>{html.escape(p.name)}</b>"


def is_roster_user(user) -> bool:
    uname = (user.username or "").lower()
    toks = _tokens(user.full_name or "")
    for p in ALL_PEOPLE:
        if uname and p.username and uname == p.username.lower():
            return True
        want = _tokens(p.name)
        if not p.username and len(want) >= 2 and want <= toks:
            return True
    return False


def by_username(uname: str) -> Person | None:
    for p in ALL_PEOPLE:
        if p.username and p.username.lower() == uname.lower().lstrip("@"):
            return p
    return None


def to_dict(p: Person) -> dict:
    return {"name": p.name, "username": p.username}


def from_dict(d: dict) -> Person:
    return Person(d["name"], d.get("username"))
