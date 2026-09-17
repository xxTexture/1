"""
📊 СИСТЕМА УЧЁТА И СТАТИСТИКИ ПЕРСОНАЛА
Отслеживает активность, тикеты, наказания и заявки для трёх категорий:
- 🛡️ Персонал сервера
- ⚙️ Персонал дискорда
- 🔨 Билдеры
"""

import discord
import datetime
import time
from typing import Optional, Dict, Any, List, Set

from config import BotConfig
from database import db

# ─── КАТЕГОРИИ ПЕРСОНАЛА ─────────────────────────────────────────────────────
STAFF_CATEGORIES = {
    "server": {
        "id": "server",
        "name": "Персонал сервера",
        "emoji": "🛡️",
        "color": 0x3498db,
        "description": "Модераторы серверов, хелперы, кураторы",
    },
    "discord": {
        "id": "discord",
        "name": "Персонал дискорда",
        "emoji": "⚙️",
        "color": 0x9b59b6,
        "description": "Модераторы и администраторы Discord-сервера",
    },
    "builder": {
        "id": "builder",
        "name": "Билдеры",
        "emoji": "🔨",
        "color": 0xe67e22,
        "description": "Строители карт, спавнов и ивентов",
    },
}

# Кэш недавних наказаний от бота для дедупликации с audit log
_recent_bot_punishments: dict[str, float] = {}


def mark_bot_punishment(guild_id: int, target_id: int, action: str):
    """Помечает, что наказание выдано ботом (чтобы audit log не считал повторно)."""
    key = f"{guild_id}_{target_id}_{action.lower()}"
    _recent_bot_punishments[key] = time.time()
    # Чистим старые записи (> 60 сек)
    now = time.time()
    old_keys = [k for k, ts in _recent_bot_punishments.items() if now - ts > 60]
    for k in old_keys:
        _recent_bot_punishments.pop(k, None)


def was_punished_by_bot(guild_id: int, target_id: int, action: str) -> bool:
    """Проверяет, было ли наказание выдано ботом за последние 15 секунд."""
    key = f"{guild_id}_{target_id}_{action.lower()}"
    ts = _recent_bot_punishments.get(key)
    if ts and (time.time() - ts) < 15:
        return True
    return False


# ─── УПРАВЛЕНИЕ РОЛЯМИ ───────────────────────────────────────────────────────
async def get_staff_roles_config() -> dict[str, list[int]]:
    """Возвращает настроенные ID ролей для каждой категории персонала."""
    custom = await db.get("staff_roles", None)

    # Дефолты из config.py
    def_server = list(getattr(BotConfig, "STAFF_SERVER_ROLES", getattr(BotConfig, "STAFF_PING_ROLES", [])))
    def_discord = list(getattr(BotConfig, "STAFF_DISCORD_ROLES", getattr(BotConfig, "DS_ADMIN_PING_ROLES", [])))
    def_builder = list(getattr(BotConfig, "STAFF_BUILDER_ROLES", getattr(BotConfig, "BUILDER_PING_ROLES", [])))

    if not def_server and BotConfig.ADMIN_ROLE_ID:
        def_server = [BotConfig.ADMIN_ROLE_ID]
    if not def_discord and BotConfig.ADMIN_ROLE_ID:
        def_discord = [BotConfig.ADMIN_ROLE_ID]
    if not def_builder and BotConfig.ADMIN_ROLE_ID:
        def_builder = [BotConfig.ADMIN_ROLE_ID]

    if custom and isinstance(custom, dict):
        return {
            "server":  [int(r) for r in custom.get("server", def_server) if r],
            "discord": [int(r) for r in custom.get("discord", def_discord) if r],
            "builder": [int(r) for r in custom.get("builder", def_builder) if r],
        }

    return {
        "server":  [int(r) for r in def_server if r],
        "discord": [int(r) for r in def_discord if r],
        "builder": [int(r) for r in def_builder if r],
    }


