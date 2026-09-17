"""
📊 СИСТЕМА СТАТИСТИКИ И РЕЙТИНГА ПЕРСОНАЛА
- Личная статистика сотрудника (тикеты, наказания, заявки, активное время)
- Рейтинг / Топ персонала за неделю и за всё время
- Разделение на категории: Персонал сервера, Персонал дискорда, Билдеры
- Управление ролями персонала отдельно по каждой категории
- Автоматический учёт времени в войсе и текстовой активности
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import datetime
import time
from typing import Optional, Literal

from config import BotConfig
from database import db
from utils.staff_tracker import (
    STAFF_CATEGORIES,
    get_staff_roles_config,
    add_staff_role,
    remove_staff_role,
    reset_staff_roles,
    get_member_categories,
    get_member_categories_sync,
    is_staff,
    record_voice_time,
    record_text_activity,
    get_staff_stats,
    format_time,
    reset_weekly_stats,
    reset_user_stats,
    reset_all_staff_stats,
)
from utils.checks import custom_check


# ─── ИНТЕРАКТИВНЫЕ ВЬЮ ДЛЯ ЛИЧНОЙ СТАТИСТИКИ ──────────────────────────────────
class StaffStatsView(discord.ui.View):
    def __init__(self, target_member: discord.Member, current_period: str = "week", cog: Optional["StaffStatsCog"] = None):
        super().__init__(timeout=180)
        self.target_member = target_member
        self.current_period = current_period
        self.cog = cog
        self._update_buttons()

    def _update_buttons(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "btn_week":
                    child.style = discord.ButtonStyle.primary if self.current_period == "week" else discord.ButtonStyle.secondary
                elif child.custom_id == "btn_all":
                    child.style = discord.ButtonStyle.primary if self.current_period == "all" else discord.ButtonStyle.secondary

    @discord.ui.button(label="📅 За неделю", style=discord.ButtonStyle.primary, custom_id="btn_week")
    async def btn_week(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_period = "week"
        self._update_buttons()
        embed = await self.cog.build_personal_stats_embed(self.target_member, self.current_period)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🏆 За всё время", style=discord.ButtonStyle.secondary, custom_id="btn_all")
    async def btn_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_period = "all"
        self._update_buttons()
        embed = await self.cog.build_personal_stats_embed(self.target_member, self.current_period)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🔄 Обновить", style=discord.ButtonStyle.secondary, emoji="🔄", custom_id="btn_refresh")
    async def btn_refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = await self.cog.build_personal_stats_embed(self.target_member, self.current_period)
        await interaction.response.edit_message(embed=embed, view=self)


# ─── ИНТЕРАКТИВНЫЕ ВЬЮ ДЛЯ РЕЙТИНГА ──────────────────────────────────────────
class StaffLeaderboardView(discord.ui.View):
    def __init__(self, guild: discord.Guild, category: str = "all", period: str = "week",
                 sort_by: str = "points", cog: Optional["StaffStatsCog"] = None):
        super().__init__(timeout=240)
        self.guild = guild
        self.category = category
        self.period = period
        self.sort_by = sort_by
        self.cog = cog
        self._update_button_styles()

    def _update_button_styles(self):
        cat_ids = {
            "cat_all": "all",
            "cat_server": "server",
            "cat_discord": "discord",
            "cat_builder": "builder",
        }
        period_ids = {
            "per_week": "week",
            "per_all": "all",
        }
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                cid = child.custom_id
                if cid in cat_ids:
                    child.style = discord.ButtonStyle.primary if self.category == cat_ids[cid] else discord.ButtonStyle.secondary
                elif cid in period_ids:
                    child.style = discord.ButtonStyle.success if self.period == period_ids[cid] else discord.ButtonStyle.secondary

    async def _update_message(self, interaction: discord.Interaction):
        self._update_button_styles()
        embed = await self.cog.build_leaderboard_embed(
            guild=self.guild,
            category=self.category,
            period=self.period,
            sort_by=self.sort_by,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    # Категории (Row 0)
    @discord.ui.button(label="🌐 Все", style=discord.ButtonStyle.primary, row=0, custom_id="cat_all")
    async def btn_cat_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.category = "all"
        await self._update_message(interaction)

    @discord.ui.button(label="🛡️ Сервер", style=discord.ButtonStyle.secondary, row=0, custom_id="cat_server")
    async def btn_cat_server(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.category = "server"
        await self._update_message(interaction)

    @discord.ui.button(label="⚙️ Дискорд", style=discord.ButtonStyle.secondary, row=0, custom_id="cat_discord")
    async def btn_cat_discord(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.category = "discord"
        await self._update_message(interaction)

    @discord.ui.button(label="🔨 Билдеры", style=discord.ButtonStyle.secondary, row=0, custom_id="cat_builder")
    async def btn_cat_builder(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.category = "builder"
        await self._update_message(interaction)

    # Период и обновление (Row 1)
    @discord.ui.button(label="📅 За неделю", style=discord.ButtonStyle.success, row=1, custom_id="per_week")
    async def btn_per_week(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.period = "week"
        await self._update_message(interaction)

    @discord.ui.button(label="🏆 За всё время", style=discord.ButtonStyle.secondary, row=1, custom_id="per_all")
    async def btn_per_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.period = "all"
        await self._update_message(interaction)

    @discord.ui.button(label="🔄 Обновить", style=discord.ButtonStyle.secondary, emoji="🔄", row=1, custom_id="lb_refresh")
    async def btn_lb_refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._update_message(interaction)


# ─── ИНТЕРАКТИВНОЕ МЕНЮ НАСТРОЙКИ РОЛЕЙ ──────────────────────────────────────
class RolePickerSelect(discord.ui.RoleSelect):
    def __init__(self, category_key: str, placeholder: str):
        self.category_key = category_key
        super().__init__(
            placeholder=placeholder,
            min_values=0,
            max_values=10,
            row=0,
            custom_id=f"role_picker_{category_key}",
        )

    async def callback(self, interaction: discord.Interaction):
        cfg = await get_staff_roles_config()
        selected_ids = [r.id for r in self.values]
        cfg[self.category_key] = selected_ids
        await db.set("staff_roles", cfg)

        cat_info = STAFF_CATEGORIES[self.category_key]
        roles_str = " ".join(f"<@&{rid}>" for rid in selected_ids) if selected_ids else "*(нет ролей)*"
        await interaction.response.send_message(
            f"✅ Роли для **{cat_info['emoji']} {cat_info['name']}** обновлены:\n{roles_str}",
            ephemeral=True,
        )


class StaffRoleInteractiveView(discord.ui.View):
    def __init__(self, category_key: str):
        super().__init__(timeout=180)
        self.category_key = category_key
        cat_info = STAFF_CATEGORIES[category_key]
        self.add_item(RolePickerSelect(category_key, f"Выберите роли для: {cat_info['name']}..."))


# ─── ПОДТВЕРЖДЕНИЕ СБРОСА ────────────────────────────────────────────────────
class ConfirmResetView(discord.ui.View):
    def __init__(self, action_type: str, target_user: Optional[discord.Member] = None):
        super().__init__(timeout=60)
        self.action_type = action_type
        self.target_user = target_user

    @discord.ui.button(label="Подтвердить сброс", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def btn_confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True

        if self.action_type == "week":
            await reset_weekly_stats()
            msg = "✅ Недельная статистика всех сотрудников успешно сброшена."
        elif self.action_type == "user" and self.target_user:
            await reset_user_stats(self.target_user.id)
            msg = f"✅ Статистика сотрудника {self.target_user.mention} полностью сброшена."
        elif self.action_type == "all":
            await reset_all_staff_stats()
            msg = "🚨 Вся статистика персонала сервера полностью очищена."
        else:
            msg = "❌ Неизвестное действие."

        await interaction.response.edit_message(content=msg, view=None)

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="❌ Сброс отменён.", view=None)


# ─── ОСНОВНОЙ COG ────────────────────────────────────────────────────────────
class StaffStatsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Хранение времени входа в войс: {user_id: timestamp}
        self.active_voice: dict[int, float] = {}
        # Хранение времени последнего сообщения: {user_id: timestamp}
        self.last_msg_times: dict[int, float] = {}

        self.voice_flush_task.start()

    def cog_unload(self):
        self.voice_flush_task.cancel()
        # Сохраняем активные войс-сессии перед выгрузкой
        now = time.time()
        for uid, start in list(self.active_voice.items()):
            elapsed = int(now - start)
            if elapsed > 0:
                self.bot.loop.create_task(record_voice_time(uid, elapsed))

    @commands.Cog.listener()
    async def on_ready(self):
        """При запуске сканируем все голосовые каналы и регистрируем персонал онлайн."""
        roles_cfg = await get_staff_roles_config()
        now = time.time()
        for guild in self.bot.guilds:
            for vc in guild.voice_channels:
                if vc == guild.afk_channel:
                    continue
                for m in vc.members:
                    if m.bot:
                        continue
                    if get_member_categories_sync(m, roles_cfg) or m.guild_permissions.administrator:
                        if m.id not in self.active_voice:
                            self.active_voice[m.id] = now

    # ─── ПЕРИОДИЧЕСКИЙ СБРОС ВОЙС-СЕССИЙ (каждые 5 мин) ──────────────────────
    @tasks.loop(minutes=5)
    async def voice_flush_task(self):
        """Сбрасывает накопленное время войса каждые 5 минут чтобы не терять при перезапуске."""
        now = time.time()
        for uid, start_ts in list(self.active_voice.items()):
            elapsed = int(now - start_ts)
            if elapsed > 0:
                await record_voice_time(uid, elapsed)
                self.active_voice[uid] = now

    @voice_flush_task.before_loop
    async def before_voice_flush(self):
        await self.bot.wait_until_ready()

    # ─── ОТСЛЕЖИВАНИЕ ВОЙС-КАНАЛОВ ───────────────────────────────────────────
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member,
                                    before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return

        roles_cfg = await get_staff_roles_config()
        if not get_member_categories_sync(member, roles_cfg) and not member.guild_permissions.administrator:
            return

        now = time.time()
        guild = member.guild
        afk_channel = guild.afk_channel

        # 1. Вход в голосовой канал
        if before.channel is None and after.channel is not None:
            if after.channel != afk_channel:
                self.active_voice[member.id] = now

        # 2. Выход из голосового канала
        elif before.channel is not None and after.channel is None:
            if member.id in self.active_voice:
                start = self.active_voice.pop(member.id)
                elapsed = int(now - start)
                if elapsed > 0:
                    await record_voice_time(member.id, elapsed)

        # 3. Перемещение между каналами
        elif before.channel != after.channel and before.channel is not None and after.channel is not None:
            if after.channel == afk_channel:
                # Ушёл в AFK канал — фиксируем время до момента перехода и останавливаем
                if member.id in self.active_voice:
                    start = self.active_voice.pop(member.id)
                    elapsed = int(now - start)
                    if elapsed > 0:
                        await record_voice_time(member.id, elapsed)
            elif before.channel == afk_channel:
                # Вышел из AFK в нормальный войс — начинаем отсчёт
                self.active_voice[member.id] = now

    # ─── ОТСЛЕЖИВАНИЕ СООБЩЕНИЙ В ЧАТЕ ───────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return

        member = message.author
        if not isinstance(member, discord.Member):
            return

        roles_cfg = await get_staff_roles_config()
        if not get_member_categories_sync(member, roles_cfg) and not member.guild_permissions.administrator:
            return

        now = time.time()
        prev = self.last_msg_times.get(member.id)
        self.last_msg_times[member.id] = now

        # Если прошло менее 5 минут (300 сек) с прошлого сообщения — считаем время диалога
        if prev and (now - prev) <= 300:
            session_seconds = int(now - prev)
            await record_text_activity(member.id, seconds=session_seconds, messages_count=1)
        else:
            # Одиночное сообщение — засчитываем 60 секунд активного присутствия
            await record_text_activity(member.id, seconds=60, messages_count=1)

    # ─── ГЕНЕРАЦИЯ EMBED ЛИЧНОЙ СТАТИСТИКИ ───────────────────────────────────
    async def build_personal_stats_embed(self, member: discord.Member, period: str = "week") -> discord.Embed:
        live_voice = 0
        if member.id in self.active_voice:
            live_voice = int(time.time() - self.active_voice[member.id])

        stats_data = await get_staff_stats(member.id, live_voice_seconds=live_voice)
        roles_cfg = await get_staff_roles_config()
        categories = get_member_categories_sync(member, roles_cfg)

        st = stats_data["weekly"] if period == "week" else stats_data["all_time"]
        other_st = stats_data["all_time"] if period == "week" else stats_data["weekly"]

        period_title = "📅 За последние 7 дней" if period == "week" else "🏆 За всё время"
        color = member.color if member.color != discord.Color.default() else 0x5865f2

        embed = discord.Embed(
            title=f"📊 Статистика сотрудника: {member.display_name}",
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        # Бейджи категорий персонала
        cat_labels = []
        for c in categories:
            info = STAFF_CATEGORIES.get(c)
            if info:
                cat_labels.append(f"{info['emoji']} **{info['name']}**")
        if not cat_labels:
            cat_labels.append("👑 **Руководство / Администратор**" if member.guild_permissions.administrator else "⚠️ *Роли не назначены*")

        embed.description = (
            f"**Сотрудник:** {member.mention} (`{member.id}`)\n"
            f"**Направление:** {', '.join(cat_labels)}\n"
            f"**Режим отображения:** `{period_title}`"
        )

        # Статус голосового канала
        voice_status = "⚫ Не в войсе"
        if member.voice and member.voice.channel:
            if member.voice.channel == member.guild.afk_channel:
                voice_status = f"💤 В AFK-канале: `{member.voice.channel.name}`"
            else:
                elapsed_cur = format_time(live_voice)
                voice_status = f"🟢 В войсе: **#{member.voice.channel.name}** *({elapsed_cur})*"

        # Блоки статистики
        tot_active_str = format_time(st["total_active_seconds"])
        voice_str = format_time(st["voice_seconds"])
        text_str = format_time(st["text_seconds"])

        embed.add_field(
            name="⏱️ Активное время",
            value=(
                f"**Всего:** `{tot_active_str}`\n"
                f"🎙️ В голосовых: `{voice_str}`\n"
                f"💬 В текстовых: `{text_str}`\n"
                f"📡 {voice_status}"
            ),
            inline=True,
        )

        # Тикеты
        tickets_cur = st["tickets"]
        tickets_other = other_st["tickets"]
        other_label = "за всё время" if period == "week" else "за неделю"
        embed.add_field(
            name="📩 Закрыто тикетов",
            value=f"**{tickets_cur}**\n_({tickets_other} {other_label})_",
            inline=True,
        )

        # Заявки
        apps_cur = st["applications"]
        apps_acc = st["apps_accepted"]
        apps_rej = st["apps_rejected"]
        embed.add_field(
            name="📋 Проверено заявок",
            value=(
                f"**{apps_cur}** всего\n"
                f"✅ Принято: `{apps_acc}`\n"
                f"❌ Отклонено: `{apps_rej}`"
            ),
            inline=True,
        )

        # Наказания
        pun_cur = st["punishments"]
        by_act = st["punishments_by_action"]
        pun_details = []
        if by_act.get("mute"): pun_details.append(f"Мутов: `{by_act['mute']}`")
        if by_act.get("ban"): pun_details.append(f"Банов: `{by_act['ban']}`")
        if by_act.get("kick"): pun_details.append(f"Киков: `{by_act['kick']}`")
        if by_act.get("warn"): pun_details.append(f"Варнов: `{by_act['warn']}`")
        if by_act.get("unmute"): pun_details.append(f"Размьютов: `{by_act['unmute']}`")
        if by_act.get("unban"): pun_details.append(f"Разбанов: `{by_act['unban']}`")
        pun_str = " | ".join(pun_details) if pun_details else "—"

        embed.add_field(
            name="🔨 Выдано наказаний",
            value=f"**{pun_cur}** всего\n{pun_str}",
            inline=False,
        )

        # Сообщения и баллы
        embed.add_field(
            name="💬 Сообщений в чате",
            value=f"**{st['messages']}** шт.",
            inline=True,
        )
        embed.add_field(
            name="⭐ Баллы рейтинга",
            value=f"**{st['points']}** очков",
            inline=True,
        )

        # Последняя активность
        last_seen_ts = stats_data.get("last_seen")
        last_seen_str = f"<t:{last_seen_ts}:R>" if last_seen_ts else "Не зафиксировано"
        embed.add_field(
            name="🕒 Последняя активность",
            value=last_seen_str,
            inline=True,
        )

        # Последние действия (до 4)
        recent = stats_data.get("recent_actions", [])
        if recent:
            recent_lines = []
            for item in recent[:4]:
                ts_str = f"<t:{item['ts']}:R>" if "ts" in item else ""
                recent_lines.append(f"• {item.get('text', 'Действие')} {ts_str}")
            embed.add_field(name="📜 Последние действия", value="\n".join(recent_lines), inline=False)

        embed.set_footer(text=f"{member.guild.name} • Рейтинг обновляется в реальном времени")
        return embed

    # ─── ГЕНЕРАЦИЯ EMBED ТОПА/РЕЙТИНГА ───────────────────────────────────────
    async def build_leaderboard_embed(self, guild: discord.Guild, category: str = "all",
                                      period: str = "week", sort_by: str = "points") -> discord.Embed:
        roles_cfg = await get_staff_roles_config()
        all_stats = await db.get("staff_stats", {})

        period_key = "weekly" if period == "week" else "all_time"
        period_label = "📅 За последние 7 дней" if period == "week" else "🏆 За всё время"

        cat_title = "🌐 Весь персонал"
        if category in STAFF_CATEGORIES:
            cat_title = f"{STAFF_CATEGORIES[category]['emoji']} {STAFF_CATEGORIES[category]['name']}"

        sort_labels = {
            "points": "⭐ По общему рейтингу",
            "tickets": "📩 По тикетам",
            "punishments": "🔨 По наказаниям",
            "applications": "📋 По заявкам",
            "active_time": "⏱️ По времени активности",
        }
        sort_label = sort_labels.get(sort_by, "⭐ По общему рейтингу")

        # Собираем всех участников сервера, подходящих под категорию
        members_list = []
        for m in guild.members:
            if m.bot:
                continue

            cats = get_member_categories_sync(m, roles_cfg)
            is_staff_mem = len(cats) > 0 or m.guild_permissions.administrator

            # Если фильтруем по конкретной категории — участник должен в неё входить
            if category != "all":
                if category not in cats:
                    continue
            else:
                if not is_staff_mem and str(m.id) not in all_stats:
                    continue

            # Живой войс
            live_voice = 0
            if m.id in self.active_voice:
                live_voice = int(time.time() - self.active_voice[m.id])

            st = await get_staff_stats(m.id, live_voice_seconds=live_voice)
            period_stats = st[period_key]

            members_list.append({
                "member": m,
                "categories": cats,
                "stats": period_stats,
            })

        # Сортировка
        if sort_by == "points":
            members_list.sort(key=lambda x: (x["stats"]["points"], x["stats"]["total_active_seconds"]), reverse=True)
        elif sort_by == "tickets":
            members_list.sort(key=lambda x: (x["stats"]["tickets"], x["stats"]["points"]), reverse=True)
        elif sort_by == "punishments":
            members_list.sort(key=lambda x: (x["stats"]["punishments"], x["stats"]["points"]), reverse=True)
        elif sort_by == "applications":
            members_list.sort(key=lambda x: (x["stats"]["applications"], x["stats"]["points"]), reverse=True)
        elif sort_by == "active_time":
            members_list.sort(key=lambda x: (x["stats"]["total_active_seconds"], x["stats"]["points"]), reverse=True)

        embed = discord.Embed(
            title=f"🏆 РЕЙТИНГ ПЕРСОНАЛА — {cat_title.upper()}",
            description=(
                f"**Период:** `{period_label}` | **Сортировка:** `{sort_label}`\n"
                f"Всего сотрудников в категории: **{len(members_list)}**\n"
                "────────────────────────────────────────"
            ),
            color=0xffd700 if period == "all" else 0x5865f2,
            timestamp=discord.utils.utcnow(),
        )

        if not members_list:
            embed.add_field(
                name="📭 Пусто",
                value="В выбранной категории ещё нет сотрудников с настроенными ролями.",
                inline=False,
            )
            embed.set_footer(text="Настроить роли можно через /персонал-роли")
            return embed

        medals = ["🥇", "🥈", "🥉"]
        lines = []

        for idx, item in enumerate(members_list[:15], start=1):
            m = item["member"]
            s = item["stats"]
            rank_badge = medals[idx - 1] if idx <= 3 else f"`{idx}.`"

            cat_emojis = "".join(STAFF_CATEGORIES[c]["emoji"] for c in item["categories"] if c in STAFF_CATEGORIES)
            cat_str = f" {cat_emojis}" if cat_emojis else ""

            time_str = format_time(s["total_active_seconds"])

            row_text = (
                f"{rank_badge} **{m.display_name}**{cat_str} ({m.mention}) — **{s['points']}** баллов\n"
                f"   📩 Тикеты: `{s['tickets']}` | 🔨 Наказания: `{s['punishments']}` | 📋 Заявки: `{s['applications']}` | ⏱️ Актив: `{time_str}`"
            )
            lines.append(row_text)

        embed.add_field(name="Таблица лидеров", value="\n\n".join(lines), inline=False)
        embed.set_footer(text=f"{guild.name} • Используйте кнопки ниже для переключения фильтров")
        return embed

    # ════════════════════════════════════════════════════════════════════════
    # 📌 КОМАНДЫ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ И ПЕРСОНАЛА
    # ════════════════════════════════════════════════════════════════════════

    # ─── /персонал-статистика ────────────────────────────────────────────────
    @app_commands.command(name="персонал-статистика", description="📊 Личная статистика сотрудника персонала")
    @custom_check()
    @app_commands.describe(
        сотрудник="Сотрудник, чью статистику нужно посмотреть (по умолчанию — ваша)",
        период="Период отображения (за неделю или за всё время)",
    )
    @app_commands.choices(период=[
        app_commands.Choice(name="📅 За неделю (7 дней)", value="week"),
        app_commands.Choice(name="🏆 За всё время",       value="all"),
    ])
    async def cmd_staff_stats(self, interaction: discord.Interaction,
                              сотрудник: Optional[discord.Member] = None,
                              период: str = "week"):
        await interaction.response.defer(ephemeral=True)
        target = сотрудник or interaction.user
        embed = await self.build_personal_stats_embed(target, period)
        view = StaffStatsView(target_member=target, current_period=period, cog=self)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    # ─── /персонал-рейтинг ───────────────────────────────────────────────────
    @app_commands.command(name="персонал-рейтинг", description="🏆 Таблица лидеров и рейтинг сотрудников персонала")
    @custom_check()
    @app_commands.describe(
        категория="Категория персонала для рейтинга",
        период="Период отображения",
        сортировка="По какому показателю сортировать",
    )
    @app_commands.choices(категория=[
        app_commands.Choice(name="🌐 Все сотрудники",      value="all"),
        app_commands.Choice(name="🛡️ Персонал сервера",     value="server"),
        app_commands.Choice(name="⚙️ Персонал дискорда",    value="discord"),
        app_commands.Choice(name="🔨 Билдеры",              value="builder"),
    ], период=[
        app_commands.Choice(name="📅 За неделю (7 дней)", value="week"),
        app_commands.Choice(name="🏆 За всё время",       value="all"),
    ], сортировка=[
        app_commands.Choice(name="⭐ По общему рейтингу",     value="points"),
        app_commands.Choice(name="📩 По закрытым тикетам",   value="tickets"),
        app_commands.Choice(name="🔨 По наказаниям",         value="punishments"),
        app_commands.Choice(name="📋 По проверенным заявкам",value="applications"),
        app_commands.Choice(name="⏱️ По времени активности", value="active_time"),
    ])
    async def cmd_staff_leaderboard(self, interaction: discord.Interaction,
                                    категория: str = "all",
                                    период: str = "week",
                                    сортировка: str = "points"):
        await interaction.response.defer(ephemeral=False)
        embed = await self.build_leaderboard_embed(
            guild=interaction.guild,
            category=категория,
            period=период,
            sort_by=сортировка,
        )
        view = StaffLeaderboardView(
            guild=interaction.guild,
            category=категория,
            period=период,
            sort_by=сортировка,
            cog=self,
        )
        await interaction.followup.send(embed=embed, view=view)

    # ════════════════════════════════════════════════════════════════════════
    # ⚙️ НАСТРОЙКА РОЛЕЙ ПЕРСОНАЛА ПО КАТЕГОРИЯМ
    # ════════════════════════════════════════════════════════════════════════
    roles_group = app_commands.Group(
        name="персонал-роли",
        description="⚙️ Настройка ролей персонала по категориям",
        default_permissions=discord.Permissions(administrator=True),
    )

    # ─── /персонал-роли список ───────────────────────────────────────────────
    @roles_group.command(name="список", description="📋 Показать настроенные роли для каждой категории персонала")
    async def roles_list(self, interaction: discord.Interaction):
        cfg = await get_staff_roles_config()
        guild = interaction.guild

        embed = discord.Embed(
            title="⚙️ Роли персонала по категориям",
            description="Сотрудники с этими ролями попадают в соответствующие топы и статистику.",
            color=0x5865f2,
        )

        for cat_key, cat_data in STAFF_CATEGORIES.items():
            r_ids = cfg.get(cat_key, [])
            roles_formatted = []
            for rid in r_ids:
                r = guild.get_role(rid)
                roles_formatted.append(r.mention if r else f"`ID: {rid}`")
            val = ", ".join(roles_formatted) if roles_formatted else "*Не настроено (добавьте через команду ниже)*"
            embed.add_field(
                name=f"{cat_data['emoji']} {cat_data['name']}",
                value=f"{cat_data['description']}\n**Роли:** {val}",
                inline=False,
            )

        embed.set_footer(text="Используйте /персонал-роли добавить/удалить/панель")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ─── /персонал-роли добавить ─────────────────────────────────────────────
    @roles_group.command(name="добавить", description="➕ Добавить роль в категорию персонала")
    @app_commands.describe(
        категория="Категория персонала",
        роль="Роль, которую нужно привязать к этой категории",
    )
    @app_commands.choices(категория=[
        app_commands.Choice(name="🛡️ Персонал сервера",  value="server"),
        app_commands.Choice(name="⚙️ Персонал дискорда", value="discord"),
        app_commands.Choice(name="🔨 Билдеры",           value="builder"),
    ])
    async def roles_add(self, interaction: discord.Interaction, категория: str, роль: discord.Role):
        success = await add_staff_role(категория, роль.id)
        cat_info = STAFF_CATEGORIES[категория]
        if success:
            await interaction.response.send_message(
                f"✅ Роль {роль.mention} успешно добавлена в категорию **{cat_info['emoji']} {cat_info['name']}**.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"ℹ️ Роль {роль.mention} уже присутствует в категории **{cat_info['emoji']} {cat_info['name']}**.",
                ephemeral=True,
            )

    # ─── /персонал-роли удалить ──────────────────────────────────────────────
    @roles_group.command(name="удалить", description="➖ Удалить роль из категории персонала")
    @app_commands.describe(
        категория="Категория персонала",
        роль="Роль, которую нужно убрать из этой категории",
    )
    @app_commands.choices(категория=[
        app_commands.Choice(name="🛡️ Персонал сервера",  value="server"),
        app_commands.Choice(name="⚙️ Персонал дискорда", value="discord"),
        app_commands.Choice(name="🔨 Билдеры",           value="builder"),
    ])
    async def roles_remove(self, interaction: discord.Interaction, категория: str, роль: discord.Role):
        success = await remove_staff_role(категория, роль.id)
        cat_info = STAFF_CATEGORIES[категория]
        if success:
            await interaction.response.send_message(
                f"✅ Роль {роль.mention} удалена из категории **{cat_info['emoji']} {cat_info['name']}**.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"❌ Роль {роль.mention} не найдена в категории **{cat_info['emoji']} {cat_info['name']}**.",
                ephemeral=True,
            )

    # ─── /персонал-роли сбросить ─────────────────────────────────────────────
    @roles_group.command(name="сбросить", description="🔄 Сбросить настройки ролей к значениям по умолчанию из config.py")
    async def roles_reset(self, interaction: discord.Interaction):
        await reset_staff_roles()
        await interaction.response.send_message(
            "✅ Настройки ролей персонала сброшены к значениям по умолчанию из `config.py`.",
            ephemeral=True,
        )

    # ─── /персонал-роли панель ───────────────────────────────────────────────
    @roles_group.command(name="панель", description="🎛️ Интерактивная панель выбора ролей для категорий")
    @app_commands.describe(категория="Для какой категории открыть выбор ролей")
    @app_commands.choices(категория=[
        app_commands.Choice(name="🛡️ Персонал сервера",  value="server"),
        app_commands.Choice(name="⚙️ Персонал дискорда", value="discord"),
        app_commands.Choice(name="🔨 Билдеры",           value="builder"),
    ])
    async def roles_panel(self, interaction: discord.Interaction, категория: str):
        cat_info = STAFF_CATEGORIES[категория]
        cfg = await get_staff_roles_config()
        current_roles = cfg.get(категория, [])
        roles_str = " ".join(f"<@&{r}>" for r in current_roles) if current_roles else "*(нет)*"

        embed = discord.Embed(
            title=f"🎛️ Выбор ролей: {cat_info['emoji']} {cat_info['name']}",
            description=(
                f"{cat_info['description']}\n\n"
                f"**Текущие роли:** {roles_str}\n\n"
                "Выберите роли в меню ниже. Они заменят текущий список для этой категории."
            ),
            color=cat_info["color"],
        )
        view = StaffRoleInteractiveView(category_key=категория)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ════════════════════════════════════════════════════════════════════════
    # 🗑️ СБРОС СТАТИСТИКИ (АДМИН)
    # ════════════════════════════════════════════════════════════════════════
    reset_group = app_commands.Group(
        name="персонал-сброс",
        description="🗑️ [АДМИН] Сброс статистики персонала",
        default_permissions=discord.Permissions(administrator=True),
    )

    @reset_group.command(name="неделя", description="📅 Сбросить недельную статистику для ВСЕХ сотрудников")
    async def reset_weekly_cmd(self, interaction: discord.Interaction):
        view = ConfirmResetView(action_type="week")
        await interaction.response.send_message(
            "⚠️ Вы уверены, что хотите сбросить **недельную** статистику для ВСЕХ сотрудников?\n"
            "Общая статистика за всё время сохранится.",
            view=view,
            ephemeral=True,
        )

    @reset_group.command(name="сотрудник", description="👤 Полностью сбросить статистику одного сотрудника")
    @app_commands.describe(сотрудник="Сотрудник, чью статистику нужно удалить")
    async def reset_user_cmd(self, interaction: discord.Interaction, сотрудник: discord.Member):
        view = ConfirmResetView(action_type="user", target_user=сотрудник)
        await interaction.response.send_message(
            f"⚠️ Вы уверены, что хотите удалить ВСЮ статистику сотрудника {сотрудник.mention}?\n"
            "Это действие нельзя отменить.",
            view=view,
            ephemeral=True,
        )

    @reset_group.command(name="полный", description="🚨 Полный сброс ВСЕЙ статистики персонала сервера")
    async def reset_all_cmd(self, interaction: discord.Interaction):
        view = ConfirmResetView(action_type="all")
        await interaction.response.send_message(
            "🚨 **ВНИМАНИЕ!** Вы собираетесь удалить ВСЮ историю и статистику ВСЕХ сотрудников сервера!\n"
            "Это действие необратимо. Подтвердите операцию:",
            view=view,
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(StaffStatsCog(bot))
