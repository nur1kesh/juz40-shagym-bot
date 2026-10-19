"""Бизнес-логика сценарийлері: жалған Telegram + басқарылатын сағат. Нақты желі/OpenAI керек емес."""
import asyncio
import io
import json
import os
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

os.environ.update(BOT_TOKEN="1:x", OPENAI_API_KEY="sk-x",
                  DB_PATH=os.path.join(tempfile.mkdtemp(), "t.db"))

from bot import config, db, exporter, service, templates as T  # noqa: E402
import bot.main as bm  # noqa: E402

REAL_WINDOW = config.in_work_window
from bot import ai as _ai_mod  # noqa: E402
REAL_CLASSIFY = _ai_mod.classify
SMART, JUNIOR, EXT = (-100, 5), (-100, 6), (-200, 9)


def run(coro):
    return asyncio.run(coro)


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def now(self):
        return self.dt

    def set(self, y, m, d, h=12, mi=0):
        self.dt = datetime(y, m, d, h, mi, tzinfo=config.TZ)

    def add(self, **kw):
        self.dt += timedelta(**kw)


class FakeBot:
    def __init__(self):
        self.sent, self.copied, self.reactions = [], [], []
        self.fail_chat = None
        self.deleted = []
        self._id = 5000

    async def send_message(self, chat_id, text, **kw):
        if self.fail_chat == chat_id:
            raise RuntimeError("boom")
        self._id += 1
        self.sent.append(NS(chat=chat_id, thread=kw.get("message_thread_id"), text=text,
                            reply_to=kw.get("reply_to_message_id"), kb=kw.get("reply_markup"), id=self._id))
        return NS(message_id=self._id)

    async def copy_messages(self, chat_id, from_chat_id, ids, **kw):
        self.copied.append((chat_id, list(ids)))
        out = []
        for _ in ids:
            self._id += 1
            out.append(NS(message_id=self._id))
        return out

    async def set_message_reaction(self, *a, **k):
        self.reactions.append(a)

    async def delete_message(self, chat_id, msg_id):
        self.deleted.append((chat_id, msg_id))

    def texts(self):
        return [m.text for m in self.sent]


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for t in ("bindings", "users", "complaints", "replies", "bot_msgs", "notices", "pending_res",
              "media_msgs", "media_groups"):
        db.x(f"DELETE FROM {t}")
    db.bind("smart_hate", *SMART)
    db.bind("junior_hate", *JUNIOR)
    db.bind("external_hate", *EXT)
    clock = Clock(datetime(2026, 10, 14, 12, 0, tzinfo=config.TZ))  # сәрсенбі
    monkeypatch.setattr(config, "now", clock.now)
    monkeypatch.setattr(service, "SEND_GAP", 0)
    monkeypatch.setattr(service, "now", clock.now)
    monkeypatch.setattr(service, "in_work_window", lambda dt=None: REAL_WINDOW(dt or clock.now()))
    monkeypatch.setattr(service.ai, "check_solution", AsyncMock(return_value={"ok": True, "reason": ""}))
    monkeypatch.setattr(service.ai, "classify", AsyncMock(
        return_value={"role": "Куратор", "title": "Кеш жауап беру", "employee_name": "Есболқызы Арай"}))
    return NS(clock=clock, bot=FakeBot())


_mid = [0]
_users = {}


def U(uid, name, username=None):
    u = NS(id=uid, full_name=name, username=username, is_bot=False)
    db.remember_user(u)
    return u


TECH = lambda: U(1, "Бекарыс Алишер | қолдау")  # noqa: E731  (тех.менеджерлер тізімінде бар)
COORD = lambda: U(2, "Бақыт Аружан | ГЕО", "aruzhanbakyt")  # noqa: E731
EXTSELL = lambda: U(3, "Айтикова Аружан | сату")  # noqa: E731


def M(user, where, text=None, caption=None, reply=None, group=None, ctype="text"):
    _mid[0] += 1
    chat, thread = where
    rt = NS(message_id=reply, forum_topic_created=None) if reply else None
    return NS(text=text, caption=caption, content_type=ctype, media_group_id=group, chat=NS(id=chat),
              message_id=_mid[0], message_thread_id=thread, is_topic_message=True, from_user=user,
              reply_to_message=rt)


