"""
📋 СИСТЕМА ЗАЯВОК В КОМАНДУ
- Типы: Персонал / ДС-Адм / Билдеры
- Причина при отклонении → в канал + ЛС
- Кулдаун на заявки
- Пинг ролей + пинг игрока в сообщении
"""

import discord
from discord.ext import commands
from discord import app_commands
import datetime
import re

from config import BotConfig
from database import db
from utils.helpers import mentions_from_ids
from texts import T

APP_TYPES = {
    "персонал": {"label": T.APP_TYPE_STAFF,   "channel": BotConfig.APP_CH_STAFF,    "ping_ids": BotConfig.STAFF_PING_ROLES,    "color": 0x3498db},
    "дс-адм":  {"label": T.APP_TYPE_DS,       "channel": BotConfig.APP_CH_DS_ADMIN, "ping_ids": BotConfig.DS_ADMIN_PING_ROLES, "color": 0x9b59b6},
    "билдеры": {"label": T.APP_TYPE_BUILDER,  "channel": BotConfig.APP_CH_BUILDER,  "ping_ids": BotConfig.BUILDER_PING_ROLES,  "color": 0xe67e22},
}


# ─── ОТКЛОНЕНИЕ ──────────────────────────────────────────────────────────────
class RejectModal(discord.ui.Modal):
    def __init__(self, app_message: discord.Message):
        super().__init__(title=T.APP_REJECT_MODAL_TITLE)
        self.app_message = app_message
        self.reason = discord.ui.TextInput(
            label=T.APP_REJECT_REASON_LABEL,
            style=discord.TextStyle.paragraph,
            required=True,
            placeholder=T.APP_REJECT_REASON_PH,
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
        new_embed.add_field(name="❌ Статус", value=T.APP_REJECTED_STATUS.format(mod=interaction.user.mention), inline=False)
        new_embed.add_field(name=T.APP_REJECTED_REASON_FIELD, value=f"```{self.reason.value}```", inline=False)
        await self.app_message.edit(embed=new_embed, view=None)

        if applicant:
            try:
                dm_embed = discord.Embed(
                    title=T.APP_REJECTED_DM_TITLE,
                    description=T.APP_REJECTED_DM_DESC.format(guild=interaction.guild.name),
                    color=discord.Color.red(), timestamp=discord.utils.utcnow(),
                )
                dm_embed.add_field(name="Причина:", value=f"```{self.reason.value}```")
                dm_embed.set_footer(text=T.APP_REJECTED_DM_FOOTER)
                await applicant.send(embed=dm_embed)
            except discord.Forbidden:
                pass

        # Учёт в статистике персонала
        try:
            from utils.staff_tracker import record_application_reviewed
            app_type = "персонал"
            title_lower = (embed.title or "").lower()
            if "дс" in title_lower or "дискорд" in title_lower:
                app_type = "дс-адм"
            elif "билдер" in title_lower:
                app_type = "билдеры"
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

        await interaction.response.send_message("❌ Заявка отклонена, игрок уведомлён.", ephemeral=True)


# ─── КНОПКИ ──────────────────────────────────────────────────────────────────
class AdminApproveView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label=T.APP_ACCEPT_BTN, style=discord.ButtonStyle.success,
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
        new_embed.add_field(name="✅ Статус", value=T.APP_ACCEPTED_STATUS.format(mod=interaction.user.mention), inline=False)
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
            app_type = "персонал"
            title_lower = (embed.title or "").lower()
            if "дс" in title_lower or "дискорд" in title_lower:
                app_type = "дс-адм"
            elif "билдер" in title_lower:
                app_type = "билдеры"
            await record_application_reviewed(
                guild_id=interaction.guild.id,
                staff_id=interaction.user.id,
                decision="accepted",
                app_type=app_type,
                applicant_id=applicant.id if applicant else (uid if user_id_match else 0),
            )
        except Exception as e:
            print(f"[StaffStats] Ошибка учёта принятия заявки: {e}")

        await interaction.response.send_message("✅ Заявка принята, игрок уведомлён.", ephemeral=True)

    @discord.ui.button(label=T.APP_REJECT_BTN, style=discord.ButtonStyle.danger,
                       emoji="❌", custom_id="app_reject_btn")
    async def btn_reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RejectModal(interaction.message))


# ─── АНКЕТА ──────────────────────────────────────────────────────────────────
class ApplicationModal(discord.ui.Modal):
    def __init__(self, app_type: str):
        super().__init__(title=f"Заявка: {APP_TYPES[app_type]['label']}")
        self.app_type = app_type
        self.q1 = discord.ui.TextInput(label=T.APP_Q1_LABEL, style=discord.TextStyle.short,
                                        required=True, placeholder=T.APP_Q1_PH, max_length=100)
        self.q2 = discord.ui.TextInput(label=T.APP_Q2_LABEL, style=discord.TextStyle.paragraph,
                                        required=True, max_length=800)
        self.add_item(self.q1); self.add_item(self.q2)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user  = interaction.user
        guild = interaction.guild

        # КД
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
            title=T.APP_NEW_TITLE.format(type=cfg["label"]),
            color=cfg["color"], timestamp=discord.utils.utcnow(),
        )
        # ✅ Игрок пингуется через mention
        embed.set_author(
            name=T.APP_AUTHOR.format(name=user.name),
            icon_url=user.display_avatar.url,
        )
        embed.add_field(name=T.APP_FIELD_NAME, value=f"```{self.q1.value}```", inline=False)
        embed.add_field(name=T.APP_FIELD_EXP,  value=f"```{self.q2.value}```", inline=False)
        embed.set_footer(text=T.APP_FOOTER.format(id=user.id))

        ping_str = mentions_from_ids(guild, cfg["ping_ids"])
        # Пингуем и роли, и самого игрока
        content = T.APP_NEW_PING.format(pings=f"{user.mention} {ping_str}".strip())
        await target.send(content=content, embed=embed, view=AdminApproveView())

        await db.set(cd_key, now_ts)
        await interaction.followup.send(T.APP_SENT_OK, ephemeral=True)


# ─── ВЫБОР ТИПА ──────────────────────────────────────────────────────────────
class ApplicationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        opts = [
            discord.SelectOption(label=T.APP_TYPE_STAFF,   value="персонал", description=T.APP_TYPE_STAFF_DESC),
            discord.SelectOption(label=T.APP_TYPE_DS,      value="дс-адм",   description=T.APP_TYPE_DS_DESC),
            discord.SelectOption(label=T.APP_TYPE_BUILDER, value="билдеры",  description=T.APP_TYPE_BUILDER_DESC),
        ]
        self.sel = discord.ui.Select(placeholder=T.APP_SELECT_PH, options=opts, custom_id="app_type_select")
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
        from config import BotTexts
        embed = discord.Embed(title=T.APP_PANEL_TITLE, description=T.APP_PANEL_DESC, color=0xffd700)
        embed.set_image(url=BotConfig.APP_BANNER)
        await interaction.channel.send(embed=embed, view=ApplicationView())
        await interaction.response.send_message("✅ Панель заявок установлена.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(ApplicationsCog(bot))
    bot.add_view(ApplicationView())
    bot.add_view(AdminApproveView())
