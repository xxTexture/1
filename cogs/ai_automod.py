"""
🚨 ИИ АВТО-МОДЕРАЦИЯ
- Точные слова/фразы — обычная проверка (быстро, без затрат токенов)
- AI-режим для списка — ИИ определяет смысл, ловит транслит/синонимы/обход

Настройка:
  /авто-мод список-добавить  название действие длительность причина [ии_проверка]
  /авто-мод список-удалить
  /авто-мод список-показать
  /авто-мод слово-добавить   список слово
  /авто-мод слово-удалить    список слово
  /авто-мод-вл добавить/удалить/список  — белый список (кого не трогать)
  /авто-мод вкл/выкл/канал-логов/статус

ai_check=True: ИИ смотрит на СМЫСЛ сообщения.
  Слова списка — это концепты/темы, а не точные фразы.
  Пример: слово "реклама" — ИИ поймёт и "заходи к нам discord.gg/xxx" и "vk.com/server"
  Пример: слово "zigono не окунь" — ИИ поймёт "zigono ne okyn" и "зигоно не рыба"
"""

import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional
import datetime
import re
import aiohttp

from database import db
from config import BotConfig
from utils.helpers import parse_duration, duration_to_str


# ─── Утилиты ─────────────────────────────────────────────────────────────────
async def get_cfg() -> dict:
    return await db.get("automod_config", {
        "enabled":        False,
        "log_channel_id": 0,
        "whitelist":      [],
        "lists":          {},
    })

async def save_cfg(cfg: dict):
    await db.set("automod_config", cfg)


def contains_trigger_exact(text: str, words: list) -> Optional[str]:
    """Точное совпадение слова/фразы."""
    text_lower = text.lower()
    for word in words:
        pattern = re.escape(word.lower())
        if re.search(r'\b' + pattern + r'\b', text_lower) or (len(word) > 4 and pattern in text_lower):
            return word
    return None


