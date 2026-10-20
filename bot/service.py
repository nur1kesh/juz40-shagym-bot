import asyncio
import html
import json
import logging
import re
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReactionTypeEmoji

from . import ai, db, people, staff as staff_list, templates as T
from .config import REMINDER_EVERY_MIN, SLA_HOURS, TZ, in_work_window, now
from .roster import STREAM_NAMES, responsible_for

log = logging.getLogger(__name__)
PROGRAM_LABEL = {"smart": "SMART", "junior": "JUNIOR"}
PROGRESS_WORDS = ("процесс", "жұмыс істеп", "жумыс истеп", "қарап жатыр", "карап жатыр", "қарастыр", "шешіліп жатыр",
                  "шешуде", "шешіп жатыр", "шешудеміз", "шешемін", "шешеміз", "айналысып", "іздеп жатыр",
                  "в работе", "работаем", "занимаемся", "в процессе", "решаем", "разбираем", "уточняем",
                  "қабылданды", "қабылдадық", "қабылдадым", "кб береміз", "кб беремін", "кері байланыс береміз",
                  "кері байланыс беремін", "жауап береміз", "жауап беремін", "хабарласамыз", "байланысамыз",
                  "принято", "приняли", "принял", "дадим обратную связь", "сообщим", "ответим")
PROGRESS_REACTION = "✍"
SOLUTION_AI_CHECK_BELOW = 80  # осыдан қысқа шешімді ғана OpenAI «мазмұнды ма» деп тексереді
SEND_GAP = 0.3        # Telegram лимитінен (≈20 хабарлама/мин топқа) қашу үшін хабарламалар арасы, сек
RETRY_BACKOFF_MIN = 5  # жіберу сәтсіз болса, қайталау алдындағы үзіліс
_locks: dict[int, asyncio.Lock] = {}
def iso(dt: datetime) -> str:
    return dt.isoformat()


def parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(TZ)


def is_progress_note(text: str) -> bool:
    """«Процессте», «шешіліп жатыр» сияқты аралық жауап (шаблонсыз, қысқа)."""
    n = T.norm(text)
    return len(n) <= 160 and any(w in n for w in PROGRESS_WORDS)


def tpl_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📝 Шешім шаблоны", callback_data="tpl:res")]])


def thr(t):
    return t or None


# ---------------------------------------------------------------- хабарлама кезегі (тек жұмыс уақытында жіберіледі)
def queue_notice(key: str, chat_id, thread_id, reply_to, text: str, buttons=None, ttl: int = 15):
    """Қысқа ескерту кезекке тұрады. ttl - минуттан кейін өзі өшеді (0 - өшпейді)."""
    db.x("UPDATE notices SET sent=2 WHERE key=? AND sent=0", key)  # ескі кезектегісінің күші жойылды
    db.x("INSERT INTO notices(key,chat_id,thread_id,reply_to,text,buttons,ttl) VALUES(?,?,?,?,?,?,?)",
         key, chat_id, thread_id, reply_to, text, json.dumps(buttons) if buttons else None, ttl)


async def clear_notices(bot: Bot, key: str, keep_id: int | None = None):
    """Кезектегіні болдырмайды, жіберілгенін чаттан өшіреді (қате түзетілгенде чат таза қалу үшін)."""
    db.x("UPDATE notices SET sent=2 WHERE key=? AND sent=0", key)
    for n in db.q("SELECT * FROM notices WHERE key=? AND sent=1 AND msg_id IS NOT NULL AND id!=?",
                  key, keep_id or -1):
        try:
            await bot.delete_message(n["chat_id"], n["msg_id"])
        except Exception:
            pass
        db.x("DELETE FROM ephemeral WHERE chat_id=? AND msg_id=?", n["chat_id"], n["msg_id"])
        db.x("UPDATE notices SET sent=2 WHERE id=?", n["id"])


def track_ephemeral(chat_id: int, msg_id: int, minutes: float):
    db.x("INSERT OR REPLACE INTO ephemeral VALUES(?,?,?)", chat_id, msg_id,
         iso(now() + timedelta(minutes=minutes)))


async def _sweep_ephemeral(bot: Bot):
    for e in db.q("SELECT * FROM ephemeral"):
        if parse_dt(e["delete_at"]) <= now():
            try:
                await bot.delete_message(e["chat_id"], e["msg_id"])
            except Exception:
                pass
            db.x("DELETE FROM ephemeral WHERE chat_id=? AND msg_id=?", e["chat_id"], e["msg_id"])


def errors_text(errors: list[str], kind: str) -> str:
    hint = "/shablon" if kind == "complaint" else "/shesim"
    return f"❌ Түзетіңіз (edit) · {hint}\n• " + "\n• ".join(html.escape(e) for e in errors)


# ---------------------------------------------------------------- жауапты адамдар
def compute_responsible(program: str, parsed: dict):
    coords, sups, notes = [], [], []

    def add(lst, items):
        for p in items:
            if p.key not in {x.key for x in lst}:
                lst.append(p)

    pairs = parsed.get("pairs") or [[None, parsed.get("stream")]]
    for subj, stream in pairs:
        c, s, n = responsible_for(program, [subj] if subj else [], stream)
        add(coords, c)
        add(sups, s)
        notes += [x for x in n if x not in notes]
    for u in parsed.get("mentioned_usernames") or []:
        p = people.by_username(u)
        if p and p.key not in {x.key for x in coords + sups}:
            coords.append(p)
    return coords, sups, notes


def resp_json(coords, sups):
    return {"coords": [people.to_dict(p) for p in coords], "sups": [people.to_dict(p) for p in sups]}