def complaint_text(student="Айдана Нұрлан", subject="SMART ГЕО-31", title="Кеш жауап беру", desc="Куратор жауап бермейді"):
    return (f"Оқушы аты-жөні: {student}\nТелефон нөмірі: 87001112233\nПочта: a@b.kz\nПәні және потогы: {subject}\n"
            f"Қызметкердің аты-жөні: Есболқызы Арай\nҚызметі/Мәселе иесі: Куратор\nМәселе атауы/типі: {title}\n"
            f"Мәселенің толық сипаттамасы: {desc}")


def resolution_text(student="Айдана Нұрлан", just="Негізделген", sat="Иә", stay="Иә"):
    return (f"Оқушы аты-жөні: {student}\nМәселенің шешімі: Оқушымен байланысқа шықтық, себебі анықталды, "
            f"кураторға ескерту жасалды.\nМәселе шындыққа негізделген бе? {just}\nОқушы риза ма? {sat}\n"
            f"Оқушы курста қалды ма? {stay}")


def C():
    return db.q("SELECT * FROM complaints ORDER BY id")


def post(e, user, where, text, **kw):
    msg = M(user, where, text=text, **kw)
    run(bm.handle(msg, e.bot, False))
    return msg


# ===================================================================== жұмыс терезесі
@pytest.mark.parametrize("args,expected", [
    ((2026, 10, 14, 10, 59), False), ((2026, 10, 14, 11, 0), True), ((2026, 10, 14, 20, 59), True),
    ((2026, 10, 14, 21, 0), False), ((2026, 10, 17, 15, 0), True),   # сенбі - жұмыс күні
    ((2026, 10, 18, 15, 0), False),                                  # жексенбі - демалыс
])
def test_work_window(args, expected):
    y, m, d, h, mi = args
    assert REAL_WINDOW(datetime(y, m, d, h, mi, tzinfo=config.TZ)) is expected


# ===================================================================== негізгі ағын
def test_direct_flow_end_to_end(env):
    tech = TECH()
    m = post(env, tech, SMART, complaint_text())
    c = C()
    assert len(c) == 1 and c[0]["status"] == "open" and c[0]["program"] == "smart"
    assert c[0]["author_role"] == "tech"                       # аты тізімнен танылды
    notify = env.bot.sent[0]
    assert notify.reply_to == m.message_id and (notify.chat, notify.thread) == SMART
    for tag in ("@zhhaniya", "@aruzhanbakyt", "@SdlyAmina", "@Meyrambekovich"):
        assert tag in notify.text
    assert env.bot.reactions                                   # 👀

    # бір сағаттан кейін еске салу
    env.clock.add(minutes=59); run(service.flush(env.bot)); assert len(env.bot.sent) == 1
    env.clock.add(minutes=2); run(service.flush(env.bot)); assert len(env.bot.sent) == 2
    assert "Еске салу" in env.bot.sent[1].text and "@zhhaniya" in env.bot.sent[1].text

    # координатор reply ішінде шешім береді
    post(env, COORD(), SMART, resolution_text(), reply=m.message_id)
    c = C()[0]
    assert c["status"] == "resolved"
    assert json.loads(c["final"])["satisfied"] == "Иә"
    assert "шешілді" in env.bot.sent[-1].text
    n = len(env.bot.sent)
    env.clock.add(hours=3); run(service.flush(env.bot)); assert len(env.bot.sent) == n   # еске салу тоқтады

    xl = __import__("openpyxl").load_workbook(io.BytesIO(exporter.build()))["ТЕХ МЕНЕДЖЕР"]
    row = [x.value for x in xl[2]]
    assert row[1] == "Айдана Нұрлан" and row[4] == "Куратор" and row[5] == "Кеш жауап беру"
    assert row[8] == "Негізделген" and row[10] == "Бекарыс Алишер" and row[11] == "Иә" and row[12] is True


def test_reply_to_bot_message_also_resolves(env):
    post(env, TECH(), SMART, complaint_text())
    notify = env.bot.sent[0]
    post(env, COORD(), SMART, resolution_text(), reply=notify.id)
    assert C()[0]["status"] == "resolved"