async def add_staff_role(category: str, role_id: int) -> bool:
    """Добавляет роль в указанную категорию персонала."""
    if category not in STAFF_CATEGORIES:
        return False
    cfg = await get_staff_roles_config()
    role_id = int(role_id)
    if role_id not in cfg[category]:
        cfg[category].append(role_id)
        await db.set("staff_roles", cfg)
        return True
    return False


async def remove_staff_role(category: str, role_id: int) -> bool:
    """Удаляет роль из указанной категории персонала."""
    if category not in STAFF_CATEGORIES:
        return False
    cfg = await get_staff_roles_config()
    role_id = int(role_id)
    if role_id in cfg[category]:
        cfg[category].remove(role_id)
        await db.set("staff_roles", cfg)
        return True
    return False


async def reset_staff_roles() -> dict[str, list[int]]:
    """Сбрасывает настроенные роли к значениям из config.py."""
    def_server = list(getattr(BotConfig, "STAFF_SERVER_ROLES", getattr(BotConfig, "STAFF_PING_ROLES", [])))
    def_discord = list(getattr(BotConfig, "STAFF_DISCORD_ROLES", getattr(BotConfig, "DS_ADMIN_PING_ROLES", [])))
    def_builder = list(getattr(BotConfig, "STAFF_BUILDER_ROLES", getattr(BotConfig, "BUILDER_PING_ROLES", [])))

    default_cfg = {
        "server":  [int(r) for r in def_server if r],
        "discord": [int(r) for r in def_discord if r],
        "builder": [int(r) for r in def_builder if r],
    }
    await db.set("staff_roles", default_cfg)
    return default_cfg


def get_member_categories_sync(member: discord.Member, roles_cfg: dict) -> list[str]:
    """Синхронно определяет категории персонала для участника."""
    user_role_ids = {r.id for r in member.roles}
    categories = []
    for cat_key in ("server", "discord", "builder"):
        target_ids = set(roles_cfg.get(cat_key, []))
        if user_role_ids & target_ids:
            categories.append(cat_key)

    # Если администратор, но специальных ролей нет — относим к серверу и дискорду
    if not categories and member.guild_permissions.administrator:
        categories.extend(["server", "discord"])

    return categories


async def get_member_categories(member: discord.Member) -> list[str]:
    """Асинхронно определяет категории персонала для участника."""
    roles_cfg = await get_staff_roles_config()
    return get_member_categories_sync(member, roles_cfg)


async def is_staff(member: discord.Member) -> bool:
    """Проверяет, является ли участник сотрудником персонала."""
    cats = await get_member_categories(member)
    return len(cats) > 0 or member.guild_permissions.administrator


# ─── ФОРМАТИРОВАНИЕ ДАННЫХ ───────────────────────────────────────────────────
def _get_empty_daily() -> dict:
    return {
        "tickets": 0,
        "punishments": 0,
        "punishments_by_action": {},
        "applications": 0,
        "apps_accepted": 0,
        "apps_rejected": 0,
        "voice_seconds": 0,
        "text_seconds": 0,
        "messages": 0,
    }


def _get_empty_user_data() -> dict:
    return {
        "total": {
            "tickets": 0,
            "punishments": 0,
            "punishments_by_action": {},
            "applications": 0,
            "apps_accepted": 0,
            "apps_rejected": 0,
            "voice_seconds": 0,
            "text_seconds": 0,
            "messages": 0,
        },
        "daily": {},
        "recent_actions": [],
        "last_seen": int(time.time()),
    }


def _prune_old_daily(data: dict, keep_days: int = 60):
    daily = data.get("daily", {})
    if len(daily) > keep_days:
        cutoff = (datetime.date.today() - datetime.timedelta(days=keep_days)).isoformat()
        old_keys = [k for k in daily.keys() if k < cutoff]
        for k in old_keys:
            daily.pop(k, None)