def tags_inline(c) -> str:
    resp = json.loads(c["responsible"] or "{}")
    people_ = [people.from_dict(d) for d in resp.get("coords", []) + resp.get("sups", [])]
    return ", ".join(people.mention(p) for p in people_) or "жауапты анықталмады"


def label(c, with_issue: bool = True) -> str:
    p = json.loads(c["parsed"] or "{}")
    bits = [PROGRAM_LABEL.get(c["program"], "?")]
    if p.get("subject_label"):
        bits.append(html.escape(re.sub(r"(?i)^\s*(smart|junior)\s*", "", p["subject_label"])) or "")
    if with_issue and p.get("issue_title"):
        bits.append(html.escape(p["issue_title"]))
    return " · ".join(b for b in bits if b)


def msg_link(c) -> str:
    cid = str(abs(c["target_chat"] or 0))
    cid = cid[3:] if cid.startswith("100") else cid
    return f"https://t.me/c/{cid}/{c['target_msg']}"


def short(c) -> str:
    p = json.loads(c["parsed"] or "{}")
    bits = [f"#{c['id']}", PROGRAM_LABEL.get(c["program"], "?")]
    if p.get("subject_label"):
        bits.append(html.escape(p["subject_label"]))
    if p.get("issue_title"):
        bits.append(html.escape(p["issue_title"]))
    return " · ".join(bits)


def sla_start(c) -> datetime:
    """24 сағат жауаптыларға хабарланған сәттен басталады (түнде/жексенбіде келгенде бірден 'өтті' болмас үшін)."""
    return parse_dt(c["notified_at"] or c["created_at"])


def deadline(c) -> datetime:
    return sla_start(c) + timedelta(hours=SLA_HOURS)


# ---------------------------------------------------------------- скриншот (мәтінсіз медиа)
MEDIA_NOTICE = "📎 Мәтінсіз. Шаблон: /shablon"


def track_media(msg, thread: int):
    """Альбомның барлық хабарламасын есте сақтаймыз (сыртқы сатудан толық көшіру үшін)."""
    gid = msg.media_group_id
    if not gid:
        return
    db.x("INSERT OR IGNORE INTO media_msgs VALUES(?,?,?)", msg.chat.id, gid, msg.message_id)
    if msg.caption or msg.text:  # caption бар - альбом "ок", ескерту керек емес
        db.x("""INSERT INTO media_groups VALUES(?,?,?,?,?,?,'ok')
                ON CONFLICT(chat_id, group_id) DO UPDATE SET status='ok'""",
             msg.chat.id, gid, thread, msg.message_id, msg.from_user.id, iso(now()))


def _recent_complaint_by(user_id: int, chat_id: int) -> bool:
    r = db.q1("SELECT created_at FROM complaints WHERE author_id=? AND src_chat=? ORDER BY id DESC LIMIT 1",
              user_id, chat_id)
    return bool(r) and now() - parse_dt(r["created_at"]) < timedelta(minutes=15)


async def process_media_only(bot: Bot, msg):
    """Мәтінсіз скриншот: шаблонды сұраймыз (альбомда caption жоқ екені расталған соң)."""
    thread = msg.message_thread_id if msg.is_topic_message else 0
    if _recent_complaint_by(msg.from_user.id, msg.chat.id):
        return  # жақында шағым жазған адамның қосымша дәлелі
    if msg.media_group_id:
        db.x("""INSERT OR IGNORE INTO media_groups VALUES(?,?,?,?,?,?,'pending')""",
             msg.chat.id, msg.media_group_id, thread, msg.message_id, msg.from_user.id, iso(now()))
        return
    queue_notice(f"{msg.chat.id}:{msg.message_id}", msg.chat.id, thread, msg.message_id, MEDIA_NOTICE)
    await flush(bot)


def _warn_pending_albums():
    for g in db.q("SELECT * FROM media_groups WHERE status='pending'"):
        if now() - parse_dt(g["created_at"]) >= timedelta(seconds=30):
            queue_notice(f"{g['chat_id']}:{g['first_msg']}", g["chat_id"], g["thread"], g["first_msg"],
                         MEDIA_NOTICE)
            db.x("UPDATE media_groups SET status='warned' WHERE chat_id=? AND group_id=?",
                 g["chat_id"], g["group_id"])


# ---------------------------------------------------------------- шағым қабылдау
async def _fill_auto(parsed: dict) -> dict:
    """Қызметі / мәселе типі / қызметкер аты бос болса, OpenAI мәтін бойынша толтырады."""
    if parsed.get("employee_role") and parsed.get("issue_title") and parsed.get("employee_name"):
        return parsed
    cls = await ai.classify(parsed["description"])
    parsed["employee_role"] = parsed.get("employee_role") or cls["role"]
    parsed["issue_title"] = parsed.get("issue_title") or cls["title"]
    parsed["employee_name"] = parsed.get("employee_name") or cls["employee_name"] or "-"
    return parsed


def _is_image_doc(msg) -> bool:
    d = getattr(msg, "document", None)
    return bool(d and (getattr(d, "mime_type", "") or "").startswith("image/"))


async def _ocr(bot: Bot, msg) -> str | None:
    """Хабарламадағы суретті (фото не сурет-файл) жүктеп, ішіндегі мәтінді оқиды."""
    try:
        photo = getattr(msg, "photo", None)
        file = photo[-1] if photo else (msg.document if _is_image_doc(msg) else None)
        if file is None:
            return None
        buf = await bot.download(file)
        data = buf.read() if buf else b""
        if not data or len(data) > 8_000_000:
            return None
        return await ai.read_screenshot(data)
    except Exception:
        log.exception("ocr failed")
        return None


