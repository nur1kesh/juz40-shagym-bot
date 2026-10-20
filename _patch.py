def sub(s, old, new):
    assert old in s, "NOT FOUND: " + old[:90]
    return s.replace(old, new, 1)


# ================================================================ db
p = 'bot/db.py'
s = open(p, encoding='utf-8').read()
s = sub(s, "CREATE INDEX IF NOT EXISTS ix_c_status", """CREATE TABLE IF NOT EXISTS digest_msgs(
  chat_id INTEGER, msg_id INTEGER, complaint_id INTEGER, PRIMARY KEY(chat_id, msg_id, complaint_id));
CREATE INDEX IF NOT EXISTS ix_c_status""")
open(p, 'w', encoding='utf-8').write(s)

# ================================================================ service
p = 'bot/service.py'
s = open(p, encoding='utf-8').read()

# digest хабарламасын шағымдармен байлау
s = sub(s, '''        lines.append("🔄 - процессте")
        await send(bot, first["target_chat"], first["target_thread"], "\\n".join(lines), None)''', '''        lines.append("🔄 - процессте")
        m = await send(bot, first["target_chat"], first["target_thread"], "\\n".join(lines), None)
        for c in rows[:25]:  # осы тізімге reply жасалса, қай шағым екенін оқушы аты бойынша табамыз
            db.x("INSERT OR IGNORE INTO digest_msgs VALUES(?,?,?)", m.chat.id if hasattr(m, "chat") else first["target_chat"],
                 m.message_id, c["id"])''')

# reply: digest-ке жауап
s = sub(s, '''    """False - reply ешбір шағымға байланбады (шақырушы reply-сыз іздеуге көше алады)."""
    c = find_complaint(msg.chat.id, msg.reply_to_message.message_id)
    if not c:
        return False
    text = msg.text or msg.caption''', '''    """False - reply ешбір шағымға байланбады (шақырушы reply-сыз іздеуге көше алады)."""
    c = find_complaint(msg.chat.id, msg.reply_to_message.message_id)
    text = msg.text or msg.caption
    if not c:
        cands = digest_candidates(msg.chat.id, msg.reply_to_message.message_id)
        if not cands:
            return False
        c = pick_from_digest(text or "", cands)
        if not c:  # қай шағым екені белгісіз
            if text and T.parse(text, T.RESOLUTION_LABELS):
                return False  # шаблон болса, оқушы аты бойынша жалпы іздеу жұмыс істейді
            if text and can_resolve(cands[0], msg.from_user):
                thread = msg.message_thread_id if msg.is_topic_message else 0
                queue_notice(f"{msg.chat.id}:{msg.message_id}", msg.chat.id, thread, msg.message_id,
                             "❓ Қай шағым? Шағымның #нөмірін немесе оқушы атын жазыңыз, не шағым хабарламасына reply етіңіз.",
                             ttl=10)
                await flush(bot)
            return True''')

s = sub(s, '''async def mark_progress(bot: Bot, msg, c, text: str):''', '''def digest_candidates(chat_id: int, msg_id: int) -> list:
    rows = db.q("SELECT c.* FROM digest_msgs d JOIN complaints c ON c.id=d.complaint_id "
                "WHERE d.chat_id=? AND d.msg_id=? AND c.status IN ('open','new') ORDER BY c.id", chat_id, msg_id)
    return list(rows)


def pick_from_digest(text: str, cands: list):
    """Ортақ тізімге жауап: #нөмір, не оқушы аты бойынша ЖАЛҒЫЗ шағымды таңдаймыз; болмаса None."""
    m = re.search(r"#\\s*(\\d+)", text)
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


async def mark_progress(bot: Bot, msg, c, text: str):''')
open(p, 'w', encoding='utf-8').write(s)

# ================================================================ tests
p = 'tests/test_scenarios.py'
s = open(p, encoding='utf-8').read()
s += '''

# ===================================================================== ортақ еске салу тізіміне (digest) reply
def _two_open_with_digest(env):
    post(env, TECH(), SMART, complaint_text(student="Жансая Абил", desc="бірінші мәселе"))
    post(env, TECH(), SMART, complaint_text(student="Шұғыла Төлеген", desc="екінші мәселе"))
    env.clock.add(hours=1); run(service.flush(env.bot))
    digest = env.bot.sent[-1]
    assert "Шешілмеген шағымдар (2)" in digest.text
    return digest


def test_progress_reply_to_digest_is_attributed_by_student_name(env):
    digest = _two_open_with_digest(env)
    n_react = len(env.bot.reactions)
    text = ("Шұғыла Төлеген- кураторы Сагидуллиева Аида. Оқушы осы уақытқа дейін жақсы оқып келіпті.\\n"
            "Бүгін кешке кураторы жеке митта сөйлеседі, жауабын жазатын боламын")
    post(env, COORD(), SMART, text, reply=digest.id)
    by_student = {json.loads(c["parsed"])["student_name"]: c for c in C()}
    assert by_student["Шұғыла Төлеген"]["progress_at"] and by_student["Жансая Абил"]["progress_at"] is None
    assert len(env.bot.reactions) == n_react + 1 and env.bot.reactions[-1][2][0].emoji == "✍"


def test_digest_reply_with_complaint_number_or_single_candidate(env):
    digest = _two_open_with_digest(env)
    first_id = C()[0]["id"]
    post(env, COORD(), SMART, f"#{first_id} қабылданды, кб береміз", reply=digest.id)
    assert C()[0]["progress_at"] and C()[1]["progress_at"] is None
    # тізімде бір ғана шағым болса - нөмір/аты керек емес
    for c in C():
        db.update("complaints", c["id"], status="deleted")
    post(env, TECH(), SMART, complaint_text(student="Жалғыз Оқушы", desc="жалғыз"))
    env.clock.add(hours=2); run(service.flush(env.bot))
    single = env.bot.sent[-1]
    post(env, COORD(), SMART, "қабылданды, кб береміз", reply=single.id)
    assert C()[-1]["progress_at"]


def test_ambiguous_digest_reply_asks_which_complaint_and_marks_nothing(env):
    digest = _two_open_with_digest(env)
    post(env, COORD(), SMART, "қабылданды, кб береміз", reply=digest.id)
    assert all(c["progress_at"] is None for c in C())
    assert "Қай шағым" in env.bot.sent[-1].text


def test_digest_reply_with_resolution_template_closes_the_named_student(env):
    digest = _two_open_with_digest(env)
    post(env, COORD(), SMART, resolution_text(student="Шұғыла Төлеген"), reply=digest.id)
    by_student = {json.loads(c["parsed"])["student_name"]: c["status"] for c in C()}
    assert by_student == {"Жансая Абил": "open", "Шұғыла Төлеген": "resolved"}
'''
open(p, 'w', encoding='utf-8').write(s)
print("patched")
