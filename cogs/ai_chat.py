"""
🧠 ИИ ЧАТ — Мультипровайдер с памятью (оптимизирован для экономии токенов)

═══════════════════════════════════════════════════════════════════
  БЕСПЛАТНЫЕ ПРОВАЙДЕРЫ (рекомендуются):

  1. Groq + Llama 3.3 70B            ← ЛУЧШИЙ ВЫБОР
     console.groq.com → API Keys
     Лимит: 6000 req/day, 500k токенов/день — ОЧЕНЬ много
     Модель: llama-3.3-70b-versatile  (умная, быстрая)
     Модель: llama-3.1-8b-instant     (если нужно ещё экономнее)

  2. Google Gemini 2.0 Flash          ← текущий вариант
     aistudio.google.com → API Keys
     Лимит: 1500 req/day (gemini-2.0-flash)
     Лимит: 250 req/day  (gemini-2.5-flash — только для него мало)

  3. Mistral (mistral.ai)
     Лимит: 1 req/сек, ~1M токенов/месяц бесплатно
     Модель: mistral-small-latest

═══════════════════════════════════════════════════════════════════
  КАК ЭКОНОМИТЬ ТОКЕНЫ (уже реализовано):
  • История на ИГРОКА, не на канал (разные игроки не раздувают контекст)
  • Только 5 пар сообщений в памяти (было 20)
  • Ответ ≤ 120 токенов (было 400)
  • Сообщение игрока обрезается до 350 символов
  • Кулдаун 20 сек между /ии от одного игрока
  • Системный промпт минимальный, инфо об игроке — одна строка
═══════════════════════════════════════════════════════════════════

Команды:
  /ии <сообщение>             — написать ИИ (любой канал)
  /ии-настройка провайдер     — выбрать провайдер + ключ
  /ии-настройка персонаж      — изменить характер/промпт
  /ии-настройка лимит         — лимит /ии в час на игрока
  /ии-настройка кулдаун       — задержка между сообщениями (сек)
  /ии-настройка макс-токены   — максимум токенов в ответе
  /ии-настройка история       — кол-во пар сообщений в памяти
  /ии-настройка вкл/выкл      — включить/выключить
  /ии-настройка статус        — текущие настройки + статистика токенов
  /ии-сброс                   — сбросить историю своего разговора
  /ии-сброс-игрок             — [АДМИН] сбросить историю конкретного игрока
  /ии-стат                    — статистика использования
"""

import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import datetime
import json
import asyncio
import urllib.request
import urllib.error
from typing import Optional

from database import db
from texts import T
from config import BotConfig

# ─── Настройки по умолчанию (можно менять через команды) ─────────────────────
MAX_HISTORY_DEFAULT   = 5     # Пар сообщений на игрока (было 20 на канал!)
MAX_TOKENS_DEFAULT    = 120   # Токенов в ответе (было 400)
MSG_TRUNCATE          = 350   # Обрезаем входящие сообщения до N символов
CMD_COOLDOWN_DEFAULT  = 20    # Сек между /ии от одного игрока (0 = без кулдауна)
DEFAULT_HOUR_LIMIT    = 10    # /ии в час на игрока

# ─── Прокси ──────────────────────────────────────────────────────────────────
async def get_proxy() -> str:
    """
    Возвращает адрес прокси.
    Приоритет: БД (настройка через /ии-настройка прокси) → config.py → без прокси.
    """
    stored = await db.get("ai_config.proxy", None)
    if stored is not None:
        return stored  # может быть "" (явно отключён) или "http://..."
    return BotConfig.AI_PROXY  # из config.py


