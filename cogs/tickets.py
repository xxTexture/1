"""
📩 СИСТЕМА ТИКЕТОВ
- Два типа: Поддержка / Тех-поддержка
- Название канала: тип_ник
- Пинг ролей из конфига при создании
- Кулдаун на создание тикетов
- Причина при закрытии → в лог и ЛС
"""

import discord
from discord.ext import commands
from discord import app_commands
import datetime
from typing import Optional

from config import BotConfig, BotTexts
from database import db
from utils.helpers import create_transcript, mentions_from_ids

TICKET_TYPES = {
    "поддержка":     {"label": "💬 Поддержка",     "color": 0x5865f2, "ping_ids": BotConfig.SUPPORT_PING_ROLES,      "prefix": "поддержка"},
    "тех-поддержка": {"label": "⚙️ Тех-поддержка", "color": 0xe67e22, "ping_ids": BotConfig.TECH_SUPPORT_PING_ROLES, "prefix": "техподдержка"},
}

TICKET_PREFIXES = ("поддержка", "техподдержка")


def is_ticket_channel(channel: discord.TextChannel) -> bool:
    return any(channel.name.startswith(p) for p in TICKET_PREFIXES)


# ─── ЗАКРЫТИЕ ───────────────────────────────────────────────────────────────
class TicketCloseModal(discord.ui.Modal, title="Закрытие тикета"):
    reason = discord.ui.TextInput(
        label="Укажите причину закрытия:",
        style=discord.TextStyle.short,
        required=True,
        placeholder="Вопрос решён / флуд / дубликат...",
        max_length=200,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("🔄 Генерирую транскрипт и закрываю тикет...", ephemeral=True)
        channel = interaction.channel
        guild   = interaction.guild

        opener_user = None
        async for msg in channel.history(limit=20, oldest_first=True):
            if msg.author == guild.me and msg.mentions:
                opener_user = msg.mentions[0]
                break

        if opener_user:
            try:
                em = discord.Embed(
                    title="Ваш тикет закрыт",
                    description=f"Тикет **#{channel.name}** на **{guild.name}** закрыт.",
                    color=discord.Color.orange(), timestamp=discord.utils.utcnow(),
                )
                em.add_field(name="Причина:", value=f"```{self.reason.value}```")
                em.set_footer(text=f"Закрыл: {interaction.user.name}")
                await opener_user.send(embed=em)
            except discord.Forbidden:
                pass

        transcript = await create_transcript(channel)
        log_ch = guild.get_channel(BotConfig.LOG_CHANNEL_1_ID)
        if log_ch:
            log_em = discord.Embed(title="🔒 Тикет закрыт", color=discord.Color.red(), timestamp=discord.utils.utcnow())
            log_em.add_field(name="Канал",  value=f"`#{channel.name}`", inline=True)
            log_em.add_field(name="Открыл", value=opener_user.mention if opener_user else "?", inline=True)
            log_em.add_field(name="Закрыл", value=interaction.user.mention, inline=True)
            log_em.add_field(name="Причина", value=f"```{self.reason.value}```", inline=False)
            await log_ch.send(embed=log_em, file=transcript)

        log_ch2 = guild.get_channel(BotConfig.LOG_CHANNEL_2_ID)
        if log_ch2 and log_ch2 != log_ch:
            log_em2 = discord.Embed(title="🔒 Тикет закрыт", color=discord.Color.red(), timestamp=discord.utils.utcnow())
            log_em2.add_field(name="Канал",  value=f"`#{channel.name}`", inline=True)
            log_em2.add_field(name="Открыл", value=opener_user.mention if opener_user else "?", inline=True)
            log_em2.add_field(name="Закрыл", value=interaction.user.mention, inline=True)
            log_em2.add_field(name="Причина", value=f"```{self.reason.value}```", inline=False)
            await log_ch2.send(embed=log_em2)

        await channel.delete()


class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Закрыть тикет", style=discord.ButtonStyle.danger,
                       emoji="🔒", custom_id="ticket_close_btn")
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketCloseModal())