async def contains_trigger_ai(text: str, concepts: list, ai_cfg: dict) -> Optional[str]:
    """
    ИИ проверяет, выражает ли сообщение один из концептов.
    concepts — список строк, каждая описывает запрещённый концепт/тему.
    Возвращает первый сработавший концепт или None.
    """
    if not ai_cfg.get("api_key") or not ai_cfg.get("provider"):
        return None  # ИИ не настроен — пропускаем

    concepts_str = "\n".join(f"- {c}" for c in concepts)
    prompt = (
        f"Сообщение пользователя: «{text}»\n\n"
        f"Список запрещённых концептов:\n{concepts_str}\n\n"
        f"Определи: содержит ли сообщение хотя бы один из этих концептов? "
        f"Учитывай транслитерацию, синонимы, намеренные опечатки, замену букв.\n"
        f"Ответь СТРОГО в формате:\n"
        f"НАРУШЕНИЕ: <точное название концепта из списка>\n"
        f"или\n"
        f"ОК\n"
        f"Больше ничего не пиши."
    )

    provider = ai_cfg.get("provider", "")
    api_key  = ai_cfg.get("api_key", "")
    model    = ai_cfg.get("model", "")

    try:
        reply = ""
        if provider == "gemini":
            url     = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                       "generationConfig": {"maxOutputTokens": 60}}
            async with aiohttp.ClientSession() as s:
                async with s.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 200:
                        data  = await r.json()
                        reply = data["candidates"][0]["content"]["parts"][0]["text"].strip()

        elif provider == "anthropic":
            headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
            payload = {"model": model, "max_tokens": 60,
                       "messages": [{"role": "user", "content": prompt}]}
            async with aiohttp.ClientSession() as s:
                async with s.post("https://api.anthropic.com/v1/messages", headers=headers,
                                  json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 200:
                        data  = await r.json()
                        reply = data["content"][0]["text"].strip()

        else:  # openai / groq / custom
            prov_urls = {
                "openai":  "https://api.openai.com/v1/chat/completions",
                "groq":    "https://api.groq.com/openai/v1/chat/completions",
                "custom":  ai_cfg.get("custom_url", ""),
            }
            url     = prov_urls.get(provider, prov_urls["openai"])
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {"model": model, "max_tokens": 60,
                       "messages": [{"role": "user", "content": prompt}]}
            async with aiohttp.ClientSession() as s:
                async with s.post(url, headers=headers, json=payload,
                                  timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 200:
                        data  = await r.json()
                        reply = data["choices"][0]["message"]["content"].strip()

        # Парсим ответ
        if reply.upper().startswith("НАРУШЕНИЕ:"):
            matched = reply[len("НАРУШЕНИЕ:"):].strip()
            # Находим ближайший концепт из списка
            for c in concepts:
                if c.lower() in matched.lower() or matched.lower() in c.lower():
                    return c
            return matched or concepts[0]
        return None

    except Exception:
        return None  # При ошибке ИИ не блокируем


# ─── COG ─────────────────────────────────────────────────────────────────────
class AutoModCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        cfg = await get_cfg()
        if not cfg.get("enabled"):
            return

        # Белый список
        whitelist = [str(x) for x in cfg.get("whitelist", [])]
        if str(message.author.id) in whitelist:
            return
        if message.author.guild_permissions.administrator:
            return

        ai_cfg = await db.get("ai_config", {})
        lists  = cfg.get("lists", {})

        for list_name, rule in lists.items():
            words    = rule.get("words", [])
            use_ai   = rule.get("ai_check", False)
            trigger  = None

            if not words:
                continue

            # Сначала быстрая точная проверка
            trigger = contains_trigger_exact(message.content, words)

            # Если не нашли точно и включена ИИ-проверка — спрашиваем ИИ
            if trigger is None and use_ai:
                trigger = await contains_trigger_ai(message.content, words, ai_cfg)

            if trigger is None:
                continue

            # ─── Применяем наказание ─────────────────────────────────────────
            action    = rule.get("action", "mute")
            dur_str   = rule.get("duration", "10m")
            reason    = rule.get("reason", f"Авто-мод: нарушение [{list_name}]")
            dur       = parse_duration(dur_str)
            action_str = ""

            try:
                await message.delete()
            except discord.Forbidden:
                pass

            punished = False
            try:
                if action == "mute":
                    until = discord.utils.utcnow() + dur
                    await message.author.timeout(until, reason=reason)
                    punished   = True
                    action_str = f"🔇 Мут на {duration_to_str(dur)}"
                elif action == "ban":
                    await message.author.ban(reason=reason, delete_message_days=0)
                    punished   = True
                    action_str = f"🔨 Бан на {duration_to_str(dur)}"
                    if dur.total_seconds() < 86400 * 365:
                        unban_at = datetime.datetime.utcnow().timestamp() + dur.total_seconds()
                        bans     = await db.get("temp_bans", {})
                        bkey     = f"{message.guild.id}_{message.author.id}"
                        bans[bkey] = {"guild_id": message.guild.id, "user_id": message.author.id, "unban_at": unban_at}
                        await db.set("temp_bans", bans)
            except discord.Forbidden:
                action_str = "❌ Нет прав"

            # Уведомление в чат
            try:
                await message.channel.send(
                    f"⚠️ {message.author.mention}, {reason}."
                    + (f" **{action_str}**." if punished else ""),
                    delete_after=8,
                )
            except Exception:
                pass

            # Лог
            log_ch_id = cfg.get("log_channel_id", 0) or BotConfig.LOG_CHANNEL_1_ID
            log_ch    = message.guild.get_channel(log_ch_id)
            if log_ch:
                mode = "🤖 ИИ-проверка" if use_ai else "📋 Точный список"
                em   = discord.Embed(title="🚨 Авто-модерация", color=discord.Color.orange(),
                                     timestamp=discord.utils.utcnow())
                em.set_thumbnail(url=message.author.display_avatar.url)
                em.add_field(name="Нарушитель",  value=f"{message.author.mention} (`{message.author}`)", inline=False)
                em.add_field(name="Список",      value=f"`{list_name}`",         inline=True)
                em.add_field(name="Режим",       value=mode,                     inline=True)
                em.add_field(name="Триггер",     value=f"`{trigger}`",           inline=True)
                em.add_field(name="Наказание",   value=action_str if punished else "—", inline=True)
                em.add_field(name="Причина",     value=reason,                   inline=False)
                em.add_field(name="Сообщение",   value=f"```{message.content[:200]}```", inline=False)
                em.add_field(name="Канал",       value=message.channel.mention,  inline=True)
                try:
                    await log_ch.send(embed=em)
                except Exception:
                    pass
            break

    # ─── ГРУППА КОМАНД ───────────────────────────────────────────────────────
    automod_group = app_commands.Group(
        name="авто-мод",
        description="🚨 Настройка авто-модерации",
        default_permissions=discord.Permissions(administrator=True),
    )

    @automod_group.command(name="вкл", description="✅ Включить авто-модерацию")
    async def enable(self, interaction: discord.Interaction):
        cfg = await get_cfg(); cfg["enabled"] = True; await save_cfg(cfg)
        await interaction.response.send_message("✅ Авто-мод **включён**.", ephemeral=True)

    @automod_group.command(name="выкл", description="❌ Выключить авто-модерацию")
    async def disable(self, interaction: discord.Interaction):
        cfg = await get_cfg(); cfg["enabled"] = False; await save_cfg(cfg)
        await interaction.response.send_message("✅ Авто-мод **выключен**.", ephemeral=True)

    @automod_group.command(name="канал-логов", description="📋 Установить канал для логов авто-мода")
    async def set_log(self, interaction: discord.Interaction):
        cfg = await get_cfg(); cfg["log_channel_id"] = interaction.channel.id; await save_cfg(cfg)
        await interaction.response.send_message(f"✅ Логи авто-мода → {interaction.channel.mention}", ephemeral=True)

    @automod_group.command(name="список-добавить", description="➕ Создать список слов/концептов с наказанием")
    @app_commands.describe(
        название="Название списка",
        действие="Тип наказания",
        длительность="Срок (15m, 1h, 7d)",
        причина="Причина наказания (видна игроку)",
        ии_проверка="Включить ИИ — ловить транслит/синонимы/обход (медленнее, тратит токены)",
    )
    @app_commands.choices(действие=[
        app_commands.Choice(name="🔇 Мут (тайм-аут)", value="mute"),
        app_commands.Choice(name="🔨 Бан",             value="ban"),
    ])
    async def add_list(self, interaction: discord.Interaction,
                       название: str, действие: str, длительность: str, причина: str,
                       ии_проверка: bool = False):
        cfg = await get_cfg()
        cfg.setdefault("lists", {})[название] = {
            "words":    [],
            "action":   действие,
            "duration": длительность,
            "reason":   причина,
            "ai_check": ии_проверка,
        }
        await save_cfg(cfg)
        act_str = "Мут" if действие == "mute" else "Бан"
        dur_str = duration_to_str(parse_duration(длительность))
        ai_note = "\n🤖 **ИИ-проверка включена** — бот будет ловить транслит, синонимы, обходы." if ии_проверка else ""
        await interaction.response.send_message(
            f"✅ Список **{название}** создан.\n"
            f"Действие: **{act_str} на {dur_str}** | Причина: `{причина}`{ai_note}\n\n"
            f"Добавь слова/концепты: `/авто-мод слово-добавить {название} <слово>`",
            ephemeral=True,
        )

    @automod_group.command(name="список-удалить", description="➖ Удалить список")
    async def remove_list(self, interaction: discord.Interaction, название: str):
        cfg = await get_cfg()
        if название not in cfg.get("lists", {}):
            await interaction.response.send_message(f"❌ Список `{название}` не найден.", ephemeral=True); return
        cfg["lists"].pop(название); await save_cfg(cfg)
        await interaction.response.send_message(f"✅ Список **{название}** удалён.", ephemeral=True)

    @automod_group.command(name="список-показать", description="📋 Показать все списки")
    async def show_lists(self, interaction: discord.Interaction):
        cfg   = await get_cfg()
        lists = cfg.get("lists", {})
        if not lists:
            await interaction.response.send_message("📭 Списков нет.", ephemeral=True); return

        embed = discord.Embed(
            title="🚨 Авто-модерация",
            description=f"Статус: {'✅ Включена' if cfg.get('enabled') else '❌ Выключена'}",
            color=0xe74c3c,
        )
        for lname, rule in lists.items():
            words_str = ", ".join(f"`{w}`" for w in rule.get("words", [])) or "*пусто*"
            act       = "🔇 Мут" if rule["action"] == "mute" else "🔨 Бан"
            dur_str   = duration_to_str(parse_duration(rule.get("duration", "10m")))
            ai_badge  = " 🤖ИИ" if rule.get("ai_check") else ""
            embed.add_field(
                name=f"📋 {lname}  [{act} на {dur_str}]{ai_badge}",
                value=f"Причина: `{rule.get('reason','—')}`\nСлова/концепты: {words_str[:400]}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @automod_group.command(name="слово-добавить", description="➕ Добавить слово/концепт в список")
    @app_commands.describe(
        список="Название списка",
        слово="Слово, фраза или концепт (при ИИ-режиме — описание темы)",
    )
    async def add_word(self, interaction: discord.Interaction, список: str, слово: str):
        cfg = await get_cfg()
        if список not in cfg.get("lists", {}):
            await interaction.response.send_message(
                f"❌ Список `{список}` не найден. Сначала `/авто-мод список-добавить`.", ephemeral=True
            ); return
        words = cfg["lists"][список].setdefault("words", [])
        if слово.lower() in [w.lower() for w in words]:
            await interaction.response.send_message(f"ℹ️ `{слово}` уже в списке.", ephemeral=True); return
        words.append(слово.lower()); await save_cfg(cfg)
        is_ai = cfg["lists"][список].get("ai_check", False)
        ai_tip = ("\n💡 ИИ-режим: можно добавлять описания концептов, например:\n"
                  "`реклама сторонних серверов`, `оскорбления администрации`") if is_ai else ""
        await interaction.response.send_message(
            f"✅ `{слово}` добавлен в **{список}**. Всего: {len(words)}{ai_tip}", ephemeral=True
        )

    @automod_group.command(name="слово-удалить", description="➖ Удалить слово/концепт из списка")
    async def remove_word(self, interaction: discord.Interaction, список: str, слово: str):
        cfg = await get_cfg()
        if список not in cfg.get("lists", {}):
            await interaction.response.send_message(f"❌ Список `{список}` не найден.", ephemeral=True); return
        words  = cfg["lists"][список].get("words", [])
        before = len(words)
        cfg["lists"][список]["words"] = [w for w in words if w.lower() != слово.lower()]
        if len(cfg["lists"][список]["words"]) == before:
            await interaction.response.send_message(f"❌ `{слово}` не найдено.", ephemeral=True); return
        await save_cfg(cfg)
        await interaction.response.send_message(f"✅ `{слово}` удалён из **{список}**.", ephemeral=True)

    @automod_group.command(name="ии-переключить", description="🤖 Включить/выключить ИИ-проверку для списка")
    async def toggle_ai(self, interaction: discord.Interaction, список: str):
        cfg = await get_cfg()
        if список not in cfg.get("lists", {}):
            await interaction.response.send_message(f"❌ Список `{список}` не найден.", ephemeral=True); return
        current = cfg["lists"][список].get("ai_check", False)
        cfg["lists"][список]["ai_check"] = not current
        await save_cfg(cfg)
        state = "включена ✅" if not current else "выключена ❌"
        await interaction.response.send_message(
            f"✅ ИИ-проверка для **{список}** {state}.\n"
            + ("⚠️ Убедись что ИИ настроен через `/ии-настройка провайдер`." if not current else ""),
            ephemeral=True,
        )

    @automod_group.command(name="статус", description="📋 Текущие настройки авто-мода")
    async def automod_status(self, interaction: discord.Interaction):
        cfg     = await get_cfg()
        lists   = cfg.get("lists", {})
        wl      = cfg.get("whitelist", [])
        log_id  = cfg.get("log_channel_id", 0)
        enabled = cfg.get("enabled", False)
        ai_cfg  = await db.get("ai_config", {})
        ai_ok   = bool(ai_cfg.get("api_key") and ai_cfg.get("provider"))

        embed = discord.Embed(title="🚨 Авто-модерация", color=discord.Color.green() if enabled else discord.Color.red())
        embed.add_field(name="Статус",     value="✅ Вкл" if enabled else "❌ Выкл", inline=True)
        embed.add_field(name="Списков",    value=str(len(lists)),                    inline=True)
        embed.add_field(name="Бел. список",value=str(len(wl)),                       inline=True)
        embed.add_field(name="Лог-канал",  value=f"<#{log_id}>" if log_id else "—",  inline=True)
        embed.add_field(name="ИИ настроен",value="✅ Да" if ai_ok else "❌ Нет",     inline=True)

        if lists:
            summary = []
            for lname, rule in lists.items():
                act     = "Мут" if rule["action"] == "mute" else "Бан"
                dur_str = duration_to_str(parse_duration(rule.get("duration", "10m")))
                ai_tag  = " 🤖" if rule.get("ai_check") else ""
                summary.append(f"• **{lname}**{ai_tag} → {act} {dur_str} ({len(rule.get('words',[]))} слов)")
            embed.add_field(name="Списки", value="\n".join(summary), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ─── Белый список ─────────────────────────────────────────────────────────
    whitelist_group = app_commands.Group(
        name="авто-мод-вл",
        description="🛡️ Белый список авто-модерации",
        default_permissions=discord.Permissions(administrator=True),
    )

    @whitelist_group.command(name="добавить", description="➕ Добавить игрока в белый список")
    async def wl_add(self, interaction: discord.Interaction, игрок: discord.Member):
        cfg = await get_cfg()
        wl  = [str(x) for x in cfg.setdefault("whitelist", [])]
        if str(игрок.id) in wl:
            await interaction.response.send_message(f"ℹ️ {игрок.mention} уже в белом списке.", ephemeral=True); return
        cfg["whitelist"].append(игрок.id); await save_cfg(cfg)
        await interaction.response.send_message(f"✅ {игрок.mention} добавлен в белый список.", ephemeral=True)

    @whitelist_group.command(name="удалить", description="➖ Убрать игрока из белого списка")
    async def wl_remove(self, interaction: discord.Interaction, игрок: discord.Member):
        cfg = await get_cfg()
        cfg["whitelist"] = [x for x in cfg.get("whitelist", []) if str(x) != str(игрок.id)]
        await save_cfg(cfg)
        await interaction.response.send_message(f"✅ {игрок.mention} убран из белого списка.", ephemeral=True)

    @whitelist_group.command(name="список", description="📋 Белый список")
    async def wl_list(self, interaction: discord.Interaction):
        cfg = await get_cfg()
        wl  = cfg.get("whitelist", [])
        if not wl:
            await interaction.response.send_message("📭 Белый список пуст.", ephemeral=True); return
        lines = [f"• {interaction.guild.get_member(int(uid)).mention if interaction.guild.get_member(int(uid)) else f'<@{uid}>'}" for uid in wl]
        await interaction.response.send_message(f"**🛡️ Белый список ({len(wl)}):**\n" + "\n".join(lines), ephemeral=True)


async def setup(bot):
    await bot.add_cog(AutoModCog(bot))