# ===================================================================== жұмыс уақыты / SLA
def test_night_complaint_is_silent_then_sla_starts_when_team_is_notified(env):
    env.clock.set(2026, 10, 17, 22, 30)                       # сенбі түн
    post(env, TECH(), SMART, complaint_text())
    assert env.bot.sent == [] and C()[0]["status"] == "new"
    env.clock.set(2026, 10, 18, 14, 0); run(service.flush(env.bot))   # жексенбі
    assert env.bot.sent == []
    env.clock.set(2026, 10, 19, 11, 0); run(service.flush(env.bot))   # дүйсенбі 11:00
    assert len(env.bot.sent) == 1
    assert "20.10 11:00" in env.bot.sent[0].text, env.bot.sent[0].text   # 24 сағат хабарланған сәттен
    assert "Дедлайн өтті" not in env.bot.sent[0].text


def test_reminders_hourly_only_in_window_and_overdue_marker(env):
    post(env, TECH(), SMART, complaint_text())                # сәрсенбі 12:00
    for _ in range(9):                                        # 13:00 ... 21:00 дейін
        env.clock.add(hours=1)
        run(service.flush(env.bot))
    # 13..20 = 8 еске салу, 21:00-де терезе жабық
    assert len([t for t in env.bot.texts() if "Еске салу" in t]) == 8
    before = len(env.bot.sent)
    env.clock.set(2026, 10, 15, 3, 0); run(service.flush(env.bot)); assert len(env.bot.sent) == before
    env.clock.set(2026, 10, 15, 12, 30); run(service.flush(env.bot))
    assert "Дедлайн өтті" in env.bot.sent[-1].text


def test_sunday_nothing_is_sent_and_monday_gets_one_reminder_not_flood(env):
    post(env, TECH(), SMART, complaint_text())
    env.clock.set(2026, 10, 18, 12, 0)
    for _ in range(5):
        env.clock.add(hours=1); run(service.flush(env.bot))
    n = len(env.bot.sent)
    assert n == 1
    env.clock.set(2026, 10, 19, 11, 0); run(service.flush(env.bot)); run(service.flush(env.bot))
    assert len(env.bot.sent) == n + 1


# ===================================================================== шаблон тексеру
def test_invalid_complaint_gets_error_then_edit_registers_it(env):
    bad = complaint_text(subject="SMART ГЕО")                 # ағын жоқ
    msg = post(env, TECH(), SMART, bad)
    assert C() == [] and "ағын жоқ" in env.bot.sent[0].text
    msg.text = complaint_text()
    run(bm.handle(msg, env.bot, True))
    assert len(C()) == 1 and C()[0]["status"] == "open"


def test_stale_error_notice_cancelled_when_fixed_before_it_was_sent(env):
    env.clock.set(2026, 10, 18, 12, 0)                        # жексенбі: хабарлама кезекте
    msg = post(env, TECH(), SMART, complaint_text(subject="SMART ГЕО"))
    msg.text = complaint_text()
    run(bm.handle(msg, env.bot, True))
    env.clock.set(2026, 10, 19, 11, 0); run(service.flush(env.bot))
    assert not any("Түзетіңіз" in t for t in env.bot.texts())
    assert len(C()) == 1


def test_screenshot_without_text_prompts_template(env):
    post(env, TECH(), SMART, None, ctype="photo")
    assert "Мәтінсіз" in env.bot.sent[0].text and C() == []


def test_chat_noise_is_ignored(env):
    post(env, TECH(), SMART, "рахмет")
    post(env, TECH(), SMART, "Қайырлы күн, бүгін сабақ қай уақытта басталады екен деп сұрап жатыр, кім біледі?" * 1)
    assert C() == []
    assert len(env.bot.sent) == 1 and "Шаблонсыз" in env.bot.sent[0].text   # тек ұзынына ескерту


def test_roster_member_message_is_not_a_complaint(env):
    post(env, COORD(), SMART, complaint_text())
    assert C() == []


def test_program_mismatch_in_topic_is_rejected(env):
    post(env, TECH(), JUNIOR, complaint_text(subject="SMART ГЕО-31"))
    assert C() == [] and "SMART" in env.bot.sent[0].text and "JUNIOR" in env.bot.sent[0].text


def test_duplicate_open_complaint_not_created_twice(env):
    t = TECH()
    post(env, t, SMART, complaint_text())
    post(env, t, SMART, complaint_text())
    assert len(C()) == 1


