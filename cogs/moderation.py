"""
🔨 МОДЕРАЦИЯ
- Mute / Ban (временный, авто-разбан) / Kick / Unmute / Unban
- /purge — bulk delete без лимитов Discord
- /ds-panel — панель модерации
- /кд-сброс — сброс кулдауна заявок/тикетов для игрока
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import datetime
from typing import Optional

from config import BotConfig
from database import db
from utils.helpers import parse_duration, duration_to_str
from utils.checks import custom_check


# ─── ПАНЕЛЬ МОДЕРАЦИИ ───────────────────────────────────────────────────────
class PanelModal(discord.ui.Modal):
    def __init__(self, action: str, member: discord.Member):
        super().__init__(title=f"Действие: {action.upper()}")
        self.action = action
        self.member = member
        self.duration_input = discord.ui.TextInput(
            label="Срок (10m / 2h / 7d) | Пусто = 1 час",
            required=False, placeholder="30m, 12h, 7d",
        )
        self.reason_input = discord.ui.TextInput(
            label="Причина наказания", required=False,
            placeholder="Нарушение правил...", max_length=300,
        )
        self.add_item(self.duration_input)
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        rsn = self.reason_input.value or "Причина не указана"
        dur = parse_duration(self.duration_input.value)
        msg = ""

        try:
            if self.action == "mute":
                until = discord.utils.utcnow() + dur
                await self.member.timeout(until, reason=rsn)
                msg = f"Mute на {duration_to_str(dur)}"

            elif self.action == "ban":
                await self.member.ban(reason=rsn, delete_message_days=0)
                msg = f"Ban на {duration_to_str(dur)}"
                # Авто-разбан
                unban_at = datetime.datetime.utcnow().timestamp() + dur.total_seconds()
                bans     = await db.get("temp_bans", {})
                bkey     = f"{interaction.guild.id}_{self.member.id}"
                bans[bkey] = {"guild_id": interaction.guild.id, "user_id": self.member.id, "unban_at": unban_at}
                await db.set("temp_bans", bans)

            elif self.action == "kick":
                await self.member.kick(reason=rsn)
                msg = "Kick"

            elif self.action == "unmute":
                await self.member.timeout(None, reason=rsn)
                msg = "Mute снят"

            await interaction.followup.send(f"✅ {msg} → {self.member.mention}", ephemeral=True)

            # Учёт в статистике персонала
            try:
                from utils.staff_tracker import record_punishment_issued
                dur_str = duration_to_str(dur) if self.action in ("mute", "ban") else ""
                await record_punishment_issued(
                    guild_id=interaction.guild.id,
                    staff_id=interaction.user.id,
                    action=self.action,
                    target_id=self.member.id,
                    duration_str=dur_str,
                    reason=rsn,
                )
            except Exception as e:
                print(f"[StaffStats] Ошибка записи наказания: {e}")

            log_em = discord.Embed(title="🚨 Лог наказания", color=discord.Color.dark_red(), timestamp=discord.utils.utcnow())
            log_em.add_field(name="Модератор",  value=interaction.user.mention, inline=True)
            log_em.add_field(name="Нарушитель", value=self.member.mention,      inline=True)
            log_em.add_field(name="Действие",   value=msg,                      inline=False)
            log_em.add_field(name="Причина",    value=rsn,                      inline=False)
            for cid in (BotConfig.LOG_CHANNEL_1_ID, BotConfig.LOG_CHANNEL_2_ID):
                ch = interaction.guild.get_channel(cid)
                if ch:
                    await ch.send(embed=log_em)

        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Недостаточно прав (роль нарушителя выше роли бота).", ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Ошибка: {e}", ephemeral=True)


class ModPanelView(discord.ui.View):
    def __init__(self, member: discord.Member):
        super().__init__(timeout=120)
        self.member = member
        opts = [
            discord.SelectOption(label="🔇 Выдать Mute",  value="mute",   description="Тайм-аут на указанный срок"),
            discord.SelectOption(label="🔨 Выдать Ban",   value="ban",    description="Блокировка на указанный срок"),
            discord.SelectOption(label="👢 Кикнуть",      value="kick",   description="Выгнать с сервера"),
            discord.SelectOption(label="🔊 Снять Mute",   value="unmute", description="Убрать тайм-аут"),
        ]
        self.sel = discord.ui.Select(placeholder="Выберите действие...", options=opts)
        self.sel.callback = self._callback
        self.add_item(self.sel)

    async def _callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(PanelModal(self.sel.values[0], self.member))


# ─── COG ────────────────────────────────────────────────────────────────────
class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.auto_unban_loop.start()

    def cog_unload(self):
        self.auto_unban_loop.cancel()

    # ─── Авто-разбан ─────────────────────────────────────────────────────────
    @tasks.loop(minutes=1)
    async def auto_unban_loop(self):
        bans   = await db.get("temp_bans", {})
        now_ts = datetime.datetime.utcnow().timestamp()
        to_del = []

        for key, data in list(bans.items()):
            if data["unban_at"] <= now_ts:
                guild = self.bot.get_guild(data["guild_id"])
                if guild:
                    try:
                        user = discord.Object(id=data["user_id"])
                        await guild.unban(user, reason="Срок бана истёк (авто-разбан)")
                        # Лог
                        log_ch = guild.get_channel(BotConfig.LOG_CHANNEL_1_ID)
                        if log_ch:
                            em = discord.Embed(
                                title="🔓 Авто-разбан",
                                description=f"Разбанен пользователь ID `{data['user_id']}` (срок истёк)",
                                color=discord.Color.green(), timestamp=discord.utils.utcnow(),
                            )
                            await log_ch.send(embed=em)
                    except discord.NotFound:
                        pass  # Уже разбанен
                    except Exception:
                        pass
                to_del.append(key)

        if to_del:
            for k in to_del:
                bans.pop(k, None)
            await db.set("temp_bans", bans)

    @auto_unban_loop.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()

    # ─── /ds-panel ───────────────────────────────────────────────────────────
    @app_commands.command(name="ds-panel", description="🛡️ Открыть панель модерации для игрока")
    @custom_check()
    async def ds_panel(self, interaction: discord.Interaction, игрок: discord.Member):
        embed = discord.Embed(
            title=f"Панель: {игрок.display_name}",
            color=discord.Color.dark_red(),
        )
        embed.set_thumbnail(url=игрок.display_avatar.url)
        embed.add_field(name="Аккаунт", value=игрок.mention,                                     inline=True)
        embed.add_field(name="ID",      value=str(игрок.id),                                      inline=True)
        embed.add_field(name="Создан",  value=discord.utils.format_dt(игрок.created_at, "D"),     inline=True)
        await interaction.response.send_message(embed=embed, view=ModPanelView(игрок), ephemeral=True)

    # ─── /unban ──────────────────────────────────────────────────────────────
    @app_commands.command(name="unban", description="🔓 Разбанить пользователя по ID")
    @custom_check()
    @app_commands.describe(user_id="ID пользователя которого нужно разбанить", причина="Причина разбана")
    async def unban(self, interaction: discord.Interaction, user_id: str, причина: Optional[str] = None):
        await interaction.response.defer(ephemeral=True)
        try:
            uid  = int(user_id)
            user = discord.Object(id=uid)
            await interaction.guild.unban(user, reason=причина or "Разбан администратором")
            # Удалить из авто-разбан списка
            bans = await db.get("temp_bans", {})
            bkey = f"{interaction.guild.id}_{uid}"
            if bkey in bans:
                bans.pop(bkey)
                await db.set("temp_bans", bans)

            # Учёт в статистике персонала
            try:
                from utils.staff_tracker import record_punishment_issued
                await record_punishment_issued(
                    guild_id=interaction.guild.id,
                    staff_id=interaction.user.id,
                    action="unban",
                    target_id=uid,
                    duration_str="",
                    reason=причина or "Разбан администратором",
                )
            except Exception as e:
                print(f"[StaffStats] Ошибка записи разбана: {e}")

            await interaction.followup.send(f"✅ Пользователь `{uid}` разбанен.", ephemeral=True)
        except ValueError:
            await interaction.followup.send("❌ Неверный ID. Введите числовой ID пользователя.", ephemeral=True)
        except discord.NotFound:
            await interaction.followup.send("❌ Этот пользователь не в бан-листе.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ Недостаточно прав для разбана.", ephemeral=True)

    # ─── /purge ──────────────────────────────────────────────────────────────
    @app_commands.command(name="purge", description="🧹 Удалить сообщения (до 1000)")
    @custom_check()
    @app_commands.describe(
        количество="Количество сообщений для удаления",
        пользователь="Удалять только сообщения этого пользователя (необязательно)",
    )
    async def purge(self, interaction: discord.Interaction,
                    количество: app_commands.Range[int, 1, 1000],
                    пользователь: Optional[discord.Member] = None):
        await interaction.response.defer(ephemeral=True)

        channel   = interaction.channel
        now       = discord.utils.utcnow()
        cutoff    = now - datetime.timedelta(days=14)  # bulk delete только для сообщений < 14 дней
        bulk_msgs = []   # можно удалить пачкой
        old_msgs  = []   # старые — нельзя bulk, пропускаем (Discord запрещает)
        deleted   = 0

        # Собираем сообщения
        async for msg in channel.history(limit=количество):
            if пользователь and msg.author != пользователь:
                continue
            if msg.created_at > cutoff:
                bulk_msgs.append(msg)
            else:
                old_msgs.append(msg)

        # Bulk delete пачками по 100
        for i in range(0, len(bulk_msgs), 100):
            chunk = bulk_msgs[i:i + 100]
            if not chunk:
                continue
            try:
                if len(chunk) == 1:
                    await chunk[0].delete()
                else:
                    await channel.delete_messages(chunk)
                deleted += len(chunk)
            except discord.HTTPException:
                # Если bulk не сработал — удаляем по одному
                for m in chunk:
                    try:
                        await m.delete()
                        deleted += 1
                    except discord.HTTPException:
                        pass
            await asyncio.sleep(0.5)  # Небольшая пауза между чанками

        target_str = f" от {пользователь.mention}" if пользователь else ""
        skipped    = len(old_msgs)
        note       = (f"\n⚠️ Пропущено {skipped} сообщ. старше 14 дней (Discord запрещает их удалять пачкой)."
                      if skipped else "")
        await interaction.followup.send(
            f"✅ Удалено **{deleted}** сообщений{target_str}.{note}", ephemeral=True
        )

    # ─── /mute ───────────────────────────────────────────────────────────────
    @app_commands.command(name="mute", description="🔇 Выдать тайм-аут игроку")
    @custom_check()
    @app_commands.describe(
        игрок="Нарушитель",
        срок="Срок (например: 10m, 2h, 1d) | По умолчанию 1 час",
        причина="Причина мута",
    )
    async def cmd_mute(self, interaction: discord.Interaction, игрок: discord.Member,
                       срок: Optional[str] = "1h", причина: Optional[str] = "Нарушение правил"):
        await interaction.response.defer(ephemeral=True)
        dur = parse_duration(срок)
        rsn = причина or "Нарушение правил"
        try:
            until = discord.utils.utcnow() + dur
            await игрок.timeout(until, reason=rsn)
            dur_str = duration_to_str(dur)
            await interaction.followup.send(f"✅ Выдан Mute на **{dur_str}** → {игрок.mention}", ephemeral=True)

            try:
                from utils.staff_tracker import record_punishment_issued
                await record_punishment_issued(
                    guild_id=interaction.guild.id,
                    staff_id=interaction.user.id,
                    action="mute",
                    target_id=игрок.id,
                    duration_str=dur_str,
                    reason=rsn,
                )
            except Exception as e:
                print(f"[StaffStats] Ошибка: {e}")

            log_em = discord.Embed(title="🚨 Лог: MUTE", color=discord.Color.orange(), timestamp=discord.utils.utcnow())
            log_em.add_field(name="Модератор",  value=interaction.user.mention, inline=True)
            log_em.add_field(name="Нарушитель", value=игрок.mention,            inline=True)
            log_em.add_field(name="Срок",       value=dur_str,                  inline=True)
            log_em.add_field(name="Причина",    value=rsn,                      inline=False)
            for cid in (BotConfig.LOG_CHANNEL_1_ID, BotConfig.LOG_CHANNEL_2_ID):
                ch = interaction.guild.get_channel(cid)
                if ch: await ch.send(embed=log_em)
        except discord.Forbidden:
            await interaction.followup.send("❌ Недостаточно прав для мута.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Ошибка: {e}", ephemeral=True)

    # ─── /unmute ─────────────────────────────────────────────────────────────
    @app_commands.command(name="unmute", description="🔊 Снять тайм-аут с игрока")
    @custom_check()
    @app_commands.describe(игрок="Игрок", причина="Причина снятия мута")
    async def cmd_unmute(self, interaction: discord.Interaction, игрок: discord.Member,
                         причина: Optional[str] = "Снятие тайм-аута"):
        await interaction.response.defer(ephemeral=True)
        rsn = причина or "Снятие тайм-аута"
        try:
            await игрок.timeout(None, reason=rsn)
            await interaction.followup.send(f"✅ Mute снят с {игрок.mention}", ephemeral=True)

            try:
                from utils.staff_tracker import record_punishment_issued
                await record_punishment_issued(
                    guild_id=interaction.guild.id,
                    staff_id=interaction.user.id,
                    action="unmute",
                    target_id=игрок.id,
                    duration_str="",
                    reason=rsn,
                )
            except Exception as e:
                print(f"[StaffStats] Ошибка: {e}")

            log_em = discord.Embed(title="🔊 Лог: MUTE СНЯТ", color=discord.Color.green(), timestamp=discord.utils.utcnow())
            log_em.add_field(name="Модератор", value=interaction.user.mention, inline=True)
            log_em.add_field(name="Игрок",     value=игрок.mention,            inline=True)
            log_em.add_field(name="Причина",   value=rsn,                      inline=False)
            for cid in (BotConfig.LOG_CHANNEL_1_ID, BotConfig.LOG_CHANNEL_2_ID):
                ch = interaction.guild.get_channel(cid)
                if ch: await ch.send(embed=log_em)
        except discord.Forbidden:
            await interaction.followup.send("❌ Недостаточно прав.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Ошибка: {e}", ephemeral=True)

    # ─── /ban ────────────────────────────────────────────────────────────────
    @app_commands.command(name="ban", description="🔨 Забанить игрока на сервере")
    @custom_check()
    @app_commands.describe(
        игрок="Нарушитель",
        срок="Срок бана (например: 1d, 7d, 30d) | Пусто = навсегда",
        причина="Причина бана",
    )
    async def cmd_ban(self, interaction: discord.Interaction, игрок: discord.Member,
                      срок: Optional[str] = None, причина: Optional[str] = "Нарушение правил"):
        await interaction.response.defer(ephemeral=True)
        rsn = причина or "Нарушение правил"
        try:
            await игрок.ban(reason=rsn, delete_message_days=0)
            dur_str = "навсегда"
            if срок:
                dur = parse_duration(срок)
                dur_str = duration_to_str(dur)
                unban_at = datetime.datetime.utcnow().timestamp() + dur.total_seconds()
                bans = await db.get("temp_bans", {})
                bkey = f"{interaction.guild.id}_{игрок.id}"
                bans[bkey] = {"guild_id": interaction.guild.id, "user_id": игрок.id, "unban_at": unban_at}
                await db.set("temp_bans", bans)

            await interaction.followup.send(f"✅ Игрок {игрок.mention} забанен ({dur_str}).", ephemeral=True)

            try:
                from utils.staff_tracker import record_punishment_issued
                await record_punishment_issued(
                    guild_id=interaction.guild.id,
                    staff_id=interaction.user.id,
                    action="ban",
                    target_id=игрок.id,
                    duration_str=dur_str,
                    reason=rsn,
                )
            except Exception as e:
                print(f"[StaffStats] Ошибка: {e}")

            log_em = discord.Embed(title="🔨 Лог: БАН", color=discord.Color.dark_red(), timestamp=discord.utils.utcnow())
            log_em.add_field(name="Модератор",  value=interaction.user.mention, inline=True)
            log_em.add_field(name="Нарушитель", value=f"{игрок} (`{игрок.id}`)", inline=True)
            log_em.add_field(name="Срок",       value=dur_str,                  inline=True)
            log_em.add_field(name="Причина",    value=rsn,                      inline=False)
            for cid in (BotConfig.LOG_CHANNEL_1_ID, BotConfig.LOG_CHANNEL_2_ID):
                ch = interaction.guild.get_channel(cid)
                if ch: await ch.send(embed=log_em)
        except discord.Forbidden:
            await interaction.followup.send("❌ Недостаточно прав для бана.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Ошибка: {e}", ephemeral=True)

    # ─── /kick ───────────────────────────────────────────────────────────────
    @app_commands.command(name="kick", description="👢 Кикнуть игрока с сервера")
    @custom_check()
    @app_commands.describe(игрок="Нарушитель", причина="Причина кика")
    async def cmd_kick(self, interaction: discord.Interaction, игрок: discord.Member,
                       причина: Optional[str] = "Кик модератором"):
        await interaction.response.defer(ephemeral=True)
        rsn = причина or "Кик модератором"
        try:
            await игрок.kick(reason=rsn)
            await interaction.followup.send(f"✅ Игрок {игрок.mention} кикнут с сервера.", ephemeral=True)

            try:
                from utils.staff_tracker import record_punishment_issued
                await record_punishment_issued(
                    guild_id=interaction.guild.id,
                    staff_id=interaction.user.id,
                    action="kick",
                    target_id=игрок.id,
                    duration_str="",
                    reason=rsn,
                )
            except Exception as e:
                print(f"[StaffStats] Ошибка: {e}")

            log_em = discord.Embed(title="👢 Лог: КИК", color=discord.Color.gold(), timestamp=discord.utils.utcnow())
            log_em.add_field(name="Модератор",  value=interaction.user.mention, inline=True)
            log_em.add_field(name="Нарушитель", value=f"{игрок} (`{игрок.id}`)", inline=True)
            log_em.add_field(name="Причина",    value=rsn,                      inline=False)
            for cid in (BotConfig.LOG_CHANNEL_1_ID, BotConfig.LOG_CHANNEL_2_ID):
                ch = interaction.guild.get_channel(cid)
                if ch: await ch.send(embed=log_em)
        except discord.Forbidden:
            await interaction.followup.send("❌ Недостаточно прав для кика.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Ошибка: {e}", ephemeral=True)

    # ─── /warn ───────────────────────────────────────────────────────────────
    @app_commands.command(name="warn", description="⚠️ Выдать предупреждение игроку")
    @custom_check()
    @app_commands.describe(игрок="Нарушитель", причина="Причина предупреждения")
    async def cmd_warn(self, interaction: discord.Interaction, игрок: discord.Member, причина: str):
        await interaction.response.defer(ephemeral=True)
        now_ts = int(datetime.datetime.utcnow().timestamp())
        warns = await db.get(f"warns.{interaction.guild.id}.{игрок.id}", [])
        warn_entry = {
            "id": len(warns) + 1,
            "mod_id": interaction.user.id,
            "mod_name": interaction.user.name,
            "reason": причина,
            "ts": now_ts,
        }
        warns.append(warn_entry)
        await db.set(f"warns.{interaction.guild.id}.{игрок.id}", warns)

        try:
            from utils.staff_tracker import record_punishment_issued
            await record_punishment_issued(
                guild_id=interaction.guild.id,
                staff_id=interaction.user.id,
                action="warn",
                target_id=игрок.id,
                duration_str="",
                reason=причина,
            )
        except Exception as e:
            print(f"[StaffStats] Ошибка: {e}")

        # Уведомление в ЛС
        try:
            dm_em = discord.Embed(
                title=f"⚠️ Предупреждение на сервере {interaction.guild.name}",
                description=f"Вам выдано предупреждение **#{len(warns)}**.",
                color=discord.Color.yellow(),
                timestamp=discord.utils.utcnow(),
            )
            dm_em.add_field(name="Причина:", value=f"```{причина}```")
            dm_em.add_field(name="Модератор:", value=interaction.user.name)
            await игрок.send(embed=dm_em)
        except discord.Forbidden:
            pass

        await interaction.followup.send(
            f"✅ Предупреждение #{len(warns)} выдано {игрок.mention}.\nПричина: `{причина}`",
            ephemeral=True,
        )

        log_em = discord.Embed(title="⚠️ Лог: ПРЕДУПРЕЖДЕНИЕ", color=discord.Color.yellow(), timestamp=discord.utils.utcnow())
        log_em.add_field(name="Модератор",  value=interaction.user.mention, inline=True)
        log_em.add_field(name="Нарушитель", value=игрок.mention,            inline=True)
        log_em.add_field(name="Варн №",     value=str(len(warns)),          inline=True)
        log_em.add_field(name="Причина",    value=причина,                  inline=False)
        for cid in (BotConfig.LOG_CHANNEL_1_ID, BotConfig.LOG_CHANNEL_2_ID):
            ch = interaction.guild.get_channel(cid)
            if ch: await ch.send(embed=log_em)

    # ─── /warns ──────────────────────────────────────────────────────────────
    @app_commands.command(name="warns", description="📋 Посмотреть предупреждения игрока")
    @custom_check()
    @app_commands.describe(игрок="Игрок")
    async def cmd_warns_list(self, interaction: discord.Interaction, игрок: discord.Member):
        warns = await db.get(f"warns.{interaction.guild.id}.{игрок.id}", [])
        if not warns:
            await interaction.response.send_message(f"ℹ️ У {игрок.mention} нет предупреждений.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"⚠️ Предупреждения: {игрок.display_name} ({len(warns)})",
            color=discord.Color.yellow(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=игрок.display_avatar.url)
        for idx, w in enumerate(warns, start=1):
            dt_str = f"<t:{w['ts']}:d> <t:{w['ts']}:t>" if "ts" in w else "?"
            embed.add_field(
                name=f"Варн #{idx}",
                value=f"**Причина:** {w.get('reason', '—')}\n**Модератор:** <@{w.get('mod_id', 0)}>\n**Дата:** {dt_str}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ─── /warn-снять ─────────────────────────────────────────────────────────
    @app_commands.command(name="warn-снять", description="🗑️ Снять предупреждение с игрока")
    @custom_check()
    @app_commands.describe(игрок="Игрок", номер="Номер варна (пусто = последний)")
    async def cmd_warn_remove(self, interaction: discord.Interaction, игрок: discord.Member, номер: Optional[int] = None):
        warns = await db.get(f"warns.{interaction.guild.id}.{игрок.id}", [])
        if not warns:
            await interaction.response.send_message(f"❌ У {игрок.mention} нет варнов.", ephemeral=True)
            return

        if номер is None:
            removed = warns.pop()
            num_str = len(warns) + 1
        else:
            if номер < 1 or номер > len(warns):
                await interaction.response.send_message(f"❌ Неверный номер варна. Доступно: 1–{len(warns)}", ephemeral=True)
                return
            removed = warns.pop(номер - 1)
            num_str = номер

        await db.set(f"warns.{interaction.guild.id}.{игрок.id}", warns)
        await interaction.response.send_message(
            f"✅ Предупреждение #{num_str} снято с {игрок.mention}. Осталось: **{len(warns)}**",
            ephemeral=True,
        )

    # ─── /кд-сброс ───────────────────────────────────────────────────────────
    @app_commands.command(name="кд-сброс", description="🔄 Сбросить кулдаун тикета/заявки для игрока")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        игрок="Игрок, которому сбросить кулдаун",
        тип="Что сбросить",
    )
    @app_commands.choices(тип=[
        app_commands.Choice(name="🎫 Тикет",              value="ticket_cd"),
        app_commands.Choice(name="🛡️ Заявка: Персонал",   value="app_cd.персонал"),
        app_commands.Choice(name="⚙️ Заявка: ДС-Адм",    value="app_cd.дс-адм"),
        app_commands.Choice(name="🔨 Заявка: Билдеры",    value="app_cd.билдеры"),
        app_commands.Choice(name="🗑️ Всё сразу",          value="all"),
    ])
    async def reset_cd(self, interaction: discord.Interaction, игрок: discord.Member, тип: str):
        if тип == "all":
            await db.delete(f"ticket_cd.{игрок.id}")
            for t in ("персонал", "дс-адм", "билдеры"):
                await db.delete(f"app_cd.{игрок.id}.{t}")
            await interaction.response.send_message(
                f"✅ Все кулдауны для {игрок.mention} сброшены.", ephemeral=True
            )
        else:
            key = f"{тип}.{игрок.id}" if тип == "ticket_cd" else f"{тип}.{игрок.id}"
            # Правильные ключи:
            if тип == "ticket_cd":
                await db.delete(f"ticket_cd.{игрок.id}")
            else:
                # тип вида "app_cd.персонал" → ключ "app_cd.{uid}.персонал"
                parts    = тип.split(".")
                app_type = parts[1]
                await db.delete(f"app_cd.{игрок.id}.{app_type}")
            await interaction.response.send_message(
                f"✅ Кулдаун `{тип}` для {игрок.mention} сброшен.", ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(ModerationCog(bot))
