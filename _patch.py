def sub(s, old, new):
    assert old in s, old[:70]
    return s.replace(old, new, 1)


p = 'bot/main.py'
s = open(p, encoding='utf-8').read()
s = sub(s, '''@dp.message(Command("bind"))''', '''@dp.message(Command("pinall"))
async def cmd_pinall(msg: Message, bot: Bot):
    """Админ: шаблондарды бірден жіберіп pin жасайды (шағым шаблоны жоғарыда көрінеді)."""
    if not await is_admin(bot, msg.chat.id, msg.from_user.id):
        return
    role = db.role_of(msg.chat.id, thread_of(msg))
    texts = [T.COMPLAINT_HELP] if role == "external_hate" else [T.RESOLUTION_HELP, T.COMPLAINT_HELP]
    for text in texts:
        m = await msg.answer(text, parse_mode="HTML")
        try:
            await bot.pin_chat_message(msg.chat.id, m.message_id, disable_notification=True)
        except Exception:
            err = await msg.answer("Pin үшін ботқа 'Pin messages' құқығы керек")
            service.track_ephemeral(err.chat.id, err.message_id, 5)
            return


@dp.message(Command("bind"))''')
s = sub(s, '''        "/shesim — шешім шаблоны\\n"''', '''        "/shesim — шешім шаблоны\\n"
        "/pinall — екі шаблонды бірден pin жасайды (админ)\\n"''')
open(p, 'w', encoding='utf-8').write(s)

p = 'tests/test_scenarios.py'
s = open(p, encoding='utf-8').read()
s += '''

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


def test_pinall_pins_resolution_then_complaint_template_in_smart_topic():
    b = PinBot()
    msg, sent = _cmd_msg(b, SMART)
    run(bm.cmd_pinall(msg, b))
    assert len(b.pinned) == 2 and "Шешім шаблоны" in sent[0] and "Шағым шаблоны" in sent[1]   # шағым шаблоны соңғы = жоғарыда


def test_pinall_in_external_topic_pins_only_complaint_template():
    b = PinBot()
    msg, sent = _cmd_msg(b, EXT)
    run(bm.cmd_pinall(msg, b))
    assert len(b.pinned) == 1 and "Шағым шаблоны" in sent[0]


def test_pinall_ignored_for_non_admin_and_reports_missing_pin_right():
    b = PinBot(admin=False)
    msg, sent = _cmd_msg(b, SMART)
    run(bm.cmd_pinall(msg, b))
    assert sent == [] and b.pinned == []
    b = PinBot(can_pin=False)
    msg, sent = _cmd_msg(b, SMART)
    run(bm.cmd_pinall(msg, b))
    assert "Pin messages" in sent[-1]
'''
open(p, 'w', encoding='utf-8').write(s)
print("ok")
