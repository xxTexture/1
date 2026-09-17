"""
🧰 НАСТРОЙКА ФОРМ ТИКЕТОВ И ЗАЯВОК
/тикет-настройка  — типы обращений, вопросы форм, роли, категории, тест формы
/заявки-настройка — вопросы анкет для заявок (персонал / дс-адм / билдеры)

Все настройки хранятся в data.json и применяются мгновенно:
панели тикетов/заявок обновляются автоматически.
"""

import discord
from discord import app_commands
from discord.ext import commands

from config import BotConfig
from texts import T
from utils.forms import (
    APP_TYPE_IDS, MAX_QUESTIONS, MAX_TYPES,
    load_app_questions, load_types,
    make_question, parse_color, save_app_questions, save_types, slugify, valid_slug,
)
from cogs.applications import app_label

ADMIN_PERMS = discord.Permissions(administrator=True)

FIELD_CHOICES = [
    app_commands.Choice(name="Название (в списке)", value="label"),
    app_commands.Choice(name="Описание (в списке)", value="description"),
    app_commands.Choice(name="Эмодзи", value="emoji"),
    app_commands.Choice(name="Цвет (HEX, напр. 5865f2)", value="color"),
    app_commands.Choice(name="Префикс канала (латиница)", value="prefix"),
]

APP_CHOICES = [
    app_commands.Choice(name="🛡️ Персонал", value="персонал"),
    app_commands.Choice(name="⚙️ ДС-Адм",  value="дс-адм"),
    app_commands.Choice(name="🔨 Билдеры", value="билдеры"),
]


# ─── АВТОДОПОЛНЕНИЕ ──────────────────────────────────────────────────────────
async def type_autocomplete(interaction: discord.Interaction, current: str):
    types = await load_types()
    return [
        app_commands.Choice(name=str(cfg.get("label", tid))[:100], value=tid[:100])
        for tid, cfg in types.items()
        if current.lower() in tid.lower() or current.lower() in str(cfg.get("label", "")).lower()
    ][:25]


# ─── ТЕСТ ФОРМЫ ──────────────────────────────────────────────────────────────
class TestTicketModal(discord.ui.Modal):
    """Открывает реальную форму типа, но вместо создания тикета показывает предпросмотр."""

    def __init__(self, type_id: str, type_cfg: dict):
        title = f"Тест: {type_cfg.get('label', type_id)}"[:45]
        super().__init__(title=title)
        self.type_cfg = type_cfg
        self.inputs = []
        questions = (type_cfg.get("questions") or [
            make_question(str(T.TICKET_REASON_LABEL), str(T.TICKET_REASON_PH))
        ])[:MAX_QUESTIONS]
        for q in questions:
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
        lines = []
        for q, ti in self.inputs:
            lines.append(f"**{q.get('label')}**\n```{(ti.value or '—')[:1000]}```")
        em = discord.Embed(
            title="🧪 Предпросмотр тикета (тест — канал не создан)",
            description="\n\n".join(lines) or "—",
            color=self.type_cfg.get("color") or 0x5865F2,
        )
        cat = self.type_cfg.get("category_id") or BotConfig.TICKET_CATEGORY_ID
        em.set_footer(text=f"Канал создавался бы в категории: {'по умолчанию' if not cat else cat}")
        await interaction.response.send_message(embed=em, ephemeral=True)


def _types_overview_embed(types: dict) -> discord.Embed:
    em = discord.Embed(
        title="🎫 Типы тикетов",
        description=f"Всего: **{len(types)}/{MAX_TYPES}**  •  Вопросы: до **{MAX_QUESTIONS}** на тип",
        color=BotConfig.TICKET_COLOR,
    )
    for tid, cfg in types.items():
        roles = [f"<@&{r}>" for r in cfg.get("ping_roles", []) if r] or ["—"]
        cat = cfg.get("category_id") or BotConfig.TICKET_CATEGORY_ID
        lines = [
            f"Код: `{tid}`",
            f"Префикс канала: `{cfg.get('prefix', '—')}`  •  Цвет: `#{(cfg.get('color') or 0):06X}`",
            f"Пинг: {' '.join(roles)}",
            f"Категория: {'по умолчанию' if not cat else f'<#{cat}>'}",
        ]
        qs = cfg.get("questions", [])
        if qs:
            lines.append("Вопросы: " + " | ".join(
                f"{i + 1}. {q.get('label', '?')}" for i, q in enumerate(qs)
            ))
        else:
            lines.append("Вопросы: стандартный (описание проблемы)")
        em.add_field(name=str(cfg.get("label", tid))[:256], value="\n".join(lines)[:1024], inline=False)
    return em