def _worth_ai(text: str) -> bool:
    """Қысқа чатқа OpenAI шақырмаймыз: көп жолды, телефоны бар немесе ұзын хабарлама ғана."""
    t = text.strip()
    return len(t) >= 40 and (t.count("\n") >= 2 or bool(re.search(r"\d{9,}", t)) or len(t) >= 80)


async def process_complaint(bot: Bot, msg, role: str, edited: bool = False):
    text = msg.text or msg.caption
    if not text:
        return
    thread = msg.message_thread_id if msg.is_topic_message else 0
    key = f"{msg.chat.id}:{msg.message_id}"
    existing = db.q1("SELECT * FROM complaints WHERE src_chat=? AND src_msg=?", msg.chat.id, msg.message_id)
    if existing and existing["status"] == "resolved":
        return
    labels = T.parse(text, T.COMPLAINT_LABELS)
    topic_program = {"smart_hate": "smart", "junior_hate": "junior"}.get(role)  # topic өзі бағдарламаны білдіреді
    parsed, errors = (None, ["шаблон жоқ"])
    if labels:
        parsed, errors, _ = T.validate_complaint(text, topic_program)
    if errors:
        # шаблон дұрыс емес/жоқ: OpenAI еркін мәтіннен өрістерді өзі ажыратып, мүмкін болса қабылдайды
        has_media = bool(getattr(msg, "photo", None) or _is_image_doc(msg))
        work = text
        ext = await ai.extract_complaint(work) if (_worth_ai(text) or has_media) else None
        if has_media and (ext is None or not ext.get("description")):
            ocr = await _ocr(bot, msg)  # сипаттама көбіне скриншотта: суреттегі мәтінді оқимыз
            if ocr:
                work = f"{text}\n\n[Скриншоттағы мәтін]\n{ocr}"
                ext = await ai.extract_complaint(work) or ext
        if ext is not None and (ext["is_complaint"] or labels or (has_media and ext.get("student_name"))):
            ext = dict(ext)
            ext["subject"] = ext.get("subject") or T.find_subject_in_text(work)
            if has_media and not ext.get("description"):
                ext["description"] = "Сипаттама тіркемеде (скриншот)"
            p2, e2, _ = T.validate_complaint(T.synth_complaint(labels, ext), topic_program)
            if not e2:
                p2["mentioned_usernames"] = re.findall(r"@(\w{4,})", text)
                p2["chat_link"] = (T.URL_RE.search(text).group(0) if T.URL_RE.search(text) else None)
                p2["auto"] = True
                parsed, errors = p2, []
            else:
                errors = e2
        elif ext is not None:
            return  # чат, шағым емес
        elif not labels:
            # OpenAI қолжетімсіз/мәтін қысқа: шаблонсыз ұзын хабарламаға бір рет ескерту
            if not existing and not edited and len(text.strip()) >= 60:
                queue_notice(key, msg.chat.id, thread, msg.message_id, "📝 Шаблонсыз. Шаблон: /shablon")
                await flush(bot)
            return
    if errors:
        queue_notice(key, msg.chat.id, thread, msg.message_id, errors_text(errors, "complaint"))
        await flush(bot)
        return
    external = role == "external_hate"
    program = parsed["program"] if external else ("smart" if role == "smart_hate" else "junior")
    if not external and parsed["program"] and parsed["program"] != program:
        queue_notice(key, msg.chat.id, thread, msg.message_id, errors_text(
            [f"Бұл {PROGRAM_LABEL[program]} topic-і, ал пәнде {PROGRAM_LABEL[parsed['program']]} көрсетілген"],
            "complaint"))
        await flush(bot)
        return
    if not existing:
        dup = db.q1("SELECT id FROM complaints WHERE author_id=? AND text=? AND status IN "
                    "('new','awaiting_program','open')", msg.from_user.id, text)
        if dup:
            queue_notice(key, msg.chat.id, thread, msg.message_id,
                         f"ℹ️ Бұл шағым #{dup['id']} ашық, қайталанбады.")
            await flush(bot)
            return
    await clear_notices(bot, key)
    if parsed.get("pairs"):  # «География SMART ҚЫРКҮЙЕК» -> «SMART ГЕО-21» (Excel форматы)
        parsed["subject_label"] = T.normalize_label(program, [tuple(x) for x in parsed["pairs"]])
    parsed = await _fill_auto(parsed)
    if program:
        coords, sups, notes = compute_responsible(program, parsed)
        resp, note = resp_json(coords, sups), "; ".join(notes)
    else:
        resp, note = {}, ""
    if existing:  # өңделген хабарлама: деректерді жаңартамыз
        db.update("complaints", existing["id"], text=text, parsed=parsed, responsible=resp, notes=note)
        return
    tgt = db.binding(f"{program}_hate") if program else None
    status = "new" if program else "awaiting_program"
    staff = "external" if external else (
        db.staff_role(msg.from_user.id) or staff_list.role_for(msg.from_user.full_name))
    cid = db.x(
        """INSERT INTO complaints(status, source, src_chat, src_thread, src_msg, author_id, author_name,
           author_role, program, target_chat, target_thread, target_msg, text, parsed, responsible, notes, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        status, "external" if external else "direct", msg.chat.id, thread, msg.message_id,
        msg.from_user.id, msg.from_user.full_name, staff, program,
        tgt["chat_id"] if tgt else None, tgt["thread_id"] if tgt else None,
        None if external else msg.message_id,
        text, json.dumps(parsed, ensure_ascii=False), json.dumps(resp, ensure_ascii=False), note, iso(now()))
    if msg.media_group_id:
        db.update("complaints", cid, src_group=msg.media_group_id)
    log.info("complaint #%s created (%s)", cid, status)
    await flush(bot)


# ---------------------------------------------------------------- шешім қабылдау
def can_resolve(c, user) -> bool:
    """Шағымды жазған адам және басқа тех/сату қызметкерлері шеше алмайды (координатор/супервайзер/админ - иә)."""
    if people.is_roster_user(user):
        return True
    if user.id == c["author_id"]:
        return False
    return not (db.staff_role(user.id) or staff_list.role_for(user.full_name))


def deny_notice(msg, thread: int, key: str):
    queue_notice(key, msg.chat.id, thread, msg.message_id,
                 "⛔ Шешімді тек координатор/супервайзер жібере алады.")


def find_complaint(chat_id: int, reply_to_id: int):
    r = db.q1("SELECT * FROM complaints WHERE target_chat=? AND target_msg=?", chat_id, reply_to_id)
    if r:
        return r
    b = db.q1("SELECT complaint_id FROM bot_msgs WHERE chat_id=? AND msg_id=?", chat_id, reply_to_id)
    return db.q1("SELECT * FROM complaints WHERE id=?", b["complaint_id"]) if b else None


async def process_reply(bot: Bot, msg, edited: bool = False) -> bool:
    """False - reply ешбір шағымға байланбады (шақырушы reply-сыз іздеуге көше алады)."""
    c = find_complaint(msg.chat.id, msg.reply_to_message.message_id)
    text = msg.text or msg.caption
    if not c:
        cands = digest_candidates(msg.chat.id, msg.reply_to_message.message_id)
        if not cands:
            return False
        c = pick_from_digest(text or "", cands)
        if not c:  # ортақ тізімге жауап, бірақ қай шағым екені белгісіз
            if text and T.parse(text, T.RESOLUTION_LABELS):
                return False  # шаблон болса, оқушы аты бойынша жалпы іздеу жұмыс істейді
            if text and can_resolve(cands[0], msg.from_user):
                thread = msg.message_thread_id if msg.is_topic_message else 0
                queue_notice(f"{msg.chat.id}:{msg.message_id}", msg.chat.id, thread, msg.message_id,
                             "❓ Қай шағым? Шағымның #нөмірін немесе оқушы атын жазыңыз, не шағым хабарламасына reply етіңіз.",
                             ttl=10)
                await flush(bot)
            return True
    if not text or c["status"] not in ("open", "new") or msg.chat.id != c["target_chat"]:
        return True
    thread = msg.message_thread_id if msg.is_topic_message else 0
    key = f"{msg.chat.id}:{msg.message_id}"
    labels = T.parse(text, T.RESOLUTION_LABELS)
    if labels and not can_resolve(c, msg.from_user):
        deny_notice(msg, thread, key)
        await flush(bot)
        return True
    if not labels:
        if not can_resolve(c, msg.from_user):
            return True
        res = await _free_reply(text, c)
        if res["kind"] == "progress":
            await mark_progress(bot, msg, c, text)
        elif res["kind"] == "resolution":
            await offer_resolution(bot, msg, c, text, res, thread, key)
        elif not edited and len(text.strip()) >= 80:
            queue_notice(key, msg.chat.id, thread, msg.message_id, "📝 Шешім шаблонсыз. Шаблон: /shesim")
            await flush(bot)
        return True
    await _resolve(bot, msg, c, text, thread, key)
    return True


async def _free_reply(text: str, c) -> dict:
    """Шаблонсыз жауап: аралық па, шешім бе, әлде басқа ма. Қысқа және анық «процессте» сөздері OpenAI-сыз танылады."""
    t = text.strip()
    if len(t) <= 100 and is_progress_note(t):
        return {"kind": "progress"}
    if len(t) < 8:
        return {"kind": "other"}
    res = await ai.classify_reply(t, c["text"])
    if res is None:  # OpenAI істемесе: тек сөздер бойынша
        return {"kind": "progress" if is_progress_note(t) else "other"}
    return res


async def offer_resolution(bot: Bot, msg, c, text: str, ext: dict, thread: int, key: str):
    """Координатор шешімді шаблонсыз жазды: бәрі анық болса жабамыз, әйтпесе дайын толтырылған шаблон ұсынамыз."""
    student = json.loads(c["parsed"] or "{}").get("student_name") or ""
    tpl = T.synth_resolution(student, ext, text)
    _, errors, _ = T.validate_resolution(tpl, student)
    if not errors:
        await _resolve(bot, msg, c, tpl, thread, key)
        return
    missing = [name for name, k in (("Негізделген бе?", "justified"), ("Риза ма?", "satisfied"),
                                    ("Қалды ма?", "stayed")) if not ext.get(k)]
    note = ("Жетпейді: " + ", ".join(missing)) if missing else "; ".join(errors)
    queue_notice(key, msg.chat.id, thread, msg.message_id,
                 f"📝 Шешім сияқты. Жабу үшін толықтырып жіберіңіз:\n<code>{html.escape(tpl)}</code>\n{html.escape(note)}",
                 ttl=30)
    await flush(bot)


def digest_candidates(chat_id: int, msg_id: int) -> list:
    rows = db.q("SELECT c.* FROM digest_msgs d JOIN complaints c ON c.id=d.complaint_id "
                "WHERE d.chat_id=? AND d.msg_id=? AND c.status IN ('open','new') ORDER BY c.id", chat_id, msg_id)
    return list(rows)


def pick_from_digest(text: str, cands: list):
    """Ортақ тізімге жауап: #нөмір, не оқушы аты бойынша ЖАЛҒЫЗ шағымды таңдаймыз; болмаса None."""
    m = re.search(r"#\s*(\d+)", text)
    if m:
        for c in cands:
            if c["id"] == int(m.group(1)):
                return c
    if len(cands) == 1:
        return cands[0]
    toks = {t for t in T.norm(text).split() if len(t) >= 3}
    scored = []
    for c in cands:
        name = json.loads(c["parsed"] or "{}").get("student_name") or ""
        scored.append((len(toks & {t for t in T.norm(name).split() if len(t) >= 3}), c))
    best = max(sc for sc, _ in scored)
    top = [c for sc, c in scored if sc == best]
    return top[0] if best > 0 and len(top) == 1 else None


async def mark_progress(bot: Bot, msg, c, text: str):
    """«Процессте» деп жауап берілді: шағым ашық қалады, бірақ белгіленеді (✍ реакциясы + еске салуда көрінеді)."""
    by = (msg.from_user.full_name or "").split("|")[0].strip()
    db.update("complaints", c["id"], progress_at=iso(now()), progress_by=by)
    db.x("INSERT INTO replies(complaint_id,user_id,user_name,text,kind,created_at) VALUES(?,?,?,?,?,?)",
         c["id"], msg.from_user.id, msg.from_user.full_name, text, "progress", iso(now()))
    try:
        await bot.set_message_reaction(msg.chat.id, msg.message_id, [ReactionTypeEmoji(emoji=PROGRESS_REACTION)])
    except Exception:
        pass


async def _resolve(bot: Bot, msg, c, text: str, thread: int, key: str):
    expected = json.loads(c["parsed"] or "{}").get("student_name")
    final, errors, _ = T.validate_resolution(text, expected)
    if not errors and len(final["edu_response"]) < SOLUTION_AI_CHECK_BELOW:
        # ұзын шешім өзі мазмұнды: OpenAI тексермейді (қайта-қайта қабылдамай, жауапты ренжітпеу үшін)
        verdict = await ai.check_solution(c["text"], final["edu_response"])
        if not verdict.get("ok", True):
            errors = [f"«Мәселенің шешімі» нақты емес: {verdict.get('reason') or 'нақтылаңыз'}"]
    if errors:
        queue_notice(key, msg.chat.id, thread, msg.message_id, errors_text(errors, "resolution"))
        await flush(bot)
        return
    await clear_notices(bot, key)
    db.x("INSERT INTO replies(complaint_id,user_id,user_name,text,kind,created_at) VALUES(?,?,?,?,?,?)",
         c["id"], msg.from_user.id, msg.from_user.full_name, text, "final", iso(now()))
    await close_complaint(bot, c["id"], msg.from_user.full_name, final)


def _overlap(a: str, b: str) -> bool:
    return bool(set(T.norm(a).split()) & set(T.norm(b or "").split()))


def is_loose_resolution(text: str) -> bool:
    """Reply-сыз жазылған, бірақ шешім шаблонының өрістері бар хабарлама."""
    return bool(set(T.parse(text, T.RESOLUTION_LABELS)) & {"solution", "justified", "satisfied", "stayed"})


async def process_loose_resolution(bot: Bot, msg, edited: bool = False):
    """Reply жоқ: оқушы аты-жөні бойынша ашық шағымды табамыз."""
    text = msg.text or msg.caption
    thread = msg.message_thread_id if msg.is_topic_message else 0
    key = f"{msg.chat.id}:{msg.message_id}"
    student = T.parse(text, T.RESOLUTION_LABELS).get("student", "")
    _, errors, _ = T.validate_resolution(text, None)
    if errors:
        queue_notice(key, msg.chat.id, thread, msg.message_id, errors_text(errors, "resolution"))
        return await flush(bot)
    cands = [c for c in db.q("SELECT * FROM complaints WHERE status IN ('open','new') "
                             "AND target_chat=? AND target_thread=?", msg.chat.id, thread)
             if _overlap(student, json.loads(c["parsed"] or "{}").get("student_name"))
             and can_resolve(c, msg.from_user)]
    if not cands:
        queue_notice(key, msg.chat.id, thread, msg.message_id,
                     f"❓ «{html.escape(student)}» бойынша ашық шағым жоқ. Шағымға reply етіңіз.")
        return await flush(bot)
    await clear_notices(bot, key)
    if len(cands) == 1:
        return await _resolve(bot, msg, cands[0], text, thread, key)
    pid = db.x("INSERT INTO pending_res(chat_id,user_id,user_name,text) VALUES(?,?,?,?)",
               msg.chat.id, msg.from_user.id, msg.from_user.full_name, text)
    queue_notice(key, msg.chat.id, thread, msg.message_id,
                 "❓ Қай шағымға?",
                 [(f"#{c['id']} · {json.loads(c['parsed']).get('subject_label', '')}", f"pick:{pid}:{c['id']}")
                  for c in cands], ttl=0)
    await flush(bot)


async def finish_pending(bot: Bot, pid: int, cid: int) -> str:
    """'pick' түймесі: күтіп тұрған шешімді таңдалған шағымға байлау."""
    pend = db.q1("SELECT * FROM pending_res WHERE id=? AND done=0", pid)
    c = db.q1("SELECT * FROM complaints WHERE id=?", cid)
    if not pend or not c or c["status"] not in ("open", "new"):
        return "Бұл шешім қазірдің өзінде өңделген"
    final, errors, _ = T.validate_resolution(
        pend["text"], json.loads(c["parsed"] or "{}").get("student_name"))
    if errors:
        return "❌ " + "; ".join(errors)
    db.x("UPDATE pending_res SET done=1 WHERE id=?", pid)
    db.x("INSERT INTO replies(complaint_id,user_id,user_name,text,kind,created_at) VALUES(?,?,?,?,?,?)",
         cid, pend["user_id"], pend["user_name"], pend["text"], "final", iso(now()))
    await close_complaint(bot, cid, pend["user_name"], final)
    return f"✅ Шағым #{cid} шешілді деп белгіленді"


async def delete_complaint(bot: Bot, cid: int) -> bool:
    """Қате шағымды өшіру: күйі 'deleted' (тарих сақталады), /open, еске салу және Excel-ден шығады,
    боттың осы шағымға жазған барлық хабарламасы (🔔, еске салу, шешім, көшірме) чаттан өшеді."""
    c = db.q1("SELECT * FROM complaints WHERE id=?", cid)
    if not c or c["status"] == "deleted":
        return False
    db.update("complaints", cid, status="deleted")
    await clear_notices(bot, f"{c['src_chat']}:{c['src_msg']}")
    for b in db.q("SELECT * FROM bot_msgs WHERE complaint_id=?", cid):
        try:
            await bot.delete_message(b["chat_id"], b["msg_id"])
        except Exception:
            pass  # 48 сағаттан асқан немесе құқық жоқ - өткізіп жібереміз
        db.x("DELETE FROM ephemeral WHERE chat_id=? AND msg_id=?", b["chat_id"], b["msg_id"])
    db.x("DELETE FROM bot_msgs WHERE complaint_id=?", cid)
    return True


async def close_complaint(bot: Bot, cid: int, by: str, final: dict | None = None):
    c = db.q1("SELECT * FROM complaints WHERE id=?", cid)
    if not c or c["status"] == "resolved":
        return False
    final = final or {"edu_response": "-", "justified": "", "satisfied": "", "stayed": ""}
    db.update("complaints", cid, status="resolved", resolved_at=iso(now()), resolved_by=by,
              final=json.dumps(final, ensure_ascii=False), echo_done=0)
    await flush(bot)
    return True


# ---------------------------------------------------------------- жіберу (тек жұмыс уақытында)
async def send(bot: Bot, chat_id, thread_id, text, cid, reply_to=None, markup=None):
    kw = dict(message_thread_id=thr(thread_id), parse_mode="HTML", reply_to_message_id=reply_to,
              reply_markup=markup, allow_sending_without_reply=True, disable_web_page_preview=True)
    try:
        m = await bot.send_message(chat_id, text, **kw)
    except TelegramRetryAfter as e:  # Telegram лимиті: күтіп, бір рет қайталаймыз
        await asyncio.sleep(e.retry_after + 1)
        m = await bot.send_message(chat_id, text, **kw)
    if cid:
        db.x("INSERT OR REPLACE INTO bot_msgs VALUES(?,?,?)", chat_id, m.message_id, cid)
    return m


async def flush(bot: Bot):
    """Кезекте тұрғанның бәрін жібереді. Жұмыс уақыты (Дс-Сб 11-21) тек САҒАТ САЙЫНҒЫ ЕСКЕ САЛУҒА қатысты,
    қалғаны (қабылдау, белгілеу, көшіру, жабу, ескертулер) кез келген уақытта жұмыс істейді.
    Бір уақытта тек біреуі жұмыс істейді (handler мен ticker қосарланып жібермеуі үшін)."""
    _warn_pending_albums()
    await _sweep_ephemeral(bot)
    lock = _locks.setdefault(id(asyncio.get_running_loop()), asyncio.Lock())
    async with lock:
        await _flush(bot)


def _recently_failed(c, t) -> bool:
    last = c["last_reminder_at"]
    return bool(last) and t - parse_dt(last) < timedelta(minutes=RETRY_BACKOFF_MIN)


async def _flush(bot: Bot):
    t = now()
    window = in_work_window()  # тек еске салуға

    for n in db.q("SELECT * FROM notices WHERE sent=0"):
        status, mid = 1, None
        try:
            await clear_notices(bot, n["key"], keep_id=n["id"])  # бұрынғы нұсқасын өшіреміз
            kb = None
            if n["buttons"]:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=bt, callback_data=d)] for bt, d in json.loads(n["buttons"])])
            m = await send(bot, n["chat_id"], n["thread_id"], n["text"], None, reply_to=n["reply_to"], markup=kb)
            mid = m.message_id
            if n["ttl"]:
                track_ephemeral(n["chat_id"], mid, n["ttl"])
            await asyncio.sleep(SEND_GAP)
        except Exception:
            log.exception("notice failed")
            status = 3
        db.x("UPDATE notices SET sent=?, msg_id=? WHERE id=?", status, mid, n["id"])

    for c in db.q("SELECT * FROM complaints WHERE status='awaiting_program'"):
        last = c["last_reminder_at"]
        if last and t - parse_dt(last) < timedelta(minutes=REMINDER_EVERY_MIN):
            continue  # сұрақ сағатына бір рет қайталанады, кез келген уақытта (жұмыс уақыты тек шағымды еске салуға)
        try:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="SMART", callback_data=f"prog:{c['id']}:smart"),
                InlineKeyboardButton(text="JUNIOR", callback_data=f"prog:{c['id']}:junior")]])
            await send(bot, c["src_chat"], c["src_thread"],
                       f"❓ #{c['id']}: SMART па, JUNIOR ма?",
                       c["id"], reply_to=c["src_msg"], markup=kb)
        except Exception:
            log.exception("program prompt failed for #%s", c["id"])
        db.update("complaints", c["id"], last_reminder_at=iso(t))

    for c in db.q("SELECT * FROM complaints WHERE status='new'"):
        if c["src_group"] and t - parse_dt(c["created_at"]) < timedelta(seconds=5):
            continue  # альбомның қалған суреттері келіп жетсін
        if _recently_failed(c, t):
            continue
        try:
            await _dispatch_new(bot, c)
            await asyncio.sleep(SEND_GAP)
        except Exception:
            log.exception("dispatch failed for #%s", c["id"])
            db.update("complaints", c["id"], last_reminder_at=iso(now()))

    due = [c for c in db.q("SELECT * FROM complaints WHERE status='open' ORDER BY id")
           if window and t - parse_dt(c["last_reminder_at"]) >= timedelta(minutes=REMINDER_EVERY_MIN)]
    groups: dict[tuple, list] = {}
    for c in due:
        groups.setdefault((c["target_chat"], c["target_thread"]), []).append(c)
    for rows in groups.values():
        try:
            await _remind_group(bot, rows, t)
            await asyncio.sleep(SEND_GAP)
        except Exception:
            log.exception("reminder failed")
            for c in rows:
                db.update("complaints", c["id"], last_reminder_at=iso(t))

    for c in db.q("SELECT * FROM complaints WHERE status='resolved' AND echo_done<2"):
        try:
            await _notify_resolved(bot, c)
        except Exception:
            log.exception("resolution notice failed for #%s", c["id"])


