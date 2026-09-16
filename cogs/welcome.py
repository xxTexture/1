"""
👋 СИСТЕМА ПРИВЕТСТВИЙ
Канал задаётся в config.py → WELCOME_CHANNEL_ID
При входе нового игрока бот пишет туда сообщение с его пингом.

Дополнительно настраивается через команды:
  /добро-пожаловать текст   — изменить текст (без перезапуска бота)
  /добро-пожаловать канал   — сменить канал (без правки config.py)
  /добро-пожаловать тест    — тестовое приветствие
  /добро-пожаловать вкл     — включить
  /добро-пожаловать выкл    — отключить
  /добро-пожаловать статус  — текущие настройки

Переменные в тексте приветствия:
  {mention} — упоминание (@игрок)
  {name}    — ник игрока
  {guild}   — название сервера
  {count}   — текущее кол-во участников
"""

import discord
from discord.ext import commands
from discord import app_commands

from config import BotConfig
from database import db
from texts import T


async def get_cfg() -> dict:
    """Получить конфиг приветствий. Канал из config.py используется как дефолт."""
    stored = await db.get("welcome_config", {})
    return {
        "channel_id": stored.get("channel_id", BotConfig.WELCOME_CHANNEL_ID),
        "text":       stored.get("text",       T.WELCOME_DEFAULT),
        "enabled":    stored.get("enabled",    BotConfig.WELCOME_CHANNEL_ID != 0),
    }


async def save_cfg(cfg: dict):
    await db.set("welcome_config", cfg)


class WelcomeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        cfg = await get_cfg()
        if not cfg["enabled"] or not cfg["channel_id"]:
            return

        channel = member.guild.get_channel(cfg["channel_id"])
        if not channel:
            return

        # Подставляем переменные
        text = cfg["text"].format(
            mention=member.mention,
            name=member.name,
            guild=member.guild.name,
            count=member.guild.member_count,
        )

        embed = discord.Embed(
            title=T.WELCOME_EMBED_TITLE,
            description=text,
            color=T.WELCOME_EMBED_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        if member.guild.icon:
            embed.set_footer(text=member.guild.name, icon_url=member.guild.icon.url)

        try:
            # content=member.mention гарантирует пинг даже если {mention} не в тексте
            await channel.send(content=member.mention, embed=embed)
        except discord.Forbidden:
            pass

    # ─── Группа настройки ────────────────────────────────────────────────────
    welcome_group = app_commands.Group(
        name="добро-пожаловать",
        description="👋 Настройка приветствий новых участников",
        default_permissions=discord.Permissions(administrator=True),
    )

    @welcome_group.command(name="канал", description="📌 Установить текущий канал для приветствий")
    async def set_channel(self, interaction: discord.Interaction):
        cfg = await get_cfg()
        cfg["channel_id"] = interaction.channel.id
        cfg["enabled"]    = True
        await save_cfg(cfg)
        await interaction.response.send_message(
            f"✅ Канал приветствий → {interaction.channel.mention}\n"
            f"Приветствия **включены**.", ephemeral=True
        )

    @welcome_group.command(name="текст", description="✏️ Изменить текст приветствия")
    @app_commands.describe(текст="Переменные: {mention} {name} {guild} {count}")
    async def set_text(self, interaction: discord.Interaction, текст: str):
        cfg = await get_cfg()
        cfg["text"] = текст
        await save_cfg(cfg)
        # Предпросмотр с подстановкой
        preview = текст.format(
            mention=interaction.user.mention,
            name=interaction.user.name,
            guild=interaction.guild.name,
            count=interaction.guild.member_count,
        )
        await interaction.response.send_message(
            f"✅ Текст обновлён.\n\n**Предпросмотр:**\n{preview[:500]}", ephemeral=True
        )

    @welcome_group.command(name="тест", description="🧪 Отправить тестовое приветствие")
    async def test_welcome(self, interaction: discord.Interaction):
        cfg = await get_cfg()
        ch_id = cfg["channel_id"]
        if not ch_id:
            await interaction.response.send_message(
                "❌ Канал не настроен.\n"
                "Используй `/добро-пожаловать канал` **или** укажи `WELCOME_CHANNEL_ID` в `config.py`.",
                ephemeral=True
            )
            return

        channel = interaction.guild.get_channel(ch_id)
        if not channel:
            await interaction.response.send_message("❌ Канал не найден.", ephemeral=True)
            return

        member = interaction.user
        text   = cfg["text"].format(
            mention=member.mention,
            name=member.name,
            guild=member.guild.name,
            count=member.guild.member_count,
        )
        embed = discord.Embed(
            title=T.WELCOME_EMBED_TITLE + " (тест)",
            description=text,
            color=T.WELCOME_EMBED_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await channel.send(content=member.mention, embed=embed)
        await interaction.response.send_message(
            f"✅ Тестовое приветствие отправлено в {channel.mention}.", ephemeral=True
        )

    @welcome_group.command(name="вкл", description="🔔 Включить приветствия")
    async def enable(self, interaction: discord.Interaction):
        cfg = await get_cfg()
        if not cfg["channel_id"]:
            await interaction.response.send_message(
                "❌ Сначала установи канал: `/добро-пожаловать канал`", ephemeral=True
            )
            return
        cfg["enabled"] = True
        await save_cfg(cfg)
        await interaction.response.send_message("✅ Приветствия включены.", ephemeral=True)

    @welcome_group.command(name="выкл", description="🔕 Отключить приветствия")
    async def disable(self, interaction: discord.Interaction):
        cfg = await get_cfg()
        cfg["enabled"] = False
        await save_cfg(cfg)
        await interaction.response.send_message("✅ Приветствия отключены.", ephemeral=True)

    @welcome_group.command(name="статус", description="📋 Текущие настройки приветствий")
    async def status(self, interaction: discord.Interaction):
        cfg     = await get_cfg()
        ch_id   = cfg["channel_id"]
        enabled = cfg["enabled"]
        text    = cfg["text"]

        embed = discord.Embed(title="👋 Настройки приветствий", color=0x2ecc71)
        embed.add_field(name="Статус",  value="✅ Включены" if enabled else "❌ Выключены", inline=True)
        embed.add_field(name="Канал",   value=f"<#{ch_id}>" if ch_id else "⚠️ не настроен", inline=True)
        embed.add_field(name="Текст",   value=text[:300] + ("..." if len(text) > 300 else ""), inline=False)
        embed.set_footer(text="Переменные: {mention} {name} {guild} {count}")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(WelcomeCog(bot))