# ─── COG ─────────────────────────────────────────────────────────────────────
class FormsSetupCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ═══════════════════════ 🎫 /тикет-настройка ═══════════════════════
    ts = app_commands.Group(
        name="тикет-настройка",
        description="🎫 [АДМИН] Типы тикетов и формы обращений",
        default_permissions=ADMIN_PERMS,
    )
    ts_q = app_commands.Group(name="вопрос", description="Вопросы формы обращения")
    ts_r = app_commands.Group(name="роль", description="Роли для пинга в тикете")
    ts.add_command(ts_q)
    ts.add_command(ts_r)

    @ts.command(name="список", description="📃 Показать все типы тикетов и их формы")
    async def ts_list(self, interaction: discord.Interaction):
        types = await load_types()
        await interaction.response.send_message(embed=_types_overview_embed(types), ephemeral=True)

    @ts.command(name="добавить", description="➕ Добавить тип обращения (свой тикет со своей формой)")
    @app_commands.describe(
        код="Уникальный код латиницей, напр. report (используется в командах)",
        название="Название в выпадающем списке, напр. 🚨 Жалоба",
        описание="Краткое описание в списке (необязательно)",
        эмодзи="Эмодзи типа (необязательно, если уже есть в названии)",
        цвет="HEX-цвет embed, напр. 5865f2 (необязательно)",
        префикс="Префикс канала латиницей, напр. zhaloba (необязательно)",
    )
    async def ts_add(self, interaction: discord.Interaction, код: str, название: str,
                     описание: str = None, эмодзи: str = None, цвет: str = None, префикс: str = None):
        код = код.lower().strip()
        if not valid_slug(код):
            await interaction.response.send_message(
                "❌ Код типа: только латиница, цифры и дефис (напр. `report`, `tech-support`).", ephemeral=True)
            return
        types = await load_types()
        if код in types:
            await interaction.response.send_message(f"❌ Тип `{код}` уже существует.", ephemeral=True)
            return
        if len(types) >= MAX_TYPES:
            await interaction.response.send_message(f"❌ Достигнут лимит Discord: {MAX_TYPES} типов.", ephemeral=True)
            return

        if цвет:
            color = parse_color(цвет)
            if color is None:
                await interaction.response.send_message("❌ Неверный цвет. Формат: `5865f2` (HEX, 6 символов).", ephemeral=True)
                return
        else:
            color = 0x5865F2

        if префикс:
            prefix = префикс.lower().strip()
            if not valid_slug(prefix):
                await interaction.response.send_message(
                    "❌ Префикс: только латиница, цифры и дефис (Discord не разрешает кириллицу в названиях каналов).",
                    ephemeral=True)
                return
        else:
            prefix = slugify(название, 24) or slugify(код, 24) or "ticket"

        types[код] = {
            "label": название[:100],
            "description": (описание or "")[:100],
            "emoji": (эмодзи or "")[:64],
            "color": color,
            "prefix": prefix,
            "ping_roles": [],
            "category_id": BotConfig.TICKET_CATEGORY_ID,
            "questions": [make_question(str(T.TICKET_REASON_LABEL), str(T.TICKET_REASON_PH), True, True, 1000)],
        }
        await save_types(types)
        updated = await self._refresh_panels()
        await interaction.response.send_message(
            f"✅ Тип **{название}** (`{код}`) добавлен! Каналы будут называться `{prefix}-ник`.\n"
            f"📝 Добавьте вопросы формы: `/тикет-настройка вопрос добавить {код} ...`\n"
            f"👥 Добавьте роли для пинга: `/тикет-настройка роль добавить {код} @роль`"
            + (f"\n🔄 Обновлённых панелей: {updated}" if updated else ""),
            ephemeral=True,
        )

    @ts.command(name="удалить", description="🗑️ Удалить тип обращения")
    @app_commands.autocomplete(код=type_autocomplete)
    async def ts_remove(self, interaction: discord.Interaction, код: str):
        types = await load_types()
        if код not in types:
            await interaction.response.send_message(f"❌ Тип `{код}` не найден.", ephemeral=True)
            return
        label = types[код].get("label", код)
        del types[код]
        await save_types(types)
        updated = await self._refresh_panels()
        await interaction.response.send_message(
            f"✅ Тип **{label}** удалён." + (f" Обновлённых панелей: {updated}" if updated else ""),
            ephemeral=True,
        )

    @ts.command(name="изменить", description="✏️ Изменить параметр типа (название, цвет, префикс...)")
    @app_commands.describe(код="Тип обращения", поле="Что изменить", значение="Новое значение")
    @app_commands.autocomplete(код=type_autocomplete)
    async def ts_edit(self, interaction: discord.Interaction, код: str,
                      поле: app_commands.Choice[str], значение: str):
        types = await load_types()
        if код not in types:
            await interaction.response.send_message(f"❌ Тип `{код}` не найден.", ephemeral=True)
            return
        field = поле.value
        if field == "label":
            types[код]["label"] = значение[:100]
        elif field == "description":
            types[код]["description"] = значение[:100]
        elif field == "emoji":
            types[код]["emoji"] = значение[:64]
        elif field == "color":
            color = parse_color(значение)
            if color is None:
                await interaction.response.send_message("❌ Неверный цвет. Формат: `5865f2`.", ephemeral=True)
                return
            types[код]["color"] = color
        elif field == "prefix":
            prefix = значение.lower().strip()
            if not valid_slug(prefix):
                await interaction.response.send_message(
                    "❌ Префикс: только латиница, цифры и дефис.", ephemeral=True)
                return
            types[код]["prefix"] = prefix
        await save_types(types)
        updated = await self._refresh_panels()
        await interaction.response.send_message(
            f"✅ Параметр «{поле.name}» обновлён." + (f" Обновлённых панелей: {updated}" if updated else ""),
            ephemeral=True,
        )

    @ts.command(name="категория", description="📁 Категория для каналов этого типа")
    @app_commands.describe(код="Тип обращения", категория="Категория (не указывать = из config.py)")
    @app_commands.autocomplete(код=type_autocomplete)
    async def ts_category(self, interaction: discord.Interaction, код: str,
                          категория: discord.CategoryChannel = None):
        types = await load_types()
        if код not in types:
            await interaction.response.send_message(f"❌ Тип `{код}` не найден.", ephemeral=True)
            return
        types[код]["category_id"] = категория.id if категория else None
        await save_types(types)
        await interaction.response.send_message(
            f"✅ Категория для `{код}`: "
            + (f"**{категория.name}**" if категория else "по умолчанию (из config.py)"),
            ephemeral=True,
        )

    @ts.command(name="тест", description="🧪 Открыть форму типа и посмотреть предпросмотр")
    @app_commands.autocomplete(код=type_autocomplete)
    async def ts_test(self, interaction: discord.Interaction, код: str):
        types = await load_types()
        cfg = types.get(код)
        if not cfg:
            await interaction.response.send_message(f"❌ Тип `{код}` не найден.", ephemeral=True)
            return
        await interaction.response.send_modal(TestTicketModal(код, cfg))

    # ── вопросы ──
    @ts_q.command(name="добавить", description="➕ Добавить вопрос в форму обращения")
    @app_commands.describe(
        код="Тип обращения",
        вопрос="Текст вопроса (до 45 символов)",
        подсказка="Серая подсказка в поле (необязательно)",
        обязательный="Обязательно ли заполнять (да/нет)",
        многострочный="Большое поле для развёрнутого ответа (да/нет)",
        макс_символов="Максимум символов в ответе (100–4000)",
    )
    @app_commands.autocomplete(код=type_autocomplete)
    async def ts_q_add(self, interaction: discord.Interaction, код: str, вопрос: str,
                       подсказка: str = None, обязательный: bool = True,
                       многострочный: bool = True,
                       макс_символов: app_commands.Range[int, 100, 4000] = 1000):
        types = await load_types()
        if код not in types:
            await interaction.response.send_message(f"❌ Тип `{код}` не найден.", ephemeral=True)
            return
        qs = types[код].get("questions", [])
        if len(qs) >= MAX_QUESTIONS:
            await interaction.response.send_message(
                f"❌ Максимум {MAX_QUESTIONS} вопросов в форме (лимит Discord). Сначала удалите лишний.",
                ephemeral=True)
            return
        qs.append(make_question(вопрос, подсказка or "", обязательный, многострочный, макс_символов))
        types[код]["questions"] = qs
        await save_types(types)
        await interaction.response.send_message(
            f"✅ Вопрос **{len(qs)}. {вопрос[:45]}** добавлен в форму `{код}`.", ephemeral=True)

    @ts_q.command(name="удалить", description="🗑️ Удалить вопрос из формы по номеру")
    @app_commands.describe(код="Тип обращения", номер="Номер вопроса (см. /тикет-настройка список)")
    @app_commands.autocomplete(код=type_autocomplete)
    async def ts_q_remove(self, interaction: discord.Interaction, код: str,
                          номер: app_commands.Range[int, 1, 5]):
        types = await load_types()
        if код not in types:
            await interaction.response.send_message(f"❌ Тип `{код}` не найден.", ephemeral=True)
            return
        qs = types[код].get("questions", [])
        if номер > len(qs):
            await interaction.response.send_message(f"❌ В форме `{код}` всего {len(qs)} вопрос(ов).", ephemeral=True)
            return
        removed = qs.pop(номер - 1)
        types[код]["questions"] = qs
        await save_types(types)
        await interaction.response.send_message(
            f"✅ Вопрос «{removed.get('label', '?')}» удалён из формы `{код}`.", ephemeral=True)

    # ── роли ──
    @ts_r.command(name="добавить", description="👥 Роль, которую пинговать при создании тикета")
    @app_commands.describe(код="Тип обращения", роль="Роль для пинга")
    @app_commands.autocomplete(код=type_autocomplete)
    async def ts_r_add(self, interaction: discord.Interaction, код: str, роль: discord.Role):
        types = await load_types()
        if код not in types:
            await interaction.response.send_message(f"❌ Тип `{код}` не найден.", ephemeral=True)
            return
        roles = types[код].get("ping_roles", [])
        if роль.id in roles:
            await interaction.response.send_message("ℹ️ Эта роль уже добавлена.", ephemeral=True)
            return
        roles.append(роль.id)
        types[код]["ping_roles"] = roles
        await save_types(types)
        await interaction.response.send_message(
            f"✅ Роль {роль.mention} будет пинговаться в тикетах `{код}`.", ephemeral=True)

    @ts_r.command(name="удалить", description="🗑️ Убрать роль из пинга тикета")
    @app_commands.describe(код="Тип обращения", роль="Роль")
    @app_commands.autocomplete(код=type_autocomplete)
    async def ts_r_remove(self, interaction: discord.Interaction, код: str, роль: discord.Role):
        types = await load_types()
        if код not in types:
            await interaction.response.send_message(f"❌ Тип `{код}` не найден.", ephemeral=True)
            return
        roles = types[код].get("ping_roles", [])
        if роль.id not in roles:
            await interaction.response.send_message("ℹ️ Этой роли нет в списке пинга.", ephemeral=True)
            return
        roles.remove(роль.id)
        types[код]["ping_roles"] = roles
        await save_types(types)
        await interaction.response.send_message(
            f"✅ Роль {роль.mention} убрана из пинга тикетов `{код}`.", ephemeral=True)

    @ts_r.command(name="очистить", description="🧹 Убрать все роли из пинга типа")
    @app_commands.autocomplete(код=type_autocomplete)
    async def ts_r_clear(self, interaction: discord.Interaction, код: str):
        types = await load_types()
        if код not in types:
            await interaction.response.send_message(f"❌ Тип `{код}` не найден.", ephemeral=True)
            return
        types[код]["ping_roles"] = []
        await save_types(types)
        await interaction.response.send_message(f"✅ Список пинга `{код}` очищен.", ephemeral=True)

    # ═══════════════════════ 📋 /заявки-настройка ═══════════════════════
    asg = app_commands.Group(
        name="заявки-настройка",
        description="📋 [АДМИН] Вопросы анкет для заявок в команду",
        default_permissions=ADMIN_PERMS,
    )
    asg_q = app_commands.Group(name="вопрос", description="Вопросы анкеты")
    asg.add_command(asg_q)

    @asg.command(name="список", description="📃 Вопросы анкеты направления")
    @app_commands.choices(направление=APP_CHOICES)
    async def asg_list(self, interaction: discord.Interaction, направление: str = None):
        qs_all = await load_app_questions()
        ids = [направление] if направление else list(APP_TYPE_IDS)
        em = discord.Embed(title="📋 Вопросы анкет", color=BotConfig.APP_COLOR)
        for aid in ids:
            qs = qs_all.get(aid, [])
            lines = [
                f"{i + 1}. **{q.get('label', '?')}** "
                f"({'обяз.' if q.get('required', True) else 'необяз.'}, "
                f"{'длинное' if q.get('long') else 'короткое'} поле, ≤{q.get('max_length', 1000)})"
                for i, q in enumerate(qs)
            ] or ["*Вопросы не настроены — будет стандартный*"]
            em.add_field(name=app_label(aid), value="\n".join(lines)[:1024], inline=False)
        await interaction.response.send_message(embed=em, ephemeral=True)

    @asg_q.command(name="добавить", description="➕ Добавить вопрос в анкету направления")
    @app_commands.describe(
        направление="Куда добавляем вопрос",
        вопрос="Текст вопроса (до 45 символов)",
        подсказка="Серая подсказка в поле (необязательно)",
        обязательный="Обязательно ли заполнять (да/нет)",
        многострочный="Большое поле для развёрнутого ответа (да/нет)",
        макс_символов="Максимум символов в ответе (100–4000)",
    )
    @app_commands.choices(направление=APP_CHOICES)
    async def asg_q_add(self, interaction: discord.Interaction, направление: str, вопрос: str,
                        подсказка: str = None, обязательный: bool = True,
                        многострочный: bool = True,
                        макс_символов: app_commands.Range[int, 100, 4000] = 1000):
        qs_all = await load_app_questions()
        qs = qs_all.get(направление, [])
        if len(qs) >= MAX_QUESTIONS:
            await interaction.response.send_message(
                f"❌ Максимум {MAX_QUESTIONS} вопросов в анкете (лимит Discord).", ephemeral=True)
            return
        qs.append(make_question(вопрос, подсказка or "", обязательный, многострочный, макс_символов))
        qs_all[направление] = qs
        await save_app_questions(qs_all)
        await interaction.response.send_message(
            f"✅ Вопрос **{len(qs)}. {вопрос[:45]}** добавлен в анкету «{направление}».", ephemeral=True)

    @asg_q.command(name="удалить", description="🗑️ Удалить вопрос анкеты по номеру")
    @app_commands.describe(направление="Направление", номер="Номер вопроса (см. /заявки-настройка список)")
    @app_commands.choices(направление=APP_CHOICES)
    async def asg_q_remove(self, interaction: discord.Interaction, направление: str,
                           номер: app_commands.Range[int, 1, 5]):
        qs_all = await load_app_questions()
        qs = qs_all.get(направление, [])
        if номер > len(qs):
            await interaction.response.send_message(
                f"❌ В анкете «{направление}» всего {len(qs)} вопрос(ов).", ephemeral=True)
            return
        removed = qs.pop(номер - 1)
        qs_all[направление] = qs
        await save_app_questions(qs_all)
        await interaction.response.send_message(
            f"✅ Вопрос «{removed.get('label', '?')}» удалён из анкеты «{направление}».", ephemeral=True)

    # ─── служебное ───
    async def _refresh_panels(self) -> int:
        """Обновляет панели тикетов после изменений."""
        try:
            from cogs.tickets import refresh_ticket_panels
            return await refresh_ticket_panels(self.bot)
        except Exception as e:
            print(f"[FormsSetup] Не удалось обновить панели: {e}")
            return 0


async def setup(bot):
    await bot.add_cog(FormsSetupCog(bot))