async def _dispatch_new(bot: Bot, c):
    if not c["target_chat"]:
        b = db.binding(f"{c['program']}_hate") if c["program"] else None
        if not b:
            log.warning("#%s: бағыт (topic) байланбаған. /bind орындаңыз", c["id"])
            return
        db.update("complaints", c["id"], target_chat=b["chat_id"], target_thread=b["thread_id"])
        c = db.q1("SELECT * FROM complaints WHERE id=?", c["id"])
    target_msg = c["target_msg"]
    if c["source"] == "external" and not target_msg:  # көшіру бір-ақ рет (қайталауда көшірмеу)
        ids = [c["src_msg"]]
        if c["src_group"]:
            ids = sorted(r["msg_id"] for r in db.q(
                "SELECT msg_id FROM media_msgs WHERE chat_id=? AND group_id=?",
                c["src_chat"], c["src_group"])) or ids
        copies = await bot.copy_messages(c["target_chat"], c["src_chat"], ids,
                                         message_thread_id=thr(c["target_thread"]))
        for m in copies:
            db.x("INSERT OR REPLACE INTO bot_msgs VALUES(?,?,?)", c["target_chat"], m.message_id, c["id"])
        target_msg = copies[0].message_id
        db.update("complaints", c["id"], target_msg=target_msg)
    due = now() + timedelta(hours=SLA_HOURS)
    p = json.loads(c["parsed"] or "{}")
    lines = [f"🔔 <b>#{c['id']}</b> · {label(c)}" + (" · сыртқы сату" if c["source"] == "external" else "")]
    if p.get("auto"):  # шаблонсыз жазылып, бот өзі танығанда - не түсінгенін көрсетеміз
        lines.append(f"👤 {html.escape(p.get('student_name') or '-')}")
    lines += [tags_inline(c), f"⏳ Дедлайн: {due:%d.%m %H:%M}"]
    if c["notes"]:
        lines.append(f"⚠️ {html.escape(c['notes'])}")
    await send(bot, c["target_chat"], c["target_thread"], "\n".join(lines), c["id"],
               reply_to=target_msg, markup=tpl_kb())
    db.update("complaints", c["id"], status="open", last_reminder_at=iso(now()), notified_at=iso(now()))
    try:
        await bot.set_message_reaction(c["src_chat"], c["src_msg"], [ReactionTypeEmoji(emoji="👀")])
    except Exception:
        pass
    if c["source"] == "direct" and not c["author_role"]:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Тех.менеджер", callback_data=f"role:{c['id']}:tech"),
            InlineKeyboardButton(text="Ішкі сату", callback_data=f"role:{c['id']}:internal")]])
        await send(bot, c["src_chat"], c["src_thread"],
                   f"👤 {html.escape(c['author_name'].split('|')[0].strip())}: рөліңіз? (бір рет)",
                   c["id"], reply_to=c["src_msg"], markup=kb)


