"""
📋 СИСТЕМА ЗАЯВОК В КОМАНДУ
- Направления: Персонал / ДС-Адм / Билдеры
- Вопросы анкеты настраиваются через /заявки-настройка (до 5 вопросов)
- Причина при отклонении → в канал + ЛС
- Кулдаун на заявки
- Пинг ролей + пинг игрока в сообщении
- Панель (/setup-applications) обновляется автоматически при изменении настроек
"""

import datetime
import re

import discord
from discord import app_commands
from discord.ext import commands

from config import BotConfig
from database import db
from texts import T
from utils.forms import APP_TYPE_IDS, MAX_QUESTIONS, app_questions_sync, make_question
from utils.helpers import clean_codeblock, mentions_from_ids, set_child_label

# Статическая часть направлений (канал/пинги/цвет). Названия и описания —
# в texts.py (APP_TYPE_*), вопросы — в /заявки-настройка.
APP_TYPES = {
    "персонал": {"label_key": "APP_TYPE_STAFF",   "desc_key": "APP_TYPE_STAFF_DESC",   "channel": BotConfig.APP_CH_STAFF,    "ping_ids": BotConfig.STAFF_PING_ROLES,    "color": 0x3498DB},
    "дс-адм":  {"label_key": "APP_TYPE_DS",       "desc_key": "APP_TYPE_DS_DESC",      "channel": BotConfig.APP_CH_DS_ADMIN, "ping_ids": BotConfig.DS_ADMIN_PING_ROLES, "color": 0x9B59B6},
    "билдеры": {"label_key": "APP_TYPE_Builder",  "desc_key": "APP_TYPE_BUILDER_DESC", "channel": BotConfig.APP_CH_BUILDER,  "ping_ids": BotConfig.BUILDER_PING_ROLES,  "color": 0xE67E22},
}


def app_label(app_type: str) -> str:
    return str(getattr(T, APP_TYPES[app_type]["label_key"]))


def app_desc(app_type: str) -> str:
    return str(getattr(T, APP_TYPES[app_type]["desc_key"]))


# ─── ПАНЕЛЬ ──────────────────────────────────────────────────────────────────
def build_app_panel_embed() -> discord.Embed:
    em = discord.Embed(
        title=str(T.APP_PANEL_TITLE)[:256],
        description=str(T.APP_PANEL_DESC)[:4096],
        color=BotConfig.APP_COLOR,
    )
    if BotConfig.APP_BANNER:
        em.set_image(url=BotConfig.APP_BANNER)
    return em


async def save_panel_message(guild_id: int, channel_id: int, message_id: int):
    panels = await db.get(f"panels.apps.{guild_id}", {})
    if not isinstance(panels, dict):
        panels = {}
    panels[str(channel_id)] = message_id
    await db.set(f"panels.apps.{guild_id}", panels)


async def refresh_app_panels(bot) -> int:
    """Обновляет все сохранённые панели заявок."""
    updated = 0
    for guild in bot.guilds:
        panels = await db.get(f"panels.apps.{guild.id}", {})
        if not isinstance(panels, dict):
            continue
        for ch_id, msg_id in list(panels.items()):
            channel = guild.get_channel(int(ch_id))
            if not channel:
                continue
            try:
                message = await channel.fetch_message(int(msg_id))
                await message.edit(embed=build_app_panel_embed(), view=ApplicationView())
                updated += 1
            except discord.NotFound:
                panels.pop(str(ch_id), None)
            except (discord.Forbidden, discord.HTTPException):
                pass
        await db.set(f"panels.apps.{guild.id}", panels)
    return updated


