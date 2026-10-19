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
    system = """Сен білім орталығының сервис бөлімінің тексерушісісің. Шағымға жазылған ШЕШІМ мәтіні нақты ма?
Нақты: кіммен байланысқа шықты, не істелді/қандай шешім қабылданды, нәтиже қандай.
Нақты емес: «шешілді», «сөйлестім», «қарап жатырмыз» сияқты жалпы сөздер.
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
