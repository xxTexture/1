"""
✏️ НАСТРОЙКА ВСЕХ ТЕКСТОВ БОТА
/тексты список   — все тексты по категориям
/тексты поиск    — найти текст по фрагменту
/тексты показать — значение и дефолт конкретного текста
/тексты изменить — изменить текст прямо из Discord (без перезапуска)
/тексты сброс    — вернуть текст(ы) к значениям по умолчанию

Переопределения хранятся в data.json → "text_overrides".
Дефолты — в texts.py.
"""

import discord
from discord import app_commands
from discord.ext import commands

from database import db
from texts import (
    T, all_text_keys, categories, category_for_key, get_default,
    is_overridden, keys_in_category, validate_text,
)

PER_PAGE = 8
SNIPPET  = 90   # длина превью значения в списке


async def key_autocomplete(interaction: discord.Interaction, current: str):
    current = current.lower()
    return [
        app_commands.Choice(name=k, value=k)
        for k in all_text_keys() if current in k.lower()
    ][:25]


async def category_autocomplete(interaction: discord.Interaction, current: str):
    current = current.lower()
    return [
        app_commands.Choice(name=c, value=c)
        for c in categories() if current in c.lower()
    ][:25]


def _snippet(key: str) -> str:
    value = str(getattr(T, key)).replace("\n", " ⏎ ")
    if len(value) > SNIPPET:
        value = value[:SNIPPET] + "…"
    return value