# ─── ОТКЛОНЕНИЕ ──────────────────────────────────────────────────────────────
class RejectModal(discord.ui.Modal):
    def __init__(self, app_message: discord.Message):
        super().__init__(title=str(T.APP_REJECT_MODAL_TITLE)[:45],
                         custom_id="app_reject_modal")
        self.app_message = app_message
        self.reason = discord.ui.TextInput(
            label=str(T.APP_REJECT_REASON_LABEL)[:45],
            style=discord.TextStyle.paragraph,
            required=True,
            placeholder=str(T.APP_REJECT_REASON_PH)[:100],
            max_length=500,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        embed         = self.app_message.embeds[0]
        user_id_match = re.search(r"ID:\s*(\d+)", embed.footer.text or "")
        applicant     = None
        if user_id_match:
            uid       = int(user_id_match.group(1))
            applicant = interaction.client.get_user(uid) or await interaction.client.fetch_user(uid)

        new_embed = embed.copy()
        new_embed.color = discord.Color.red()
        new_embed.add_field(name=T.APP_STATUS_FIELD_NO,
                            value=T.APP_REJECTED_STATUS.format(mod=interaction.user.mention), inline=False)
        new_embed.add_field(name=T.APP_REJECTED_REASON_FIELD,
                            value=f"```{clean_codeblock(self.reason.value, 500)}```", inline=False)
        await self.app_message.edit(embed=new_embed, view=None)

        if applicant:
            try:
                dm_embed = discord.Embed(
                    title=T.APP_REJECTED_DM_TITLE,
                    description=T.APP_REJECTED_DM_DESC.format(guild=interaction.guild.name),
                    color=discord.Color.red(), timestamp=discord.utils.utcnow(),
                )
                dm_embed.add_field(name=T.APP_DM_REASON_FIELD,
                                   value=f"```{clean_codeblock(self.reason.value, 500)}```")
                dm_embed.set_footer(text=T.APP_REJECTED_DM_FOOTER)
                await applicant.send(embed=dm_embed)
            except discord.Forbidden:
                pass

        # Учёт в статистике персонала
        try:
            from utils.staff_tracker import record_application_reviewed
            app_type = detect_app_type(embed.title or "")
            await record_application_reviewed(
                guild_id=interaction.guild.id,
                staff_id=interaction.user.id,
                decision="rejected",
                app_type=app_type,
                applicant_id=applicant.id if applicant else (uid if user_id_match else 0),
                reason=self.reason.value,
            )
        except Exception as e:
            print(f"[StaffStats] Ошибка учёта отклонения заявки: {e}")

        await interaction.response.send_message(T.APP_REJECT_OK, ephemeral=True)


def detect_app_type(title: str) -> str:
    """Определяет направление заявки по заголовку embed (для статистики)."""
    title_lower = title.lower()
    if "дс" in title_lower or "дискорд" in title_lower:
        return "дс-адм"
    if "билдер" in title_lower:
        return "билдеры"
    return "персонал"


# ─── КНОПКИ РЕШЕНИЯ ──────────────────────────────────────────────────────────
class AdminApproveView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        set_child_label(self, "app_accept_btn", T.APP_ACCEPT_BTN)
        set_child_label(self, "app_reject_btn", T.APP_REJECT_BTN)

    @discord.ui.button(label="Принять", style=discord.ButtonStyle.success,
                       emoji="✅", custom_id="app_accept_btn")
    async def btn_accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed         = interaction.message.embeds[0]
        user_id_match = re.search(r"ID:\s*(\d+)", embed.footer.text or "")
        applicant     = None
        if user_id_match:
            uid       = int(user_id_match.group(1))
            applicant = interaction.client.get_user(uid) or await interaction.client.fetch_user(uid)

        new_embed = embed.copy()
        new_embed.color = discord.Color.green()
        new_embed.add_field(name=T.APP_STATUS_FIELD_OK,
                            value=T.APP_ACCEPTED_STATUS.format(mod=interaction.user.mention), inline=False)
        await interaction.message.edit(embed=new_embed, view=None)

        if applicant:
            try:
                dm_embed = discord.Embed(
                    title=T.APP_ACCEPTED_DM_TITLE,
                    description=T.APP_ACCEPTED_DM_DESC.format(guild=interaction.guild.name),
                    color=discord.Color.green(), timestamp=discord.utils.utcnow(),
                )
                await applicant.send(embed=dm_embed)
            except discord.Forbidden:
                pass

        # Учёт в статистике персонала
        try:
            from utils.staff_tracker import record_application_reviewed
            app_type = detect_app_type(embed.title or "")
            await record_application_reviewed(
                guild_id=interaction.guild.id,
                staff_id=interaction.user.id,
                decision="accepted",
                app_type=app_type,
                applicant_id=applicant.id if applicant else (uid if user_id_match else 0),
            )
        except Exception as e:
            print(f"[StaffStats] Ошибка учёта принятия заявки: {e}")

        await interaction.response.send_message(T.APP_ACCEPT_OK, ephemeral=True)

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.danger,
                       emoji="❌", custom_id="app_reject_btn")
    async def btn_reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RejectModal(interaction.message))


