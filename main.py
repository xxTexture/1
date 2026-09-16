"""
🚀 ТОЧКА ВХОДА БОТА
"""

import discord
from discord.ext import commands
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import BotConfig

COGS = [
    "cogs.tickets",
    "cogs.applications",
    "cogs.moderation",
    "cogs.auto_reply",
    "cogs.donate",
    "cogs.info_cmd",
    "cogs.ai_chat",
    "cogs.ai_automod",
    "cogs.access_control",
    "cogs.admin_utils",
    "cogs.help_cmd",
    "cogs.welcome",
    "cogs.cmd_logger",
    "cogs.server_logs",
]


class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.all())

    async def setup_hook(self):
        print("⏳ Загрузка модулей...")
        loaded = 0
        for cog in COGS:
            try:
                await self.load_extension(cog)
                print(f"  ✅ {cog}")
                loaded += 1
            except Exception as e:
                print(f"  ❌ {cog}: {e}")
        print(f"  → {loaded}/{len(COGS)} модулей загружено")
        print("⏳ Синхронизация команд...")
        try:
            synced = await self.tree.sync()
            print(f"  ✅ {len(synced)} команд синхронизировано")
        except Exception as e:
            print(f"  ❌ Ошибка синхронизации: {e}")

    async def on_ready(self):
        print(f"\n{'='*50}")
        print(f"  🤖 {self.user}  (ID: {self.user.id})")
        print(f"  📡 Серверов: {len(self.guilds)}")
        print(f"{'='*50}\n")
        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="за порядком | /команды")
        )

    async def on_app_command_error(self, interaction: discord.Interaction, error):
        if isinstance(error, discord.app_commands.CheckFailure):
            return
        print(f"[ERR] {getattr(interaction.command, 'name', '?')}: {error}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Ошибка: `{error}`", ephemeral=True)
        except Exception:
            pass


if __name__ == "__main__":
    bot = MyBot()
    try:
        bot.run(BotConfig.TOKEN, log_handler=None)
    except discord.LoginFailure:
        print("❌ Неверный токен! Проверь BotConfig.TOKEN в config.py")
    except Exception as e:
        print(f"❌ Ошибка запуска: {e}")
