import json
import logging

from openai import AsyncOpenAI

from .config import OPENAI_API_KEY, OPENAI_MODEL
from .roster import SUBJECTS
from .templates import ISSUE_TYPES, ROLES, match_enum

log = logging.getLogger(__name__)
client = AsyncOpenAI(api_key=OPENAI_API_KEY, timeout=20, max_retries=1)


async def check_solution(complaint: str, solution: str) -> dict:
    """Шешім нақты ма (кім, не істеді, қандай нәтиже)? Қате болса - өткізіп жібереді (fail-open)."""
    system = """Сен білім орталығының сервис бөлімінің тексерушісісің. Шағымға жазылған ШЕШІМ мәтіні мазмұнды ма?
Нақты (ok=true): қандай да бір нақты әрекет немесе нәтиже айтылған (байланысты, ауыстырылды, ескерту берілді,
түсіндірілді, қайта қосылды...). Толық болмаса да, егер не істелгені түсінікті болса - ok=true.
Нақты емес (ok=false): ТЕК ешқандай әрекет/нәтиже жоқ жалпы сөз: «шешілді», «сөйлестім», «қарап жатырмыз», «ок».
Күмән болса ok=true қой.
Тек JSON: {"ok": true/false, "reason": "ok=false болса, қазақша бір сөйлем: нені нақтылау керек"}"""
    try:
        r = await client.chat.completions.create(
            model=OPENAI_MODEL, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": json.dumps({"complaint": complaint, "solution": solution},
                                                             ensure_ascii=False)}])
        return json.loads(r.choices[0].message.content)
    except Exception:
        log.exception("check_solution failed; accepting")
        return {"ok": True, "reason": ""}


