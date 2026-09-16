"""
🛠️ УТИЛИТЫ АДМИНИСТРАТОРА
/say        — написать от имени бота
/say-dm     — написать в ЛС от имени бота
/ip         — информация по Discord аккаунту + хранимые данные (IP, заметки)
/ip-сет     — установить IP/заметку для игрока (хранится в БД)
"""

import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional

from database import db
from utils.checks import custom_check


class AdminUtilsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ─── /say ────────────────────────────────────────────────────────────────
    @app_commands.command(name="say", description="🗣️ Написать от имени бота")
    @custom_check()
    @app_commands.describe(текст="Текст сообщения", канал="Канал (по умолчанию текущий)")
    async def say(self, interaction: discord.Interaction, текст: str, канал: Optional[discord.TextChannel] = None):
        target = канал or interaction.channel
        await target.send(текст)
        await interaction.response.send_message(f"✅ Отправлено в {target.mention}.", ephemeral=True)

    # ─── /say-dm ─────────────────────────────────────────────────────────────
    @app_commands.command(name="say-dm", description="📨 Написать игроку в ЛС от имени бота")
    @custom_check()
    @app_commands.describe(игрок="Получатель", текст="Текст сообщения")
    async def say_dm(self, interaction: discord.Interaction, игрок: discord.Member, текст: str):
        try:
            await игрок.send(текст)
            await interaction.response.send_message(f"✅ Отправлено в ЛС {игрок.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(f"❌ ЛС {игрок.mention} закрыты.", ephemeral=True)

    # ─── /ip-сет — сохранить IP/заметку для игрока ──────────────────────────
    @app_commands.command(name="ip-сет", description="💾 Сохранить IP / заметку для игрока (для /ip)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        игрок="Игрок",
        ip="IP адрес (например: 192.168.1.1)",
        заметка="Заметка об игроке (необязательно)",
    )
    async def ip_set(self, interaction: discord.Interaction, игрок: discord.Member,
                     ip: Optional[str] = None, заметка: Optional[str] = None):
        stored = await db.get(f"player_data.{игрок.id}", {})
        if ip:       stored["ip"]   = ip
        if заметка:  stored["note"] = заметка
        await db.set(f"player_data.{игрок.id}", stored)
        parts = []
        if ip:      parts.append(f"IP: `{ip}`")
        if заметка: parts.append(f"Заметка: `{заметка}`")
        await interaction.response.send_message(
            f"✅ Данные для {игрок.mention} сохранены: {', '.join(parts)}", ephemeral=True
        )

    # ─── /ip — полная информация ─────────────────────────────────────────────
    @app_commands.command(name="ip", description="🔍 Полная информация по аккаунту игрока")
    @custom_check()
    @app_commands.describe(игрок="Участник сервера")
    async def ip_info(self, interaction: discord.Interaction, игрок: discord.Member):
        await interaction.response.defer(ephemeral=True)

        now     = discord.utils.utcnow()
        created = игрок.created_at
        joined  = игрок.joined_at
        acc_age = now - created
        srv_age = now - joined if joined else None

        # Данные из БД (IP, заметки)
        stored  = await db.get(f"player_data.{игрок.id}", {})
        stored_ip   = stored.get("ip")
        stored_note = stored.get("note")

        # Флаги
        flags_map = {
            discord.UserFlags.staff:                  "👨‍💼 Staff",
            discord.UserFlags.partner:                "🤝 Partner",
            discord.UserFlags.hypesquad:              "🏠 HypeSquad Events",
            discord.UserFlags.bug_hunter:             "🐛 Bug Hunter",
            discord.UserFlags.bug_hunter_level_2:     "🐛 Bug Hunter Lv.2",
            discord.UserFlags.hypesquad_bravery:      "🦁 Bravery",
            discord.UserFlags.hypesquad_brilliance:   "💡 Brilliance",
            discord.UserFlags.hypesquad_balance:      "⚖️ Balance",
            discord.UserFlags.early_supporter:        "💜 Early Supporter",
            discord.UserFlags.verified_bot_developer: "🤖 Verified Bot Dev",
            discord.UserFlags.active_developer:       "⚡ Active Developer",
        }
        badges = [label for flag, label in flags_map.items() if игрок.public_flags.value & flag.value]

        # Статус и устройство
        status_map = {
            discord.Status.online:    "🟢 В сети",
            discord.Status.idle:      "🌙 Не активен",
            discord.Status.dnd:       "🔴 Не беспокоить",
            discord.Status.offline:   "⚫ Не в сети",
            discord.Status.invisible: "⚫ Невидимый",
        }
        status_str = status_map.get(игрок.status, "⚫ Не в сети")
        devices    = []
        if игрок.desktop_status != discord.Status.offline: devices.append("💻 ПК")
        if игрок.mobile_status  != discord.Status.offline: devices.append("📱 Телефон")
        if игрок.web_status     != discord.Status.offline: devices.append("🌐 Браузер")

        # Предупреждение о молодом аккаунте
        warning = ""
        if acc_age.days < 3:   warning = "\n\n🚨 **АККАУНТ СОЗДАН МЕНЕЕ 3 ДНЕЙ НАЗАД!**"
        elif acc_age.days < 7: warning = "\n\n⚠️ **Аккаунт моложе 7 дней!**"
        elif acc_age.days < 30:warning = "\n\n⚠️ Аккаунт моложе 30 дней."

        embed = discord.Embed(
            title=f"👤 {игрок.display_name}",
            description=f"`{игрок}` | ID: `{игрок.id}`{warning}",
            color=игрок.color if игрок.color != discord.Color.default() else 0x5865f2,
            timestamp=now,
        )
        embed.set_thumbnail(url=игрок.display_avatar.url)

        # ── IP (из БД) ──
        if stored_ip:
            embed.add_field(name="🌐 IP адрес", value=f"`{stored_ip}`", inline=True)
        else:
            embed.add_field(name="🌐 IP адрес", value="*не задан*\n`/ip-сет @игрок ip:...`", inline=True)

        # ── Заметка ──
        if stored_note:
            embed.add_field(name="📝 Заметка", value=stored_note, inline=True)

        # ── Даты ──
        embed.add_field(
            name="📅 Аккаунт создан",
            value=f"{discord.utils.format_dt(created, 'D')}\n{discord.utils.format_dt(created, 'R')}\n_({acc_age.days} дн.)_",
            inline=True,
        )
        embed.add_field(
            name="📥 На сервере",
            value=(f"{discord.utils.format_dt(joined, 'D')}\n{discord.utils.format_dt(joined, 'R')}\n_({srv_age.days} дн.)_"
                   if joined else "?"),
            inline=True,
        )
        embed.add_field(
            name="📡 Статус",
            value=f"{status_str}\n{' / '.join(devices) if devices else '—'}",
            inline=True,
        )

        # ── Ник / Буст ──
        if игрок.display_name != игрок.name:
            embed.add_field(name="🏷️ Ник", value=игрок.display_name, inline=True)
        if игрок.premium_since:
            embed.add_field(name="💎 Буст с", value=discord.utils.format_dt(игрок.premium_since, "D"), inline=True)

        # ── Бейджи ──
        if badges:
            embed.add_field(name="🏅 Значки", value="\n".join(badges), inline=True)

        # ── Роли ──
        roles = [r for r in reversed(игрок.roles) if r.name != "@everyone"]
        if roles:
            roles_str = " ".join(r.mention for r in roles[:15])
            if len(roles) > 15: roles_str += f"\n_...и ещё {len(roles)-15}_"
            embed.add_field(name=f"🎭 Роли ({len(roles)})", value=roles_str, inline=False)

        # ── Права ──
        perms = игрок.guild_permissions
        imp   = []
        if perms.administrator:    imp.append("👑 Администратор")
        if perms.manage_guild:     imp.append("⚙️ Управление")
        if perms.ban_members:      imp.append("🔨 Бан")
        if perms.kick_members:     imp.append("👢 Кик")
        if perms.moderate_members: imp.append("🔇 Тайм-аут")
        if perms.manage_messages:  imp.append("🗑️ Удалять сообщ.")
        if imp:
            embed.add_field(name="🔐 Права", value=" | ".join(imp), inline=False)

        embed.set_footer(text=f"Запросил: {interaction.user.name}")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(AdminUtilsCog(bot))