# ─── Провайдеры ──────────────────────────────────────────────────────────────
PROVIDERS = {
    "groq": {
        "name":          "⚡ Groq (ЛУЧШИЙ БЕСПЛАТНЫЙ)",
        "url":           "https://api.groq.com/openai/v1/chat/completions",
        "default_model": "llama-3.1-8b-instant",
        "auth_header":   "Authorization",
        "auth_prefix":   "Bearer ",
        "free_note":     "14 400 req/day на llama-3.1-8b-instant — console.groq.com",
    },
    "gemini": {
        "name":          "💎 Google Gemini",
        "default_model": "gemini-2.0-flash",
        "free_note":     "1500 req/day (2.0-flash). 250 req/day (2.5-flash) — aistudio.google.com",
    },
    "mistral": {
        "name":          "🌬️ Mistral AI (бесплатно)",
        "url":           "https://api.mistral.ai/v1/chat/completions",
        "default_model": "mistral-small-latest",
        "auth_header":   "Authorization",
        "auth_prefix":   "Bearer ",
        "free_note":     "~1M токенов/мес бесплатно — console.mistral.ai",
    },
    "openai": {
        "name":          "🤖 OpenAI (ChatGPT)",
        "url":           "https://api.openai.com/v1/chat/completions",
        "default_model": "gpt-4o-mini",
        "auth_header":   "Authorization",
        "auth_prefix":   "Bearer ",
    },
    "anthropic": {
        "name":          "🧠 Anthropic (Claude)",
        "url":           "https://api.anthropic.com/v1/messages",
        "default_model": "claude-haiku-4-5-20251001",
        "auth_header":   "x-api-key",
        "auth_prefix":   "",
    },
    "custom": {
        "name":          "🔧 Свой API (OpenAI-совместимый)",
        "url":           "",
        "default_model": "gpt-3.5-turbo",
        "auth_header":   "Authorization",
        "auth_prefix":   "Bearer ",
    },
}

DEFAULT_SYSTEM = T.AI_DEFAULT_SYSTEM


# ─── Вызовы API ──────────────────────────────────────────────────────────────
async def _call_openai_compat(url, api_key, model, system, history, auth_header, auth_prefix, max_tokens,
                               proxy: str = "") -> str:
    import ssl as ssl_module
    api_key  = api_key.strip()
    messages = [{"role": "system", "content": system}] + history
    headers  = {
        auth_header:    f"{auth_prefix}{api_key}",
        "Content-Type": "application/json",
    }
    payload   = {"model": model, "messages": messages, "max_tokens": max_tokens}
    # ssl=False отключает проверку сертификата и для целевого хоста и для прокси
    ssl_ctx   = ssl_module.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode    = ssl_module.CERT_NONE
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    try:
        async with aiohttp.ClientSession(connector=connector) as s:
            kwargs = {
                "headers": headers,
                "json":    payload,
                "timeout": aiohttp.ClientTimeout(total=20),
                "ssl":     False,  # отключаем SSL и для самого запроса
            }
            if proxy:
                kwargs["proxy"]     = proxy
                kwargs["proxy_headers"] = {}  # пустые proxy headers чтобы не было auth issues
            async with s.post(url, **kwargs) as r:
                if r.status != 200:
                    text = await r.text()
                    return f"⚠️ Ошибка API ({r.status}): {text[:300]}"
                data = await r.json()
                return data["choices"][0]["message"]["content"].strip()
    finally:
        await connector.close()


async def _call_anthropic(api_key, model, system, history, max_tokens, proxy: str = "") -> str:
    headers   = {
        "x-api-key":         api_key.strip(),
        "anthropic-version": "2023-06-01",
        "Content-Type":      "application/json",
    }
    payload   = {"model": model, "max_tokens": max_tokens, "system": system, "messages": history}
    connector = aiohttp.TCPConnector(ssl=False)
    try:
        async with aiohttp.ClientSession(connector=connector) as s:
            kwargs = {"headers": headers, "json": payload, "timeout": aiohttp.ClientTimeout(total=20), "ssl": False}
            if proxy:
                kwargs["proxy"] = proxy
            async with s.post("https://api.anthropic.com/v1/messages", **kwargs) as r:
                if r.status != 200:
                    return f"⚠️ Ошибка API ({r.status}): {(await r.text())[:200]}"
                data = await r.json()
                return data["content"][0]["text"].strip()
    finally:
        await connector.close()


async def _call_gemini(api_key, model, system, history, max_tokens, proxy: str = "") -> str:
    contents  = [{"role": "user" if m["role"] == "user" else "model",
                  "parts": [{"text": m["content"]}]} for m in history]
    payload   = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents":           contents,
        "generationConfig":   {"maxOutputTokens": max_tokens},
    }
    url       = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key.strip()}"
    connector = aiohttp.TCPConnector(ssl=False)
    try:
        async with aiohttp.ClientSession(connector=connector) as s:
            kwargs = {"json": payload, "timeout": aiohttp.ClientTimeout(total=20), "ssl": False}
            if proxy:
                kwargs["proxy"] = proxy
            async with s.post(url, **kwargs) as r:
                if r.status != 200:
                    return f"⚠️ Ошибка API ({r.status}): {(await r.text())[:200]}"
                data = await r.json()
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    finally:
        await connector.close()


