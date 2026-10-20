"""Шағым және шешім шаблондары: кодпен талдау және тексеру (OpenAI керек емес)."""
import difflib
import re

ROLES = ["Куратор", "Координатор", "Мұғалім", "Ішкі сату менеджері", "Сыртқы сату менеджері", "Курс",
         "Қолдау қызметі"]
ISSUE_TYPES = ["Нәтижеге қанағаттанбау", "Кеш жауап беру", "Куратор ауыстыру", "Дөрекілігі",
               "Куратордың ұнамауы", "Дұрыс емес қарым-қатынас", "Жеке мәселе", "меңгерушіден келген запрос",
               "Тапсырманы шынайы тексермейді", "Куратор қатал", "Мұғалім мәселесі", "Тапсырманы кеш тексереді",
               "Сұраққа толық жауап бермейді", "Сөйлеу мәнері ұнамайды", "Курс системасы", "Менеджер қателігі",
               "Рейтинг жіберілмеді", "Байланысқа шықпады", "Деңгей көтерілмеді", "Жұмысы сапасы",
               "Материал мәселесі", "Менеджер мәселесі", "Топ ауыстыру"]
JUSTIFIED = ["Негізделген", "Жартылай негізделген", "Негізделмеген", "КБ бермеді"]
SATISFIED = ["Иә", "Жоқ", "КБ бермеді"]
STAYED = ["Иә", "Жоқ"]
SYNONYMS = {"да": "Иә", "иа": "Иә", "ия": "Иә", "yes": "Иә", "нет": "Жоқ", "жок": "Жоқ", "no": "Жоқ"}

COMPLAINT_LABELS = {
    "student": ["Оқушы аты-жөні", "Оқушы аты жөні"],
    "phone": ["Телефон нөмірі", "Телефон"],
    "email": ["Почта", "Email", "E-mail"],  # ескі шаблонда болған: жолды оқимыз да, елемейміз
    "subject": ["Пәні және потогы", "Пәні және потоғы", "Пән және потог"],
    "employee": ["Қызметкердің аты-жөні", "Қызметкер аты-жөні"],
    "role": ["Қызметі/Мәселе иесі", "Қызметі / Мәселе иесі", "Қызметі", "Мәселе иесі"],
    "title": ["Мәселе атауы/типі", "Мәселе атауы / типі", "Мәселе атауы", "Мәселе типі"],
    "desc": ["Мәселенің толық сипаттамасы", "Мәселенің сипаттамасы"],
}
RESOLUTION_LABELS = {
    "student": ["Оқушы аты-жөні", "Оқушы аты жөні"],
    "solution": ["Мәселенің толық шешімі", "Мәселенің шешімі", "Толық шешімі"],
    "justified": ["Мәселе шындыққа негізделген бе"],
    "satisfied": ["Оқушы риза ма"],
    "stayed": ["Оқушы курста қалды ма"],
}

COMPLAINT_TEMPLATE = """Оқушы аты-жөні:
Телефон нөмірі:
Пәні және потогы:
Қызметкердің аты-жөні:
Мәселенің толық сипаттамасы: """

RESOLUTION_TEMPLATE = """Оқушы аты-жөні:
Мәселенің шешімі:
Мәселе шындыққа негізделген бе?
Оқушы риза ма?
Оқушы курста қалды ма? """

COMPLAINT_HELP = "📝 <b>Шағым шаблоны</b> · /shablon\n<code>" + COMPLAINT_TEMPLATE + "</code>"

RESOLUTION_HELP = (
    "📝 <b>Шешім шаблоны</b> (шағымға reply) · /shesim\n<code>" + RESOLUTION_TEMPLATE + "</code>\n"
    "<blockquote expandable><b>Негізделген бе:</b> " + " / ".join(JUSTIFIED) + "\n"
    "<b>Риза ма:</b> " + " / ".join(SATISFIED) + "\n"
    "<b>Қалды ма:</b> " + " / ".join(STAYED) + "\n"
    "Қате болса хабарламаны өңдеңіз (edit).</blockquote>")

RESOLUTION_COMPACT = "<code>" + RESOLUTION_TEMPLATE + "</code>"

# Бір pin хабарлама: екі шаблон қатар (әрқайсысын бір басумен көшіруге болады)
COMBINED_HELP = (
    "📝 <b>Жаңа шағым</b>\n<code>" + COMPLAINT_TEMPLATE + "</code>\n\n"
    "✅ <b>Шешім</b> (шағымға reply)\n<code>" + RESOLUTION_TEMPLATE + "</code>\n"
    "<blockquote expandable><b>Негізделген бе:</b> " + " / ".join(JUSTIFIED) + "\n"
    "<b>Риза ма:</b> " + " / ".join(SATISFIED) + "\n"
    "<b>Қалды ма:</b> " + " / ".join(STAYED) + "\n"
    "Қате болса хабарламаны өңдеңіз (edit).</blockquote>\n"
    "Шаблонды қайта алу: /shablon · /shesim")