async def classify(description: str) -> dict:
    """Шағым мәтінінен: кім туралы (қызметі), мәселе типі, қызметкер аты. Қате болса "-" (Excel-де бос қалмайды)."""
    system = f"""Сен білім орталығының сервис бөлімінің көмекшісісің. Оқушының/ата-ананың шағымы бойынша анықта.
Тек JSON: {{"role": ..., "title": ..., "employee_name": ...}}
- role: шағым КІМ туралы - тек мына тізімнен БІР мән: {" | ".join(ROLES)}
- title: шағымның негізгі мәселесі - тек мына тізімнен БІР мән: {" | ".join(ISSUE_TYPES)}
- employee_name: шағым айтылған қызметкердің аты-жөні мәтінде болса, әйтпесе null
Тізімнен тыс мән жазба. Мәтінде жоқ нәрсені ойлап қоспа."""
    out = {"role": "-", "title": "-", "employee_name": None}
    try:
        r = await client.chat.completions.create(
            model=OPENAI_MODEL, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": description}])
        d = json.loads(r.choices[0].message.content)
        out["role"] = match_enum(str(d.get("role") or ""), ROLES) or "-"
        out["title"] = match_enum(str(d.get("title") or ""), ISSUE_TYPES) or "-"
        name = d.get("employee_name")
        out["employee_name"] = str(name).strip() if name and str(name).strip().lower() != "null" else None
    except Exception:
        log.exception("classify failed; leaving '-'")
    return out


async def extract_complaint(text: str) -> dict | None:
    """Шаблонсыз/еркін жазылған шағымнан өрістерді ажыратады. Қате болса None (шақырушы бұрынғы ережеге көшеді)."""
    system = f"""Сен білім орталығының сервис бөлімінің көмекшісісің. Хабарлама - қызметкер жазған оқушы шағымы,
еркін форматта (нөмірленген тізім болуы мүмкін, нөмірлері қате болуы мүмкін). Өрістерді ажыратып ал.
Тек JSON: {{"is_complaint": bool, "student_name": ..., "phone": ..., "subject": ..., "employee_name": ..., "description": ...}}
- is_complaint: оқушы/ата-ана шағымы немесе мәселе болса true; сәлем, рахмет, сұрақ, жалпы чат болса false
- student_name: оқушының аты-жөні
- phone: болса
- subject: пән және ағын мәтіндегідей, мысалы «ГЕО-21» немесе «SMART ФИЗ, МАТ-01». Пән кодтары: {", ".join(SUBJECTS)}. Ағын: -01/-11/-21/-31. Мәтінде болмаса null
- employee_name: шағым айтылған куратор/мұғалімнің аты-жөні (тек мәтінде болса)
- description: шағымның мәні, мәтіндегі сөздермен (аты, телефон, пән, @тегтерді қоспай)
Мәтінде жоқ нәрсені ойлап қоспа, жоқ өріс - null."""
    try:
        r = await client.chat.completions.create(
            model=OPENAI_MODEL, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": text}])
        d = json.loads(r.choices[0].message.content)
        out = {"is_complaint": bool(d.get("is_complaint"))}
        for k in ("student_name", "phone", "subject", "employee_name", "description"):
            v = d.get(k)
            out[k] = str(v).strip() if v and str(v).strip().lower() not in ("null", "none", "-") else None
        return out
    except Exception:
        log.exception("extract_complaint failed")
        return None


async def classify_reply(reply: str, complaint: str) -> dict | None:
    """Координатордың шаблонсыз жауабы: шешім бе (нақты әрекет/нәтиже), аралық жауап па, әлде басқа ма.
    Шешім болса өрістерді де суырып алады. Қате болса None."""
    system = """Координатор оқушы шағымына шаблонсыз жауап жазды. Тек JSON:
{"kind": "resolution" | "progress" | "other", "solution": ..., "satisfied": ..., "justified": ..., "stayed": ...}
- resolution: координатор әрекетті ЖАСАДЫ (оқушымен/ата-анамен байланысты, сөйледі, ауыстырды) ЖӘНЕ шешімді айтты
  (не істелді немесе не істелетіні анық белгіленді, мысалы «рейтинг шыққан соң куратор ауыстырылады»). Оқушының реакциясы
  (риза / наразы) айтылса - міндетті түрде resolution
- progress: қабылдады / қарап жатыр / кері байланыс береміз / ертең-кейін ІСТЕЙМІН (жоспар) дейді; әрекет әлі жасалмаған, шешім жоқ
- other: рахмет, сәлем, сұрақ, шағымға қатысы жоқ чат
resolution болғанда:
- solution: жауаптың мәні, мәтіндегі сөздермен, 1-3 сөйлем
- satisfied: "Иә" оқушы шешімге риза/келісті; "Жоқ" наразы; айтылмаса null
- justified: "Негізделген" | "Жартылай негізделген" | "Негізделмеген" | "КБ бермеді" - тек мәтінде нақты айтылса, әйтпесе null
- stayed: "Иә" оқушы курста қалады/қалды; "Жоқ" кетеді/кетті; айтылмаса null
Мәтінде жоқ нәрсені ойлап қоспа, жоқ өріс - null."""
    try:
        r = await client.chat.completions.create(
            model=OPENAI_MODEL, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": json.dumps({"complaint": complaint[:600], "reply": reply[:800]},
                                                             ensure_ascii=False)}])
        d = json.loads(r.choices[0].message.content)
        kind = d.get("kind") if d.get("kind") in ("resolution", "progress", "other") else "other"
        sol = d.get("solution")
        return {
            "kind": kind,
            "solution": str(sol).strip() if sol and str(sol).strip().lower() != "null" else None,
            "satisfied": match_enum(str(d.get("satisfied") or ""), ["Иә", "Жоқ"]),
            "justified": match_enum(str(d.get("justified") or ""),
                                    ["Негізделген", "Жартылай негізделген", "Негізделмеген", "КБ бермеді"]),
            "stayed": match_enum(str(d.get("stayed") or ""), ["Иә", "Жоқ"]),
        }
    except Exception:
        log.exception("classify_reply failed")
        return None