def format_time(seconds: int) -> str:
    """Форматирует секунды в читаемую строку (дни, часы, минуты)."""
    if seconds <= 0:
        return "0 мин."
    total_mins = int(seconds // 60)
    if total_mins < 60:
        return f"{total_mins} мин."
    hours = total_mins // 60
    mins = total_mins % 60
    if hours < 24:
        if mins > 0:
            return f"{hours} ч. {mins} мин."
        return f"{hours} ч."
    days = hours // 24
    rem_hours = hours % 24
    if rem_hours > 0:
        return f"{days} д. {rem_hours} ч."
    return f"{days} д."


def calculate_points(tickets: int, punishments: int, applications: int, active_seconds: int, messages: int = 0) -> int:
    """
    Формула баллов активности сотрудника:
    - 1 закрытый тикет: 5 баллов
    - 1 выданное наказание: 3 балла
    - 1 проверенная заявка: 4 балла
    - 1 час активного времени: 2 балла
    - 50 сообщений в чате: 1 балл
    """
    hours = max(0, active_seconds) // 3600
    pts = (tickets * 5) + (punishments * 3) + (applications * 4) + (hours * 2) + (messages // 50)
    return pts


# ─── ФИКСАЦИЯ ДЕЙСТВИЙ ───────────────────────────────────────────────────────
async def record_ticket_closed(guild_id: int, staff_id: int, channel_name: str, reason: str = ""):
    """Фиксирует закрытие тикета сотрудником."""
    now_ts = int(time.time())
    today_str = datetime.date.today().isoformat()
    data = await db.get(f"staff_stats.{staff_id}", None)
    if not data or not isinstance(data, dict):
        data = _get_empty_user_data()

    data.setdefault("total", {})
    data["total"]["tickets"] = data["total"].get("tickets", 0) + 1

    daily = data.setdefault("daily", {}).setdefault(today_str, _get_empty_daily())
    daily["tickets"] = daily.get("tickets", 0) + 1

    recent = data.setdefault("recent_actions", [])
    reason_part = f" — {reason[:40]}" if reason else ""
    recent.insert(0, {
        "ts": now_ts,
        "type": "ticket",
        "text": f"Закрыл тикет #{channel_name}{reason_part}"
    })
    data["recent_actions"] = recent[:12]
    data["last_seen"] = now_ts
    _prune_old_daily(data)
    await db.set(f"staff_stats.{staff_id}", data)


async def record_punishment_issued(guild_id: int, staff_id: int, action: str, target_id: int, duration_str: str = "", reason: str = ""):
    """Фиксирует выданное наказание сотрудником."""
    mark_bot_punishment(guild_id, target_id, action)

    now_ts = int(time.time())
    today_str = datetime.date.today().isoformat()
    data = await db.get(f"staff_stats.{staff_id}", None)
    if not data or not isinstance(data, dict):
        data = _get_empty_user_data()

    action_key = action.lower()
    data.setdefault("total", {})
    data["total"]["punishments"] = data["total"].get("punishments", 0) + 1
    by_act = data["total"].setdefault("punishments_by_action", {})
    by_act[action_key] = by_act.get(action_key, 0) + 1

    daily = data.setdefault("daily", {}).setdefault(today_str, _get_empty_daily())
    daily["punishments"] = daily.get("punishments", 0) + 1
    d_by_act = daily.setdefault("punishments_by_action", {})
    d_by_act[action_key] = d_by_act.get(action_key, 0) + 1

    action_names = {
        "mute": "Mute",
        "ban": "Ban",
        "kick": "Kick",
        "unmute": "Unmute",
        "unban": "Unban",
        "warn": "Warn",
    }
    act_label = action_names.get(action_key, action_key.capitalize())
    dur_part = f" ({duration_str})" if duration_str else ""
    recent = data.setdefault("recent_actions", [])
    recent.insert(0, {
        "ts": now_ts,
        "type": "punishment",
        "text": f"Выдал {act_label}{dur_part} игроку <@{target_id}>"
    })
    data["recent_actions"] = recent[:12]
    data["last_seen"] = now_ts
    _prune_old_daily(data)
    await db.set(f"staff_stats.{staff_id}", data)


async def record_application_reviewed(guild_id: int, staff_id: int, decision: str, app_type: str, applicant_id: int, reason: str = ""):
    """Фиксирует проверку заявки сотрудником."""
    now_ts = int(time.time())
    today_str = datetime.date.today().isoformat()
    data = await db.get(f"staff_stats.{staff_id}", None)
    if not data or not isinstance(data, dict):
        data = _get_empty_user_data()

    data.setdefault("total", {})
    data["total"]["applications"] = data["total"].get("applications", 0) + 1
    if decision == "accepted":
        data["total"]["apps_accepted"] = data["total"].get("apps_accepted", 0) + 1
    else:
        data["total"]["apps_rejected"] = data["total"].get("apps_rejected", 0) + 1

    daily = data.setdefault("daily", {}).setdefault(today_str, _get_empty_daily())
    daily["applications"] = daily.get("applications", 0) + 1
    if decision == "accepted":
        daily["apps_accepted"] = daily.get("apps_accepted", 0) + 1
    else:
        daily["apps_rejected"] = daily.get("apps_rejected", 0) + 1

    dec_word = "Принял" if decision == "accepted" else "Отклонил"
    recent = data.setdefault("recent_actions", [])
    recent.insert(0, {
        "ts": now_ts,
        "type": "application",
        "text": f"{dec_word} заявку [{app_type}] от <@{applicant_id}>"
    })
    data["recent_actions"] = recent[:12]
    data["last_seen"] = now_ts
    _prune_old_daily(data)
    await db.set(f"staff_stats.{staff_id}", data)


async def record_voice_time(staff_id: int, seconds: int):
    """Добавляет время в войсе к статистике сотрудника."""
    if seconds <= 0:
        return
    now_ts = int(time.time())
    today_str = datetime.date.today().isoformat()
    data = await db.get(f"staff_stats.{staff_id}", None)
    if not data or not isinstance(data, dict):
        data = _get_empty_user_data()

    data.setdefault("total", {})
    data["total"]["voice_seconds"] = data["total"].get("voice_seconds", 0) + seconds

    daily = data.setdefault("daily", {}).setdefault(today_str, _get_empty_daily())
    daily["voice_seconds"] = daily.get("voice_seconds", 0) + seconds
    data["last_seen"] = now_ts
    _prune_old_daily(data)
    await db.set(f"staff_stats.{staff_id}", data)


async def record_text_activity(staff_id: int, seconds: int, messages_count: int = 1):
    """Добавляет текстовую активность и сообщения к статистике сотрудника."""
    now_ts = int(time.time())
    today_str = datetime.date.today().isoformat()
    data = await db.get(f"staff_stats.{staff_id}", None)
    if not data or not isinstance(data, dict):
        data = _get_empty_user_data()

    data.setdefault("total", {})
    data["total"]["text_seconds"] = data["total"].get("text_seconds", 0) + max(0, seconds)
    data["total"]["messages"] = data["total"].get("messages", 0) + messages_count

    daily = data.setdefault("daily", {}).setdefault(today_str, _get_empty_daily())
    daily["text_seconds"] = daily.get("text_seconds", 0) + max(0, seconds)
    daily["messages"] = daily.get("messages", 0) + messages_count
    data["last_seen"] = now_ts
    _prune_old_daily(data)
    await db.set(f"staff_stats.{staff_id}", data)


# ─── ПОЛУЧЕНИЕ СТАТИСТИКИ ────────────────────────────────────────────────────
async def get_staff_stats(staff_id: int, live_voice_seconds: int = 0) -> dict:
    """
    Возвращает статистику сотрудника:
    - 'weekly': статистика за последние 7 дней (+ live voice)
    - 'all_time': статистика за всё время (+ live voice)
    - 'recent_actions': список последних действий
    - 'last_seen': временная метка последней активности
    """
    data = await db.get(f"staff_stats.{staff_id}", None)
    if not data or not isinstance(data, dict):
        data = _get_empty_user_data()

    today = datetime.date.today()
    week_days = set((today - datetime.timedelta(days=i)).isoformat() for i in range(7))

    w_tickets = 0
    w_punishments = 0
    w_punishments_by_action = {}
    w_applications = 0
    w_apps_accepted = 0
    w_apps_rejected = 0
    w_voice_seconds = live_voice_seconds
    w_text_seconds = 0
    w_messages = 0

    daily = data.get("daily", {})
    for day_str, day_data in daily.items():
        if day_str in week_days and isinstance(day_data, dict):
            w_tickets += day_data.get("tickets", 0)
            w_punishments += day_data.get("punishments", 0)
            for act, cnt in day_data.get("punishments_by_action", {}).items():
                w_punishments_by_action[act] = w_punishments_by_action.get(act, 0) + cnt
            w_applications += day_data.get("applications", 0)
            w_apps_accepted += day_data.get("apps_accepted", 0)
            w_apps_rejected += day_data.get("apps_rejected", 0)
            w_voice_seconds += day_data.get("voice_seconds", 0)
            w_text_seconds += day_data.get("text_seconds", 0)
            w_messages += day_data.get("messages", 0)

    total = data.get("total", {})
    t_voice = total.get("voice_seconds", 0) + live_voice_seconds
    t_text = total.get("text_seconds", 0)

    w_points = calculate_points(w_tickets, w_punishments, w_applications, w_voice_seconds + w_text_seconds, w_messages)
    t_points = calculate_points(
        total.get("tickets", 0),
        total.get("punishments", 0),
        total.get("applications", 0),
        t_voice + t_text,
        total.get("messages", 0)
    )

    return {
        "weekly": {
            "tickets": w_tickets,
            "punishments": w_punishments,
            "punishments_by_action": w_punishments_by_action,
            "applications": w_applications,
            "apps_accepted": w_apps_accepted,
            "apps_rejected": w_apps_rejected,
            "voice_seconds": w_voice_seconds,
            "text_seconds": w_text_seconds,
            "total_active_seconds": w_voice_seconds + w_text_seconds,
            "messages": w_messages,
            "points": w_points,
        },
        "all_time": {
            "tickets": total.get("tickets", 0),
            "punishments": total.get("punishments", 0),
            "punishments_by_action": total.get("punishments_by_action", {}),
            "applications": total.get("applications", 0),
            "apps_accepted": total.get("apps_accepted", 0),
            "apps_rejected": total.get("apps_rejected", 0),
            "voice_seconds": t_voice,
            "text_seconds": t_text,
            "total_active_seconds": t_voice + t_text,
            "messages": total.get("messages", 0),
            "points": t_points,
        },
        "recent_actions": data.get("recent_actions", []),
        "last_seen": data.get("last_seen"),
    }


# ─── СБРОС СТАТИСТИКИ ────────────────────────────────────────────────────────
async def reset_weekly_stats():
    """Сбрасывает недельную статистику для всех сотрудников (очищает ежедневные записи)."""
    all_stats = await db.get("staff_stats", {})
    if isinstance(all_stats, dict):
        for uid, udata in all_stats.items():
            if isinstance(udata, dict):
                udata["daily"] = {}
        await db.set("staff_stats", all_stats)


async def reset_user_stats(user_id: int):
    """Сбрасывает всю статистику конкретного сотрудника."""
    await db.delete(f"staff_stats.{user_id}")


async def reset_all_staff_stats():
    """Сбрасывает всю статистику всех сотрудников."""
    await db.delete("staff_stats")
    await db.delete("staff_stats_reset")
