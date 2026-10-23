"""«Инфо-21» дегенде жауапты координаторлар мен супервайзерді табу: хэштег, команда және түймелер үшін ортақ қозғалтқыш."""
import html
import re

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import people, templates as T
from .roster import JUNIOR_CALC, STREAM_NAMES, SUBJECTS, responsible_for

PROGRAM_WORDS = {"SMART": "smart", "СМАРТ": "smart", "JUNIOR": "junior", "ДЖУНИОР": "junior", "ДЖУНИЯР": "junior"}
TOKEN_RE = re.compile(r"[A-Za-zА-ЯЁа-яёӘІҢҒҮҰҚӨҺәіңғүұқөһ0-9]+")
MAX_CODES = 5
MONTH_BUTTONS = [("01", "шілде"), ("11", "тамыз"), ("21", "қыркүйек"), ("31", "қазан"), ("41", "қараша")]
SUBJECTS_JUNIOR = ["МАТ", "ФИЗ", "ХИМ", "ИНФО", "МС", "ГЕОМ", "БИО", "ГЕО", "ТАРИХ", "ДЖТ", "ҚҰҚЫҚ", "АНГЛ",
                   "ӘДЕБТІЛ", "РУСЛИТ"]


def _stream_of(tok: str) -> str | None:
    if re.fullmatch(r"\d{2}", tok):
        return tok
    return T.month_stream(tok.upper())


def _parse_one(tokens: list[str], i: int):
    """tokens[i:] басынан бір код оқиды: [smart|junior] ПӘН [ағын]. -> (код, келесі индекс) немесе None."""
    j = i
    program = None
    if tokens[j].upper() in PROGRAM_WORDS:
        program = PROGRAM_WORDS[tokens[j].upper()]
        j += 1
    if j >= len(tokens):
        return None
    tok = tokens[j].upper()
    stream = None
    m = re.fullmatch(r"(\D+?)(\d{2})", tok)  # «инфо21»
    if m and T._subject(m.group(1)):
        subj, stream, j = T._subject(m.group(1)), m.group(2), j + 1
    else:
        subj = T._subject(tok)
        if not subj:
            return None
        j += 1
        if j < len(tokens):
            st = _stream_of(tokens[j])
            if st:
                stream, j = st, j + 1
    return {"program": program, "subject": subj, "stream": stream}, j


def parse_codes(text: str, hashtag_only: bool) -> list[dict]:
    """hashtag_only=True: тек «#инфо-21» түріндегі белгілер; False: команда аргументі («инфо-21 мат-31»)."""
    chunks = []
    if hashtag_only:
        for part in re.split(r"#", text or "")[1:]:
            chunks.append(TOKEN_RE.findall(part.split("\n")[0][:40]))
    else:
        chunks.append(TOKEN_RE.findall(text or ""))
    codes, seen = [], set()
    for tokens in chunks:
        i = 0
        while i < len(tokens):
            r = _parse_one(tokens, i)
            if not r:
                if hashtag_only:
                    break
                i += 1
                continue
            code, i = r
            key = (code["program"], code["subject"], code["stream"])
            if key not in seen:
                seen.add(key)
                codes.append(code)
            if hashtag_only:
                break
    return codes[:MAX_CODES]


def label(code: dict) -> str:
    base = code["subject"] + (f"-{code['stream']}" if code["stream"] else "")
    return ("JUNIOR " if code["program"] == "junior" else "") + base


def needs_stream(code: dict) -> bool:
    return code["program"] != "junior" and not code["stream"]


def block(code: dict) -> str:
    """Бір код үшін: 👥 тек координаторлар (@-тегтермен), супервайзерсіз."""
    program = code["program"] or "smart"
    coords, _, _ = responsible_for(program, [code["subject"]], code["stream"])
    return "👥 <b>{}</b> · {}".format(
        html.escape(label(code)), ", ".join(people.mention(p) for p in coords) or "координатор тізімде жоқ")


def render(codes: list[dict]) -> tuple[str | None, list[dict]]:
    """-> (дайын кодтардың мәтіні, ағыны жоқ кодтар)."""
    ready = [c for c in codes if not needs_stream(c)]
    pending = [c for c in codes if needs_stream(c)]
    return ("\n\n".join(block(c) for c in ready) or None), pending


# ------------------------------------------------------------------ түймелер
def stream_kb(code: dict, orig: int) -> InlineKeyboardMarkup:
    p = "j" if code["program"] == "junior" else "s"
    row = [InlineKeyboardButton(text=f"{name} {st}", callback_data=f"kq:{p}:{code['subject']}:{st}:{orig}")
           for st, name in MONTH_BUTTONS]
    return InlineKeyboardMarkup(inline_keyboard=[row[:3], row[3:]])


def program_kb(orig: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="SMART", callback_data=f"km:p:s:{orig}"),
        InlineKeyboardButton(text="JUNIOR", callback_data=f"km:p:j:{orig}")]])


def subject_kb(p: str, orig: int) -> InlineKeyboardMarkup:
    subjects = SUBJECTS_JUNIOR if p == "j" else SUBJECTS
    rows, row = [], []
    for s in subjects:
        row.append(InlineKeyboardButton(text=s, callback_data=f"km:s:{p}:{s}:{orig}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


HELP = ("👥 Жауапты координаторларды белгілеу:\n• хабарламаға <code>#инфо-21</code> жазыңыз (топта хэштег қосулы болса)\n"
        "• немесе <code>/koord инфо-21</code>\n• <code>/koord</code> жалғыз — түймелермен таңдау\n"
        "Ағын: сан (01/11/21/31/41) немесе ай аты (шілде, тамыз, қыркүйек, қазан, қараша). "
        "Junior: <code>#junior-био</code>")
UNKNOWN = "Пән танылмады. Мысал: /koord инфо-21 · пәндер: " + ", ".join(SUBJECTS)
