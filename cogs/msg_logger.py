"""
💬 ЛОГИРОВАНИЕ КАЖДОГО СООБЩЕНИЯ
Пишет в указанный канал каждое сообщение на сервере:
  - Кто отправил (имя, упоминание, ID)
  - В каком канале
  - Во сколько (дата и время)
  - Текст сообщения + вложения + ссылка

Настройка:
  MSG_LOG_CHANNEL_ID в config.py  ← ID канала (главный способ)
  /лог-сообщений канал            ← сменить канал через Discord
  /лог-сообщений вкл / выкл       ← включить/выключить
  /лог-сообщений статус           ← текущие настройки
  /лог-сообщений боты             ← логировать ли сообщения ботов
  /лог-сообщений игнор ...        ← исключить каналы из логов
"""

import discord
from discord.ext import commands
from discord import app_commands
import datetime

from config import BotConfig
from database import db


COLOR_MSG = 0x5865f2  # blurple


# ─── Хелперы ───────────────────────────────────────────────────────────────
async def get_log_channel(guild: discord.Guild) -> discord.TextChannel | None:
    ch_id = await db.get("msglog.channel_id", BotConfig.MSG_LOG_CHANNEL_ID)
    if not ch_id:
        return None
    return guild.get_channel(ch_id)


async def is_logging_enabled() -> bool:
    return await db.get("msglog.enabled", BotConfig.MSG_LOG_CHANNEL_ID != 0)


async def is_log_bots() -> bool:
    return await db.get("msglog.log_bots", False)


async def get_ignored_channels() -> list[int]:
    return await db.get("msglog.ignore_channels", [])


def ts(dt: datetime.datetime | None = None) -> str:
    d = dt or datetime.datetime.utcnow()
    # убираем tzinfo чтобы strftime не ругался, время показываем как есть
    if d.tzinfo is not None:
        d = d.replace(tzinfo=None)
    return d.strftime("%d.%m.%Y %H:%M:%S UTC")


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit - 3] + "..."


class MsgLoggerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ════════════════════════════════════════════════════════════════════════
    # 💬 ЛОГИРОВАНИЕ КАЖДОГО СООБЩЕНИЯ
    # ════════════════════════════════════════════════════════════════════════
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Только серверные сообщения
        if not message.guild:
            return

        # Логируем только обычные сообщения и ответы (без системных: пины, входы, бусты)
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return

        if not await is_logging_enabled():
            return

        log_ch = await get_log_channel(message.guild)
        if not log_ch:
            return

        # Не логируем сам канал логов (защита от петли)
        if message.channel.id == log_ch.id:
            return

        # Боты
        if message.author.bot:
            # Свои сообщения бота не логируем никогда
            if self.bot.user and message.author.id == self.bot.user.id:
                return
            if not await is_log_bots():
                return

        # Игнорируемые каналы
        ignored = await get_ignored_channels()
        if message.channel.id in ignored:
            return
        # Если это тред — проверяем и родительский канал
        parent_id = getattr(message.channel, "parent_id", None) or getattr(
            getattr(message.channel, "parent", None), "id", None
        )
        if parent_id and parent_id in ignored:
            return

        author = message.author
        channel = message.channel

        # ── Содержимое ──
        content = (message.content or "").strip()
        if content:
            content_txt = _truncate(content, 2000)
            content_field = f"```{content_txt}```"
        else:
            # Нет текста — описываем что там (вложения, стикеры, embed)
            parts = []
            if message.attachments:
                parts.append("📎 вложения")
            if message.stickers:
                parts.append("🌟 стикеры: " + ", ".join(s.name for s in message.stickers))
            if message.embeds:
                parts.append(f"📦 embed ({len(message.embeds)} шт.)")
            if message.poll:
                parts.append("📊 опрос")
            content_field = f"`[нет текста — {', '.join(parts) if parts else 'пустое сообщение'}]`"

        # ── Embed ──
        embed = discord.Embed(
            title="💬 Новое сообщение",
            color=COLOR_MSG,
            timestamp=message.created_at,  # точное время отправки
        )
        try:
            embed.set_author(name=str(author), icon_url=author.display_avatar.url)
        except Exception:
            embed.set_author(name=str(author))

        # Кто
        if isinstance(author, discord.Member):
            author_name = f"{author.mention}\n`{author}` (`{author.id}`)"
        else:
            author_name = f"{author.mention} (`{author.id}`)"
        embed.add_field(name="👤 Автор", value=author_name, inline=True)

        # Где
        ch_mention = channel.mention if hasattr(channel, "mention") else f"#{getattr(channel, 'name', '?')}"
        ch_name = getattr(channel, "name", "?")
        embed.add_field(name="📁 Канал", value=f"{ch_mention}\n`#{ch_name}`", inline=True)

        # Когда — Discord сам покажет в часовом поясе читающего + точное UTC в футере
        try:
            time_str = discord.utils.format_dt(message.created_at, "F")
        except Exception:
            time_str = ts(message.created_at)
        embed.add_field(name="🕐 Время", value=time_str, inline=True)

        # Ответ на сообщение
        if message.reference and message.reference.message_id:
            ref_id = message.reference.message_id
            ref_url = f"https://discord.com/channels/{message.guild.id}/{message.channel.id}/{ref_id}"
            embed.add_field(name="↩️ Ответ на", value=f"[Перейти к сообщению]({ref_url})", inline=True)

        embed.add_field(name="📝 Содержимое", value=content_field, inline=False)

        # Вложения
        if message.attachments:
            att_lines = []
            for a in message.attachments[:5]:
                size_kb = a.size / 1024
                size_str = f"{size_kb:.0f} КБ" if size_kb < 1024 else f"{size_kb / 1024:.1f} МБ"
                att_lines.append(f"📎 [{a.filename}]({a.url}) ({size_str})")
            if len(message.attachments) > 5:
                att_lines.append(f"…и ещё {len(message.attachments) - 5}")
            embed.add_field(name="📎 Вложения", value="\n".join(att_lines)[:1000], inline=False)
            # Первую картинку показываем превью
            first = message.attachments[0]
            try:
                if first.filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                    embed.set_image(url=first.url)
            except Exception:
                pass

        embed.add_field(name="🔗 Ссылка", value=f"[Перейти к сообщению]({message.jump_url})", inline=True)

        embed.set_footer(text=f"ID сообщения: {message.id} • {ts(message.created_at)}")

        try:
            await log_ch.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass
        except Exception:
            pass

    # ════════════════════════════════════════════════════════════════════════
    # ⚙️ КОМАНДЫ УПРАВЛЕНИЯ
    # ════════════════════════════════════════════════════════════════════════
    log_group = app_commands.Group(
        name="лог-сообщений",
        description="💬 Настройка логирования каждого сообщения",
        default_permissions=discord.Permissions(administrator=True),
    )

    @log_group.command(name="канал", description="📌 Установить текущий канал для логов сообщений")
    async def set_channel(self, interaction: discord.Interaction):
        await db.set("msglog.channel_id", interaction.channel.id)
        await db.set("msglog.enabled", True)
        await interaction.response.send_message(
            f"✅ Логи сообщений → {interaction.channel.mention}\nЛогирование **включено**.",
            ephemeral=True,
        )

    @log_group.command(name="вкл", description="✅ Включить логирование сообщений")
    async def enable(self, interaction: discord.Interaction):
        ch_id = await db.get("msglog.channel_id", BotConfig.MSG_LOG_CHANNEL_ID)
        if not ch_id:
            await interaction.response.send_message(
                "❌ Канал не настроен. Сначала `/лог-сообщений канал`.", ephemeral=True
            )
            return
        await db.set("msglog.enabled", True)
        await interaction.response.send_message("✅ Логирование сообщений включено.", ephemeral=True)

    @log_group.command(name="выкл", description="🔕 Выключить логирование сообщений")
    async def disable(self, interaction: discord.Interaction):
        await db.set("msglog.enabled", False)
        await interaction.response.send_message("✅ Логирование сообщений выключено.", ephemeral=True)

    @log_group.command(name="боты", description="🤖 Логировать ли сообщения ботов")
    @app_commands.describe(включить="True = логировать ботов, False = пропускать")
    async def set_bots(self, interaction: discord.Interaction, включить: bool):
        await db.set("msglog.log_bots", включить)
        state = "✅ будут логироваться" if включить else "❌ будут пропускаться"
        await interaction.response.send_message(
            f"Сообщения ботов {state}.", ephemeral=True
        )

    @log_group.command(name="статус", description="📋 Текущие настройки логов сообщений")
    async def status(self, interaction: discord.Interaction):
        enabled = await is_logging_enabled()
        ch_id = await db.get("msglog.channel_id", BotConfig.MSG_LOG_CHANNEL_ID)
        log_bots = await is_log_bots()
        ignored = await get_ignored_channels()

        embed = discord.Embed(title="💬 Логирование сообщений", color=COLOR_MSG)
        embed.add_field(name="Статус", value="✅ Включено" if enabled else "❌ Выключено", inline=True)
        embed.add_field(name="Канал", value=f"<#{ch_id}>" if ch_id else "⚠️ не настроен", inline=True)
        embed.add_field(name="Боты", value="✅ Да" if log_bots else "❌ Нет", inline=True)
        if ignored:
            embed.add_field(
                name=f"Игнор каналов ({len(ignored)})",
                value=" ".join(f"<#{c}>" for c in ignored[:10]) + (f"\n…и ещё {len(ignored) - 10}" if len(ignored) > 10 else ""),
                inline=False,
            )
        else:
            embed.add_field(name="Игнор каналов", value="—", inline=False)
        embed.add_field(
            name="Что логируется",
            value="👤 Кто отправил\n📁 В каком канале\n🕐 Во сколько\n📝 Текст + 📎 вложения + 🔗 ссылка",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Подгруппа «игнор» ──
    ignore_group = app_commands.Group(
        name="игнор",
        description="🚫 Каналы-исключения для логов сообщений",
        parent=log_group,
    )

    @ignore_group.command(name="добавить", description="➕ Не логировать сообщения из канала")
    @app_commands.describe(канал="Канал для исключения")
    async def ignore_add(self, interaction: discord.Interaction, канал: discord.TextChannel):
        ignored = await get_ignored_channels()
        if канал.id in ignored:
            await interaction.response.send_message(f"{канал.mention} уже в игноре.", ephemeral=True)
            return
        ignored.append(канал.id)
        await db.set("msglog.ignore_channels", ignored)
        await interaction.response.send_message(f"✅ {канал.mention} добавлен в игнор.", ephemeral=True)

    @ignore_group.command(name="убрать", description="➖ Снова логировать сообщения из канала")
    @app_commands.describe(канал="Канал для возврата в логи")
    async def ignore_remove(self, interaction: discord.Interaction, канал: discord.TextChannel):
        ignored = await get_ignored_channels()
        if канал.id not in ignored:
            await interaction.response.send_message(f"{канал.mention} нет в игноре.", ephemeral=True)
            return
        ignored.remove(канал.id)
        await db.set("msglog.ignore_channels", ignored)
        await interaction.response.send_message(f"✅ {канал.mention} убран из игнора.", ephemeral=True)

    @ignore_group.command(name="список", description="📋 Показать игнорируемые каналы")
    async def ignore_list(self, interaction: discord.Interaction):
        ignored = await get_ignored_channels()
        if not ignored:
            await interaction.response.send_message("Игнор-список пуст — логируются все каналы.", ephemeral=True)
            return
        await interaction.response.send_message(
            "🚫 Игнорируются:\n" + "\n".join(f"• <#{c}> (`{c}`)" for c in ignored),
            ephemeral=True,
        )

    @ignore_group.command(name="очистить", description="🗑️ Убрать все каналы из игнора")
    async def ignore_clear(self, interaction: discord.Interaction):
        await db.set("msglog.ignore_channels", [])
        await interaction.response.send_message("✅ Игнор-список очищен.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(MsgLoggerCog(bot))