# ===================================================================== кім шеше алады
def test_author_cannot_close_own_complaint(env):
    tech = TECH()
    m = post(env, tech, SMART, complaint_text())
    post(env, tech, SMART, resolution_text(), reply=m.message_id)
    assert C()[0]["status"] == "open"


def test_other_tech_manager_cannot_close(env):
    m = post(env, TECH(), SMART, complaint_text())
    other = U(11, "Куанов Али | қолдау")
    post(env, other, SMART, resolution_text(), reply=m.message_id)
    assert C()[0]["status"] == "open"


def test_resolution_validation_errors_keep_complaint_open_and_edit_fixes(env):
    m = post(env, TECH(), SMART, complaint_text())
    r = post(env, COORD(), SMART, resolution_text(sat="мүмкін"), reply=m.message_id)
    assert C()[0]["status"] == "open" and "Риза ма" in env.bot.sent[-1].text
    r.text = resolution_text()
    run(bm.handle(r, env.bot, True))
    assert C()[0]["status"] == "resolved"


def test_vague_solution_rejected_by_ai_check(env, monkeypatch):
    monkeypatch.setattr(service.ai, "check_solution",
                        AsyncMock(return_value={"ok": False, "reason": "нақты емес"}))
    m = post(env, TECH(), SMART, complaint_text())
    post(env, COORD(), SMART, resolution_text(), reply=m.message_id)
    assert C()[0]["status"] == "open" and "нақты емес" in env.bot.sent[-1].text


# ===================================================================== reply-сыз шешім
def test_loose_resolution_single_match_closes(env):
    post(env, TECH(), SMART, complaint_text())
    post(env, COORD(), SMART, resolution_text())
    assert C()[0]["status"] == "resolved"


def test_loose_resolution_multiple_matches_asks_and_pick_closes(env):
    post(env, TECH(), SMART, complaint_text(student="Айдана Нұрлан"))
    post(env, TECH(), SMART, complaint_text(student="Айдана Сәрсен", subject="SMART БИО-11", desc="басқа мәселе"))
    post(env, COORD(), SMART, resolution_text(student="Айдана Нұрлан").replace("Айдана Нұрлан", "Айдана"))
    q = env.bot.sent[-1]
    assert "Қай шағымға" in q.text
    cb = q.kb.inline_keyboard[0][0].callback_data
    pid, cid = cb.split(":")[1:]
    msg = run(service.finish_pending(env.bot, int(pid), int(cid)))
    assert "шешілді" in msg and [c["status"] for c in C()].count("resolved") == 1


def test_loose_resolution_unknown_student(env):
    post(env, TECH(), SMART, complaint_text())
    post(env, COORD(), SMART, resolution_text(student="Бейтаныс Адам"))
    assert C()[0]["status"] == "open" and "жоқ" in env.bot.sent[-1].text


# ===================================================================== сыртқы сату
def test_external_flow_forward_and_echo(env):
    ext = EXTSELL()
    src = post(env, ext, EXT, complaint_text())
    c = C()[0]
    assert c["source"] == "external" and c["status"] == "open" and c["program"] == "smart"
    assert env.bot.copied == [(SMART[0], [src.message_id])]
    assert (env.bot.sent[0].chat, env.bot.sent[0].thread) == SMART
    copied_id = c["target_msg"]
    post(env, COORD(), SMART, resolution_text(), reply=copied_id)
    assert C()[0]["status"] == "resolved"
    dests = [(m.chat, m.thread) for m in env.bot.sent if "шешілді" in m.text]
    assert dests == [SMART, EXT]                               # екі жаққа да жауап
    assert env.bot.sent[-1].reply_to == src.message_id
    xl = __import__("openpyxl").load_workbook(io.BytesIO(exporter.build()))
    assert "СЫРТҚЫ САТУ" in xl.sheetnames and xl["СЫРТҚЫ САТУ"][2][11].value == "Айтикова Аружан"


