"""Супервайзерлер мен координаторлар тізімі.

Формат жолы:  ПӘН: Аты-жөні @username; Аты-жөні (username жоқ); ...
username жоқ адамдар үшін бот Telegram-дағы көрсетілетін атынан (display name) іздейді.
"""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Person:
    name: str
    username: str | None = None

    @property
    def key(self) -> str:
        return (self.username or self.name).lower()


SUBJECTS = ["ИНФО", "ФИЗ", "МАТ", "ГЕОМ", "ХИМ", "БИО", "МС", "ГЕО", "АНГЛ",
            "ӘДЕБТІЛ", "РУСЛИТ", "ДЖТ", "ТАРИХ", "ҚҰҚЫҚ", "ТОЕФЛ"]

# Есеп / ауызша / тілдік (Junior үшін)
JUNIOR_CALC = {"МАТ", "ФИЗ", "ХИМ", "ИНФО", "МС", "ГЕОМ"}


def _parse(block: str) -> dict[str, list[Person]]:
    out: dict[str, list[Person]] = {}
    for line in block.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        subj, rest = line.split(":", 1)
        people = []
        for chunk in rest.split(";"):
            chunk = chunk.strip()
            if not chunk:
                continue
            m = re.search(r"@(\w+)", chunk)
            name = re.sub(r"@\w+", "", chunk).strip()
            people.append(Person(name, m.group(1) if m else None))
        out[subj.strip()] = people
    return out


SUPERVISORS = _parse("""
ИНФО: Мұсағали Амина @amina_info140
ФИЗ: Қуанышқызы Мерей Мирида @mereiphys
МАТ: Асқарбек Асанбай @asanbay_juz40
ГЕОМ: Асқарбек Асанбай @asanbay_juz40
ХИМ: Қайырбекова Асем @assema_chemistry140
БИО: Мухамедали Мерей @mereyka_bio140
МС: Рахметқали Гүлдана @guldana_mc
ГЕО: Қалдыбай Әмірхан @Meyrambekovich
АНГЛ: Рахатов Мәулен @maulen_juz40eng
ӘДЕБТІЛ: Жасыбаева Зере @zere_adebtil140
РУСЛИТ: Берекеева Мариям @mariyam_ruslit140
ДЖТ: Жұмаханов Ерғанат @yerganat_dzht140
ТАРИХ: Оралбаева Аружан @oralbayeva_tarikhjuz40
ҚҰҚЫҚ: Жасузакова Айтөре @aitoreshka_JUZ40quqyq
""")

