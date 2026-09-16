"""
🤖 АВТО-ОТВЕТ — группа /авто-ответ
Подкоманды: установить / выкл / список
Команда скрыта от пользователей без прав moderate_members.
"""

import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional
import random

from database import db
from texts import T


class AutoReplyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Группа скрыта от обычных пользователей (нужен moderate_members)
    авто_ответ = app_commands.Group(
        name="авто-ответ",
        description="🤖 Управление авто-ответом для игроков",
        default_permissions=discord.Permissions(moderate_members=True),
    )

    @авто_ответ.command(name="установить", description="Установить авто-ответ для игрока (до 5 вариантов)")
    @app_commands.describe(
        игрок="Игрок",
        вариант1="Вариант ответа 1 (обязательно)",
        вариант2="Вариант ответа 2",
        вариант3="Вариант ответа 3",
        вариант4="Вариант ответа 4",
        вариант5="Вариант ответа 5",
    )
    async def set_cmd(
        self, interaction: discord.Interaction, игрок: discord.Member,
        вариант1: str,
        вариант2: Optional[str] = None,
        вариант3: Optional[str] = None,
        вариант4: Optional[str] = None,
        вариант5: Optional[str] = None,
    ):
        msgs = [m for m in [вариант1, вариант2, вариант3, вариант4, вариант5] if m]
        await db.set(f"auto_reply.{игрок.id}", msgs)
        preview = "\n".join(f"  {i+1}. {m}" for i, m in enumerate(msgs))
        await interaction.response.send_message(
            T.AR_SET_OK.format(user=игрок.mention, count=len(msgs), preview=preview),
            ephemeral=True,
        )

    @авто_ответ.command(name="выкл", description="Отключить авто-ответ для игрока")
    async def off_cmd(self, interaction: discord.Interaction, игрок: discord.Member):
        await db.delete(f"auto_reply.{игрок.id}")
        await interaction.response.send_message(T.AR_OFF_OK.format(user=игрок.mention), ephemeral=True)

    @авто_ответ.command(name="список", description="Список всех активных авто-ответов")
    async def list_cmd(self, interaction: discord.Interaction):
        all_data = await db.get("auto_reply", {})
        if not all_data:
            await interaction.response.send_message(T.AR_EMPTY, ephemeral=True)
            return
        lines = []
        for uid, msgs in all_data.items():
            m    = interaction.guild.get_member(int(uid))
            name = m.mention if m else f"<@{uid}>"
            preview = msgs[0][:40] + ("..." if len(msgs[0]) > 40 else "")
            lines.append(f"• {name} — {len(msgs)} вар.: `{preview}`")
        embed = discord.Embed(title=T.AR_LIST_TITLE, description="\n".join(lines), color=0x5865f2)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ─── Слушаем сообщения ───────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        msgs = await db.get(f"auto_reply.{message.author.id}")
        if not msgs:
            return
        try:
            await message.reply(random.choice(msgs), mention_author=False)
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(AutoReplyCog(bot))