def test_external_without_program_asks_buttons_and_pick_routes(env):
    ext = EXTSELL()
    post(env, ext, EXT, complaint_text(subject="ГЕО-31"))
    c = C()[0]
    assert c["status"] == "awaiting_program"
    assert "SMART па" in env.bot.sent[0].text
    tgt = db.binding("smart_hate")
    parsed = json.loads(c["parsed"])
    coords, sups, notes = service.compute_responsible("smart", parsed)
    db.update("complaints", c["id"], program="smart", status="new", last_reminder_at=None, target_chat=tgt["chat_id"],
              target_thread=tgt["thread_id"], responsible=service.resp_json(coords, sups), notes="")
    run(service.flush(env.bot))
    assert C()[0]["status"] == "open" and env.bot.copied


def test_junior_routing(env):
    post(env, TECH(), JUNIOR, complaint_text(subject="JUNIOR МАТ"))
    t = env.bot.sent[0].text
    assert "@arystanbekova7" in t and "@maqzhan1" in t and "@bayashkarr" not in t
    post(env, TECH(), JUNIOR, complaint_text(student="Басқа Оқушы", subject="JUNIOR БИО", desc="x"))
    t = env.bot.sent[1].text
    assert "@bayashkarr" in t and "@nurlnvash" in t and "@maqzhan1" in t


# ===================================================================== сенімділік
def test_concurrent_flush_does_not_duplicate_messages(env):
    tech = TECH()
    msg = M(tech, SMART, text=complaint_text())
    db.x("INSERT INTO complaints(status,source,src_chat,src_thread,src_msg,author_id,author_name,author_role,program,"
         "target_chat,target_thread,target_msg,text,parsed,responsible,created_at) VALUES('new','direct',?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
         SMART[0], SMART[1], msg.message_id, 1, "x", "tech", "smart", SMART[0], SMART[1], msg.message_id, "t",
         json.dumps(T.validate_complaint(complaint_text())[0]), json.dumps({}), env.clock.now().isoformat())

    async def both():
        await asyncio.gather(service.flush(env.bot), service.flush(env.bot), service.flush(env.bot))
    run(both())
    assert len([t for t in env.bot.texts() if "🔔" in t]) == 1


def test_echo_failure_does_not_repeat_target_notice(env):
    post(env, EXTSELL(), EXT, complaint_text())
    c = C()[0]
    env.bot.fail_chat = EXT[0]                                 # сыртқы топқа жазу сәтсіз
    post(env, COORD(), SMART, resolution_text(), reply=c["target_msg"])
    for _ in range(3):
        env.clock.add(seconds=20); run(service.flush(env.bot))
    assert len([m for m in env.bot.sent if m.chat == SMART[0] and "шешілді" in m.text]) == 1


def test_dispatch_failure_after_copy_does_not_copy_again(env):
    env.bot.fail_chat = SMART[0]                               # notify жіберу сәтсіз, copy сәтті
    post(env, EXTSELL(), EXT, complaint_text())
    env.bot.fail_chat = None
    for _ in range(3):
        env.clock.add(minutes=6); run(service.flush(env.bot))
    assert len(env.bot.copied) == 1


def test_excel_formula_injection_is_neutralised(env):
    m = post(env, TECH(), SMART, complaint_text(desc="=HYPERLINK(\"http://evil\",\"x\")"))
    post(env, COORD(), SMART, resolution_text(), reply=m.message_id)
    ws = __import__("openpyxl").load_workbook(io.BytesIO(exporter.build()))["ТЕХ МЕНЕДЖЕР"]
    cell = ws[2][6]
    assert cell.data_type != "f"


# ===================================================================== пән талдау
@pytest.mark.parametrize("label,program,pairs", [
    ("SMART ГЕО-31", "smart", [("ГЕО", "31")]),
    ("SMART ФИЗ, МАТ-01", "smart", [("ФИЗ", "01"), ("МАТ", "01")]),
    ("SMART ДЖТ-11, АНГЛ-11, МС-71, Т-71", "smart", [("ДЖТ", "11"), ("АНГЛ", "11"), ("МС", "71"), ("ТАРИХ", "71")]),
    ("JUNIOR БИО", "junior", [("БИО", None)]),
    ("SMART Орыс тілі-21", "smart", [("РУСЛИТ", "21")]),
    ("SMART Ағылшын тілі-01", "smart", [("АНГЛ", "01")]),
    ("SMART Қазақстан тарихы-31", "smart", [("ТАРИХ", "31")]),
])
def test_subject_parsing(label, program, pairs):
    p, got = T.parse_subject(label)
    assert p == program and got == pairs


