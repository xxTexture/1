"""
📩 СИСТЕМА ТИКЕТОВ
- Типы обращений и формы настраиваются через /тикет-настройка (data.json)
  (помощь, тех-поддержка, жалобы на игроков и персонал + свои типы)
- Название канала: префикс-ник (латиница — требование Discord)
- Пинг ролей типа при создании
- 🖼️ Баннер внутри тикета: /тикет-настройка баннер <код> <url>
- 🔁 Кнопка «Передать»: пингует старшие роли и пускает их в канал
  (/тикет-настройка передача …), текст кнопки — через /тексты (TICKET_FORWARD_BTN)
- 🔒 Кнопки «Закрыть» и «Передать» — только для персонала
  (игрок НЕ может закрыть/передать свой тикет; текст отказа: TICKET_STAFF_ONLY)
- Кулдаун на создание тикетов
- Причина при закрытии → ЛС автору + логи + транскрипт
- Панель (/setup-tickets) обновляется автоматически при изменении настроек
"""

import datetime

import discord
from discord import app_commands
from discord.ext import commands

from config import BotConfig
from database import db
from texts import T
from utils.forms import MAX_QUESTIONS, load_types, make_question, slugify, ticket_prefixes_sync
from utils.helpers import clean_codeblock, create_transcript, mentions_from_ids, set_child_label


def is_ticket_channel(channel) -> bool:
    """Канал тикета = имя начинается с префикса одного из типов."""
    name = getattr(channel, "name", "") or ""
    name = name.lower()
    return any(name.startswith(p) for p in ticket_prefixes_sync())


def type_banner(type_cfg: dict) -> str:
    """URL баннера внутри тикета для этого типа ("" = без баннера)."""
    return str(type_cfg.get("banner") or "").strip()


def _is_staff(user: discord.Member, type_cfg: dict) -> bool:
    """Может ли пользователь пользоваться служебными кнопками тикета."""
    if user.guild_permissions.manage_channels or user.guild_permissions.administrator:
        return True
    allowed = {int(r) for r in (type_cfg.get("ping_roles") or []) if r}
    allowed |= {int(r) for r in (type_cfg.get("escalate_roles") or []) if r}
    return any(r.id in allowed for r in getattr(user, "roles", []))


def build_type_options(types: dict) -> list[discord.SelectOption]:
    """Пункты выпадающего списка из типов тикетов (лимит Discord — 25)."""
    options = []
    for type_id, cfg in list(types.items())[:25]:
        label = str(cfg.get("label") or type_id)[:100]
        description = str(cfg.get("description") or "")[:100] or None
        emoji = cfg.get("emoji") or None
        if emoji and emoji in label:
            emoji = None  # не дублируем эмодзи, если он уже в названии
        options.append(discord.SelectOption(
            label=label, value=str(type_id)[:100],
            description=description, emoji=emoji,
        ))
    return options


# ─── ПАНЕЛЬ ──────────────────────────────────────────────────────────────────
def build_panel_embed() -> discord.Embed:
    em = discord.Embed(
        title=str(T.TICKET_PANEL_TITLE)[:256],
        description=str(T.TICKET_PANEL_DESC)[:4096],
        color=BotConfig.TICKET_COLOR,
    )
    if BotConfig.TICKET_BANNER:
        em.set_image(url=BotConfig.TICKET_BANNER)
    return em


async def save_panel_message(guild_id: int, channel_id: int, message_id: int):
    panels = await db.get(f"panels.tickets.{guild_id}", {})
    if not isinstance(panels, dict):
        panels = {}
    panels[str(channel_id)] = message_id
    await db.set(f"panels.tickets.{guild_id}", panels)


async def refresh_ticket_panels(bot) -> int:
    """Обновляет все сохранённые панели тикетов (после изменения настроек)."""
    updated = 0
    for guild in bot.guilds:
        panels = await db.get(f"panels.tickets.{guild.id}", {})
        if not isinstance(panels, dict):
            continue
        for ch_id, msg_id in list(panels.items()):
            channel = guild.get_channel(int(ch_id))
            if not channel:
                continue
            try:
                message = await channel.fetch_message(int(msg_id))
                await message.edit(embed=build_panel_embed(), view=TicketPanelView())
                updated += 1
            except discord.NotFound:
                panels.pop(str(ch_id), None)
            except (discord.Forbidden, discord.HTTPException):
                pass
        await db.set(f"panels.tickets.{guild.id}", panels)
    return updated


