"""
📋 СИСТЕМА ЗАЯВОК В КОМАНДУ
- Направления: Персонал / ДС-Адм / Билдеры
- Вопросы анкеты настраиваются через /заявки-настройка (до 10 вопросов;
  анкеты из 6+ вопросов показываются в 2 окна — лимит Discord 5 полей на окно)
- Панель как в тикетах: кнопка «Подать заявку» → меню направлений → анкета
  (текст кнопки: APP_BTN_LABEL / APP_BTN_EMOJI через /тексты)
- 🖼️ Баннер внутри анкеты: /заявки-настройка баннер <направление> <url>
- Причина при отклонении → в канал + ЛС; при принятии → ЛС (тексты через /тексты)
- Кулдаун на заявки (проверяется до открытия анкеты)
- Пинг ролей + пинг игрока в сообщении
- Панель (/setup-applications) обновляется автоматически при изменении настроек
"""

import datetime
import re
import secrets

import discord
from discord import app_commands
from discord.ext import commands

from config import BotConfig
from database import db
from texts import T
from utils.forms import (
    APP_TYPE_IDS, app_questions_sync, app_settings_sync, make_question,
)
try:  # новые константы (utils/forms.py из обновления); fallback — те же значения
    from utils.forms import MAX_APP_QUESTIONS, MODAL_PAGE_SIZE
except ImportError:
    MAX_APP_QUESTIONS = 10
    MODAL_PAGE_SIZE = 5
from utils.helpers import clean_codeblock, mentions_from_ids, set_child_label


def _txt(name: str, default: str) -> str:
    """Текст из /тексты с запасным значением (если texts.py ещё не обновлён)."""
    try:
        return str(getattr(T, name))
    except AttributeError:
        return default

# Статическая часть направлений (канал/пинги/цвет). Названия и описания —
# в texts.py (APP_TYPE_*), вопросы — в /заявки-настройка.
APP_TYPES = {
    "персонал": {"label_key": "APP_TYPE_STAFF",   "desc_key": "APP_TYPE_STAFF_DESC",   "channel": BotConfig.APP_CH_STAFF,    "ping_ids": BotConfig.STAFF_PING_ROLES,    "color": 0x3498DB},
    "дс-адм":  {"label_key": "APP_TYPE_DS",       "desc_key": "APP_TYPE_DS_DESC",      "channel": BotConfig.APP_CH_DS_ADMIN, "ping_ids": BotConfig.DS_ADMIN_PING_ROLES, "color": 0x9B59B6},
    "билдеры": {"label_key": "APP_TYPE_BUILDER",  "desc_key": "APP_TYPE_BUILDER_DESC", "channel": BotConfig.APP_CH_BUILDER,  "ping_ids": BotConfig.BUILDER_PING_ROLES,  "color": 0xE67E22},
}


# Запасные названия/описания (если texts.py ещё не обновлён)
_APP_LABEL_FALLBACK = {"персонал": "🛡️ Персонал", "дс-адм": "⚙️ ДС-Адм", "билдеры": "🔨 Билдеры"}
_APP_DESC_FALLBACK = {"персонал": "Модерация и помощь игрокам",
                      "дс-адм": "Управление Discord-сервером",
                      "билдеры": "Строительство карт и спавнов"}


def app_label(app_type: str) -> str:
    key = APP_TYPES[app_type]["label_key"]
    candidates = [key]
    if key == "APP_TYPE_BUILDER":
        candidates.append("APP_TYPE_Builder")  # старый вариант ключа из прошлых версий
    for candidate in candidates:
        try:
            return str(getattr(T, candidate))
        except AttributeError:
            continue
    return _APP_LABEL_FALLBACK.get(app_type, app_type)


def app_desc(app_type: str) -> str:
    try:
        return str(getattr(T, APP_TYPES[app_type]["desc_key"]))
    except AttributeError:
        return _APP_DESC_FALLBACK.get(app_type, "")


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
    """Обновляет все сохранённые панели заявок (заодно переводит старые на новый вид)."""
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
                await message.edit(embed=build_app_panel_embed(), view=ApplicationPanelView())
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
                    description=T.APP_REJECTED_DM_DESC.format(
                        guild=interaction.guild.name,
                        type=app_label(detect_app_type(embed.title or "")),
                    ),
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
                    description=T.APP_ACCEPTED_DM_DESC.format(
                        guild=interaction.guild.name,
                        type=app_label(detect_app_type(embed.title or "")),
                    ),
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


# ─── КУЛДАУН ─────────────────────────────────────────────────────────────────
async def cooldown_left(user_id: int, app_type: str) -> float:
    """Сколько секунд осталось до конца кулдауна (<= 0 = можно подавать)."""
    last_ts = await db.get(f"app_cd.{user_id}.{app_type}", 0)
    return BotConfig.APPLICATION_COOLDOWN - (datetime.datetime.utcnow().timestamp() - last_ts)