def norm(s: str) -> str:
    s = (s or "").lower().replace("ё", "е")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s)).strip()


def parse(text: str, labels: dict[str, list[str]]) -> dict[str, str]:
    """`Белгі: мән` жолдарын оқиды; мән келесі белгіге дейін көп жолды болуы мүмкін."""
    aliases = sorted(((a, k) for k, al in labels.items() for a in al), key=lambda x: -len(x[0]))
    pats = [(k, re.compile(r"^\s*(?:\d+\s*[.)]\s*)?" + re.escape(a) + r"(?!\w)\s*\??\s*[:：\-–—]?\s*(.*)$", re.I))
            for a, k in aliases]
    out: dict[str, str] = {}
    cur = None
    for line in (text or "").splitlines():
        for k, p in pats:
            m = p.match(line)
            if m:
                cur = k
                out[k] = m.group(1).strip()
                break
        else:
            if cur:
                out[cur] = (out[cur] + "\n" + line.strip()).strip()
    for k, v in out.items():  # толтырылмай қалған "A/B/C/D" тізімін бос деп санаймыз
        if k not in ("desc", "solution") and v.count("/") >= 3:
            out[k] = ""
    return out


def match_enum(val: str, options: list[str]) -> str | None:
    n = norm(val)
    if not n:
        return None
    if n in SYNONYMS and SYNONYMS[n] in options:
        return SYNONYMS[n]
    by_norm = {norm(o): o for o in options}
    if n in by_norm:
        return by_norm[n]
    if len(n) >= 3:
        cands = [o for k, o in by_norm.items() if k.startswith(n) or n.startswith(k)]
        if len(cands) == 1:
            return cands[0]
    m = difflib.get_close_matches(n, list(by_norm), n=1, cutoff=0.72)
    return by_norm[m[0]] if m else None


# ------------------------------------------------------------------ пән және ағын
SUBJECT_ALIASES = {
    "ИНФО": "ИНФО", "ИНФОРМАТИКА": "ИНФО", "ФИЗ": "ФИЗ", "ФИЗИКА": "ФИЗ", "МАТ": "МАТ", "МАТЕМАТИКА": "МАТ",
    "ГЕОМ": "ГЕОМ", "ГЕОМЕТРИЯ": "ГЕОМ", "ХИМ": "ХИМ", "ХИМИЯ": "ХИМ", "БИО": "БИО", "БИОЛОГИЯ": "БИО",
    "МС": "МС", "ГЕО": "ГЕО", "ГЕОГРАФИЯ": "ГЕО", "АНГЛ": "АНГЛ", "АНГЛИЙСКИЙ": "АНГЛ",
    "ӘДЕБТІЛ": "ӘДЕБТІЛ", "ТІЛ": "ӘДЕБТІЛ", "РУСЛИТ": "РУСЛИТ", "ОРЫС": "РУСЛИТ", "АҒЫЛШЫН": "АНГЛ", "ДЖТ": "ДЖТ", "ТАРИХ": "ТАРИХ", "Т": "ТАРИХ",
    "ҚҰҚЫҚ": "ҚҰҚЫҚ", "ҚУҚЫҚ": "ҚҰҚЫҚ", "ТОЕФЛ": "ТОЕФЛ",
}
_KEYS_BY_LEN = sorted((k for k in SUBJECT_ALIASES if len(k) >= 3 and k != "ТІЛ"), key=len, reverse=True)


def _subject(tok: str) -> str | None:
    if tok in SUBJECT_ALIASES:
        return SUBJECT_ALIASES[tok]
    for k in _KEYS_BY_LEN:
        if tok.startswith(k):
            return SUBJECT_ALIASES[k]
    return None


def parse_subject(label: str):
    """'SMART ФИЗ, МАТ-01' -> ('smart', [('ФИЗ','01'), ('МАТ','01')])"""
    up = (label or "").upper()
    program = ("smart" if re.search(r"SMART|СМАРТ", up) else "junior" if re.search(r"JUNIOR|ДЖУНИОР|ДЖУНИЯР", up) else None)
    up = re.sub(r"SMART|JUNIOR|СМАРТ|ДЖУНИОР|ДЖУНИЯР", " ", up)
    items: list[list] = []  # [пән|None, ағын|None]; танылмаған сөздер де ағынды тасиды ("Орыс тілі-21")
    for m in re.finditer(r"([A-ZА-ЯӘІҢҒҮҰҚӨҺ]+)\s*(?:-\s*(\d{2})(?!\d))?", up):
        items.append([_subject(m.group(1)), m.group(2)])
    carry = None
    for it in reversed(items):  # ағын алдыңғы пәндерге де қолданылады
        if it[1]:
            carry = it[1]
        else:
            it[1] = carry
    pairs = [it for it in items if it[0]]
    return program, [tuple(p) for p in pairs]


