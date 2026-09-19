"""
📦 ХРАНИЛИЩЕ ФОРМ
Типы тикетов и вопросы анкет для заявок.
Всё настраивается из Discord:
  /тикет-настройка ...   — типы тикетов, вопросы форм, роли, категории
  /заявки-настройка ...  — вопросы анкет для заявок

Структура типа тикета (хранится в data.json → "ticket_types"):
{
  "код-типа": {
    "label": "💬 Поддержка",        # название в выпадающем списке (до 100)
    "description": "...",           # описание в списке (до 100)
    "emoji": "💬",                  # эмодзи (если не указан внутри label)
    "color": 5865F2,                # цвет embed тикета
    "prefix": "support",            # префикс канала (латиница! Discord не даёт кириллицу)
    "ping_roles": [123, 456],       # кого пинговать при создании
    "escalate_roles": [789],        # 🔁 роли для кнопки «Передать» (старший состав)
    "category_id": None,            # категория каналов (None = из config.py)
    "banner": "",                   # 🖼️ URL картинки-баннера внутри тикета ("" = нет)
    "questions": [                  # форма обращения (до 5 вопросов)
      {"label": "...", "placeholder": "...", "required": true, "long": true, "max_length": 1000}
    ]
  }
}
"""

import re

from config import BotConfig
from database import db

MAX_QUESTIONS = 5     # лимит Discord: не больше 5 полей в модальном окне
MAX_TYPES     = 25    # лимит Discord: не больше 25 пунктов в выпадающем списке

APP_TYPE_IDS = ("персонал", "дс-адм", "билдеры")

# Префиксы старых тикетов (на случай каналов, созданных до обновления)
LEGACY_PREFIXES = ("поддержка", "техподдержка")

_TRANS = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    " ": "-", "_": "-", "і": "i", "ї": "i", "є": "e", "ґ": "g",
}


def slugify(text: str, max_len: int = 20) -> str:
    """
    Превращает произвольный текст в допустимое имя Discord-канала:
    латиница, цифры и дефисы (кириллица транслитерируется).
    """
    text = (text or "").lower().strip()
    out = []
    for ch in text:
        if ch in _TRANS:
            out.append(_TRANS[ch])
        elif ch.isascii() and (ch.isalnum() or ch == "-"):
            out.append(ch)
    slug = "".join(out)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:max_len].strip("-")


def parse_color(value: str):
    """Парсит '#5865f2' / '5865F2' в int. При ошибке — None."""
    v = (value or "").strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{6}", v):
        return int(v, 16)
    return None


def valid_slug(value: str) -> bool:
    """Код типа / префикс канала: латиница, цифры, дефис."""
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,29}", (value or "").strip()))


def make_question(label: str, placeholder: str = "", required: bool = True,
                  long: bool = True, max_length: int = 1000) -> dict:
    return {
        "label": str(label)[:45],
        "placeholder": str(placeholder or "")[:100],
        "required": bool(required),
        "long": bool(long),
        "max_length": max(1, min(int(max_length or 1000), 4000)),
    }


# ─── ТИПЫ ТИКЕТОВ ────────────────────────────────────────────────────────────

# Коды готовых типов «Жалобы» — добавляются одноразово при первом запуске
REPORT_TYPE_IDS = ("жалоба-игрок", "жалоба-персонал")


def default_ticket_types() -> dict:
    """Стандартные типы (помощь, тех-поддержка, жалобы), с латинскими префиксами."""
    return {
        "поддержка": {
            "label": "💬 Помощь",
            "description": "Общие вопросы, помощь, баги",
            "emoji": "💬",
            "color": 0x5865F2,
            "prefix": "support",
            "ping_roles": [r for r in BotConfig.SUPPORT_PING_ROLES if r],
            "escalate_roles": [r for r in BotConfig.SUPPORT_ESCALATE_ROLES if r],
            "category_id": BotConfig.TICKET_CATEGORY_ID,
            "banner": "",
            "questions": [
                make_question("Кратко опишите вашу проблему:",
                              "Опишите ситуацию подробно...", True, True, 1000)
            ],
        },
        "тех-поддержка": {
            "label": "⚙️ Тех-поддержка",
            "description": "Технические проблемы с сервером",
            "emoji": "⚙️",
            "color": 0xE67E22,
            "prefix": "tech-support",
            "ping_roles": [r for r in BotConfig.TECH_SUPPORT_PING_ROLES if r],
            "escalate_roles": [r for r in BotConfig.TECH_SUPPORT_ESCALATE_ROLES if r],
            "category_id": BotConfig.TICKET_CATEGORY_ID,
            "banner": "",
            "questions": [
                make_question("Кратко опишите вашу проблему:",
                              "Что случилось? Что уже пробовали?", True, True, 1000)
            ],
        },
        "жалоба-игрок": {
            "label": "🚨 Жалоба на игрока",
            "description": "Читы, оскорбления, гриферство и другие нарушения",
            "emoji": "🚨",
            "color": 0xED4245,
            "prefix": "report-player",
            "ping_roles": [r for r in BotConfig.REPORT_PLAYER_PING_ROLES if r],
            "escalate_roles": [r for r in BotConfig.REPORT_PLAYER_ESCALATE_ROLES if r],
            "category_id": BotConfig.TICKET_CATEGORY_ID,
            "banner": "",
            "questions": [
                make_question("Ник нарушителя:", "Steve_123", True, False, 100),
                make_question("Опишите нарушение:",
                              "Что случилось, когда, на каком режиме...", True, True, 1500),
                make_question("Есть ли доказательства? (необязательно)",
                              "Ссылки на ролики или опишите — фото/видео можно кинуть прямо в канал тикета", False, True, 500),
            ],
        },
        "жалоба-персонал": {
            "label": "🛡️ Жалоба на персонал",
            "description": "Жалобы на действия сотрудников проекта",
            "emoji": "🛡️",
            "color": 0xFEE75C,
            "prefix": "report-staff",
            "ping_roles": [r for r in BotConfig.REPORT_STAFF_PING_ROLES if r],
            "escalate_roles": [r for r in BotConfig.REPORT_STAFF_ESCALATE_ROLES if r],
            "category_id": BotConfig.TICKET_CATEGORY_ID,
            "banner": "",
            "questions": [
                make_question("Ник/ID сотрудника:", "Helper228", True, False, 100),
                make_question("Что произошло?",
                              "Опишите ситуацию максимально подробно...", True, True, 1500),
            ],
        },
    }