# ─── Fallback через urllib (если aiohttp блокируется) ────────────────────────
async def _call_urllib_fallback(url: str, api_key: str, model: str, system: str,
                                 history: list, max_tokens: int,
                                 auth_header: str = "Authorization",
                                 auth_prefix: str = "Bearer ") -> str:
    """Резервный вызов через urllib — работает там, где aiohttp блокируется."""
    messages = [{"role": "system", "content": system}] + history
    payload  = json.dumps({
        "model": model, "messages": messages, "max_tokens": max_tokens
    }).encode("utf-8")
    headers  = {
        auth_header:    f"{auth_prefix}{api_key}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        loop = asyncio.get_event_loop()
        def _do():
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        data = await loop.run_in_executor(None, _do)
        return data["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        return f"⚠️ Ошибка API ({e.code}): {body}"
    except Exception as e:
        return f"⚠️ Ошибка: {e}"


# ─── История (PER-USER, не per-channel!) ─────────────────────────────────────
async def load_user_history(user_id: int, max_pairs: int) -> list:
    history = await db.get(f"ai_history_u.{user_id}", [])
    # Обрезаем до нужного размера
    max_msgs = max_pairs * 2
    return history[-max_msgs:] if len(history) > max_msgs else history


async def save_user_history(user_id: int, history: list, max_pairs: int):
    max_msgs = max_pairs * 2
    if len(history) > max_msgs:
        history = history[-max_msgs:]
    await db.set(f"ai_history_u.{user_id}", history)


# ─── Профиль игрока ───────────────────────────────────────────────────────────
async def update_player_profile(member: discord.Member):
    profile = {
        "name":    member.name,
        "nick":    member.display_name,
        "roles":   [r.name for r in member.roles if r.name != "@everyone"][-3:],
        "updated": datetime.datetime.utcnow().isoformat(),
    }
    await db.set(f"ai_player.{member.id}", profile)
    return profile


def build_compact_system(base: str, profile: dict) -> str:
    """Минимальный системный промпт с инфо об игроке — экономит токены."""
    roles = ", ".join(profile.get("roles", [])) or "—"
    nick  = profile.get("nick", profile.get("name", "?"))
    # Одна строка вместо многословного блока
    return f"{base}\n[Собеседник: {nick}, роли: {roles}]"


# ─── COG ─────────────────────────────────────────────────────────────────────
class AIChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot       = bot
        # Кулдаун: user_id -> timestamp последнего /ии
        self._last_use: dict[int, float] = {}

    # ─── Кулдаун между /ии ───────────────────────────────────────────────────
    def _check_cmd_cooldown(self, user_id: int, cooldown_secs: int) -> float:
        """Возвращает 0 если можно, иначе — сколько секунд ждать."""
        if cooldown_secs <= 0:
            return 0
        now  = datetime.datetime.utcnow().timestamp()
        last = self._last_use.get(user_id, 0)
        wait = cooldown_secs - (now - last)
        return max(0.0, wait)

    # ─── Лимит в час ─────────────────────────────────────────────────────────
    async def _check_hour_limit(self, user_id: int) -> tuple:
        limit = await db.get("ai_config.hour_limit", DEFAULT_HOUR_LIMIT)
        hour  = datetime.datetime.utcnow().strftime("%Y-%m-%d-%H")
        key   = f"ai_usage.{user_id}.{hour}"
        count = await db.get(key, 0)
        if count >= limit:
            return False, 0
        await db.set(key, count + 1)
        return True, limit - count - 1

    # ─── Генерация ───────────────────────────────────────────────────────────
    async def _generate(self, user_text: str, member: discord.Member) -> str:
        cfg = await db.get("ai_config", {})

        if cfg.get("enabled") is False:
            return "❌ ИИ-чат отключён администратором."

        provider = cfg.get("provider", "")
        api_key  = cfg.get("api_key", "")
        if not provider or not api_key:
            return T.AI_NOT_CONFIGURED

        prov_cfg   = PROVIDERS.get(provider, PROVIDERS["custom"])
        model      = cfg.get("model", prov_cfg["default_model"])
        max_tokens = cfg.get("max_tokens", MAX_TOKENS_DEFAULT)
        max_pairs  = cfg.get("max_history", MAX_HISTORY_DEFAULT)
        base_sys   = cfg.get("system", DEFAULT_SYSTEM)

        # Обновляем профиль и строим компактный системный промпт
        profile    = await update_player_profile(member)
        system     = build_compact_system(base_sys, profile)

        # История PER-USER
        history    = await load_user_history(member.id, max_pairs)

        # Обрезаем входящее сообщение
        user_text_trimmed = user_text[:MSG_TRUNCATE]
        history.append({"role": "user", "content": user_text_trimmed})

        proxy = await get_proxy()

        try:
            if provider == "anthropic":
                reply = await _call_anthropic(api_key, model, system, history, max_tokens, proxy)
            elif provider == "gemini":
                reply = await _call_gemini(api_key, model, system, history, max_tokens, proxy)
            else:
                url         = cfg.get("custom_url", "") if provider == "custom" else prov_cfg["url"]
                auth_header = prov_cfg.get("auth_header", "Authorization")
                auth_prefix = prov_cfg.get("auth_prefix", "Bearer ")
                reply = await _call_openai_compat(
                    url, api_key, model, system, history, auth_header, auth_prefix, max_tokens, proxy
                )
                # Если aiohttp дал 403 — пробуем urllib fallback (без прокси — прямой запрос)
                if reply.startswith("⚠️ Ошибка API (403)") and not proxy:
                    reply = await _call_urllib_fallback(
                        url, api_key, model, system, history, max_tokens, auth_header, auth_prefix
                    )
        except Exception as e:
            return f"⚠️ Ошибка соединения: {e}"

        history.append({"role": "assistant", "content": reply})
        await save_user_history(member.id, history, max_pairs)
        return reply

    # ─── /ии ─────────────────────────────────────────────────────────────────
    @app_commands.command(name="ии", description="🤖 Написать ИИ-боту (работает в любом канале)")
    @app_commands.describe(сообщение="Твоё сообщение")
    async def ask_ai(self, interaction: discord.Interaction, сообщение: str):
        cfg      = await db.get("ai_config", {})
        cooldown = cfg.get("cmd_cooldown", CMD_COOLDOWN_DEFAULT)
        wait     = self._check_cmd_cooldown(interaction.user.id, cooldown)

        if wait > 0:
            await interaction.response.send_message(
                f"⏳ Подожди ещё **{wait:.0f} сек** перед следующим сообщением.",
                ephemeral=True,
            )
            return

        allowed, remaining = await self._check_hour_limit(interaction.user.id)
        if not allowed:
            limit = cfg.get("hour_limit", DEFAULT_HOUR_LIMIT)
            await interaction.response.send_message(
                T.AI_LIMIT_MSG.format(limit=limit), ephemeral=True
            )
            return

        self._last_use[interaction.user.id] = datetime.datetime.utcnow().timestamp()
        await interaction.response.defer()

        reply  = await self._generate(сообщение, interaction.user)
        output = f"**{interaction.user.display_name}:** {сообщение[:200]}\n{reply}"

        try:
            await interaction.followup.send(output[:2000])
        except discord.HTTPException:
            pass

    # ─── Обновляем профиль при смене ролей ───────────────────────────────────
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles != after.roles:
            await update_player_profile(after)

    # ─── /ии-настройка ───────────────────────────────────────────────────────
    ai_setup = app_commands.Group(
        name="ии-настройка",
        description="⚙️ Настройка ИИ-чата",
        default_permissions=discord.Permissions(administrator=True),
    )

    @ai_setup.command(name="провайдер", description="🔑 Выбрать провайдер ИИ и ввести ключ")
    @app_commands.describe(
        провайдер="Провайдер (Groq — лучший бесплатный!)",
        ключ="API-ключ",
        модель="Конкретная модель (необязательно)",
        своя_ссылка="URL для кастомного API",
    )
    @app_commands.choices(провайдер=[
        app_commands.Choice(name="⚡ Groq — ЛУЧШИЙ БЕСПЛАТНЫЙ (14 400 req/day, llama-3.1-8b)", value="groq"),
        app_commands.Choice(name="💎 Google Gemini — 1500 req/day (2.0-flash)",          value="gemini"),
        app_commands.Choice(name="🌬️ Mistral AI    — ~1M токенов/мес бесплатно",        value="mistral"),
        app_commands.Choice(name="🤖 OpenAI ChatGPT",                                    value="openai"),
        app_commands.Choice(name="🧠 Anthropic Claude",                                  value="anthropic"),
        app_commands.Choice(name="🔧 Свой API (OpenAI-совместимый)",                     value="custom"),
    ])
    async def set_provider(self, interaction: discord.Interaction, провайдер: str, ключ: str,
                           модель: Optional[str] = None, своя_ссылка: Optional[str] = None):
        cfg             = await db.get("ai_config", {})
        cfg["provider"] = провайдер
        cfg["api_key"]  = ключ.strip()
        cfg["model"]    = модель or PROVIDERS[провайдер]["default_model"]
        if провайдер == "custom" and своя_ссылка:
            cfg["custom_url"] = своя_ссылка
        await db.set("ai_config", cfg)
        prov = PROVIDERS[провайдер]
        note = prov.get("free_note", "")
        await interaction.response.send_message(
            f"✅ Провайдер: **{prov['name']}**\nМодель: `{cfg['model']}`\nКлюч сохранён."
            + (f"\nℹ️ {note}" if note else ""),
            ephemeral=True,
        )

    @ai_setup.command(name="персонаж", description="🎭 Изменить характер/промпт ИИ")
    async def set_system(self, interaction: discord.Interaction, промпт: str):
        cfg = await db.get("ai_config", {})
        cfg["system"] = промпт
        await db.set("ai_config", cfg)
        await interaction.response.send_message("✅ Промпт обновлён.", ephemeral=True)

    @ai_setup.command(name="лимит", description="⏱️ Лимит команды /ии в час на игрока")
    async def set_limit(self, interaction: discord.Interaction,
                        сообщений_в_час: app_commands.Range[int, 1, 500]):
        cfg = await db.get("ai_config", {})
        cfg["hour_limit"] = сообщений_в_час
        await db.set("ai_config", cfg)
        await interaction.response.send_message(
            f"✅ Лимит: **{сообщений_в_час}** /ии в час на игрока.", ephemeral=True
        )

    @ai_setup.command(name="кулдаун", description="⏲️ Задержка между /ии одного игрока (секунд)")
    @app_commands.describe(секунд="0 = без задержки, 20 = рекомендуется")
    async def set_cooldown(self, interaction: discord.Interaction,
                           секунд: app_commands.Range[int, 0, 300]):
        cfg = await db.get("ai_config", {})
        cfg["cmd_cooldown"] = секунд
        await db.set("ai_config", cfg)
        msg = f"✅ Кулдаун между /ии: **{секунд} сек**." if секунд > 0 else "✅ Кулдаун **отключён**."
        await interaction.response.send_message(msg, ephemeral=True)

    @ai_setup.command(name="макс-токены", description="📏 Максимум токенов в ответе ИИ (экономия!)")
    @app_commands.describe(токенов="80-120 = коротко и экономно, 200-300 = развёрнуто")
    async def set_max_tokens(self, interaction: discord.Interaction,
                             токенов: app_commands.Range[int, 50, 500]):
        cfg = await db.get("ai_config", {})
        cfg["max_tokens"] = токенов
        await db.set("ai_config", cfg)
        tip = " (рекомендуется)" if токенов <= 150 else " (расход токенов растёт!)" if токенов > 250 else ""
        await interaction.response.send_message(
            f"✅ Макс. токенов в ответе: **{токенов}**{tip}", ephemeral=True
        )

    @ai_setup.command(name="история", description="🗂️ Кол-во пар сообщений в памяти на игрока")
    @app_commands.describe(пар="3-5 = экономно, 10+ = помнит больше но тратит токены")
    async def set_history(self, interaction: discord.Interaction,
                          пар: app_commands.Range[int, 1, 20]):
        cfg = await db.get("ai_config", {})
        cfg["max_history"] = пар
        await db.set("ai_config", cfg)
        tip = " (рекомендуется)" if пар <= 5 else " (больше токенов!)" if пар > 8 else ""
        await interaction.response.send_message(
            f"✅ История: **{пар} пар** сообщений на игрока{tip}", ephemeral=True
        )

    @ai_setup.command(name="прокси", description="🔒 Настроить HTTP прокси (Xray/VLESS/SOCKS5)")
    @app_commands.describe(
        адрес="Адрес прокси, например: http://127.0.0.1:10808 | Оставь пустым чтобы отключить",
    )
    async def set_proxy(self, interaction: discord.Interaction, адрес: str = ""):
        cfg = await db.get("ai_config", {})
        cfg["proxy"] = адрес.strip()
        await db.set("ai_config", cfg)
        if адрес.strip():
            await interaction.response.send_message(
                f"✅ Прокси установлен: `{адрес.strip()}`\n"
                f"Все запросы к ИИ идут через него.\n"
                f"Проверь: `/ии-тест`",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "✅ Прокси **отключён**. Прямое подключение.",
                ephemeral=True,
            )

    @ai_setup.command(name="вкл", description="✅ Включить ИИ-чат")
    async def enable(self, interaction: discord.Interaction):
        cfg = await db.get("ai_config", {}); cfg["enabled"] = True; await db.set("ai_config", cfg)
        await interaction.response.send_message("✅ ИИ-чат включён. /ии доступна во всех каналах.", ephemeral=True)

    @ai_setup.command(name="выкл", description="🔕 Выключить ИИ-чат")
    async def disable(self, interaction: discord.Interaction):
        cfg = await db.get("ai_config", {}); cfg["enabled"] = False; await db.set("ai_config", cfg)
        await interaction.response.send_message("✅ ИИ-чат отключён.", ephemeral=True)

    @ai_setup.command(name="статус", description="📋 Настройки + советы по экономии токенов")
    async def status(self, interaction: discord.Interaction):
        cfg        = await db.get("ai_config", {})
        prov_name  = PROVIDERS.get(cfg.get("provider", ""), {}).get("name", "не настроен")
        enabled    = cfg.get("enabled", True)
        max_tok    = cfg.get("max_tokens",  MAX_TOKENS_DEFAULT)
        max_hist   = cfg.get("max_history", MAX_HISTORY_DEFAULT)
        cooldown   = cfg.get("cmd_cooldown", CMD_COOLDOWN_DEFAULT)
        hour_lim   = cfg.get("hour_limit",  DEFAULT_HOUR_LIMIT)

        # Оцениваем ежедневный расход
        # ~(sys ~100) + (история ~avg 60 tok * max_hist*2) + (input ~80) + (output max_tok)
        est_per_req = 100 + (60 * max_hist * 2) + 80 + max_tok
        # Если провайдер groq: 500k/day, gemini 2.0-flash: ~1500 req/day
        if "groq" in cfg.get("provider", ""):
            days_500k = 500_000 // est_per_req
            capacity  = f"≈ {days_500k} запросов из 6000/day (Groq лимит по запросам)"
        elif "gemini" in cfg.get("provider", ""):
            capacity = f"≈ 1500 req/day (Gemini 2.0-flash)"
        else:
            capacity = "зависит от провайдера"

        embed = discord.Embed(
            title="🧠 Настройки ИИ-чата",
            description="История хранится **отдельно для каждого игрока**.",
            color=0x5865f2,
        )
        proxy_val = cfg.get("proxy", None)
        if proxy_val is None:
            proxy_val = BotConfig.AI_PROXY
        proxy_display = f"`{proxy_val}`" if proxy_val else "❌ Не настроен (прямое подключение)"

        embed.add_field(name="Статус",         value="✅ Вкл" if enabled else "❌ Выкл",         inline=True)
        embed.add_field(name="Провайдер",       value=prov_name,                                  inline=True)
        embed.add_field(name="Модель",          value=f"`{cfg.get('model','—')}`",                inline=True)
        embed.add_field(name="Макс. токены",    value=f"{max_tok} (ответ)",                       inline=True)
        embed.add_field(name="История",         value=f"{max_hist} пар/игрок",                   inline=True)
        embed.add_field(name="Кулдаун /ии",     value=f"{cooldown} сек",                          inline=True)
        embed.add_field(name="Лимит /ии",       value=f"{hour_lim}/час/игрок",                   inline=True)
        embed.add_field(name="API ключ",        value="✅ Задан" if cfg.get("api_key") else "❌", inline=True)
        embed.add_field(name="Прокси",          value=proxy_display,                              inline=False)
        embed.add_field(name="~Токенов/запрос", value=str(est_per_req),                           inline=True)
        embed.add_field(name="Ёмкость",         value=capacity,                                   inline=False)

        # Советы
        tips = []
        if max_tok > 150:   tips.append(f"💡 Снизь макс-токены до 120: `/ии-настройка макс-токены 120`")
        if max_hist > 5:    tips.append(f"💡 Снизь историю до 5 пар: `/ии-настройка история 5`")
        if cooldown < 15:   tips.append(f"💡 Поставь кулдаун 20 сек: `/ии-настройка кулдаун 20`")
        if hour_lim > 15:   tips.append(f"💡 Снизь лимит до 10/час: `/ии-настройка лимит 10`")
        if "gemini-2.5" in cfg.get("model", ""):
            tips.append("⚠️ gemini-2.5-flash: 250 req/day. Попробуй `gemini-2.0-flash` (1500/day) или Groq `llama-3.1-8b-instant` (14 400/day!)")
        if "groq" in cfg.get("provider","") and "70b" in cfg.get("model",""):
            tips.append("💡 Groq llama-3.3-70b-versatile: только 1000 req/day. Смени на `llama-3.1-8b-instant` (14 400/day): `/ии-настройка провайдер`")
        if tips:
            embed.add_field(name="💡 Советы по экономии", value="\n".join(tips), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ─── /ии-сброс ───────────────────────────────────────────────────────────
    @app_commands.command(name="ии-сброс", description="🔄 Сбросить историю своего разговора с ИИ")
    async def reset_my_history(self, interaction: discord.Interaction):
        await db.delete(f"ai_history_u.{interaction.user.id}")
        await interaction.response.send_message("✅ Твоя история разговора с ИИ сброшена.", ephemeral=True)

    @app_commands.command(name="ии-сброс-игрок", description="🧹 [АДМИН] Сбросить историю конкретного игрока")
    @app_commands.default_permissions(administrator=True)
    async def reset_player(self, interaction: discord.Interaction, игрок: discord.Member):
        await db.delete(f"ai_history_u.{игрок.id}")
        await db.delete(f"ai_player.{игрок.id}")
        await interaction.response.send_message(
            f"✅ История и профиль {игрок.mention} удалены.", ephemeral=True
        )

    # ─── /ии-стат ────────────────────────────────────────────────────────────
    @app_commands.command(name="ии-стат", description="📊 Статистика использования /ии")
    @app_commands.default_permissions(administrator=True)
    async def ai_stat(self, interaction: discord.Interaction):
        all_data   = await db.get("ai_usage", {})
        now        = datetime.datetime.utcnow()
        hour_key   = now.strftime("%Y-%m-%d-%H")
        day_pfx    = now.strftime("%Y-%m-%d")
        hour_total = day_total = 0
        user_hour: dict = {}

        for uid, hours in all_data.items():
            for hk, cnt in hours.items():
                if hk == hour_key:
                    hour_total += cnt
                    user_hour[uid] = user_hour.get(uid, 0) + cnt
                if hk.startswith(day_pfx):
                    day_total += cnt

        top   = sorted(user_hour.items(), key=lambda x: x[1], reverse=True)[:5]
        lines = []
        for uid, cnt in top:
            m    = interaction.guild.get_member(int(uid))
            name = m.display_name if m else f"<@{uid}>"
            lines.append(f"• {name}: {cnt}")

        embed = discord.Embed(title="📊 Статистика /ии", color=0x2ecc71, timestamp=discord.utils.utcnow())
        embed.add_field(name="Этот час", value=str(hour_total), inline=True)
        embed.add_field(name="Сегодня",  value=str(day_total),  inline=True)
        if lines:
            embed.add_field(name="Топ за час", value="\n".join(lines), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


    # ─── /ии-модели ──────────────────────────────────────────────────────────
    @app_commands.command(name="ии-модели", description="📋 Получить список доступных моделей у текущего провайдера")
    @app_commands.default_permissions(administrator=True)
    async def list_models(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        cfg      = await db.get("ai_config", {})
        provider = cfg.get("provider", "")
        api_key  = cfg.get("api_key", "")

        if not provider or not api_key:
            await interaction.followup.send(T.AI_NOT_CONFIGURED, ephemeral=True)
            return

        models = []
        error  = ""

        try:
            async with aiohttp.ClientSession() as s:

                if provider in ("groq", "openai", "custom", "mistral"):
                    urls = {
                        "groq":    "https://api.groq.com/openai/v1/models",
                        "openai":  "https://api.openai.com/v1/models",
                        "mistral": "https://api.mistral.ai/v1/models",
                        "custom":  cfg.get("custom_url", "").replace(
                            "/chat/completions", "/models"
                        ),
                    }
                    url = urls.get(provider, "")
                    if not url:
                        error = "Для кастомного API укажи URL в настройках."
                    else:
                        async with s.get(
                            url,
                            headers={
                                "Authorization": f"Bearer {api_key}",
                                "User-Agent": "Mozilla/5.0 (compatible; DiscordBot/1.0)",
                            },
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as r:
                            if r.status == 200:
                                data   = await r.json()
                                raw    = data.get("data", data.get("models", []))
                                models = sorted(
                                    [m.get("id", m.get("name", "?")) for m in raw]
                                )
                            else:
                                error = f"Ошибка {r.status}: {(await r.text())[:300]}"

                elif provider == "anthropic":
                    async with s.get(
                        "https://api.anthropic.com/v1/models",
                        headers={
                            "x-api-key":         api_key,
                            "anthropic-version": "2023-06-01",
                        },
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as r:
                        if r.status == 200:
                            data   = await r.json()
                            models = [m.get("id", "?") for m in data.get("data", [])]
                        else:
                            error = f"Ошибка {r.status}: {(await r.text())[:300]}"

                elif provider == "gemini":
                    async with s.get(
                        f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as r:
                        if r.status == 200:
                            data   = await r.json()
                            models = [
                                m["name"].replace("models/", "")
                                for m in data.get("models", [])
                                if "generateContent" in m.get("supportedGenerationMethods", [])
                            ]
                        else:
                            error = f"Ошибка {r.status}: {(await r.text())[:300]}"

        except Exception as e:
            error = str(e)

        if error:
            await interaction.followup.send(
                f"❌ Не удалось получить список моделей:\n```{error}```\n"
                f"Возможные причины:\n"
                f"• Неверный API ключ\n"
                f"• Ключ не активирован (Groq: подтверди email)\n"
                f"• Нет доступа к интернету с хостинга бота",
                ephemeral=True,
            )
            return

        if not models:
            await interaction.followup.send("📭 Список моделей пуст.", ephemeral=True)
            return

        # Текущая модель
        current = cfg.get("model", "—")

        # Разбиваем на чанки по 20 моделей (Discord лимит поля — 1024 символа)
        chunks     = [models[i:i+20] for i in range(0, len(models), 20)]
        prov_name  = PROVIDERS.get(provider, {}).get("name", provider)

        embed = discord.Embed(
            title=f"📋 Модели: {prov_name}",
            description=f"Всего: **{len(models)}** | Текущая: `{current}`\nЧтобы сменить: `/ии-настройка провайдер` → поле `модель`",
            color=0x5865f2,
        )
        for i, chunk in enumerate(chunks[:4]):  # макс 4 поля
            embed.add_field(
                name=f"Модели {i*20+1}–{i*20+len(chunk)}",
                value="\n".join(f"`{m}`" for m in chunk),
                inline=True,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ─── /ии-тест ────────────────────────────────────────────────────────────
    @app_commands.command(name="ии-тест", description="🧪 Проверить подключение к ИИ")
    @app_commands.default_permissions(administrator=True)
    async def test_ai(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        cfg      = await db.get("ai_config", {})
        provider = cfg.get("provider", "")
        api_key  = cfg.get("api_key", "").strip()
        model    = cfg.get("model", "")

        # Показываем что сохранено
        key_preview = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else f"{api_key[:4]}..."
        debug_info  = (
            f"Провайдер: `{provider}`\n"
            f"Модель: `{model}`\n"
            f"Ключ: `{key_preview}`\n"
            f"Длина ключа: {len(api_key)} симв.\n\n"
        )

        reply = await self._generate("Скажи ровно одно слово: OK", interaction.user)
        await db.delete(f"ai_history_u.{interaction.user.id}")

        if reply.startswith("⚠️") or reply.startswith("❌"):
            await interaction.followup.send(
                f"❌ **Ошибка:** ```{reply}```\n\n"
                f"**Сохранённые настройки:**\n{debug_info}"
                f"Если ключ правильный — попробуй `/ии-модели`.",
                ephemeral=True,
            )
        else:
            prov_name = PROVIDERS.get(provider, {}).get("name", provider)
            await interaction.followup.send(
                f"✅ **Подключение работает!**\n{debug_info}"
                f"Провайдер: **{prov_name}**\nОтвет ИИ: `{reply[:100]}`",
                ephemeral=True,
            )


async def setup(bot):
    await bot.add_cog(AIChatCog(bot))