# ===================================================================== чат таза болуы (қысқа хабарлар)
def test_bot_messages_are_short(env):
    post(env, TECH(), SMART, "Қайырлы күн, бүгін сабақ қай уақытта басталады екен деп сұрап жатыр, кім біледі?")
    post(env, TECH(), SMART, None, ctype="photo")
    post(env, TECH(), SMART, complaint_text(subject="SMART ГЕО"))        # қате
    post(env, TECH(), SMART, complaint_text(student="Басқа Бала", desc="x"))   # дұрыс
    env.clock.add(hours=1); run(service.flush(env.bot))                  # еске салу
    for m in env.bot.sent:
        assert len(m.text) < 260, (len(m.text), m.text)


def test_error_notice_is_deleted_when_fixed(env):
    msg = post(env, TECH(), SMART, complaint_text(subject="SMART ГЕО"))
    err = env.bot.sent[0]
    msg.text = complaint_text()
    run(bm.handle(msg, env.bot, True))
    assert (err.chat, err.id) in env.bot.deleted


def test_notice_auto_deletes_after_ttl(env):
    post(env, TECH(), SMART, None, ctype="photo")
    n = env.bot.sent[0]
    env.clock.add(minutes=10); run(service.flush(env.bot)); assert (n.chat, n.id) not in env.bot.deleted
    env.clock.add(minutes=6); run(service.flush(env.bot)); assert (n.chat, n.id) in env.bot.deleted


def test_reminders_are_digest_when_many_open(env):
    for i in range(3):
        post(env, TECH(), SMART, complaint_text(student=f"Оқушы Нөмір{i}", desc=f"мәселе {i}"))
    sent_before = len(env.bot.sent)
    env.clock.add(hours=1); run(service.flush(env.bot))
    new = env.bot.sent[sent_before:]
    assert len(new) == 1, [m.text for m in new]                         # 3 емес, 1 хабарлама
    assert "Шешілмеген шағымдар (3)" in new[0].text and new[0].text.count("t.me/c/") == 3


def test_resolved_notice_short_in_topic_full_echo_for_external(env):
    ext = post(env, EXTSELL(), EXT, complaint_text())
    post(env, COORD(), SMART, resolution_text(), reply=C()[0]["target_msg"])
    topic_msg = [m for m in env.bot.sent if m.chat == SMART[0] and "шешілді" in m.text][0]
    echo_msg = [m for m in env.bot.sent if m.chat == EXT[0] and "шешілді" in m.text][0]
    assert len(topic_msg.text) < 80 and "Оқушымен байланысқа" not in topic_msg.text
    assert "Оқушымен байланысқа" in echo_msg.text


# ===================================================================== автоматты іріктеу (OpenAI)
def minimal_text(student="Айдана Нұрлан", subject="SMART ГЕО-31", desc="Куратор Арай 3 күн жауап бермейді"):
    return (f"Оқушы аты-жөні: {student}\nПәні және потогы: {subject}\n"
            f"Мәселенің толық сипаттамасы: {desc}")


def test_template_has_no_prefill_and_no_option_lists():
    assert "SMART ГЕО-31" not in T.COMPLAINT_TEMPLATE and "SMART ГЕО-31" not in T.COMPLAINT_HELP
    assert "Қызметі" not in T.COMPLAINT_HELP and "Мәселе типі" not in T.COMPLAINT_HELP
    assert "blockquote" not in T.COMPLAINT_HELP


def test_minimal_complaint_gets_role_title_employee_from_ai(env, monkeypatch):
    monkeypatch.setattr(service.ai, "classify", AsyncMock(
        return_value={"role": "Мұғалім", "title": "Мұғалім мәселесі", "employee_name": "Асан Бекұлы"}))
    post(env, TECH(), SMART, minimal_text())
    p = json.loads(C()[0]["parsed"])
    assert (p["employee_role"], p["issue_title"], p["employee_name"]) == ("Мұғалім", "Мұғалім мәселесі", "Асан Бекұлы")
    assert "Мұғалім мәселесі" in env.bot.sent[0].text