def cooldown_msg(remaining: float) -> str:
    h = int(remaining // 3600); m = int((remaining % 3600) // 60)
    return T.APP_CD_MSG.format(h=h, m=m)


def answer_budget(n_answers: int) -> int:
    """Символов на один ответ так, чтобы embed влез в лимит Discord (6000)."""
    return min(900, max(200, 5000 // max(1, n_answers)))


# ─── АНКЕТА (ФОРМА, ДО 10 ВОПРОСОВ = ДО 2 ОКОН) ───────────────────────────────
class ApplicationModal(discord.ui.Modal):
    """Анкета направления. Вопросы берутся из /заявки-настройка.

    Лимит Discord — 5 полей в одном окне, поэтому анкеты из 6–10 вопросов
    открываются в 2 окна подряд: ответы первой части подхватываются дальше.
    """

    def __init__(self, app_type: str, page: int = 0,
                 prev_answers: list[tuple[str, str]] | None = None,
                 user_id: int = 0, flow: str | None = None):
        base_title = str(T.APP_MODAL_TITLE)
        try:
            base_title = base_title.format(type=app_label(app_type))
        except (KeyError, IndexError):
            pass

        questions_all = app_questions_sync(app_type)
        if not questions_all:
            questions_all = [make_question(T.APP_Q1_LABEL, T.APP_Q1_PH, True, False, 100)]
        questions_all = questions_all[:MAX_APP_QUESTIONS]

        total_pages = max(1, (len(questions_all) + MODAL_PAGE_SIZE - 1) // MODAL_PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        page_qs = questions_all[page * MODAL_PAGE_SIZE:(page + 1) * MODAL_PAGE_SIZE]

        title = base_title
        if total_pages > 1:
            title = f"{base_title} ({page + 1}/{total_pages})"

        # Уникальный custom_id на каждую подачу: параллельные заявки разных
        # игроков (и повторные открытия) не должны пересекаться.
        flow = flow or secrets.token_hex(3)
        try:
            idx = APP_TYPE_IDS.index(app_type)
        except ValueError:
            idx = 0
        super().__init__(title=title[:45], custom_id=f"appm{idx}p{page}u{user_id or 0}f{flow}")

        self.app_type = app_type
        self.page = page
        self.total_pages = total_pages
        self.prev_answers = list(prev_answers or [])
        self._flow = flow
        self.inputs: list[tuple[dict, discord.ui.TextInput]] = []

        for q in page_qs:
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
        answers = list(self.prev_answers)
        for q, ti in self.inputs:
            answers.append((str(q.get("label", "?")), (ti.value or "").strip()))

        # Есть ещё страницы → открываем следующее окно анкеты
        if self.page + 1 < self.total_pages:
            await interaction.response.send_modal(ApplicationModal(
                self.app_type, page=self.page + 1, prev_answers=answers,
                user_id=interaction.user.id, flow=self._flow,
            ))
            return

        await self._finish(interaction, answers)

    async def _finish(self, interaction: discord.Interaction, answers: list[tuple[str, str]]):
        await interaction.response.defer(ephemeral=True)
        user  = interaction.user
        guild = interaction.guild

        # Кулдаун (повторная проверка — на случай долгого заполнения анкеты)
        remaining = await cooldown_left(user.id, self.app_type)
        if remaining > 0:
            await interaction.followup.send(cooldown_msg(remaining), ephemeral=True); return

        cfg    = APP_TYPES[self.app_type]
        target = guild.get_channel(cfg["channel"])
        if not target:
            await interaction.followup.send(T.APP_NO_CHANNEL, ephemeral=True); return

        # Настройки направления: баннер (/заявки-настройка баннер)
        settings = app_settings_sync(self.app_type)

        embed = discord.Embed(
            title=T.APP_NEW_TITLE.format(type=app_label(self.app_type)),
            color=cfg["color"], timestamp=discord.utils.utcnow(),
        )
        embed.set_author(
            name=T.APP_AUTHOR.format(name=user.name),
            icon_url=user.display_avatar.url,
        )
        filled = [(label, v) for label, v in answers if v]
        per = answer_budget(len(filled))
        for label, value in filled:
            embed.add_field(
                name=label[:256],
                value=f"```{clean_codeblock(value, per)}```",
                inline=False,
            )
        # 🖼️ Баннер внутри анкеты (настраивается: /заявки-настройка баннер)
        banner = str(settings.get("banner") or "").strip()
        if banner:
            embed.set_image(url=banner)
        embed.set_footer(text=T.APP_FOOTER.format(id=user.id))

        ping_str = mentions_from_ids(guild, [r for r in cfg["ping_ids"] if r])
        # Пингуем и роли, и самого игрока
        content = T.APP_NEW_PING.format(pings=f"{user.mention} {ping_str}".strip())
        try:
            await target.send(content=content, embed=embed, view=AdminApproveView())
        except discord.HTTPException as e:
            print(f"[Applications] Не удалось отправить заявку: {e}")
            await interaction.followup.send(T.APP_NO_CHANNEL, ephemeral=True); return

        try:
            await db.set(f"app_cd.{user.id}.{self.app_type}",
                         datetime.datetime.utcnow().timestamp())
        except Exception as e:
            # Заявка уже отправлена — ошибка записи кулдауна не должна пугать игрока
            print(f"[Applications] Не удалось сохранить кулдаун заявки: {e}")
        await interaction.followup.send(T.APP_SENT_OK, ephemeral=True)


# ─── ПАНЕЛЬ: КНОПКА → МЕНЮ → АНКЕТА (как в тикетах) ──────────────────────────
def build_app_options() -> list[discord.SelectOption]:
    """Пункты меню направлений (берутся из актуальных настроек/текстов)."""
    return [
        discord.SelectOption(
            label=app_label(aid)[:100], value=aid, description=app_desc(aid)[:100],
        )
        for aid in APP_TYPE_IDS
    ]


class AppTypeSelectView(discord.ui.View):
    """Эфемерное меню выбора направления (как выбор типа в тикетах)."""

    def __init__(self, options: list[discord.SelectOption]):
        super().__init__(timeout=120)
        sel = discord.ui.Select(
            placeholder=str(T.APP_SELECT_PH)[:100],
            options=options,
        )
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction):
        app_type = interaction.data["values"][0]
        # Кулдаун проверяем ДО анкеты, чтобы игрок не заполнял 10 полей зря
        remaining = await cooldown_left(interaction.user.id, app_type)
        if remaining > 0:
            await interaction.response.send_message(cooldown_msg(remaining), ephemeral=True)
            return
        await interaction.response.send_modal(
            ApplicationModal(app_type, user_id=interaction.user.id))


class ApplicationPanelView(discord.ui.View):
    """Персистентная панель: кнопка → меню направлений → анкета."""

    def __init__(self):
        super().__init__(timeout=None)
        set_child_label(self, "open_app_panel_btn",
                          _txt("APP_BTN_LABEL", "Подать заявку"), _txt("APP_BTN_EMOJI", "📋"))

    @discord.ui.button(label="Подать заявку", style=discord.ButtonStyle.primary,
                       emoji="📋", custom_id="open_app_panel_btn")
    async def open_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            _txt("APP_TYPE_SELECT_PROMPT", "Выберите направление:"),
            view=AppTypeSelectView(build_app_options()), ephemeral=True,
        )


class ApplicationView(discord.ui.View):
    """⚠️ Старая панель со списком (для сообщений, созданных до обновления).

    Новые панели — ApplicationPanelView (кнопка). Этот класс оставлен,
    чтобы старые панели продолжали работать после рестарта бота;
    при первом же обновлении панели она станет нового вида.
    """

    def __init__(self):
        super().__init__(timeout=None)
        self.sel = discord.ui.Select(placeholder=str(T.APP_SELECT_PH)[:100],
                                     options=build_app_options(),
                                     custom_id="app_type_select")
        self.sel.callback = self._cb
        self.add_item(self.sel)

    async def _cb(self, interaction: discord.Interaction):
        app_type = self.sel.values[0]
        remaining = await cooldown_left(interaction.user.id, app_type)
        if remaining > 0:
            await interaction.response.send_message(cooldown_msg(remaining), ephemeral=True)
            return
        await interaction.response.send_modal(
            ApplicationModal(app_type, user_id=interaction.user.id))


# ─── COG ─────────────────────────────────────────────────────────────────────
class ApplicationsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="setup-applications", description="📋 [АДМИН] Установить панель заявок")
    @app_commands.default_permissions(administrator=True)
    async def setup_apps(self, interaction: discord.Interaction):
        message = await interaction.channel.send(embed=build_app_panel_embed(), view=ApplicationPanelView())
        await save_panel_message(interaction.guild.id, interaction.channel.id, message.id)
        await interaction.response.send_message(T.APP_PANEL_OK, ephemeral=True)


async def setup(bot):
    await bot.add_cog(ApplicationsCog(bot))
    bot.add_view(ApplicationPanelView())
    bot.add_view(ApplicationView())  # старые панели (список) — до их обновления
    bot.add_view(AdminApproveView())
