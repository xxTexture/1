"""
🧪 Смоук-тест: проверяет загрузку всех модулей и новые системы
(формы тикетов/заявок и настройку текстов) БЕЗ подключения к Discord.

Запуск:  python tests/test_smoke.py
(бот при этом НЕ запускается и реальный data.json не трогается —
тест работает во временной папке)
"""

import asyncio
import os
import sys
import tempfile

BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK_DIR = tempfile.mkdtemp(prefix="bot_test_")
os.chdir(WORK_DIR)          # свежий data.json во временной папке
sys.path.insert(0, BOT_DIR)

PASSED = []


def ok(name):
    PASSED.append(name)
    print(f"  ✅ {name}")


async def main():
    # ── 1. Импорт всех модулей ────────────────────────────────────────────
    import discord
    from discord.ext import commands

    import config
    import database
    import texts
    import utils.forms as forms
    import utils.helpers as helpers

    from main import COGS
    ok(f"Импорт базовых модулей ({len(COGS)} когов в main.COGS)")

    # ── 2. Все коги загружаются в бота (без сети) ────────────────────────
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
    for cog in COGS:
        await bot.load_extension(cog)
    ok(f"Загружены все коги: {len(bot.cogs)} шт.")

    # ── 3. Новые команды зарегистрированы ────────────────────────────────
    ts = bot.tree.get_command("тикет-настройка")
    assert ts is not None, "нет /тикет-настройка"
    ts_subs = {c.name for c in ts.commands}
    assert {"список", "добавить", "удалить", "изменить", "категория", "тест", "вопрос", "роль"} <= ts_subs, ts_subs
    q_sub = next(c for c in ts.commands if c.name == "вопрос")
    assert {c.name for c in q_sub.commands} == {"добавить", "удалить"}
    r_sub = next(c for c in ts.commands if c.name == "роль")
    assert {c.name for c in r_sub.commands} == {"добавить", "удалить", "очистить"}
    ok("/тикет-настройка: 6 команд + подгруппы «вопрос» и «роль»")

    asg = bot.tree.get_command("заявки-настройка")
    assert asg is not None and asg.get_command("вопрос") is not None
    ok("/заявки-настройка: список + подгруппа «вопрос»")

    tx = bot.tree.get_command("тексты")
    assert tx is not None
    assert {"список", "поиск", "показать", "изменить", "сброс"} <= {c.name for c in tx.commands}
    ok("/тексты: список, поиск, показать, изменить, сброс")

    # ── 4. Система текстов: дефолт → переопределение → сброс ─────────────
    from texts import T, get_default, is_overridden, validate_text

    default_panel = T.TICKET_PANEL_TITLE
    assert default_panel == get_default("TICKET_PANEL_TITLE")
    assert not is_overridden("TICKET_PANEL_TITLE")

    await database.db.set("text_overrides.TICKET_PANEL_TITLE", "🎮 СВОЙ ЗАГОЛОВОК")
    assert T.TICKET_PANEL_TITLE == "🎮 СВОЙ ЗАГОЛОВОК", "override не применился"
    assert is_overridden("TICKET_PANEL_TITLE")

    await database.db.delete("text_overrides.TICKET_PANEL_TITLE")
    assert T.TICKET_PANEL_TITLE == default_panel, "сброс не сработал"
    ok("T: дефолт → переопределение → сброс")

    assert validate_text("TICKET_MODAL_TITLE", "x" * 46) is not None, "лимит 45 не сработал"
    assert validate_text("TICKET_PANEL_DESC", "норм текст") is None
    assert validate_text("TICKET_PANEL_DESC", "  ") is not None
    ok("validate_text: лимиты Discord и пустые значения")

    # форматирование с плейсхолдерами из переопределения
    await database.db.set("text_overrides.TICKET_CREATED_MSG", "Тикет тут → {channel}")
    assert T.TICKET_CREATED_MSG.format(channel="#test") == "Тикет тут → #test"
    await database.db.delete("text_overrides.TICKET_CREATED_MSG")
    ok("Плейсхолдеры {channel} работают в переопределённых текстах")

    # ── 5. Хранилище типов тикетов ───────────────────────────────────────
    types = await forms.load_types()
    assert set(types.keys()) == {"поддержка", "тех-поддержка"}, types.keys()
    assert types["поддержка"]["prefix"] == "support"
    assert len(types["поддержка"]["questions"]) == 1
    ok("load_types: стандартные типы с латинскими префиксами")

    assert forms.slugify("Тех Поддержка!") == "teh-podderzhka"
    assert forms.slugify("Жалоба на игрока") == "zhaloba-na-igroka"
    assert forms.parse_color("#E67E22") == 0xE67E22
    assert forms.parse_color("zzz") is None
    assert forms.valid_slug("tech-support") and not forms.valid_slug("поддержка")
    ok("slugify/parse_color/valid_slug")

    prefixes = forms.ticket_prefixes_sync()
    assert "support" in prefixes and "поддержка" in prefixes  # legacy
    ok("ticket_prefixes_sync: новые + legacy префиксы")

    # добавление/удаление типа через то же хранилище, что использует ког
    types["report"] = {
        "label": "🚨 Жалоба", "description": "на игрока", "emoji": "🚨",
        "color": 0xED4245, "prefix": "zhaloba", "ping_roles": [],
        "category_id": None,
        "questions": [forms.make_question("На кого жалоба?", "Ник игрока"),
                      forms.make_question("Что случилось?", "Подробности", True, True, 2000)],
    }
    await forms.save_types(types)
    types2 = await forms.load_types()
    assert "report" in types2 and len(types2["report"]["questions"]) == 2
    ok("CRUD типов тикетов через хранилище")

    # ── 6. Вопросы заявок ─────────────────────────────────────────────────
    qs = await forms.load_app_questions()
    assert set(qs.keys()) == set(forms.APP_TYPE_IDS)
    assert all(len(v) == 2 for v in qs.values())
    qs["персонал"].append(forms.make_question("Сколько часов в день сможете играть?"))
    await forms.save_app_questions(qs)
    assert len(forms.app_questions_sync("персонал")) == 3
    ok("Вопросы заявок: дефолты + сохранение")

    # ── 7. Модалки и вьюхи строятся из настроек ───────────────────────────
    from cogs.tickets import (
        TicketModal, TicketCloseView, TicketPanelView, TicketTypeSelectView,
        build_panel_embed, build_type_options, is_ticket_channel,
    )
    from cogs.applications import ApplicationModal, AdminApproveView, ApplicationView

    modal = TicketModal("report", types2["report"])
    assert len(modal.children) == 2, "форма не собрала 2 вопроса"
    assert modal.children[1].max_length == 2000

    std_modal = TicketModal("поддержка", types2["поддержка"])
    assert len(std_modal.children) == 1

    empty_cfg = dict(types2["report"], questions=[])
    fallback = TicketModal("report", empty_cfg)
    assert len(fallback.children) == 1, "нет fallback-вопроса при пустой форме"
    ok("TicketModal: 2 вопроса / 1 вопрос / fallback при пустой форме")

    app_modal = ApplicationModal("персонал")
    assert len(app_modal.children) == 3, "анкета не подхватила 3-й вопрос"
    ok("ApplicationModal строится из /заявки-настройка")

    options = build_type_options(types2)
    assert len(options) == 3 and options[0].value == "поддержка"
    select_view = TicketTypeSelectView(options)
    assert len(select_view.children) == 1
    ok("Панель тикетов: select из динамических типов")

    panel = TicketPanelView()
    btn = next(c for c in panel.children if c.custom_id == "open_ticket_panel_btn")
    assert btn.label == T.TICKET_BTN_LABEL

    await database.db.set("text_overrides.TICKET_BTN_LABEL", "🆘 Написать в поддержку")
    panel2 = TicketPanelView()
    btn2 = next(c for c in panel2.children if c.custom_id == "open_ticket_panel_btn")
    assert btn2.label == "🆘 Написать в поддержку", "label кнопки не подхватил override"
    await database.db.delete("text_overrides.TICKET_BTN_LABEL")
    ok("Кнопка панели берёт текст из /тексты без перезапуска")

    close_view = TicketCloseView()
    approve_view = AdminApproveView()
    app_view = ApplicationView()
    em = build_panel_embed()
    assert em.title == T.TICKET_PANEL_TITLE
    ok("CloseView/ApproveView/PanelView/Embed собираются")

    # ── 8. Определение каналов тикетов ────────────────────────────────────
    class FakeCh:
        def __init__(self, name):
            self.name = name

    assert is_ticket_channel(FakeCh("support-ivan"))
    assert is_ticket_channel(FakeCh("zhaloba-petya"))
    assert is_ticket_channel(FakeCh("поддержка-old"))       # legacy
    assert not is_ticket_channel(FakeCh("обычный-канал"))
    ok("is_ticket_channel: новые и legacy префиксы")

    # ── 9. Обновление панелей без гильдий не падает ──────────────────────
    from cogs.tickets import refresh_ticket_panels
    from cogs.applications import refresh_app_panels
    assert await refresh_ticket_panels(bot) == 0
    assert await refresh_app_panels(bot) == 0
    ok("refresh_*_panels: работают (пустой список гильдий)")

    # ── 10. Все ключи T.* из когов существуют в TextDefaults ──────────────
    import re
    missing = []
    for root, _, files in os.walk(BOT_DIR):
        if "__pycache__" in root:
            continue
        for f in files:
            if not f.endswith(".py"):
                continue
            src = open(os.path.join(root, f), encoding="utf-8").read()
            for key in re.findall(r"\bT\.([A-Z][A-Z0-9_]+)", src):
                if not hasattr(texts.TextDefaults, key):
                    missing.append((f, key))
    assert not missing, f"Не найдены ключи текстов: {missing}"
    ok("Все T.КЛЮЧ из кода есть в texts.py")

    await bot.close()
    print(f"\n🎉 ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ: {len(PASSED)}")


if __name__ == "__main__":
    asyncio.run(main())
