"""Тех.менеджерлер және ішкі сату менеджерлері (аты-жөні бойынша, username жоқ)."""
import re

TECH = """Бекарыс Алишер; Куанов Али; Абдрахман Аяулым; Қалымхан Таслима; Жолдас Асель; Жолдасбаев Бексултан"""
INTERNAL = """Досқали Айша; Макимова Дилляра; Сұлтан Мерей; Жұмағұлова Айжұлдыз; Қонысбай Айбегім;
Серікқызы Гүлназ; Саркулова Раушан; Мұзарапова Мөлдір; Сейт Ернұр; Мырзабек Айгерім; Алидибек Ақерке"""


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^\wәіңғүұқөһ]+", s.lower()) if len(t) > 1}


_TABLE = [(_tokens(n), "tech") for n in TECH.split(";")] + \
         [(_tokens(n), "internal") for n in INTERNAL.replace("\n", " ").split(";")]


def role_for(full_name: str) -> str | None:
    toks = _tokens(full_name or "")
    for want, role in _TABLE:
        if want and want <= toks:
            return role
    return None