async def load_types() -> dict:
    """Загружает типы тикетов; при первом запуске создаёт стандартные."""
    types = await db.get("ticket_types")
    if not isinstance(types, dict) or not types:
        types = default_ticket_types()
        await db.set("ticket_types", types)
        await db.set("migrations.report_types", True)
        return types
    # Одноразовая миграция: досоздать типы «Жалобы» у существующих установок.
    # Если админ удалит их потом — назад не возвращаются (флаг выставлен).
    if not await db.get("migrations.report_types"):
        defaults = default_ticket_types()
        changed = False
        for rid in REPORT_TYPE_IDS:
            if rid not in types:
                types[rid] = defaults[rid]
                changed = True
        await db.set("migrations.report_types", True)
        if changed:
            await db.set("ticket_types", types)
    return types


async def save_types(types: dict):
    await db.set("ticket_types", types)


def types_sync() -> dict:
    """Синхронная версия (для построения View/проверки имён каналов)."""
    t = db.get_sync("ticket_types")
    if isinstance(t, dict) and t:
        return t
    return default_ticket_types()


def ticket_prefixes_sync() -> tuple:
    """Все префиксы каналов тикетов (+ старые, для совместимости)."""
    prefixes = []
    for cfg in types_sync().values():
        p = str(cfg.get("prefix") or "").lower().strip()
        if p:
            prefixes.append(p)
    return tuple(prefixes) + LEGACY_PREFIXES


# ─── ВОПРОСЫ ЗАЯВОК ──────────────────────────────────────────────────────────

def default_app_questions() -> dict:
    return {
        "персонал": [
            make_question("Ваш ник и возраст:", "Иван, 18 лет", True, False, 100),
            make_question("Опыт и почему мы должны взять вас?",
                          "Расскажите о своём опыте и мотивации...", True, True, 800),
        ],
        "дс-адм": [
            make_question("Ваш ник и возраст:", "Иван, 18 лет", True, False, 100),
            make_question("Опыт и почему мы должны взять вас?",
                          "Расскажите о своём опыте и мотивации...", True, True, 800),
        ],
        "билдеры": [
            make_question("Ваш ник и возраст:", "Иван, 18 лет", True, False, 100),
            make_question("Опыт и почему мы должны взять вас?",
                          "Ссылки на ваши постройки приветствуются...", True, True, 800),
        ],
    }


async def load_app_questions() -> dict:
    """Загружает вопросы заявок; досоздаёт отсутствующие направления."""
    qs = await db.get("app_questions")
    defaults = default_app_questions()
    if not isinstance(qs, dict):
        qs = defaults
        await db.set("app_questions", qs)
    else:
        changed = False
        for aid in APP_TYPE_IDS:
            if aid not in qs or not isinstance(qs[aid], list):
                qs[aid] = defaults[aid]
                changed = True
        if changed:
            await db.set("app_questions", qs)
    return qs


async def save_app_questions(qs: dict):
    await db.set("app_questions", qs)


def app_questions_sync(app_type: str) -> list:
    """Синхронно (для построения Modal без await)."""
    qs = db.get_sync(f"app_questions.{app_type}")
    if isinstance(qs, list) and qs:
        return qs
    return default_app_questions().get(app_type, [])


# ─── НАСТРОЙКИ НАПРАВЛЕНИЙ ЗАЯВОК (баннер) ───────────────────────────────────
#
# Хранится в data.json → "app_settings":
# {
#   "персонал": {"banner": "https://..."}, ...
# }

def default_app_settings() -> dict:
    """Настройки по умолчанию для каждого направления заявок."""
    return {
        aid: {
            # 🖼️ баннер внутри сообщения-анкеты ("" = нет). Изначально тот же,
            # что и у панели в config.py — можно поменять: /заявки-настройка баннер
            "banner": BotConfig.APP_BANNER or "",
        }
        for aid in APP_TYPE_IDS
    }


async def load_app_settings() -> dict:
    """Загружает настройки направлений; досоздаёт отсутствующие ключи."""
    st = await db.get("app_settings")
    defaults = default_app_settings()
    if not isinstance(st, dict):
        st = defaults
        await db.set("app_settings", st)
        return st
    changed = False
    for aid in APP_TYPE_IDS:
        if aid not in st or not isinstance(st[aid], dict):
            st[aid] = dict(defaults[aid])
            changed = True
            continue
        for key, val in defaults[aid].items():
            if key not in st[aid]:
                st[aid][key] = val
                changed = True
    if changed:
        await db.set("app_settings", st)
    return st


async def save_app_settings(st: dict):
    await db.set("app_settings", st)


def app_settings_sync(app_type: str) -> dict:
    """Синхронно (для построения Modal без await). Мерджит с дефолтами."""
    base = default_app_settings().get(app_type, {"banner": ""})
    st = db.get_sync(f"app_settings.{app_type}")
    if isinstance(st, dict):
        merged = dict(base)
        merged.update({k: v for k, v in st.items() if k in base})
        return merged
    return base
