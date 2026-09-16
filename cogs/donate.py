"""
💳 МАГАЗИН ДОНАТА
/donate        — публичная команда
/donate-admin  — группа настройки (скрыта от обычных пользователей)
"""

import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional

from database import db
from texts import T


async def get_config() -> dict:
    return await db.get("donate_config", {"text": T.DONATE_DEFAULT_TEXT, "items": [], "payments": []})

async def save_config(cfg: dict):
    await db.set("donate_config", cfg)


class DonateCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ─── /donate (публичная) ─────────────────────────────────────────────────
    @app_commands.command(name="donate", description="💳 Магазин доната — товары и способы оплаты")
    async def donate(self, interaction: discord.Interaction):
        cfg   = await get_config()
        embed = discord.Embed(title=T.DONATE_TITLE, description=cfg.get("text", T.DONATE_DEFAULT_TEXT), color=0xf1c40f)

        items = cfg.get("items", [])
        if items:
            rows = []
            for i, it in enumerate(items, 1):
                row = f"**{i}. {it['name']}** — `{it['price']}`"
                if it.get("desc"):
                    row += f"\n> {it['desc']}"
                rows.append(row)
            embed.add_field(name=T.DONATE_ITEMS_FIELD, value="\n".join(rows), inline=False)
        else:
            embed.add_field(name=T.DONATE_ITEMS_FIELD, value=T.DONATE_NO_ITEMS, inline=False)

        payments = cfg.get("payments", [])
        if payments:
            embed.add_field(
                name=T.DONATE_PAYS_FIELD,
                value="\n".join(f"**{p['name']}**\n> `{p['details']}`" for p in payments),
                inline=False,
            )
        else:
            embed.add_field(name=T.DONATE_PAYS_FIELD, value=T.DONATE_NO_PAYMENTS, inline=False)

        embed.set_footer(text=T.DONATE_FOOTER)
        await interaction.response.send_message(embed=embed)

    # ─── Группа /donate-admin (скрыта от обычных) ───────────────────────────
    donate_admin = app_commands.Group(
        name="donate-admin",
        description="⚙️ Управление магазином доната",
        default_permissions=discord.Permissions(administrator=True),
    )

    @donate_admin.command(name="текст", description="✏️ Изменить вступительный текст")
    async def set_text(self, interaction: discord.Interaction, текст: str):
        cfg = await get_config(); cfg["text"] = текст; await save_config(cfg)
        await interaction.response.send_message("✅ Текст обновлён.", ephemeral=True)

    @donate_admin.command(name="товар-добавить", description="➕ Добавить товар")
    @app_commands.describe(название="Название", цена="Цена (100₽)", описание="Краткое описание (необязательно)")
    async def add_item(self, interaction: discord.Interaction, название: str, цена: str, описание: Optional[str] = None):
        cfg  = await get_config()
        item = {"name": название, "price": цена}
        if описание: item["desc"] = описание
        cfg.setdefault("items", []).append(item); await save_config(cfg)
        await interaction.response.send_message(f"✅ **{название}** ({цена}) добавлен.", ephemeral=True)

    @donate_admin.command(name="товар-удалить", description="➖ Удалить товар по номеру")
    async def remove_item(self, interaction: discord.Interaction, номер: app_commands.Range[int, 1, 50]):
        cfg   = await get_config(); items = cfg.get("items", [])
        if номер > len(items):
            await interaction.response.send_message("❌ Нет товара с таким номером.", ephemeral=True); return
        removed = items.pop(номер - 1); cfg["items"] = items; await save_config(cfg)
        await interaction.response.send_message(f"✅ **{removed['name']}** удалён.", ephemeral=True)

    @donate_admin.command(name="оплата-добавить", description="➕ Добавить способ оплаты")
    async def add_payment(self, interaction: discord.Interaction, название: str, реквизиты: str):
        cfg = await get_config()
        cfg.setdefault("payments", []).append({"name": название, "details": реквизиты}); await save_config(cfg)
        await interaction.response.send_message(f"✅ Способ **{название}** добавлен.", ephemeral=True)

    @donate_admin.command(name="оплата-удалить", description="➖ Удалить способ оплаты по номеру")
    async def remove_payment(self, interaction: discord.Interaction, номер: app_commands.Range[int, 1, 20]):
        cfg      = await get_config(); payments = cfg.get("payments", [])
        if номер > len(payments):
            await interaction.response.send_message("❌ Нет способа с таким номером.", ephemeral=True); return
        removed = payments.pop(номер - 1); cfg["payments"] = payments; await save_config(cfg)
        await interaction.response.send_message(f"✅ **{removed['name']}** удалён.", ephemeral=True)

    @donate_admin.command(name="список", description="📋 Текущее содержимое магазина")
    async def list_all(self, interaction: discord.Interaction):
        cfg   = await get_config()
        items = cfg.get("items", []); pays = cfg.get("payments", [])
        lines = ["**🛍️ Товары:**"]
        for i, it in enumerate(items, 1):
            lines.append(f"  {i}. {it['name']} — {it['price']}" + (f" | {it.get('desc','')}" if it.get("desc") else ""))
        if not items: lines.append("  *(пусто)*")
        lines += ["\n**💰 Способы оплаты:**"]
        for i, p in enumerate(pays, 1):
            lines.append(f"  {i}. {p['name']}: {p['details']}")
        if not pays: lines.append("  *(пусто)*")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot):
    await bot.add_cog(DonateCog(bot))
