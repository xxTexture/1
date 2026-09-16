"""
🔐 СИСТЕМА ДОСТУПА К КОМАНДАМ
Администраторы имеют доступ ко всему автоматически.
Остальные — через /доступ добавить.
"""

import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional

from database import db


class AccessCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    access = app_commands.Group(
        name="доступ", description="🔐 Управление доступом к командам",
        default_permissions=discord.Permissions(administrator=True),
    )

    @access.command(name="добавить", description="➕ Выдать доступ к команде")
    @app_commands.describe(команда="Название команды (например: ds-panel)", роль="Роль", пользователь="Пользователь")
    async def add_access(self, interaction: discord.Interaction, команда: str,
                         роль: Optional[discord.Role] = None, пользователь: Optional[discord.Member] = None):
        if not роль and not пользователь:
            await interaction.response.send_message("❌ Укажи хотя бы роль или пользователя.", ephemeral=True); return

        data    = await db.get(f"access.{команда}", {"roles": [], "users": []})
        changed = []
        if роль and str(роль.id) not in [str(r) for r in data["roles"]]:
            data["roles"].append(роль.id); changed.append(f"роль {роль.mention}")
        if пользователь and str(пользователь.id) not in [str(u) for u in data["users"]]:
            data["users"].append(пользователь.id); changed.append(f"{пользователь.mention}")

        await db.set(f"access.{команда}", data)
        if changed:
            await interaction.response.send_message(f"✅ Доступ к `/{команда}` выдан: {', '.join(changed)}", ephemeral=True)
        else:
            await interaction.response.send_message("ℹ️ Уже настроено.", ephemeral=True)

    @access.command(name="убрать", description="➖ Забрать доступ к команде")
    @app_commands.describe(команда="Название команды", роль="Роль", пользователь="Пользователь")
    async def remove_access(self, interaction: discord.Interaction, команда: str,
                             роль: Optional[discord.Role] = None, пользователь: Optional[discord.Member] = None):
        data    = await db.get(f"access.{команда}", {"roles": [], "users": []})
        changed = []
        if роль:
            before = len(data["roles"])
            data["roles"] = [r for r in data["roles"] if str(r) != str(роль.id)]
            if len(data["roles"]) < before: changed.append(f"роль {роль.mention}")
        if пользователь:
            before = len(data["users"])
            data["users"] = [u for u in data["users"] if str(u) != str(пользователь.id)]
            if len(data["users"]) < before: changed.append(пользователь.mention)

        await db.set(f"access.{команда}", data)
        if changed:
            await interaction.response.send_message(f"✅ Доступ убран у: {', '.join(changed)}", ephemeral=True)
        else:
            await interaction.response.send_message("ℹ️ Не найдено совпадений.", ephemeral=True)

    @access.command(name="список", description="📋 Кто имеет доступ к команде")
    async def list_access(self, interaction: discord.Interaction, команда: str):
        data  = await db.get(f"access.{команда}", {"roles": [], "users": []})
        roles = data.get("roles", [])
        users = data.get("users", [])
        lines = [f"**🔐 Доступ к `/{команда}`:**\n"]

        if roles:
            lines.append("**Роли:**")
            for rid in roles:
                role = interaction.guild.get_role(int(rid))
                lines.append(f"  • {role.mention if role else f'<@&{rid}>'}")
        else:
            lines.append("**Роли:** *(нет)*")

        lines.append("")
        if users:
            lines.append("**Пользователи:**")
            for uid in users:
                m = interaction.guild.get_member(int(uid))
                lines.append(f"  • {m.mention if m else f'<@{uid}>'}")
        else:
            lines.append("**Пользователи:** *(нет)*")

        lines.append("\n_Администраторы имеют доступ ко всему автоматически._")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @access.command(name="очистить", description="🗑️ Сбросить все права на команду")
    async def clear_access(self, interaction: discord.Interaction, команда: str):
        await db.delete(f"access.{команда}")
        await interaction.response.send_message(f"✅ Права на `/{команда}` сброшены.", ephemeral=True)

    @access.command(name="все-команды", description="📋 Все команды с настроенным доступом")
    async def list_all(self, interaction: discord.Interaction):
        all_access = await db.get("access", {})
        if not all_access:
            await interaction.response.send_message("📭 Ни одна команда не настроена.", ephemeral=True); return
        lines = ["**🔐 Команды с кастомным доступом:**"]
        for cmd_name, data in all_access.items():
            r = len(data.get("roles", []))
            u = len(data.get("users", []))
            lines.append(f"  • `/{cmd_name}` — {r} ролей, {u} польз.")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot):
    await bot.add_cog(AccessCog(bot))