class TextEditModal(discord.ui.Modal):
    def __init__(self, key: str):
        super().__init__(title=f"Текст: {key}"[:45], custom_id=f"text_edit_{key}")
        self.key = key
        current = str(getattr(T, key))
        self.value_input = discord.ui.TextInput(
            label=key[:45],
            style=discord.TextStyle.paragraph,
            required=True,
            default=current[:4000],
            max_length=4000,
            placeholder="Новое значение текста...",
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        error = validate_text(self.key, self.value_input.value)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        await db.set(f"text_overrides.{self.key}", self.value_input.value)
        await interaction.response.send_message(
            f"✅ Текст `{self.key}` обновлён — изменения применены сразу.\n"
            "↩️ Вернуть значение по умолчанию: `/тексты сброс`",
            ephemeral=True,
        )


class TextSetupCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    texts_group = app_commands.Group(
        name="тексты",
        description="✏️ [АДМИН] Настройка всех текстов бота",
        default_permissions=discord.Permissions(administrator=True),
    )

    @texts_group.command(name="список", description="📃 Все тексты бота по категориям")
    @app_commands.describe(категория="Показать только категорию", страница="Номер страницы")
    @app_commands.autocomplete(категория=category_autocomplete)
    async def list_texts(self, interaction: discord.Interaction,
                         категория: str = None, страница: app_commands.Range[int, 1, 99] = 1):
        if not категория:
            em = discord.Embed(
                title="✏️ Категории текстов",
                description=(
                    "Выберите категорию в `/тексты список`, чтобы увидеть тексты.\n"
                    "Изменить текст: `/тексты изменить`\n"
                    "Найти по фразе: `/тексты поиск`"
                ),
                color=0x5865F2,
            )
            for cat in categories():
                keys = keys_in_category(cat)
                overridden = sum(1 for k in keys if is_overridden(k))
                mark = f" • изменено: {overridden}" if overridden else ""
                em.add_field(name=f"{cat} ({len(keys)})", value=f"`/тексты список {cat}`{mark}", inline=False)
            await interaction.response.send_message(embed=em, ephemeral=True)
            return

        keys = keys_in_category(категория)
        if not keys:
            await interaction.response.send_message("❌ В этой категории нет текстов.", ephemeral=True)
            return
        pages = (len(keys) + PER_PAGE - 1) // PER_PAGE
        страница = min(страница, pages)
        chunk = keys[(страница - 1) * PER_PAGE: страница * PER_PAGE]
        em = discord.Embed(
            title=f"✏️ {категория}",
            description=f"Страница **{страница}/{pages}** • 🔶 = изменён через `/тексты изменить`",
            color=0x5865F2,
        )
        for key in chunk:
            mark = " 🔶" if is_overridden(key) else ""
            em.add_field(name=f"`{key}`{mark}", value=_snippet(key), inline=False)
        await interaction.response.send_message(embed=em, ephemeral=True)

    @texts_group.command(name="поиск", description="🔍 Найти текст по фрагменту (в значении или ключе)")
    @app_commands.describe(фрагмент="Что ищем, напр. «тикет»")
    async def search_texts(self, interaction: discord.Interaction, фрагмент: str):
        frag = фрагмент.lower()
        found = [
            k for k in all_text_keys()
            if frag in k.lower() or frag in str(getattr(T, k)).lower()
        ][:15]
        if not found:
            await interaction.response.send_message(
                f"❌ Ничего не нашлось по «{фрагмент}».", ephemeral=True)
            return
        em = discord.Embed(
            title=f"🔍 Поиск: «{фрагмент}»",
            description=f"Найдено: **{len(found)}** (показаны первые 15)",
            color=0x5865F2,
        )
        for key in found:
            mark = " 🔶" if is_overridden(key) else ""
            em.add_field(name=f"`{key}`{mark}", value=_snippet(key), inline=False)
        await interaction.response.send_message(embed=em, ephemeral=True)

    @texts_group.command(name="показать", description="👁️ Текущее значение текста и значение по умолчанию")
    @app_commands.describe(ключ="Ключ текста (напр. TICKET_PANEL_TITLE)")
    @app_commands.autocomplete(ключ=key_autocomplete)
    async def show_text(self, interaction: discord.Interaction, ключ: str):
        if ключ not in all_text_keys():
            await interaction.response.send_message(f"❌ Текст `{ключ}` не найден.", ephemeral=True)
            return
        em = discord.Embed(title=f"✏️ {ключ}", color=0x5865F2)
        em.add_field(name="Категория", value=category_for_key(ключ), inline=False)
        em.add_field(name="Текущее значение", value=f"```{str(getattr(T, ключ))[:1000]}```", inline=False)
        em.add_field(name="По умолчанию", value=f"```{str(get_default(ключ))[:1000]}```", inline=False)
        em.set_footer(text="🔶 изменён" if is_overridden(ключ) else "стандартный текст")
        await interaction.response.send_message(embed=em, ephemeral=True)

    @texts_group.command(name="изменить", description="✏️ Изменить текст бота (откроется окно редактирования)")
    @app_commands.describe(ключ="Ключ текста (напр. TICKET_PANEL_TITLE)")
    @app_commands.autocomplete(ключ=key_autocomplete)
    async def edit_text(self, interaction: discord.Interaction, ключ: str):
        if ключ not in all_text_keys():
            await interaction.response.send_message(f"❌ Текст `{ключ}` не найден.", ephemeral=True)
            return
        await interaction.response.send_modal(TextEditModal(ключ))

    @texts_group.command(name="сброс", description="↩️ Вернуть текст к значению по умолчанию (или все тексты)")
    @app_commands.describe(ключ="Ключ текста; если не указать — сбросить ВСЕ изменённые тексты")
    @app_commands.autocomplete(ключ=key_autocomplete)
    async def reset_text(self, interaction: discord.Interaction, ключ: str = None):
        if ключ:
            if not is_overridden(ключ):
                await interaction.response.send_message(f"ℹ️ `{ключ}` и так стандартный.", ephemeral=True)
                return
            await db.delete(f"text_overrides.{ключ}")
            await interaction.response.send_message(
                f"✅ `{ключ}` возвращён к значению по умолчанию.", ephemeral=True)
            return
        overrides = await db.get("text_overrides", {})
        count = len(overrides) if isinstance(overrides, dict) else 0
        await db.set("text_overrides", {})
        await interaction.response.send_message(
            f"✅ Все тексты сброшены к значениям по умолчанию (было изменено: {count}).",
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(TextSetupCog(bot))
