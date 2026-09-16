"""
📋 ЛОГИРОВАНИЕ КОМАНД
Записывает в указанный канал каждую слеш-команду:
  - Кто использовал (имя, упоминание, ID)
  - Когда (дата и время)
  - Какая команда и какие аргументы
  - В каком канале / сервере
  - Результат: ✅ Выполнена / ❌ Отказано / ⚠️ Ошибка

Настройка:
  LOG_CMD_CHANNEL_ID в config.py  ← ID канала (главный способ)
  /лог-команд канал               ← сменить канал через Discord
  /лог-команд выкл                ← отключить
  /лог-команд вкл                 ← включить
  /лог-команд статус              ← текущие настройки
"""

import discord
from discord.ext import commands
from discord import app_commands
import datetime

from config import BotConfig
from database import db


# ─── Цвета по результату ─────────────────────────────────────────────────────
COLOR_OK      = 0x2ecc71   # зелёный — успешно
COLOR_DENIED  = 0xe74c3c   # красный — отказано (нет прав)
COLOR_ERROR   = 0xe67e22   # оранжевый — ошибка выполнения
COLOR_UNKNOWN = 0x95a5a6   # серый — неизвестно


async def get_log_channel(guild: discord.Guild) -> discord.TextChannel | None:
    """Возвращает канал для логов команд."""
    # Приоритет: БД → config.py
    ch_id = await db.get("cmd_log.channel_id", BotConfig.LOG_CMD_CHANNEL_ID)
    if not ch_id:
        return None
    return guild.get_channel(ch_id)


async def is_logging_enabled() -> bool:
    return await db.get("cmd_log.enabled", BotConfig.LOG_CMD_CHANNEL_ID != 0)


def format_options(interaction: discord.Interaction) -> str:
    """Извлекает аргументы команды из interaction data."""
    try:
        data    = interaction.data or {}
        options = data.get("options", [])
        if not options:
            return ""
        parts = []
        for opt in options:
            val = opt.get("value")
            if val is None:
                # Подгруппа — рекурсивно
                sub_opts = opt.get("options", [])
                sub_name = opt.get("name", "")
                sub_parts = [f"{o['name']}:`{o.get('value','?')}`" for o in sub_opts]
                sub_str   = " ".join(sub_parts)
                parts.append(f"{sub_name} {sub_str}".strip())
            else:
                parts.append(f"{opt['name']}:`{str(val)[:60]}`")
        return " ".join(parts)
    except Exception:
        return ""


def get_full_command_name(interaction: discord.Interaction) -> str:
    """Возвращает полное имя команды включая группу и подгруппу."""
    try:
        data = interaction.data or {}
        name = data.get("name", "?")
        opts = data.get("options", [])
        # Подкоманда или группа
        if opts and opts[0].get("type") in (1, 2):  # SUB_COMMAND=1, SUB_COMMAND_GROUP=2
            name += f" {opts[0]['name']}"
            sub_opts = opts[0].get("options", [])
            if sub_opts and sub_opts[0].get("type") in (1, 2):
                name += f" {sub_opts[0]['name']}"
        return name
    except Exception:
        return interaction.command.name if interaction.command else "?"


class CmdLoggerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Перехватываем app_command_error для логирования отказов
        bot.tree.on_error = self._on_tree_error

    # ─── Перехват КАЖДОЙ команды ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        # Только слеш-команды (type 2 = APPLICATION_COMMAND)
        if interaction.type != discord.InteractionType.application_command:
            return
        if not interaction.guild:
            return

        # Ждём немного чтобы взаимодействие обработалось и статус стал известен
        # (мы не знаем результат в этот момент — логируем как "выполнена")
        # Ошибки поймает _on_tree_error
        await self._log(interaction, status="ok")

    async def _on_tree_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Перехватчик ошибок команд — логирует отказ или ошибку."""
        if isinstance(error, app_commands.CheckFailure):
            await self._log(interaction, status="denied")
        else:
            await self._log(interaction, status="error", error_text=str(error))

        # Стандартная обработка ошибки
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"❌ Ошибка: `{error}`", ephemeral=True
                )
        except Exception:
            pass

    async def _log(self, interaction: discord.Interaction,
                   status: str = "ok", error_text: str = ""):
        if not await is_logging_enabled():
            return

        log_ch = await get_log_channel(interaction.guild)
        if not log_ch:
            return

        cmd_name = get_full_command_name(interaction)
        args_str = format_options(interaction)
        user     = interaction.user
        channel  = interaction.channel

        # Цвет и метка
        if status == "ok":
            color  = COLOR_OK
            badge  = "✅ Выполнена"
        elif status == "denied":
            color  = COLOR_DENIED
            badge  = "❌ Отказано (нет прав)"
        else:
            color  = COLOR_ERROR
            badge  = "⚠️ Ошибка"

        embed = discord.Embed(
            title=f"/{cmd_name}",
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(
            name=f"{user.display_name} ({user.name})",
            icon_url=user.display_avatar.url,
        )

        # Аргументы
        if args_str:
            embed.add_field(name="Аргументы", value=f"`{args_str}`", inline=False)

        # Инфо строкой
        ch_mention = channel.mention if hasattr(channel, "mention") else f"#{getattr(channel, 'name', '?')}"
        embed.add_field(name="Пользователь", value=f"{user.mention} (`{user.id}`)", inline=True)
        embed.add_field(name="Канал",         value=ch_mention,                     inline=True)
        embed.add_field(name="Результат",     value=badge,                          inline=True)

        if error_text:
            embed.add_field(name="Ошибка", value=f"```{error_text[:300]}```", inline=False)

        embed.set_footer(text=f"ID: {user.id} • {interaction.guild.name}")

        try:
            await log_ch.send(embed=embed)
        except discord.Forbidden:
            pass
        except Exception:
            pass

    # ─── Команды управления ───────────────────────────────────────────────────
    log_group = app_commands.Group(
        name="лог-команд",
        description="📋 Настройка логирования команд",
        default_permissions=discord.Permissions(administrator=True),
    )

    @log_group.command(name="канал", description="📌 Установить текущий канал для логов команд")
    async def set_channel(self, interaction: discord.Interaction):
        await db.set("cmd_log.channel_id", interaction.channel.id)
        await db.set("cmd_log.enabled",    True)
        await interaction.response.send_message(
            f"✅ Логи команд → {interaction.channel.mention}\nЛогирование **включено**.",
            ephemeral=True,
        )

    @log_group.command(name="вкл", description="✅ Включить логирование команд")
    async def enable(self, interaction: discord.Interaction):
        ch_id = await db.get("cmd_log.channel_id", BotConfig.LOG_CMD_CHANNEL_ID)
        if not ch_id:
            await interaction.response.send_message(
                "❌ Канал не настроен. Сначала `/лог-команд канал`.", ephemeral=True
            )
            return
        await db.set("cmd_log.enabled", True)
        await interaction.response.send_message("✅ Логирование команд включено.", ephemeral=True)

    @log_group.command(name="выкл", description="🔕 Выключить логирование команд")
    async def disable(self, interaction: discord.Interaction):
        await db.set("cmd_log.enabled", False)
        await interaction.response.send_message("✅ Логирование команд выключено.", ephemeral=True)

    @log_group.command(name="статус", description="📋 Текущие настройки логирования")
    async def status(self, interaction: discord.Interaction):
        enabled = await is_logging_enabled()
        ch_id   = await db.get("cmd_log.channel_id", BotConfig.LOG_CMD_CHANNEL_ID)
        ch_str  = f"<#{ch_id}>" if ch_id else "⚠️ не настроен"
        # Из config.py
        cfg_ch  = f"`{BotConfig.LOG_CMD_CHANNEL_ID}`" if BotConfig.LOG_CMD_CHANNEL_ID else "не задан"

        embed = discord.Embed(title="📋 Логирование команд", color=0x5865f2)
        embed.add_field(name="Статус",          value="✅ Включено" if enabled else "❌ Выключено", inline=True)
        embed.add_field(name="Канал",           value=ch_str,                                       inline=True)
        embed.add_field(name="Канал (config)",  value=cfg_ch,                                       inline=True)
        embed.add_field(
            name="Что логируется",
            value=(
                "✅ Успешно выполненные команды\n"
                "❌ Отказы (нет прав)\n"
                "⚠️ Ошибки выполнения"
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(CmdLoggerCog(bot))