# ------------------------------------------------------------------ шағымды тексеру
URL_RE = re.compile(r"https?://\S+|t\.me/\S+", re.I)
REQUIRED_C = {"student": "Оқушы аты-жөні", "subject": "Пәні және потогы",
              "desc": "Мәселенің толық сипаттамасы"}  # қызметі, мәселе типі, қызметкер - міндетті емес (OpenAI толтырады)


def synth_complaint(labels: dict, ext: dict) -> str:
    """Қолмен жазылған өрістер басым; жетпегенін OpenAI суырып алған мәнмен толтырып, шаблон мәтінін құрастырамыз."""
    def pick(key, ext_key):
        return (labels.get(key) or "").strip() or str(ext.get(ext_key) or "").strip()
    return "\n".join([
        f"Оқушы аты-жөні: {pick('student', 'student_name')}",
        f"Телефон нөмірі: {pick('phone', 'phone')}",
        f"Пәні және потогы: {pick('subject', 'subject')}",
        f"Қызметкердің аты-жөні: {pick('employee', 'employee_name')}",
        f"Мәселенің толық сипаттамасы: {pick('desc', 'description')}",
    ])


def validate_complaint(text: str):
    """-> (parsed | None, errors, labels_found)"""
    f = parse(text, COMPLAINT_LABELS)
    errors = []
    for k, label in REQUIRED_C.items():
        if not f.get(k, "").strip():
            errors.append(f"«{label}» толтырылмаған")
    # қолмен жазылса және тізімге сәйкес болса - қолданамыз, әйтпесе OpenAI таңдайды (қате емес)
    role = match_enum(f.get("role", ""), ROLES) if f.get("role") else None
    title = match_enum(f.get("title", ""), ISSUE_TYPES) if f.get("title") else None
    program, pairs = parse_subject(f.get("subject", ""))
    if f.get("subject"):
        if not pairs:
            errors.append("«Пән»: танылмады, мысал: SMART ГЕО-31")
        elif program != "junior" and any(not st for _, st in pairs):
            errors.append("«Пән»: ағын жоқ, мысал: SMART ГЕО-31")
    if errors:
        return None, errors, f
    parsed = {
        "student_name": f["student"], "phone": f.get("phone") or "",
        "program": program, "pairs": [list(p) for p in pairs],
        "subjects": [s for s, _ in pairs], "stream": next((st for _, st in pairs if st), None),
        "subject_label": f["subject"].strip(), "employee_name": (f.get("employee") or "").strip() or None,
        "employee_role": role, "issue_title": title, "description": f["desc"],
        "chat_link": (URL_RE.search(text).group(0) if URL_RE.search(text) else None),
        "mentioned_usernames": re.findall(r"@(\w{4,})", text),
    }
    return parsed, [], f


def synth_resolution(student: str, ext: dict, fallback_text: str = "") -> str:
    """Шаблонсыз жазылған шешімнен шешім шаблонын құрастырады (жетпейтін өрістер бос қалады)."""
    sol = (ext.get("solution") or fallback_text or "").strip()
    return "\n".join([
        f"Оқушы аты-жөні: {student}",
        f"Мәселенің шешімі: {sol}",
        f"Мәселе шындыққа негізделген бе? {ext.get('justified') or ''}",
        f"Оқушы риза ма? {ext.get('satisfied') or ''}",
        f"Оқушы курста қалды ма? {ext.get('stayed') or ''}",
    ])


def validate_resolution(text: str, student_expected: str | None):
    f = parse(text, RESOLUTION_LABELS)
    errors = []
    sol = f.get("solution", "").strip()
    if len(sol) < 30:
        errors.append("«Шешім»: тым қысқа (кемі 30 символ, не істелгенін жазыңыз)")
    just = match_enum(f.get("justified", ""), JUSTIFIED)
    sat = match_enum(f.get("satisfied", ""), SATISFIED)
    stay = match_enum(f.get("stayed", ""), STAYED)
    if not just:
        errors.append("«Негізделген бе?»: " + "/".join(JUSTIFIED))
    if not sat:
        errors.append("«Риза ма?»: " + "/".join(SATISFIED))
    if not stay:
        errors.append("«Қалды ма?»: " + "/".join(STAYED))
    st = f.get("student", "").strip()
    if not st:
        errors.append("«Оқушы аты-жөні» жоқ")
    elif student_expected and not (set(norm(st).split()) & set(norm(student_expected).split())):
        errors.append(f"«Оқушы»: шағымдағымен сәйкес емес ({student_expected})")
    if errors:
        return None, errors, f
    return {"edu_response": sol, "justified": just, "satisfied": sat, "stayed": stay}, [], f