# ─── АНКЕТА (ФОРМА) ──────────────────────────────────────────────────────────
class ApplicationModal(discord.ui.Modal):
    """Анкета направления. Вопросы берутся из /заявки-настройка."""

    def __init__(self, app_type: str):
        title = str(T.APP_MODAL_TITLE)
        try:
            title = title.format(type=app_label(app_type))
        except (KeyError, IndexError):
            pass
        super().__init__(title=title[:45], custom_id=f"app_modal_{APP_TYPE_IDS.index(app_type)}")
        self.app_type = app_type
        self.inputs: list[tuple[dict, discord.ui.TextInput]] = []

        questions = app_questions_sync(app_type)[:MAX_QUESTIONS]
        if not questions:
            questions = [make_question(T.APP_Q1_LABEL, T.APP_Q1_PH, True, False, 100)]
        for q in questions:
            ti = discord.ui.TextInput(
                label=str(q.get("label", "Вопрос"))[:45],
                style=discord.TextStyle.paragraph if q.get("long") else discord.TextStyle.short,
                required=bool(q.get("required", True)),
                placeholder=str(q.get("placeholder") or "")[:100] or None,
                max_length=max(1, min(int(q.get("max_length", 1000)), 4000)),
            )
            self.add_item(ti)
            self.inputs.append((q, ti))

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user  = interaction.user
        guild = interaction.guild

        # Кулдаун
        cd_key    = f"app_cd.{user.id}.{self.app_type}"
        last_ts   = await db.get(cd_key, 0)
        now_ts    = datetime.datetime.utcnow().timestamp()
        remaining = BotConfig.APPLICATION_COOLDOWN - (now_ts - last_ts)
        if remaining > 0:
            h = int(remaining // 3600); m = int((remaining % 3600) // 60)
            await interaction.followup.send(T.APP_CD_MSG.format(h=h, m=m), ephemeral=True); return

        cfg    = APP_TYPES[self.app_type]
        target = guild.get_channel(cfg["channel"])
        if not target:
            await interaction.followup.send(T.APP_NO_CHANNEL, ephemeral=True); return

        embed = discord.Embed(
            title=T.APP_NEW_TITLE.format(type=app_label(self.app_type)),
            color=cfg["color"], timestamp=discord.utils.utcnow(),
        )
        embed.set_author(
            name=T.APP_AUTHOR.format(name=user.name),
            icon_url=user.display_avatar.url,
        )
        for q, ti in self.inputs:
            value = (ti.value or "").strip()
            if not value:
                continue
            embed.add_field(
                name=str(q.get("label", "?"))[:256],
                value=f"```{clean_codeblock(value, 900)}```",
                inline=False,
            )
        embed.set_footer(text=T.APP_FOOTER.format(id=user.id))

        ping_str = mentions_from_ids(guild, [r for r in cfg["ping_ids"] if r])
        # Пингуем и роли, и самого игрока
        content = T.APP_NEW_PING.format(pings=f"{user.mention} {ping_str}".strip())
        await target.send(content=content, embed=embed, view=AdminApproveView())

        await db.set(cd_key, now_ts)
        await interaction.followup.send(T.APP_SENT_OK, ephemeral=True)


# ─── ВЫБОР НАПРАВЛЕНИЯ ───────────────────────────────────────────────────────
class ApplicationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        opts = [
            discord.SelectOption(
                label=app_label(aid)[:100], value=aid, description=app_desc(aid)[:100],
            )
            for aid in APP_TYPE_IDS
        ]
        self.sel = discord.ui.Select(placeholder=str(T.APP_SELECT_PH)[:100],
                                     options=opts, custom_id="app_type_select")
        self.sel.callback = self._cb
        self.add_item(self.sel)

    async def _cb(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ApplicationModal(self.sel.values[0]))


# ─── COG ─────────────────────────────────────────────────────────────────────
class ApplicationsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="setup-applications", description="📋 [АДМИН] Установить панель заявок")
    @app_commands.default_permissions(administrator=True)
    async def setup_apps(self, interaction: discord.Interaction):
        message = await interaction.channel.send(embed=build_app_panel_embed(), view=ApplicationView())
        await save_panel_message(interaction.guild.id, interaction.channel.id, message.id)
        await interaction.response.send_message(T.APP_PANEL_OK, ephemeral=True)


async def setup(bot):
    await bot.add_cog(ApplicationsCog(bot))
    bot.add_view(ApplicationView())
    bot.add_view(AdminApproveView())
