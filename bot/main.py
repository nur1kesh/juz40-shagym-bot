import asyncio
import json
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from . import ai, db, exporter, people, service, templates as T
from .config import BOT_TOKEN, now

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")
dp = Dispatcher()

BIND_ROLES = {"smart": "smart_hate", "junior": "junior_hate", "external": "external_hate"}


def thread_of(msg: Message) -> int:
    return msg.message_thread_id if msg.is_topic_message else 0


async def is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        return m.status in ("creator", "administrator")
    except Exception:
        return False


async def is_admin_anywhere(bot: Bot, user_id: int) -> bool:
    """Байланған топтардың кез келгенінде админ болса - True."""
    chats = {r["chat_id"] for r in db.q("SELECT chat_id FROM bindings")}
    return any([await is_admin(bot, cid, user_id) for cid in chats])


# ------------------------------------------------------------------ командалар
@dp.message(Command("start", "help"))
async def cmd_help(msg: Message):
    await msg.answer(
        "Шағым ботының командалары:\n"
        "/bind smart|junior|external — осы topic-ті байлау (админ)\n"
        "/open — ашық шағымдар\n"
        "/close ID — шағымды қолмен жабу (админ)\n"
        "/export [YYYY-MM] [all] — Excel (тек ботқа ЖЕКЕ чатта, админге)\n"
        "/setrole tech|internal — қызметкердің хабарламасына reply етіп рөлін көрсету (админ)\n"
        "/bindings — қазіргі байлаулар")


async def send_template(msg: Message, bot: Bot, text: str, command: CommandObject):
    m = await msg.answer(text, parse_mode="HTML")
    if "pin" in (command.args or "") and await is_admin(bot, msg.chat.id, msg.from_user.id):
        try:
            await bot.pin_chat_message(msg.chat.id, m.message_id, disable_notification=True)
        except Exception:
            await msg.answer("Pin жасау үшін ботқа 'Pin messages' құқығы керек")


@dp.message(Command("shablon"))
async def cmd_shablon(msg: Message, command: CommandObject, bot: Bot):
    await send_template(msg, bot, T.COMPLAINT_HELP, command)


@dp.message(Command("shesim"))
async def cmd_shesim(msg: Message, command: CommandObject, bot: Bot):
    await send_template(msg, bot, T.RESOLUTION_HELP, command)


@dp.message(Command("bind"))
async def cmd_bind(msg: Message, command: CommandObject, bot: Bot):
    if not await is_admin(bot, msg.chat.id, msg.from_user.id):
        return
    role = BIND_ROLES.get((command.args or "").strip().lower())
    if not role:
        return await msg.reply("Қолданылуы: /bind smart | junior | external (сәйкес topic ішінде)")
    db.bind(role, msg.chat.id, thread_of(msg))
    await msg.reply(f"✅ {role} → chat {msg.chat.id}, topic {thread_of(msg)}")


@dp.message(Command("bindings"))
async def cmd_bindings(msg: Message):
    rows = db.q("SELECT * FROM bindings")
    await msg.reply("\n".join(f"{r['role']}: chat {r['chat_id']}, topic {r['thread_id']}" for r in rows)
                    or "Байлау жоқ")


@dp.message(Command("open"))
async def cmd_open(msg: Message):
    await msg.reply(service.fmt_open_list(), parse_mode="HTML")


@dp.message(Command("close"))
async def cmd_close(msg: Message, command: CommandObject, bot: Bot):
    if not await is_admin(bot, msg.chat.id, msg.from_user.id):
        return
    try:
        cid = int((command.args or "").strip().lstrip("#"))
    except ValueError:
        return await msg.reply("Қолданылуы: /close 12")
    ok = await service.close_complaint(bot, cid, msg.from_user.full_name)
    await msg.reply("Жабылды" if ok else "Табылмады немесе бұрын жабылған")


@dp.message(Command("export"))
async def cmd_export(msg: Message, command: CommandObject, bot: Bot):
    if msg.chat.type != "private":  # топта файл көрінбейді: тек жеке чатта
        if await is_admin(bot, msg.chat.id, msg.from_user.id):
            await msg.reply("🔒 Экспорт тек боттың жеке чатында жұмыс істейді. Ботқа жеке жазыңыз.")
        return
    if not await is_admin_anywhere(bot, msg.from_user.id):
        return await msg.answer("Экспорт тек байланған топтардың админдеріне қолжетімді.")
    args = (command.args or "").split()
    month = next((a for a in args if len(a) == 7 and a[4] == "-"), None)
    data = exporter.build(include_open="all" in args, month=month)
    await msg.reply_document(BufferedInputFile(data, f"shagymdar_{now():%Y%m%d_%H%M}.xlsx"))


@dp.message(Command("setrole"), F.reply_to_message)
async def cmd_setrole(msg: Message, command: CommandObject, bot: Bot):
    if not await is_admin(bot, msg.chat.id, msg.from_user.id):
        return
    role = (command.args or "").strip().lower()
    if role not in ("tech", "internal"):
        return await msg.reply("Қолданылуы: /setrole tech | internal")
    u = msg.reply_to_message.from_user
    db.remember_user(u)
    db.set_staff_role(u.id, role)
    db.x("UPDATE complaints SET author_role=? WHERE author_id=? AND author_role IS NULL", role, u.id)
    await msg.reply(f"{u.full_name} → {role}")