# ─── ЗАКРЫТИЕ ────────────────────────────────────────────────────────────────
class TicketCloseModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title=str(T.TICKET_CLOSE_MODAL_TITLE)[:45],
                         custom_id="ticket_close_modal")
        self.reason = discord.ui.TextInput(
            label=str(T.TICKET_CLOSE_REASON_LABEL)[:45],
            style=discord.TextStyle.short,
            required=True,
            placeholder=str(T.TICKET_CLOSE_REASON_PH)[:100],
            max_length=200,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        # 🔒 Контрольная проверка прав — игрок не может закрыть свой тикет
        #    (даже если дошёл до формы через /ticket close)
        channel  = interaction.channel
        meta     = await db.get(f"tickets.{channel.id}", {})
        type_id  = meta.get("type") if isinstance(meta, dict) else None
        type_cfg = (await load_types()).get(type_id, {}) if type_id else {}
        if is_ticket_channel(channel) and not _is_staff(interaction.user, type_cfg):
            await interaction.response.send_message(T.TICKET_STAFF_ONLY, ephemeral=True)
            return

        await interaction.response.send_message(T.TICKET_CLOSE_PROGRESS, ephemeral=True)
        guild  = interaction.guild
        reason = self.reason.value

        # ── Всё, что "не критично", делаем в защищённых шагах: сбой любого
        #    из них НЕ должен мешать удалению канала ──
        opener_user = await self._find_opener(interaction, channel, guild)
        await self._dm_opener(interaction, channel, guild, opener_user, reason)
        await self._send_logs(interaction, channel, guild, opener_user, reason)
        await self._record_staff(interaction, guild, reason)

        # ── Главное: очистить БД и УДАЛИТЬ канал — в любом случае ──
        try:
            await db.delete(f"tickets.{channel.id}")
        except Exception as e:
            print(f"[Tickets] Не удалось очистить БД тикета: {e}")

        try:
            await channel.delete(reason=f"Тикет закрыт ({interaction.user}): {reason[:100]}")
        except discord.Forbidden:
            # Самая частая причина: у бота нет серверного права «Управление каналами»
            await interaction.followup.send(
                "⚠️ Тикет обработан (ЛС и логи отправлены), но **удалить канал не удалось**: "
                "у бота нет права **«Управление каналами»**.\n"
                "🔧 Зайди в *Настройки сервера → Роли → роль бота* и включи это право "
                "(или выдай боту «Администратор»), затем удали канал вручную.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"❌ Не удалось удалить канал: `{e}`. Удали его вручную.",
                ephemeral=True,
            )

    # ── вспомогательные шаги (все безопасны: ошибки только в консоль) ──
    async def _find_opener(self, interaction, channel, guild):
        """Кто открыл тикет: из БД, иначе старый способ (поиск пинга)."""
        try:
            meta      = await db.get(f"tickets.{channel.id}", {})
            opener_id = meta.get("opener") if isinstance(meta, dict) else None
            if opener_id:
                member = guild.get_member(int(opener_id))
                if member is not None:
                    return member
                try:
                    return await interaction.client.fetch_user(int(opener_id))
                except discord.HTTPException:
                    pass
            async for msg in channel.history(limit=20, oldest_first=True):
                if msg.author == guild.me and msg.mentions:
                    return msg.mentions[0]
        except Exception as e:
            print(f"[Tickets] Не удалось определить автора тикета: {e}")
        return None

    async def _dm_opener(self, interaction, channel, guild, opener_user, reason):
        """ЛС автору тикета о закрытии (не мешает закрытию при сбое)."""
        if not opener_user:
            return
        try:
            em = discord.Embed(
                title=str(T.TICKET_CLOSED_DM_TITLE),
                description=str(T.TICKET_CLOSED_DM_DESC).format(name=channel.name, guild=guild.name),
                color=discord.Color.orange(), timestamp=discord.utils.utcnow(),
            )
            em.add_field(name=str(T.TICKET_CLOSED_DM_REASON)[:256],
                         value=f"```{clean_codeblock(reason, 200)}```")
            em.set_footer(text=str(T.TICKET_CLOSED_DM_FOOTER).format(mod=interaction.user.name))
            await opener_user.send(embed=em)
        except discord.Forbidden:
            pass  # ЛС закрыты — ничего страшного
        except Exception as e:
            print(f"[Tickets] ЛС автору тикета не отправлено: {e}")

    async def _send_logs(self, interaction, channel, guild, opener_user, reason):
        """Транскрипт + сообщения в каналы логов (не мешает закрытию при сбое)."""
        transcript = None
        try:
            transcript = await create_transcript(channel)
        except Exception as e:
            print(f"[Tickets] Не удалось создать транскрипт: {e}")
        try:
            log_fields = (
                (T.TICKET_LOG_F_CHANNEL, f"`#{channel.name}`", True),
                (T.TICKET_LOG_F_OPENER,  opener_user.mention if opener_user else "—", True),
                (T.TICKET_LOG_F_CLOSER,  interaction.user.mention, True),
                (T.TICKET_LOG_F_REASON,  f"```{clean_codeblock(reason, 200)}```", False),
            )
            for ch_id, with_file in ((BotConfig.LOG_CHANNEL_1_ID, True),
                                     (BotConfig.LOG_CHANNEL_2_ID, False)):
                log_ch = guild.get_channel(ch_id)
                if not log_ch:
                    continue
                log_em = discord.Embed(title=str(T.TICKET_LOG_TITLE)[:256],
                                       color=discord.Color.red(),
                                       timestamp=discord.utils.utcnow())
                for name, value, inline in log_fields:
                    log_em.add_field(name=str(name)[:256], value=value, inline=inline)
                try:
                    if with_file and transcript is not None:
                        await log_ch.send(embed=log_em, file=transcript)
                    else:
                        await log_ch.send(embed=log_em)
                except discord.HTTPException as e:
                    print(f"[Tickets] Лог в #{log_ch.name} не отправлен: {e}")
        except Exception as e:
            print(f"[Tickets] Ошибка логирования тикета: {e}")

    async def _record_staff(self, interaction, guild, reason):
        """Учёт в статистике персонала (не мешает закрытию при сбое)."""
        try:
            from utils.staff_tracker import record_ticket_closed
            await record_ticket_closed(
                guild_id=guild.id,
                staff_id=interaction.user.id,
                channel_name=interaction.channel.name,
                reason=reason,
            )
        except Exception as e:
            print(f"[StaffStats] Ошибка учёта тикета: {e}")


class TicketControlView(discord.ui.View):
    """Постоянные кнопки внутри тикета: «Закрыть» и «Передать» (эскалация)."""

    def __init__(self):
        super().__init__(timeout=None)
        set_child_label(self, "ticket_close_btn", T.TICKET_CLOSE_BTN)
        set_child_label(self, "ticket_forward_btn", T.TICKET_FORWARD_BTN)

    @discord.ui.button(label="Закрыть тикет", style=discord.ButtonStyle.danger,
                       emoji="🔒", custom_id="ticket_close_btn", row=0)
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Закрывать тикет может только персонал — игрок закрыть свой тикет не может.
        channel  = interaction.channel
        meta     = await db.get(f"tickets.{channel.id}", {})
        type_id  = meta.get("type") if isinstance(meta, dict) else None
        type_cfg = (await load_types()).get(type_id, {}) if type_id else {}
        if is_ticket_channel(channel) and not _is_staff(interaction.user, type_cfg):
            await interaction.response.send_message(T.TICKET_STAFF_ONLY, ephemeral=True)
            return
        await interaction.response.send_modal(TicketCloseModal())

    @discord.ui.button(label="Передать тикет", style=discord.ButtonStyle.secondary,
                       emoji="🔁", custom_id="ticket_forward_btn", row=0)
    async def forward_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Передача тикета старшему составу: пингует escalate_roles и пускает их в канал."""
        channel = interaction.channel
        guild   = interaction.guild

        meta     = await db.get(f"tickets.{channel.id}", {})
        type_id  = meta.get("type") if isinstance(meta, dict) else None
        type_cfg = (await load_types()).get(type_id, {}) if type_id else {}

        if not type_id or not is_ticket_channel(channel):
            await interaction.response.send_message(T.TICKET_ONLY_IN_TICKET, ephemeral=True)
            return

        if not _is_staff(interaction.user, type_cfg):
            await interaction.response.send_message(T.TICKET_STAFF_ONLY, ephemeral=True)
            return

        escalate = [r for r in (type_cfg.get("escalate_roles") or []) if r]
        if not escalate:
            await interaction.response.send_message(T.TICKET_FORWARD_NO_ROLES, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # Выдаём старшим ролям доступ в канал
        granted = []
        for rid in escalate:
            role = guild.get_role(rid)
            if role:
                try:
                    await channel.set_permissions(role, read_messages=True, send_messages=True)
                    granted.append(role)
                except discord.HTTPException:
                    pass
        pings = " ".join(r.mention for r in granted) or "—"

        em = discord.Embed(
            title=str(T.TICKET_FORWARDED_TITLE)[:256],
            description=str(T.TICKET_FORWARDED_DESC).format(
                user=interaction.user.mention, roles=pings)[:4096],
            color=type_cfg.get("color") or 0x5865F2,
            timestamp=discord.utils.utcnow(),
        )
        banner = type_banner(type_cfg)
        if banner:
            em.set_image(url=banner)
        await channel.send(content=pings, embed=em, view=TicketControlView())

        meta = dict(meta if isinstance(meta, dict) else {})
        meta.update({
            "forwarded": True,
            "forwarded_by": interaction.user.id,
            "forwarded_at": int(datetime.datetime.utcnow().timestamp()),
        })
        await db.set(f"tickets.{channel.id}", meta)
        await interaction.followup.send(T.TICKET_FORWARD_OK.format(roles=pings), ephemeral=True)


# ─── СОЗДАНИЕ (ФОРМА) ────────────────────────────────────────────────────────
class TicketModal(discord.ui.Modal):
    """Форма обращения. Вопросы берутся из настроек типа (/тикет-настройка вопрос)."""

    def __init__(self, type_id: str, type_cfg: dict):
        title = str(T.TICKET_MODAL_TITLE)
        try:
            title = title.format(label=type_cfg.get("label", ""))
        except (KeyError, IndexError):
            pass
        super().__init__(title=title[:45])
        self.type_id  = type_id
        self.type_cfg = type_cfg
        self.inputs: list[tuple[dict, discord.ui.TextInput]] = []

        questions = type_cfg.get("questions") or []
        if not questions:
            questions = [make_question(T.TICKET_REASON_LABEL, T.TICKET_REASON_PH)]
        for q in questions[:MAX_QUESTIONS]:
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
        guild = interaction.guild
        user  = interaction.user

        # Кулдаун
        cooldown_key = f"ticket_cd.{user.id}"
        last_ts      = await db.get(cooldown_key, 0)
        now_ts       = datetime.datetime.utcnow().timestamp()
        remaining    = BotConfig.TICKET_COOLDOWN - (now_ts - last_ts)
        if remaining > 0:
            await interaction.followup.send(
                T.TICKET_CD_MSG.format(m=int(remaining // 60), s=int(remaining % 60)),
                ephemeral=True,
            )
            return

        type_cfg = self.type_cfg

        # Имя канала: латинский префикс + транслитерированный ник
        prefix    = slugify(type_cfg.get("prefix") or type_cfg.get("label") or "", 24) or "ticket"
        safe_nick = slugify(user.name, 20) or "user"
        ch_name   = f"{prefix}-{safe_nick}"[:100]

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            # игрок может сам кидать фото/видео прямо в канал тикета
            user:               discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me:           discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
        }
        ping_roles = [r for r in type_cfg.get("ping_roles", []) if r]
        for rid in ping_roles:
            role = guild.get_role(rid)
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        cat_id = type_cfg.get("category_id") or BotConfig.TICKET_CATEGORY_ID
        category = guild.get_channel(int(cat_id)) if cat_id else None

        try:
            channel = await guild.create_text_channel(
                name=ch_name, overwrites=overwrites, category=category,
                reason=f"Тикет ({type_cfg.get('label', self.type_id)}): {user}",
            )
        except discord.HTTPException as e:
            print(f"[Tickets] Не удалось создать канал: {e}")
            await interaction.followup.send(T.TICKET_CREATE_ERROR, ephemeral=True)
            return

        # Первый embed в канале: тип + ответы из формы + баннер
        ping_str = mentions_from_ids(guild, ping_roles)
        inner_em = discord.Embed(
            title=T.TICKET_INNER_TITLE,
            description=T.TICKET_INNER_DESC.format(type=type_cfg.get("label", self.type_id)),
            color=type_cfg.get("color") or 0x5865F2,
            timestamp=discord.utils.utcnow(),
        )
        inner_em.set_author(
            name=f"{user.display_name} • {user.name}",
            icon_url=user.display_avatar.url,
        )
        for q, ti in self.inputs:
            value = (ti.value or "").strip()
            if not value:
                continue
            inner_em.add_field(
                name=str(q.get("label", "?"))[:256],
                value=f"```{clean_codeblock(value, 900)}```",
                inline=False,
            )
        # 🖼️ Баннер внутри тикета (настраивается: /тикет-настройка баннер)
        banner = type_banner(type_cfg)
        if banner:
            inner_em.set_image(url=banner)
        inner_em.set_footer(text=T.TICKET_CREATED_FOOTER.format(name=user.name, id=user.id))

        content = f"{user.mention} {ping_str}".strip()
        await channel.send(content=content, embed=inner_em, view=TicketControlView())

        await db.set(f"tickets.{channel.id}", {
            "opener": user.id, "type": self.type_id, "created": int(now_ts),
        })
        await db.set(cooldown_key, now_ts)
        await interaction.followup.send(
            T.TICKET_CREATED_MSG.format(channel=channel.mention), ephemeral=True
        )


# ─── ВЫБОР ТИПА И ПАНЕЛЬ ─────────────────────────────────────────────────────
class TicketTypeSelectView(discord.ui.View):
    """Эфемерное меню выбора типа (собирается из актуальных настроек)."""

    def __init__(self, options: list[discord.SelectOption]):
        super().__init__(timeout=120)
        sel = discord.ui.Select(
            placeholder=str(T.TICKET_SELECT_PH)[:100],
            options=options,
        )
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction):
        type_id = interaction.data["values"][0]
        types   = await load_types()
        cfg     = types.get(type_id)
        if not cfg:
            await interaction.response.send_message(T.TICKET_NO_TYPES, ephemeral=True)
            return
        await interaction.response.send_modal(TicketModal(type_id, cfg))


class TicketPanelView(discord.ui.View):
    """Персистентная панель: кнопка → меню типов → форма."""

    def __init__(self):
        super().__init__(timeout=None)
        set_child_label(self, "open_ticket_panel_btn", T.TICKET_BTN_LABEL, T.TICKET_BTN_EMOJI)

    @discord.ui.button(label="Создать тикет", style=discord.ButtonStyle.secondary,
                       emoji="📩", custom_id="open_ticket_panel_btn")
    async def open_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        types   = await load_types()
        options = build_type_options(types)
        if not options:
            await interaction.response.send_message(T.TICKET_NO_TYPES, ephemeral=True)
            return
        await interaction.response.send_message(
            T.TICKET_TYPE_SELECT_PROMPT, view=TicketTypeSelectView(options), ephemeral=True
        )


# ─── COG ─────────────────────────────────────────────────────────────────────
class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="setup-tickets", description="⚙️ [АДМИН] Установить панель тикетов")
    @app_commands.default_permissions(administrator=True)
    async def setup_tickets(self, interaction: discord.Interaction):
        message = await interaction.channel.send(embed=build_panel_embed(), view=TicketPanelView())
        await save_panel_message(interaction.guild.id, interaction.channel.id, message.id)
        await interaction.response.send_message(T.TICKET_PANEL_OK, ephemeral=True)

    ticket_group = app_commands.Group(
        name="ticket", description="Управление тикетом",
        default_permissions=discord.Permissions(manage_channels=True),
    )

    @ticket_group.command(name="add", description="➕ Добавить пользователя в тикет")
    async def ticket_add(self, interaction: discord.Interaction, игрок: discord.Member):
        if not is_ticket_channel(interaction.channel):
            await interaction.response.send_message(T.TICKET_ONLY_IN_TICKET, ephemeral=True); return
        await interaction.channel.set_permissions(игрок, read_messages=True, send_messages=True)
        await interaction.response.send_message(T.TICKET_ADD_OK.format(user=игрок.mention))

    @ticket_group.command(name="remove", description="➖ Удалить пользователя из тикета")
    async def ticket_remove(self, interaction: discord.Interaction, игрок: discord.Member):
        if not is_ticket_channel(interaction.channel):
            await interaction.response.send_message(T.TICKET_ONLY_IN_TICKET, ephemeral=True); return
        await interaction.channel.set_permissions(игрок, overwrite=None)
        await interaction.response.send_message(T.TICKET_REMOVE_OK.format(user=игрок.mention))

    @ticket_group.command(name="rename", description="✏️ Переименовать канал тикета")
    async def ticket_rename(self, interaction: discord.Interaction, новое_имя: str):
        if not is_ticket_channel(interaction.channel):
            await interaction.response.send_message(T.TICKET_ONLY_IN_TICKET, ephemeral=True); return
        await interaction.channel.edit(name=новое_имя)
        await interaction.response.send_message(T.TICKET_RENAME_OK.format(name=новое_имя))

    @ticket_group.command(name="close", description="🔒 Закрыть текущий тикет с указанием причины")
    async def ticket_close(self, interaction: discord.Interaction):
        if not is_ticket_channel(interaction.channel):
            await interaction.response.send_message(T.TICKET_ONLY_IN_TICKET, ephemeral=True); return
        await interaction.response.send_modal(TicketCloseModal())


async def setup(bot):
    await bot.add_cog(TicketsCog(bot))
    bot.add_view(TicketPanelView())
    bot.add_view(TicketControlView())
