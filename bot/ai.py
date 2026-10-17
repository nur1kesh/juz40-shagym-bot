import json
import logging

from openai import AsyncOpenAI

from .config import OPENAI_API_KEY, OPENAI_MODEL

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