# ------------------------------------------------------------------ callback-тер
@dp.callback_query(F.data.startswith("prog:"))
async def cb_program(cb: CallbackQuery, bot: Bot):
    _, cid, program = cb.data.split(":")
    c = db.q1("SELECT * FROM complaints WHERE id=?", int(cid))
    if not c or c["status"] != "awaiting_program":
        return await cb.answer("Қазірдің өзінде өңделген")
    if cb.from_user.id != c["author_id"] and not await is_admin(bot, cb.message.chat.id, cb.from_user.id):
        return await cb.answer("Тек жазған адам немесе админ", show_alert=True)
    tgt = db.binding(f"{program}_hate")
    if not tgt:
        return await cb.answer(f"{program} topic байланбаған (/bind)", show_alert=True)
    parsed = json.loads(c["parsed"])
    coords, sups, notes = service.compute_responsible(program, parsed)
    db.update("complaints", c["id"], program=program, status="new", last_reminder_at=None,
              target_chat=tgt["chat_id"], target_thread=tgt["thread_id"],
              responsible=service.resp_json(coords, sups),
              notes="; ".join(notes))
    await cb.message.edit_text(f"Шағым #{cid} → {program.upper()}")
    await cb.answer()
    await service.flush(bot)


@dp.callback_query(F.data.startswith("pick:"))
async def cb_pick(cb: CallbackQuery, bot: Bot):
    _, pid, cid = cb.data.split(":")
    pend = db.q1("SELECT * FROM pending_res WHERE id=?", int(pid))
    if not pend or (cb.from_user.id != pend["user_id"]
                    and not await is_admin(bot, cb.message.chat.id, cb.from_user.id)):
        return await cb.answer("Тек шешімді жазған адам немесе админ", show_alert=True)
    result = await service.finish_pending(bot, int(pid), int(cid))
    await cb.message.edit_text(result)
    await cb.answer()


@dp.callback_query(F.data == "tpl:res")
async def cb_template(cb: CallbackQuery):
    await cb.message.answer(T.RESOLUTION_HELP, parse_mode="HTML")
    await cb.answer()


@dp.callback_query(F.data.startswith("role:"))
async def cb_role(cb: CallbackQuery, bot: Bot):
    _, cid, role = cb.data.split(":")
    c = db.q1("SELECT * FROM complaints WHERE id=?", int(cid))
    if not c or cb.from_user.id != c["author_id"]:
        return await cb.answer("Тек жазған адам таңдай алады", show_alert=True)
    db.set_staff_role(c["author_id"], role)
    db.x("UPDATE complaints SET author_role=? WHERE author_id=? AND author_role IS NULL", role, c["author_id"])
    await cb.message.edit_text(f"✅ {c['author_name']} → {'Тех.менеджер' if role == 'tech' else 'Ішкі сату'}")
    await cb.answer()


# ------------------------------------------------------------------ негізгі хабарламалар
async def handle(msg: Message, bot: Bot, edited: bool):
    db.remember_user(msg.from_user)
    if msg.text and msg.text.startswith("/"):
        return
    role = db.role_of(msg.chat.id, thread_of(msg))
    if not role:
        return  # бағыт topic-інен тыс ештеңе істемейміз
    service.track_media(msg, thread_of(msg))
    media_only = not (msg.text or msg.caption) and msg.content_type in ("photo", "video", "document", "animation")
    rt = msg.reply_to_message
    is_reply = bool(rt) and rt.message_id != msg.message_thread_id and not rt.forum_topic_created
    try:
        if is_reply:
            if role != "external_hate":
                await service.process_reply(bot, msg, edited)
        elif media_only:
            if not people.is_roster_user(msg.from_user):
                await service.process_media_only(bot, msg)
        elif role != "external_hate" and service.is_loose_resolution(msg.text or msg.caption or ""):
            await service.process_loose_resolution(bot, msg, edited)
        elif not people.is_roster_user(msg.from_user):
            await service.process_complaint(bot, msg, role, edited)
    except Exception:
        log.exception("handler error")


GROUP = (F.chat.type.in_({"group", "supergroup"}), F.from_user, ~F.from_user.is_bot)


@dp.message(*GROUP)
async def on_group_message(msg: Message, bot: Bot):
    await handle(msg, bot, edited=False)


@dp.edited_message(*GROUP)
async def on_group_edit(msg: Message, bot: Bot):
    await handle(msg, bot, edited=True)


async def ticker(bot: Bot):
    while True:
        try:
            await service.flush(bot)
        except Exception:
            log.exception("tick error")
        await asyncio.sleep(15)


async def startup_checks(bot: Bot):
    me = await bot.get_me()
    log.info("bot @%s started", me.username)
    if not me.can_read_all_group_messages:
        log.warning("Group Privacy қосулы: ботты топта АДМИН етіңіз (немесе BotFather-да Group Privacy-ді өшіріңіз)")
    ok = await ai.check_solution("Куратор жауап бермейді", "шешілді")
    if ok.get("ok", True):
        log.warning("OpenAI тексеруі күтілгендей жауап бермеді (қате немесе модель өте жұмсақ): %s", ok)


async def main():
    bot = Bot(BOT_TOKEN)
    await startup_checks(bot)
    asyncio.create_task(ticker(bot))
    await dp.start_polling(bot, allowed_updates=["message", "edited_message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