def test_manual_values_are_kept_and_ai_not_called(env, monkeypatch):
    spy = AsyncMock(return_value={"role": "Курс", "title": "Курс системасы", "employee_name": "X"})
    monkeypatch.setattr(service.ai, "classify", spy)
    post(env, TECH(), SMART, complaint_text())          # қызметі/типі/қызметкер қолмен жазылған
    p = json.loads(C()[0]["parsed"])
    assert (p["employee_role"], p["issue_title"], p["employee_name"]) == ("Куратор", "Кеш жауап беру", "Есболқызы Арай")
    spy.assert_not_awaited()


def test_invalid_manual_role_is_not_an_error_ai_decides(env):
    text = complaint_text().replace("Қызметі/Мәселе иесі: Куратор", "Қызметі/Мәселе иесі: ойдан шығарылған")
    post(env, TECH(), SMART, text)
    assert len(C()) == 1 and json.loads(C()[0]["parsed"])["employee_role"] == "Куратор"   # AI (mock) таңдады


def test_classify_validates_ai_output_and_falls_back_to_dash(monkeypatch):
    from bot import ai

    def resp(content):
        return NS(choices=[NS(message=NS(content=content))])
    good = AsyncMock(return_value=resp(json.dumps({"role": "куратор", "title": "кеш жауап беру", "employee_name": "Арай"})))
    monkeypatch.setattr(ai.client.chat.completions, "create", good)
    assert run(REAL_CLASSIFY("текст")) == {"role": "Куратор", "title": "Кеш жауап беру", "employee_name": "Арай"}
    junk = AsyncMock(return_value=resp(json.dumps({"role": "жоқ тізімде", "title": "бірдеңе", "employee_name": "null"})))
    monkeypatch.setattr(ai.client.chat.completions, "create", junk)
    assert run(REAL_CLASSIFY("текст")) == {"role": "-", "title": "-", "employee_name": None}
    monkeypatch.setattr(ai.client.chat.completions, "create", AsyncMock(side_effect=RuntimeError("down")))
    assert run(REAL_CLASSIFY("текст"))["role"] == "-"


def test_ai_failure_does_not_block_complaint(env, monkeypatch):
    from bot import ai
    monkeypatch.setattr(service.ai, "classify", REAL_CLASSIFY)
    monkeypatch.setattr(ai.client.chat.completions, "create", AsyncMock(side_effect=RuntimeError("down")))
    post(env, TECH(), SMART, minimal_text())
    p = json.loads(C()[0]["parsed"])
    assert C()[0]["status"] == "open" and p["employee_role"] == "-" and p["issue_title"] == "-"


# ===================================================================== /pinall
class PinBot(FakeBot):
    def __init__(self, admin=True, can_pin=True):
        super().__init__()
        self.admin, self.can_pin, self.pinned = admin, can_pin, []

    async def get_chat_member(self, chat_id, user_id):
        return NS(status="administrator" if self.admin else "member")

    async def pin_chat_message(self, chat_id, msg_id, **kw):
        if not self.can_pin:
            raise RuntimeError("no rights")
        self.pinned.append(msg_id)


def _cmd_msg(bot, where):
    sent = []

    async def answer(text, **kw):
        bot._id += 1
        sent.append(text)
        return NS(message_id=bot._id, chat=NS(id=where[0]))
    return NS(chat=NS(id=where[0]), message_thread_id=where[1], is_topic_message=True,
              from_user=NS(id=99), answer=answer), sent


def test_pinall_pins_one_message_with_both_templates_in_smart_topic():
    b = PinBot()
    msg, sent = _cmd_msg(b, SMART)
    run(bm.cmd_pinall(msg, b))
    assert len(b.pinned) == 1 and len(sent) == 1                       # БІР pin хабарлама
    assert "Жаңа шағым" in sent[0] and "Шешім" in sent[0]               # екі шаблон бірге
    assert sent[0].count("<code>") == 2


def test_pinall_in_external_topic_pins_only_complaint_template():
    b = PinBot()
    msg, sent = _cmd_msg(b, EXT)
    run(bm.cmd_pinall(msg, b))
    assert len(b.pinned) == 1 and "Шағым шаблоны" in sent[0] and "Шешім" not in sent[0]


def test_pinall_ignored_for_non_admin_and_reports_missing_pin_right():
    b = PinBot(admin=False)
    msg, sent = _cmd_msg(b, SMART)
    run(bm.cmd_pinall(msg, b))
    assert sent == [] and b.pinned == []
    b = PinBot(can_pin=False)
    msg, sent = _cmd_msg(b, SMART)
    run(bm.cmd_pinall(msg, b))
    assert "Pin messages" in sent[-1]


