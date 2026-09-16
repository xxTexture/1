"""
📋 ПОЛНЫЕ ЛОГИ СЕРВЕРА
Отслеживает абсолютно все события и пишет в указанный канал.

Настройка:
  SERVER_LOG_CHANNEL_ID в config.py  ← ID канала
  /сервер-лог канал                  ← сменить канал через Discord
  /сервер-лог вкл/выкл               ← включить/выключить
  /сервер-лог фильтр                 ← включить/выключить отдельные категории
  /сервер-лог статус                 ← текущие настройки

Категории событий:
  messages   — удаление, редактирование сообщений
  voice      — войс каналы (вход, выход, перемещение, мут/дизмут)
  members    — вход/выход участников, смена ника, смена ролей
  channels   — создание, удаление, редактирование каналов
  roles      — создание, удаление, редактирование ролей
  moderation — баны, кики, тайм-ауты
  server     — изменения настроек сервера, эмодзи
  invites    — создание/удаление инвайтов
"""

import discord
from discord.ext import commands
from discord import app_commands
import datetime
from typing import Optional

from config import BotConfig
from database import db


# ─── Цвета по категории ───────────────────────────────────────────────────────
COLORS = {
    "messages":   0xe74c3c,   # красный
    "voice":      0x3498db,   # синий
    "members":    0x2ecc71,   # зелёный
    "channels":   0x9b59b6,   # фиолетовый
    "roles":      0xf39c12,   # жёлтый
    "moderation": 0xe67e22,   # оранжевый
    "server":     0x1abc9c,   # бирюзовый
    "invites":    0x95a5a6,   # серый
}

ALL_CATEGORIES = list(COLORS.keys())

DEFAULT_FILTERS = {cat: True for cat in ALL_CATEGORIES}


# ─── Утилиты ─────────────────────────────────────────────────────────────────
async def get_log_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    ch_id = await db.get("srvlog.channel_id", BotConfig.SERVER_LOG_CHANNEL_ID)
    if not ch_id:
        return None
    return guild.get_channel(ch_id)


async def is_enabled() -> bool:
    return await db.get("srvlog.enabled", BotConfig.SERVER_LOG_CHANNEL_ID != 0)


async def is_cat_enabled(category: str) -> bool:
    filters = await db.get("srvlog.filters", DEFAULT_FILTERS)
    return filters.get(category, True)


def ts(dt: Optional[datetime.datetime] = None) -> str:
    """Форматирует время для футера."""
    d = dt or datetime.datetime.utcnow()
    return d.strftime("%d.%m.%Y %H:%M:%S UTC")


async def send_log(guild: discord.Guild, category: str, embed: discord.Embed):
    """Отправляет embed в канал логов если категория включена."""
    if not await is_enabled():
        return
    if not await is_cat_enabled(category):
        return
    ch = await get_log_channel(guild)
    if not ch:
        return
    embed.color = COLORS.get(category, 0x95a5a6)
    if not embed.timestamp:
        embed.timestamp = discord.utils.utcnow()
    try:
        await ch.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass


# ─── COG ─────────────────────────────────────────────────────────────────────
class ServerLogsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Кэш сообщений для логирования удалений (message_id → content)
        self._msg_cache: dict[int, tuple[str, str, int]] = {}  # id → (content, author_mention, channel_id)

    # ════════════════════════════════════════════════════════════════════════
    # 💬 СООБЩЕНИЯ
    # ════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Кэшируем сообщения для последующего логирования удалений."""
        if message.guild and not message.author.bot:
            self._msg_cache[message.id] = (
                message.content[:1000] or "[нет текста]",
                message.author.mention,
                message.channel.id,
            )
            # Ограничиваем размер кэша
            if len(self._msg_cache) > 2000:
                oldest = list(self._msg_cache.keys())[:500]
                for k in oldest:
                    del self._msg_cache[k]

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return

        content = message.content[:1000] if message.content else "[нет текста / вложение]"
        embed   = discord.Embed(title="🗑️ Сообщение удалено")
        embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        embed.add_field(name="Автор",   value=message.author.mention,   inline=True)
        embed.add_field(name="Канал",   value=message.channel.mention,  inline=True)
        embed.add_field(name="Содержимое", value=f"```{content}```",    inline=False)

        if message.attachments:
            embed.add_field(
                name="Вложения",
                value="\n".join(a.filename for a in message.attachments),
                inline=False,
            )
        embed.set_footer(text=f"ID сообщения: {message.id} • {ts(message.created_at)}")
        await send_log(message.guild, "messages", embed)

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]):
        if not messages or not messages[0].guild:
            return
        guild   = messages[0].guild
        channel = messages[0].channel
        embed   = discord.Embed(title=f"🗑️ Массовое удаление: {len(messages)} сообщений")
        embed.add_field(name="Канал", value=channel.mention, inline=True)
        embed.add_field(name="Кол-во", value=str(len(messages)), inline=True)
        embed.set_footer(text=ts())
        await send_log(guild, "messages", embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not before.guild or before.author.bot:
            return
        if before.content == after.content:
            return  # Только embed-обновления — не логируем

        before_txt = before.content[:500] if before.content else "[пусто]"
        after_txt  = after.content[:500]  if after.content  else "[пусто]"

        embed = discord.Embed(title="✏️ Сообщение изменено")
        embed.set_author(name=str(before.author), icon_url=before.author.display_avatar.url)
        embed.add_field(name="Автор",   value=before.author.mention,  inline=True)
        embed.add_field(name="Канал",   value=before.channel.mention, inline=True)
        embed.add_field(name="🔗 Ссылка", value=f"[Перейти]({after.jump_url})", inline=True)
        embed.add_field(name="До",      value=f"```{before_txt}```",  inline=False)
        embed.add_field(name="После",   value=f"```{after_txt}```",   inline=False)
        embed.set_footer(text=f"ID: {before.id} • {ts()}")
        await send_log(before.guild, "messages", embed)

    # ════════════════════════════════════════════════════════════════════════
    # 🎙️ ВОЙС КАНАЛЫ
    # ════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member,
                                    before: discord.VoiceState, after: discord.VoiceState):
        guild = member.guild
        embed = discord.Embed()
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)

        # Зашёл в канал
        if before.channel is None and after.channel is not None:
            embed.title = "🔊 Вход в войс"
            embed.add_field(name="Участник", value=member.mention,          inline=True)
            embed.add_field(name="Канал",    value=after.channel.mention,   inline=True)

        # Вышел из канала
        elif before.channel is not None and after.channel is None:
            embed.title = "🔇 Выход из войса"
            embed.add_field(name="Участник", value=member.mention,          inline=True)
            embed.add_field(name="Канал",    value=before.channel.mention,  inline=True)

        # Перемещён / перешёл в другой канал
        elif before.channel != after.channel:
            embed.title = "🔀 Перемещение в войсе"
            embed.add_field(name="Участник", value=member.mention,          inline=True)
            embed.add_field(name="Откуда",   value=before.channel.mention,  inline=True)
            embed.add_field(name="Куда",     value=after.channel.mention,   inline=True)

            # Проверяем кто переместил через audit log
            try:
                await asyncio.sleep(0.5)
                async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.member_move):
                    if (datetime.datetime.utcnow() - entry.created_at.replace(tzinfo=None)).seconds < 5:
                        if entry.user != member:
                            embed.add_field(name="Переместил", value=entry.user.mention, inline=True)
                        break
            except discord.Forbidden:
                pass

        # Изменения в рамках того же канала (мут, стрим и т.д.)
        elif before.channel == after.channel:
            changes = []
            if before.self_mute   != after.self_mute:
                changes.append(f"Микрофон: {'🔇 Замьючен' if after.self_mute else '🎙️ Включён'} (сам)")
            if before.self_deaf   != after.self_deaf:
                changes.append(f"Наушники: {'🔕 Оглушён' if after.self_deaf else '🔔 Слышит'} (сам)")
            if before.mute        != after.mute:
                changes.append(f"Микрофон: {'🔇 Замьючен' if after.mute else '🎙️ Включён'} (сервером)")
            if before.deaf        != after.deaf:
                changes.append(f"Наушники: {'🔕 Оглушён' if after.deaf else '🔔 Слышит'} (сервером)")
            if before.self_stream != after.self_stream:
                changes.append(f"Стрим: {'▶️ Начат' if after.self_stream else '⏹️ Остановлен'}")
            if before.self_video  != after.self_video:
                changes.append(f"Камера: {'📷 Включена' if after.self_video else '📷 Выключена'}")

            if not changes:
                return
            embed.title = "🎙️ Изменение в войсе"
            embed.add_field(name="Участник", value=member.mention,                inline=True)
            embed.add_field(name="Канал",    value=after.channel.mention,         inline=True)
            embed.add_field(name="Изменения", value="\n".join(changes),           inline=False)
        else:
            return

        embed.set_footer(text=f"ID: {member.id} • {ts()}")
        await send_log(guild, "voice", embed)

    # ════════════════════════════════════════════════════════════════════════
    # 👤 УЧАСТНИКИ
    # ════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        created  = member.created_at
        age_days = (datetime.datetime.utcnow() - created.replace(tzinfo=None)).days
        warning  = " ⚠️ Новый аккаунт!" if age_days < 7 else ""

        embed = discord.Embed(title=f"📥 Участник вошёл{warning}")
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.add_field(name="Участник",      value=f"{member.mention} (`{member.id}`)", inline=False)
        embed.add_field(name="Аккаунт создан",value=discord.utils.format_dt(created, "D"),inline=True)
        embed.add_field(name="Возраст",       value=f"{age_days} дн.",                   inline=True)
        embed.add_field(name="Участников",    value=str(member.guild.member_count),       inline=True)
        embed.set_footer(text=ts())
        await send_log(member.guild, "members", embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        roles_str = ", ".join(r.mention for r in member.roles if r.name != "@everyone") or "нет"
        embed = discord.Embed(title="📤 Участник вышел / кикнут")
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.add_field(name="Участник", value=f"{member.mention} (`{member.id}`)", inline=False)
        embed.add_field(name="Роли",     value=roles_str[:500],                     inline=False)
        embed.set_footer(text=ts())
        await send_log(member.guild, "members", embed)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        guild  = before.guild
        embeds = []

        # Смена никнейма
        if before.nick != after.nick:
            embed = discord.Embed(title="✏️ Смена ника")
            embed.set_author(name=str(after), icon_url=after.display_avatar.url)
            embed.add_field(name="Участник", value=after.mention,                      inline=True)
            embed.add_field(name="Было",     value=before.nick or before.name,         inline=True)
            embed.add_field(name="Стало",    value=after.nick  or after.name,          inline=True)
            embeds.append(embed)

        # Смена ролей
        added   = set(after.roles)  - set(before.roles)
        removed = set(before.roles) - set(after.roles)
        if added or removed:
            embed = discord.Embed(title="🎭 Изменение ролей")
            embed.set_author(name=str(after), icon_url=after.display_avatar.url)
            embed.add_field(name="Участник",      value=after.mention, inline=False)
            if added:
                embed.add_field(name="➕ Добавлены",  value=" ".join(r.mention for r in added),   inline=True)
            if removed:
                embed.add_field(name="➖ Удалены",    value=" ".join(r.mention for r in removed), inline=True)

            # Кто изменил роли
            try:
                import asyncio
                await asyncio.sleep(0.5)
                async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.member_role_update):
                    if (datetime.datetime.utcnow() - entry.created_at.replace(tzinfo=None)).seconds < 5:
                        embed.add_field(name="Изменил", value=entry.user.mention, inline=True)
                        break
            except discord.Forbidden:
                pass
            embeds.append(embed)

        # Тайм-аут
        if before.timed_out_until != after.timed_out_until:
            if after.timed_out_until:
                embed = discord.Embed(title="🔇 Выдан тайм-аут (мут)")
                embed.add_field(name="Участник", value=after.mention, inline=True)
                embed.add_field(name="До",       value=discord.utils.format_dt(after.timed_out_until, "f"), inline=True)
            else:
                embed = discord.Embed(title="🔊 Тайм-аут снят")
                embed.add_field(name="Участник", value=after.mention, inline=True)
            embed.set_author(name=str(after), icon_url=after.display_avatar.url)
            embeds.append(embed)

        for embed in embeds:
            embed.set_footer(text=f"ID: {after.id} • {ts()}")
            await send_log(guild, "members", embed)

    # ════════════════════════════════════════════════════════════════════════
    # 🚨 МОДЕРАЦИЯ (баны/кики)
    # ════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        embed = discord.Embed(title="🔨 Бан")
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
        embed.add_field(name="Пользователь", value=f"{user.mention} (`{user.id}`)", inline=True)

        try:
            import asyncio
            await asyncio.sleep(0.5)
            async for entry in guild.audit_logs(limit=3, action=discord.AuditLogAction.ban):
                if entry.target.id == user.id:
                    embed.add_field(name="Забанил", value=entry.user.mention,          inline=True)
                    embed.add_field(name="Причина", value=entry.reason or "не указана",inline=True)
                    break
        except discord.Forbidden:
            pass

        embed.set_footer(text=ts())
        await send_log(guild, "moderation", embed)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        embed = discord.Embed(title="🔓 Разбан")
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
        embed.add_field(name="Пользователь", value=f"{user.mention} (`{user.id}`)", inline=True)
        embed.set_footer(text=ts())
        await send_log(guild, "moderation", embed)

    # ════════════════════════════════════════════════════════════════════════
    # 📁 КАНАЛЫ
    # ════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        embed = discord.Embed(title="📁 Канал создан")
        embed.add_field(name="Канал", value=f"{channel.mention} (`#{channel.name}`)", inline=True)
        embed.add_field(name="Тип",   value=str(channel.type),                        inline=True)
        embed.set_footer(text=f"ID: {channel.id} • {ts()}")
        await send_log(channel.guild, "channels", embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        embed = discord.Embed(title="🗑️ Канал удалён")
        embed.add_field(name="Название", value=f"`#{channel.name}`", inline=True)
        embed.add_field(name="Тип",      value=str(channel.type),    inline=True)
        embed.set_footer(text=f"ID: {channel.id} • {ts()}")
        await send_log(channel.guild, "channels", embed)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        changes = []
        if before.name != after.name:
            changes.append(f"Название: `{before.name}` → `{after.name}`")
        if hasattr(before, "topic") and before.topic != after.topic:
            changes.append(f"Тема: `{before.topic or '—'}` → `{after.topic or '—'}`")
        if hasattr(before, "slowmode_delay") and before.slowmode_delay != after.slowmode_delay:
            changes.append(f"Медленный режим: `{before.slowmode_delay}с` → `{after.slowmode_delay}с`")
        if hasattr(before, "nsfw") and before.nsfw != after.nsfw:
            changes.append(f"NSFW: `{before.nsfw}` → `{after.nsfw}`")
        if before.category != after.category:
            changes.append(f"Категория: `{before.category}` → `{after.category}`")

        if not changes:
            return
        embed = discord.Embed(title="✏️ Канал изменён")
        embed.add_field(name="Канал",     value=after.mention,        inline=True)
        embed.add_field(name="Изменения", value="\n".join(changes),   inline=False)
        embed.set_footer(text=f"ID: {after.id} • {ts()}")
        await send_log(before.guild, "channels", embed)

    # ════════════════════════════════════════════════════════════════════════
    # 🎭 РОЛИ
    # ════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        embed = discord.Embed(title="🎭 Роль создана")
        embed.add_field(name="Роль",  value=role.mention,             inline=True)
        embed.add_field(name="Цвет",  value=str(role.color),          inline=True)
        embed.set_footer(text=f"ID: {role.id} • {ts()}")
        await send_log(role.guild, "roles", embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        embed = discord.Embed(title="🗑️ Роль удалена")
        embed.add_field(name="Название", value=f"`{role.name}`", inline=True)
        embed.set_footer(text=f"ID: {role.id} • {ts()}")
        await send_log(role.guild, "roles", embed)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        changes = []
        if before.name    != after.name:    changes.append(f"Название: `{before.name}` → `{after.name}`")
        if before.color   != after.color:   changes.append(f"Цвет: `{before.color}` → `{after.color}`")
        if before.hoist   != after.hoist:   changes.append(f"Отображать отдельно: `{before.hoist}` → `{after.hoist}`")
        if before.mentionable != after.mentionable:
            changes.append(f"Упоминаемая: `{before.mentionable}` → `{after.mentionable}`")
        if before.permissions != after.permissions:
            changes.append("Права изменены")

        if not changes:
            return
        embed = discord.Embed(title="✏️ Роль изменена")
        embed.add_field(name="Роль",      value=after.mention,       inline=True)
        embed.add_field(name="Изменения", value="\n".join(changes),  inline=False)
        embed.set_footer(text=f"ID: {after.id} • {ts()}")
        await send_log(before.guild, "roles", embed)

    # ════════════════════════════════════════════════════════════════════════
    # 🏠 СЕРВЕР
    # ════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        changes = []
        if before.name      != after.name:      changes.append(f"Название: `{before.name}` → `{after.name}`")
        if before.icon      != after.icon:      changes.append("Иконка изменена")
        if before.banner    != after.banner:    changes.append("Баннер изменён")
        if before.description != after.description:
            changes.append(f"Описание изменено")
        if before.verification_level != after.verification_level:
            changes.append(f"Верификация: `{before.verification_level}` → `{after.verification_level}`")

        if not changes:
            return
        embed = discord.Embed(title="🏠 Сервер изменён")
        embed.add_field(name="Изменения", value="\n".join(changes), inline=False)
        embed.set_footer(text=ts())
        await send_log(after, "server", embed)

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild: discord.Guild,
                                      before: list, after: list):
        added   = [e for e in after  if e not in before]
        removed = [e for e in before if e not in after]
        if not added and not removed:
            return

        embed = discord.Embed(title="😀 Эмодзи изменены")
        if added:
            embed.add_field(name="➕ Добавлены",
                            value=" ".join(str(e) for e in added[:10]), inline=False)
        if removed:
            embed.add_field(name="➖ Удалены",
                            value=" ".join(f"`:{e.name}:`" for e in removed[:10]), inline=False)
        embed.set_footer(text=ts())
        await send_log(guild, "server", embed)

    # ════════════════════════════════════════════════════════════════════════
    # 🔗 ИНВАЙТЫ
    # ════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        embed = discord.Embed(title="🔗 Инвайт создан")
        embed.add_field(name="Создал",    value=invite.inviter.mention if invite.inviter else "?", inline=True)
        embed.add_field(name="Канал",     value=invite.channel.mention if invite.channel else "?", inline=True)
        embed.add_field(name="Код",       value=f"`{invite.code}`",                                inline=True)
        embed.add_field(name="Исп./Макс",
                        value=f"`{invite.uses}/{invite.max_uses or '∞'}`",                        inline=True)
        if invite.max_age:
            embed.add_field(name="Истекает", value=f"Через {invite.max_age // 3600}ч",            inline=True)
        embed.set_footer(text=ts())
        if invite.guild:
            await send_log(invite.guild, "invites", embed)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        embed = discord.Embed(title="🗑️ Инвайт удалён")
        embed.add_field(name="Код",    value=f"`{invite.code}`",                                   inline=True)
        embed.add_field(name="Канал",  value=invite.channel.mention if invite.channel else "?",   inline=True)
        embed.set_footer(text=ts())
        if invite.guild:
            await send_log(invite.guild, "invites", embed)

    # ════════════════════════════════════════════════════════════════════════
    # ⚙️ КОМАНДЫ УПРАВЛЕНИЯ
    # ════════════════════════════════════════════════════════════════════════

    log_group = app_commands.Group(
        name="сервер-лог",
        description="📋 Настройка полных логов сервера",
        default_permissions=discord.Permissions(administrator=True),
    )

    @log_group.command(name="канал", description="📌 Установить текущий канал для логов")
    async def set_channel(self, interaction: discord.Interaction):
        await db.set("srvlog.channel_id", interaction.channel.id)
        await db.set("srvlog.enabled",    True)
        await interaction.response.send_message(
            f"✅ Логи сервера → {interaction.channel.mention}\nЛогирование **включено**.", ephemeral=True
        )

    @log_group.command(name="вкл", description="✅ Включить логирование")
    async def enable(self, interaction: discord.Interaction):
        ch_id = await db.get("srvlog.channel_id", BotConfig.SERVER_LOG_CHANNEL_ID)
        if not ch_id:
            await interaction.response.send_message("❌ Канал не настроен. Сначала `/сервер-лог канал`.", ephemeral=True)
            return
        await db.set("srvlog.enabled", True)
        await interaction.response.send_message("✅ Логирование включено.", ephemeral=True)

    @log_group.command(name="выкл", description="🔕 Выключить логирование")
    async def disable(self, interaction: discord.Interaction):
        await db.set("srvlog.enabled", False)
        await interaction.response.send_message("✅ Логирование выключено.", ephemeral=True)

    @log_group.command(name="фильтр", description="🔧 Включить/выключить конкретную категорию логов")
    @app_commands.describe(категория="Категория событий", включить="True = включить, False = выключить")
    @app_commands.choices(категория=[
        app_commands.Choice(name="💬 Сообщения",   value="messages"),
        app_commands.Choice(name="🎙️ Войс",        value="voice"),
        app_commands.Choice(name="👤 Участники",   value="members"),
        app_commands.Choice(name="📁 Каналы",      value="channels"),
        app_commands.Choice(name="🎭 Роли",        value="roles"),
        app_commands.Choice(name="🚨 Модерация",   value="moderation"),
        app_commands.Choice(name="🏠 Сервер",      value="server"),
        app_commands.Choice(name="🔗 Инвайты",     value="invites"),
    ])
    async def set_filter(self, interaction: discord.Interaction, категория: str, включить: bool):
        filters = await db.get("srvlog.filters", DEFAULT_FILTERS)
        filters[категория] = включить
        await db.set("srvlog.filters", filters)
        state = "✅ включена" if включить else "❌ выключена"
        await interaction.response.send_message(
            f"Категория **{категория}** {state}.", ephemeral=True
        )

    @log_group.command(name="статус", description="📋 Текущие настройки логов")
    async def status(self, interaction: discord.Interaction):
        enabled = await is_enabled()
        ch_id   = await db.get("srvlog.channel_id", BotConfig.SERVER_LOG_CHANNEL_ID)
        filters = await db.get("srvlog.filters", DEFAULT_FILTERS)

        embed = discord.Embed(title="📋 Логи сервера", color=0x5865f2)
        embed.add_field(name="Статус",  value="✅ Вкл" if enabled else "❌ Выкл",          inline=True)
        embed.add_field(name="Канал",   value=f"<#{ch_id}>" if ch_id else "⚠️ не задан",  inline=True)

        cats_str = "\n".join(
            f"{'✅' if filters.get(c, True) else '❌'} {c}"
            for c in ALL_CATEGORIES
        )
        embed.add_field(name="Категории", value=cats_str, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    import asyncio  # noqa — нужен внутри листенеров
    await bot.add_cog(ServerLogsCog(bot))
