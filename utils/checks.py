"""
Система проверки прав доступа к командам.
Администраторы имеют доступ ко всему автоматически.
Для остальных — настраивается через /доступ-добавить.
"""

import discord
from discord import app_commands
from database import db


async def has_command_access(interaction: discord.Interaction, command_name: str) -> bool:
    """Возвращает True, если у пользователя есть доступ к команде."""
    # Администраторы всегда имеют доступ
    if interaction.user.guild_permissions.administrator:
        return True

    data = await db.get(f"access.{command_name}", {"roles": [], "users": []})
    allowed_users = [str(u) for u in data.get("users", [])]
    allowed_roles = [str(r) for r in data.get("roles", [])]

    if str(interaction.user.id) in allowed_users:
        return True

    user_role_ids = [str(r.id) for r in interaction.user.roles]
    if any(rid in allowed_roles for rid in user_role_ids):
        return True

    return False


def custom_check():
    """
    Декоратор для команд с кастомной проверкой доступа.
    Использование: @custom_check() перед командой.
    """
    async def predicate(interaction: discord.Interaction) -> bool:
        cmd = interaction.command.name if interaction.command else "unknown"
        if not await has_command_access(interaction, cmd):
            await interaction.response.send_message(
                "❌ У вас нет доступа к этой команде.\n"
                "Обратитесь к администратору для получения прав.",
                ephemeral=True,
            )
            return False
        return True

    return app_commands.check(predicate)