# ===================================================================== шын чаттан шыққан ақаулар
def test_reminder_has_student_snippet_deadline_and_people(env):
    post(env, TECH(), SMART, complaint_text(student="Жансая Абил", desc="Куратор жауап бермейді, ата-ана ашулы"))
    env.clock.add(hours=1); run(service.flush(env.bot))
    t = env.bot.sent[-1].text
    for part in ("Еске салу", "#", "1 сағ", "Жансая Абил", "Куратор жауап бермейді", "@zhhaniya", "Кеш жауап беру",
                 "дедлайн", "Жауап күтілуде"):
        assert part in t, (part, t)


def test_digest_lines_include_student_names(env):
    for i, name in enumerate(("Айдос Нұрлан", "Мерей Асан")):
        post(env, TECH(), SMART, complaint_text(student=name, desc=f"мәселе {i}"))
    env.clock.add(hours=1); run(service.flush(env.bot))
    t = env.bot.sent[-1].text
    assert "Айдос Нұрлан" in t and "Мерей Асан" in t


class CbMsg:
    def __init__(self, bot, where, mid):
        self.chat, self.message_id, self._bot, self.where = NS(id=where[0]), mid, bot, where
        self.answered = []

    async def answer(self, text, **kw):
        self._bot._id += 1
        self.answered.append(self._bot._id)
        return NS(message_id=self._bot._id, chat=self.chat)


def test_reply_to_template_button_message_resolves_the_complaint(env):
    """Координатор «Шешім шаблоны» батырмасын басып, шыққан шаблонға reply жасайды (шын чаттағы ақау)."""
    m = post(env, TECH(), SMART, complaint_text())
    notify = env.bot.sent[0]
    cb_msg = CbMsg(env.bot, SMART, notify.id)
    cb = NS(message=cb_msg, answer=AsyncMock())
    run(bm.cb_template(cb))
    template_msg_id = cb_msg.answered[0]
    post(env, COORD(), SMART, resolution_text(), reply=template_msg_id)
    assert C()[0]["status"] == "resolved"


def test_reply_to_unknown_message_falls_back_to_name_matching(env):
    post(env, TECH(), SMART, complaint_text())
    post(env, COORD(), SMART, resolution_text(), reply=999999)     # шаблоны өшіп қалған/белгісіз хабарлама
    assert C()[0]["status"] == "resolved"


def test_progress_reply_marks_complaint_and_reminder_shows_it(env):
    post(env, TECH(), SMART, complaint_text())
    notify = env.bot.sent[0]
    n_react = len(env.bot.reactions)
    post(env, COORD(), SMART, "процессте, шешіліп жатыр", reply=notify.id)
    c = C()[0]
    assert c["status"] == "open" and c["progress_at"] and "Аружан" in c["progress_by"]
    assert len(env.bot.reactions) == n_react + 1 and env.bot.reactions[-1][2][0].emoji == "✍"
    env.clock.add(hours=1); run(service.flush(env.bot))
    assert "Процессте" in env.bot.sent[-1].text and "Аружан" in env.bot.sent[-1].text


def test_progress_variants_and_noise():
    for yes in ("процессте", "Шешіліп жатыр", "қарап жатырмыз", "в работе", "жұмыс істеп жатырмыз, бүгін шешеміз"):
        assert service.is_progress_note(yes), yes
    for no in ("рахмет", "ок", "Қайырлы күн", "түсінікті"):
        assert not service.is_progress_note(no), no


def test_progress_from_author_or_staff_is_ignored(env):
    tech = TECH()
    post(env, tech, SMART, complaint_text())
    notify = env.bot.sent[0]
    post(env, tech, SMART, "процессте", reply=notify.id)
    assert C()[0]["progress_at"] is None


def test_progress_then_template_still_resolves(env):
    post(env, TECH(), SMART, complaint_text())
    notify = env.bot.sent[0]
    post(env, COORD(), SMART, "процессте", reply=notify.id)
    post(env, COORD(), SMART, resolution_text(), reply=notify.id)
    assert C()[0]["status"] == "resolved"