# ағын коды -> {пән: [координаторлар]}
COORDINATORS: dict[str, dict[str, list[Person]]] = {
    "01": _parse("""
ИНФО: Мұса Дариға @darigamussa
ФИЗ: Талғатұлы Таңат @tanatphyscoord
ӘДЕБТІЛ: Әшімова Арайлым @araailyma
АНГЛ: Сәндібек Мадина @sandibekmadina
ҚҰҚЫҚ: Серікхан Ақмарал @Akma_quqyq
ХИМ: Нағымова Махаббат @Nagymova_Mahabbat
ГЕО: Дәулетбекқызы Камшат @kamoshh_geo
РУСЛИТ: Абдуллина Камила @camiabdullina
БИО: Савронова Парвина @parvina_bio140; Мустафаева Ақдидар @dikosh_juz40
МАТ: Бердібекова Гюзель @guzeltm_juz40; Орынбай Айдана @Aidana_apai
ТАРИХ: Махан Дамир @damirmakhan; Еркінқызы Балауса @balausatarikh; Әбу Айгерім @gera_t01
ДЖТ: Абдрахманова Назира @naziradzht
МС: Дюсегалиева Даяна @dayanka04
"""),
    "11": _parse("""
ИНФО: Мұса Дариға @darigamussa
ФИЗ: Тұрарова Аруна @turararuna
ӘДЕБТІЛ: Әшімова Арайлым @araailyma
АНГЛ: Сәндібек Мадина @sandibekmadina
ҚҰҚЫҚ: Серікхан Ақмарал @Akma_quqyq
ГЕО: Диханбай Феруза @fbrgeooo; Абдықаппарова Айгүл @ab_a_ta
РУСЛИТ: Абдуллина Камила @camiabdullina
БИО: Жомартқызы Әсел @qaselok_bio140; Тлекбаева Альбина @tallbyna
МАТ: Байхан Елдос @eld0sikk_juz40; Избасар Айгерім @aiggesar; Исатаева Жаннур @yssataeva
ТАРИХ: Мақсат Темірлан @timatarikh; Тәуекел Ернұр @ernuragai; Байбосынова Мөлдір @baibosynovam01; Аблимитов Арманжан @vrmvn_tarikh
ДЖТ: Тұрсын Балауса @balausa_dzhtcoordinator; Әбдікәрім Нұртөре @nurtoreshkaa
МС: Оразәлі Бағжан @bagzhanmc
ХИМ: Совет Назым @beckhac_e; Ергалиева Алина @yergalieva5
"""),
    "21": _parse("""
ИНФО: Ниязбек Шұғыла @niyazbekshugyla
ФИЗ: Орынбек Меруерт @meruert_juz40; Каненова Алтынай
ТАРИХ: Махан Дамир @damirmakhan; Қуанышбек Жансая; Оспан Әділет @ospan0v_017; Темірболат Дәурен @Daurenttt; Абдығали Бақтығали @baqtygali17; Есенгелді Қарақат @EsengeldiKarakat
ГЕО: Серік Гүлназ @takogeo; Алшағыр Нұржан @nurzhannn_geo; Алтынбек Ақбота @botashgeo
ҚҰҚЫҚ: Ахат Майра @kazbekkyzzy
ГЕОМ: Қайырғазина Талшын @toshageom140
МАТ: Орынбай Айдана @Aidana_apai; Бейсенбай Айтолқын @aitolkynbeisenbay; Саматқызы Шұғыла @samatqyzyshugyla; Сулейменов Шалқар @Shalkar_21
ДЖТ: Орал Нұрсұлтан @daniarulyy_dzht; Урумбай Азамат @azamatdzht; Садық Айшуақ @shuaqdzht21
ХИМ: Қайбулла Мерей @mereykaybulla; Бигалиева Гулим @g_bigalieva
РУСЛИТ: Айдар Гаухария @riyaa_drr
МС: Дюсегалиева Даяна @dayanka04; Кеңшілік Айым @aiymmc
БИО: Мустафаева Ақдидар @dikosh_juz40; Мұрат Аяулым @ayaulym_bio140; Серіков Әділбек @adil_bio140
АНГЛ: Сәндібек Мадина @sandibekmadina
ӘДЕБТІЛ: Шанжархан Назерке @naz_erkemmm
"""),
    "31": _parse("""
ИНФО: Сайлаубеков Елнұр @elnurqwe
ФИЗ: Жетіру Замира @zhetiruzamira; Саттарқызы Назерке @nazerke_sattarqyzy
ӘДЕБТІЛ: Ералхан Айым @aiym_eralkhan
АНГЛ: Сәндібек Мадина @sandibekmadina
ТОЕФЛ: Серикбаева Ажар @azhaarsss
ҚҰҚЫҚ: Серікхан Ақмарал @Akma_quqyq
ГЕО: Маселбек Жания @zhhaniya; Бақыт Аружан @aruzhanbakyt; Сейдуалы Амина @SdlyAmina
РУСЛИТ: Айдар Гаухария @riyaa_drr
БИО: Абайева Нурдана @abaievan1; Қадырхан Ақбота @Botashkajuz40
МАТ: Аяпбергенова Гульназ @naz140; Сарсенова Аяжан @aayazhann_s; Рахметов Рамазан @rakhmetovramazan
ТАРИХ: Турсынова Сандуғаш @sandutursyn; Жарбосынова Манар @manar_zharbosynova; Қасай Гүлім @sagingalievnaaa; Тәуекел Ернұр @ernuragai; Айдарова Қарақат @Aidarova_Karakat
ДЖТ: Оразова Анеля @aaneleka; Кенджебаева Анар @anar_dzht31
МС: Бейсен Амина @s_amiwka; Дюсегалиева Даяна @dayanka04
ХИМ: Сұлтанова Нұрай @nuraisultanova
"""),
}
# Бұл ағындарда ГЕОМ жеке координаторы жоқ болса МАТ координаторларына түседі
STREAM_NAMES = {"01": "01 шілде", "11": "11 тамыз", "21": "21 қыркүйек", "31": "31 қазан", "41": "41 қараша"}

# JUNIOR
JUNIOR_HEAD = Person("Есенжан Мағжан", "maqzhan1")
JUNIOR_CALC_COORD = [Person("Рыстанбекова Айдана", "arystanbekova7")]
JUNIOR_OTHER_COORDS = [
    Person("Нұрланқызы Шырын", "nurlnvash"),
    Person("Қабдиева Баян", "bayashkarr"),
    Person("Жасын Әлімбеков", "Zhasynnn5555"),
]


def responsible_for(program: str, subjects: list[str], stream: str | None):
    """-> (coordinators, supervisors, notes). Дубликатсыз."""
    coords: list[Person] = []
    sups: list[Person] = []
    notes: list[str] = []

    def add(lst: list[Person], people):
        for p in people:
            if p.key not in {x.key for x in lst}:
                lst.append(p)

    if program == "junior":
        calc = [s for s in subjects if s in JUNIOR_CALC]
        other = [s for s in subjects if s not in JUNIOR_CALC]
        if calc or not subjects:
            add(coords, JUNIOR_CALC_COORD)
        if other or not subjects:
            add(coords, JUNIOR_OTHER_COORDS)
        add(sups, [JUNIOR_HEAD])
        return coords, sups, notes

    # smart
    by_stream = COORDINATORS.get(stream or "", {})
    if stream and not by_stream:
        notes.append(f"«{stream}» ағыны тізімде жоқ — тек супервайзер белгіленді")
    if not stream:
        notes.append("Ағын көрсетілмеген — тек супервайзер белгіленді")
    for s in subjects:
        add(sups, SUPERVISORS.get(s, []))
        found = by_stream.get(s) or (by_stream.get("МАТ") if s == "ГЕОМ" else None)
        add(coords, found or [])
    if not subjects:
        notes.append("Пән анықталмады — жауапты адамдарды қолмен белгілеңіз")
    return coords, sups, notes


ALL_PEOPLE: list[Person] = []


def _collect():
    seen = {}
    for lst in SUPERVISORS.values():
        for p in lst:
            seen[p.key] = p
    for st in COORDINATORS.values():
        for lst in st.values():
            for p in lst:
                seen[p.key] = p
    for p in [JUNIOR_HEAD, *JUNIOR_CALC_COORD, *JUNIOR_OTHER_COORDS]:
        seen[p.key] = p
    ALL_PEOPLE.extend(seen.values())


_collect()