# ─── СОЗДАНИЕ ────────────────────────────────────────────────────────────────
class TicketModal(discord.ui.Modal, title="Создание обращения"):
    def __init__(self, ticket_type: str):
        super().__init__()
        self.ticket_type = ticket_type
        self.reason = discord.ui.TextInput(
            label="Кратко опишите вашу проблему:",
            style=discord.TextStyle.paragraph,
            required=True,
            placeholder="Опишите ситуацию подробно...",
            max_length=1000,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user  = interaction.user

        # КД
        cooldown_key = f"ticket_cd.{user.id}"
        last_ts      = await db.get(cooldown_key, 0)
        now_ts       = datetime.datetime.utcnow().timestamp()
        remaining    = BotConfig.TICKET_COOLDOWN - (now_ts - last_ts)
        if remaining > 0:
            await interaction.followup.send(
                f"⏳ Подожди **{int(remaining//60)}м {int(remaining%60)}с** перед созданием нового тикета.", ephemeral=True
            )
            return

        type_cfg  = TICKET_TYPES[self.ticket_type]
        safe_nick = "".join(c for c in user.name.lower() if c.isalnum() or c in "_-")[:20]
        ch_name   = f"{type_cfg['prefix']}-{safe_nick}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user:               discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me:           discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
        }
        for rid in type_cfg["ping_ids"]:
            role = guild.get_role(rid)
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        category = guild.get_channel(BotConfig.TICKET_CATEGORY_ID) if BotConfig.TICKET_CATEGORY_ID else None
        channel  = await guild.create_text_channel(name=ch_name, overwrites=overwrites, category=category)

        ping_str = mentions_from_ids(guild, type_cfg["ping_ids"])
        inner_em = discord.Embed(
            title="📞 ОЖИДАЙТЕ ОТВЕТА",
            description=f"Тип: **{type_cfg['label']}**\nАдминистрация скоро подключится.",
            color=type_cfg["color"], timestamp=discord.utils.utcnow(),
        )
        inner_em.add_field(name="📝 Причина:", value=f"```{self.reason.value}```", inline=False)
        inner_em.set_footer(text=f"Создал: {user.name}")
        await channel.send(content=f"{user.mention} {ping_str}", embed=inner_em, view=TicketCloseView())

        await db.set(cooldown_key, now_ts)
        await interaction.followup.send(f"✅ Тикет создан: {channel.mention}", ephemeral=True)


class TicketTypeSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.select(
        placeholder="Выберите тип обращения...",
        options=[
            discord.SelectOption(label="💬 Поддержка",     value="поддержка",     description="Общие вопросы, жалобы, баги"),
            discord.SelectOption(label="⚙️ Тех-поддержка", value="тех-поддержка", description="Технические проблемы"),
        ],
    )
    async def select_type(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.send_modal(TicketModal(select.values[0]))


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Создать тикет", style=discord.ButtonStyle.secondary,
                       emoji="📩", custom_id="open_ticket_panel_btn")
    async def open_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Выберите тип обращения:", view=TicketTypeSelectView(), ephemeral=True
        )


# ─── COG ────────────────────────────────────────────────────────────────────
class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="setup-tickets", description="⚙️ [АДМИН] Установить панель тикетов")
    @app_commands.default_permissions(administrator=True)
    async def setup_tickets(self, interaction: discord.Interaction):
        embed = discord.Embed(title=BotTexts.T_PANEL_TITLE, description=BotTexts.T_PANEL_DESC, color=BotTexts.T_COLOR)
        embed.set_image(url=BotConfig.TICKET_BANNER)
        await interaction.channel.send(embed=embed, view=TicketPanelView())
        await interaction.response.send_message("✅ Панель тикетов установлена.", ephemeral=True)

    ticket_group = app_commands.Group(
        name="ticket", description="Управление тикетом",
        default_permissions=discord.Permissions(manage_channels=True),
    )

    @ticket_group.command(name="add", description="➕ Добавить пользователя в тикет")
    async def ticket_add(self, interaction: discord.Interaction, игрок: discord.Member):
        if not is_ticket_channel(interaction.channel):
            await interaction.response.send_message("❌ Только в каналах тикетов.", ephemeral=True); return
        await interaction.channel.set_permissions(игрок, read_messages=True, send_messages=True)
        await interaction.response.send_message(f"✅ {игрок.mention} добавлен в тикет.")

    @ticket_group.command(name="remove", description="➖ Удалить пользователя из тикета")
    async def ticket_remove(self, interaction: discord.Interaction, игрок: discord.Member):
        if not is_ticket_channel(interaction.channel):
            await interaction.response.send_message("❌ Только в каналах тикетов.", ephemeral=True); return
        await interaction.channel.set_permissions(игрок, overwrite=None)
        await interaction.response.send_message(f"✅ {игрок.mention} удалён из тикета.")

    @ticket_group.command(name="rename", description="✏️ Переименовать канал тикета")
    async def ticket_rename(self, interaction: discord.Interaction, новое_имя: str):
        if not is_ticket_channel(interaction.channel):
            await interaction.response.send_message("❌ Только в каналах тикетов.", ephemeral=True); return
        await interaction.channel.edit(name=новое_имя)
        await interaction.response.send_message(f"✅ Канал переименован в `{новое_имя}`.")


async def setup(bot):
    await bot.add_cog(TicketsCog(bot))
    bot.add_view(TicketPanelView())
    bot.add_view(TicketCloseView())
