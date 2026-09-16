"""
ℹ️ КОМАНДА /info
Отправляет игроку в ЛС информацию о сервере.
Настраивается через Discord без правок кода.
"""

import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional

from database import db

DEFAULT_TEXT = (
    "🌐 **Информация о сервере**\n\n"
    "Привет! Здесь всё о нашем проекте.\n\n"
    "📌 **IP сервера:** `play.yourserver.net`\n\n"
    "🎁 **Уникальные услуги:**\n"
    "• Персональные привилегии\n"
    "• Эксклюзивные возможности для донатеров\n\n"
    "_Настройте через /info-настройка текст_"
)


async def get_info_config() -> dict:
    return await db.get("info_config", {"text": DEFAULT_TEXT, "links": []})


class InfoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="info", description="ℹ️ Получить информацию о сервере (отправим в ЛС)")
    async def info(self, interaction: discord.Interaction):
        cfg   = await get_info_config()
        embed = discord.Embed(description=cfg.get("text", DEFAULT_TEXT), color=0x2ecc71, timestamp=discord.utils.utcnow())
        embed.set_author(
            name=interaction.guild.name,
            icon_url=interaction.guild.icon.url if interaction.guild.icon else None,
        )
        links = cfg.get("links", [])
        if links:
            embed.add_field(name="🔗 Полезные ссылки", value="\n".join(f"[{l['label']}]({l['url']})" for l in links), inline=False)
        embed.set_footer(text=f"Запросил: {interaction.user.name}")

        try:
            await interaction.user.send(embed=embed)
            await interaction.response.send_message("✅ Информация отправлена в ЛС!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Не могу отправить ЛС. Разрешите личные сообщения от участников сервера.", ephemeral=True
            )

    # ─── ГРУППА НАСТРОЙКИ ────────────────────────────────────────────────────
    info_admin = app_commands.Group(
        name="info-настройка", description="⚙️ Настройка команды /info",
        default_permissions=discord.Permissions(administrator=True),
    )

    @info_admin.command(name="текст", description="✏️ Изменить текст /info")
    async def set_text(self, interaction: discord.Interaction, текст: str):
        cfg = await get_info_config()
        cfg["text"] = текст
        await db.set("info_config", cfg)
        await interaction.response.send_message("✅ Текст /info обновлён.", ephemeral=True)

    @info_admin.command(name="ссылка-добавить", description="➕ Добавить ссылку")
    async def add_link(self, interaction: discord.Interaction, название: str, ссылка: str):
        cfg = await get_info_config()
        cfg.setdefault("links", []).append({"label": название, "url": ссылка})
        await db.set("info_config", cfg)
        await interaction.response.send_message(f"✅ Ссылка **{название}** добавлена.", ephemeral=True)

    @info_admin.command(name="ссылка-удалить", description="➖ Удалить ссылку по номеру")
    async def remove_link(self, interaction: discord.Interaction, номер: app_commands.Range[int, 1, 20]):
        cfg   = await get_info_config()
        links = cfg.get("links", [])
        if номер > len(links):
            await interaction.response.send_message("❌ Ссылки с таким номером нет.", ephemeral=True); return
        removed = links.pop(номер - 1)
        cfg["links"] = links
        await db.set("info_config", cfg)
        await interaction.response.send_message(f"✅ Ссылка **{removed['label']}** удалена.", ephemeral=True)

    @info_admin.command(name="показать", description="👁️ Предпросмотр /info")
    async def preview(self, interaction: discord.Interaction):
        cfg   = await get_info_config()
        links = cfg.get("links", [])
        lines = ["**📋 Текст /info:**", cfg.get("text", ""), ""]
        if links:
            lines.append("**🔗 Ссылки:**")
            for i, l in enumerate(links, 1):
                lines.append(f"  {i}. {l['label']} → {l['url']}")
        else:
            lines.append("**🔗 Ссылки:** *(нет)*")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot):
    await bot.add_cog(InfoCog(bot))