def _snippet(c, n: int = 110) -> str:
    d = " ".join((json.loads(c["parsed"] or "{}").get("description") or c["text"] or "").split())
    return html.escape(d[:n] + ("…" if len(d) > n else ""))


def _reminder_text(c, t: datetime) -> str:
    p = json.loads(c["parsed"] or "{}")
    hrs = int((t - sla_start(c)).total_seconds() // 3600)
    over = t - sla_start(c) > timedelta(hours=SLA_HOURS)
    head = (f"🚨 <b>Дедлайн өтті</b> · #{c['id']} · {hrs} сағ" if over
            else f"⏰ <b>Еске салу</b> · #{c['id']} · {hrs} сағ өтті (дедлайн {deadline(c):%d.%m %H:%M})")
    who = html.escape(p.get("student_name") or "-")
    lines = [head, f"👤 {who} · {label(c, with_issue=False)}"]
    about = " · ".join(x for x in (p.get("issue_title"), p.get("employee_role")) if x and x != "-")
    emp = p.get("employee_name")
    if about or (emp and emp != "-"):
        lines.append("📌 " + html.escape(about) + (f": {html.escape(emp)}" if emp and emp != "-" else ""))
    lines.append(f"💬 «{_snippet(c)}»")
    if c["progress_at"]:
        lines.append(f"🔄 <b>Процессте</b> · {html.escape(c['progress_by'] or '')}, {parse_dt(c['progress_at']):%d.%m %H:%M}")
    else:
        lines.append("⏳ Жауап күтілуде")
    lines.append(f"👥 {tags_inline(c)}")
    return "\n".join(lines)


async def _remind_group(bot: Bot, rows: list, t: datetime):
    """Бір topic-те бір уақытта бірнеше шағым болса - бір ортақ хабарлама (чат толмауы үшін)."""
    first = rows[0]
    if len(rows) == 1:
        c = first
        await send(bot, c["target_chat"], c["target_thread"], _reminder_text(c, t), c["id"],
                   reply_to=c["target_msg"], markup=tpl_kb())
    else:
        lines = [f"⏰ <b>Шешілмеген шағымдар ({len(rows)})</b>"]
        for c in rows[:25]:
            p = json.loads(c["parsed"] or "{}")
            hrs = int((t - sla_start(c)).total_seconds() // 3600)
            mark = " 🚨" if t - sla_start(c) > timedelta(hours=SLA_HOURS) else ""
            mark += " 🔄" if c["progress_at"] else ""
            lines.append(f'<a href="{msg_link(c)}">#{c["id"]}</a> · {html.escape(p.get("student_name") or "-")} · '
                         f'{label(c, with_issue=False)} · {hrs} сағ{mark}\n└ {tags_inline(c)}')
        lines.append("🔄 - процессте")
        m = await send(bot, first["target_chat"], first["target_thread"], "\n".join(lines), None)
        for c in rows[:25]:  # осы тізімге reply жасалса, қай шағым екенін оқушы аты бойынша табамыз
            db.x("INSERT OR IGNORE INTO digest_msgs VALUES(?,?,?)", first["target_chat"], m.message_id, c["id"])
    for c in rows:
        db.update("complaints", c["id"], last_reminder_at=iso(t), reminder_count=(c["reminder_count"] or 0) + 1)


async def _notify_resolved(bot: Bot, c):
    """echo_done: 0 - ештеңе жіберілмеген, 1 - шағым topic-іне жіберілді, 2 - бәрі дайын (қайталау болмауы үшін)."""
    f = json.loads(c["final"] or "{}")
    text = f"✅ <b>#{c['id']}</b> шешілді · {html.escape((c['resolved_by'] or '').split('|')[0].strip())}"
    echo = text + "\n" + html.escape((f.get("edu_response") or "-")[:500])
    if c["echo_done"] < 1:
        await send(bot, c["target_chat"], c["target_thread"], text, c["id"], reply_to=c["target_msg"])
        db.update("complaints", c["id"], echo_done=1 if c["source"] == "external" else 2)
    if c["source"] == "external":
        db.update("complaints", c["id"], echo_done=2)  # сәтсіз болса да қайталамаймыз (дубль болмасын)
        await send(bot, c["src_chat"], c["src_thread"], echo, c["id"], reply_to=c["src_msg"])


def _open_rows(chat_id: int | None = None, thread_id: int | None = None, external_src: bool = False):
    rows = db.q("SELECT * FROM complaints WHERE status IN ('open','new','awaiting_program') ORDER BY id")
    if chat_id is None:
        return rows
    if external_src:
        return [c for c in rows if c["source"] == "external" and c["src_chat"] == chat_id and c["src_thread"] == thread_id]
    return [c for c in rows if c["target_chat"] == chat_id and c["target_thread"] == thread_id]


def _status_line(c, t: datetime) -> str:
    if c["status"] == "awaiting_program":
        return "❓ SMART/JUNIOR таңдалмаған"
    if c["status"] == "new":
        return "🕒 жіберілуде"
    hrs = int((t - sla_start(c)).total_seconds() // 3600)
    over = t - sla_start(c) > timedelta(hours=SLA_HOURS)
    flag = "🚨 дедлайн өтті" if over else f"дедлайн {deadline(c):%d.%m %H:%M}"
    prog = ""
    if c["progress_at"]:
        prog = f" · 🔄 процессте ({html.escape((c['progress_by'] or '').split('|')[0].strip())}, {parse_dt(c['progress_at']):%H:%M})"
    return f"🕒 {hrs} сағ · {flag}{prog}"


def _open_item(c, t: datetime) -> str:
    p = json.loads(c["parsed"] or "{}")
    resp = json.loads(c["responsible"] or "{}")
    names = [d["name"] for d in resp.get("coords", []) + resp.get("sups", [])]
    people_line = ", ".join(html.escape(n) for n in names[:4]) + (f" +{len(names) - 4}" if len(names) > 4 else "")
    title = f'<a href="{msg_link(c)}">#{c["id"]}</a>' if c["target_msg"] else f"#{c['id']}"
    about = " · ".join(x for x in (p.get("issue_title"), p.get("employee_role")) if x and x != "-")
    emp = p.get("employee_name")
    lines = [f"{title} · <b>{html.escape(p.get('student_name') or '-')}</b> · {label(c, with_issue=False)}"]
    if about or (emp and emp != "-"):
        lines.append("📌 " + html.escape(about) + (f": {html.escape(emp)}" if emp and emp != "-" else ""))
    lines.append(_status_line(c, t))
    lines.append(f"✍ {html.escape((c['author_name'] or '-').split('|')[0].strip())}"
                 + (f" · 👥 {people_line}" if people_line else ""))
    return "\n".join(lines)


def fmt_open_list(chat_id: int | None = None, thread_id: int | None = None, external_src: bool = False) -> list[str]:
    """Ашық шағымдардың толық тізімі (Telegram лимитіне қарай бөліктерге бөлінеді). Атаулар @-сыз: ешкімді қоңыраулатпайды."""
    rows = _open_rows(chat_id, thread_id, external_src)
    if not rows:
        return ["Ашық шағым жоқ ✅"]
    t = now()
    over = sum(1 for c in rows if c["status"] == "open" and t - sla_start(c) > timedelta(hours=SLA_HOURS))
    prog = sum(1 for c in rows if c["progress_at"])
    head = f"📋 <b>Ашық шағымдар ({len(rows)})</b>" + (f" · 🚨 {over}" if over else "") + (f" · 🔄 {prog}" if prog else "")
    chunks, cur = [], head
    for c in rows:
        item = _open_item(c, t)
        if len(cur) + len(item) + 2 > 3800:
            chunks.append(cur)
            cur = item
        else:
            cur += "\n\n" + item
    chunks.append(cur)
    return chunks
