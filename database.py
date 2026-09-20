"""
Простое JSON-хранилище для данных бота.
Используется для: кулдаунов, авто-ответов, временных банов,
настроек доната, /info, прав доступа и т.д.

Путь к файлу:
  • По умолчанию — data.json РЯДОМ С ФАЙЛАМИ БОТА (не зависит от того,
    из какой папки запущен процесс — это чинит ошибки вида
    PermissionError: [Errno 13] при запуске из другой директории).
  • Можно переопределить переменной окружения BOT_DATA_FILE
    (используется в тестах).
"""

import json
import asyncio
import os
import time
from typing import Any


def _default_data_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "data.json")


DATA_FILE = os.environ.get("BOT_DATA_FILE") or _default_data_path()


def _save_error_console_hint(path: str, err: Exception) -> str:
    """Подробная подсказка в консоль: что проверить при ошибке записи."""
    exists = os.path.exists(path)
    parent = os.path.dirname(path) or "."
    lines = [
        "",
        "  ═══════════════════════════════════════════════════════════",
        "  ❌ БОТ НЕ МОЖЕТ ЗАПИСАТЬ data.json — настройки не сохранятся!",
        "  ═══════════════════════════════════════════════════════════",
        f"  Файл:    {path}",
        f"  Папка запуска: {os.getcwd()}",
        f"  Файл существует: {'да' if exists else 'нет'}",
        f"  Ошибка:  {type(err).__name__}: {err}",
        "",
        "  Как исправить (Linux):",
        f"    1. Проверь владельца:  ls -l {path}",
        "    2. Бот должен быть запущен от пользователя-владельца файла.",
        f"    3. Выдай права:  chmod 644 {path}   (и chmod 755 на папку бота)",
        "    4. Если файл от root:  chown <user>:<user> data.json",
        "  Как исправить (Windows):",
        "    1. ПКМ по data.json → Свойства → снять галку «Только чтение».",
        "    2. Запускай бота из его папки, не из-под администратора в системных путях.",
        "  ═══════════════════════════════════════════════════════════",
        "",
    ]
    try:
        if exists:
            st = os.stat(path)
            lines.insert(8, f"  Права файла: {oct(st.st_mode & 0o777)}")
    except Exception:
        pass
    try:
        parent_mode = oct(os.stat(parent).st_mode & 0o777)
        lines.insert(9, f"  Права папки: {parent_mode}")
    except Exception:
        pass
    return "\n".join(lines)


class Database:
    def __init__(self, path: str = DATA_FILE):
        self.path = path
        self._lock = asyncio.Lock()
        self._data: dict = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            self._data = {}
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except PermissionError as e:
            print(f"[DB] ❌ Нет прав на ЧТЕНИЕ {self.path}: {e}")
            print(_save_error_console_hint(self.path, e))
            self._data = {}
            return
        except (ValueError, UnicodeDecodeError) as e:
            # Битый JSON — уносим в бэкап, чтобы бот не падал в цикле,
            # данные при этом не теряются молча.
            backup = f"{self.path}.corrupt-{int(time.time())}.bak"
            try:
                os.replace(self.path, backup)
                print(f"[DB] ⚠️ data.json повреждён ({e}). "
                      f"Оригинал сохранён как {backup}, начат чистый файл.")
            except Exception as be:
                print(f"[DB] ⚠️ data.json повреждён ({e}), бэкап не удался: {be}")
            self._data = {}
            return
        except OSError as e:
            print(f"[DB] ❌ Не удалось прочитать {self.path}: {e}")
            self._data = {}
            return
        if not isinstance(self._data, dict):
            print(f"[DB] ⚠️ {self.path} содержит не объект — начат чистый файл.")
            self._data = {}

    def _save(self):
        # Атомарная запись: сначала во временный файл, потом переименование.
        # Если бот упадёт посередине — data.json останется целым.
        payload = json.dumps(self._data, ensure_ascii=False, indent=2)
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, self.path)
            return
        except PermissionError as first_err:
            # Частый случай: файл read-only (залит через панель/FTP).
            # Пробуем снять флаг и повторить один раз.
            try:
                if os.path.exists(self.path):
                    os.chmod(self.path, 0o644)
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(payload)
                os.replace(tmp, self.path)
                print(f"[DB] ⚠️ Файл {self.path} был read-only — "
                      f"права исправлены автоматически.")
                return
            except Exception:
                pass
            print(_save_error_console_hint(self.path, first_err))
            raise RuntimeError(
                f"Нет доступа на запись в {os.path.basename(self.path)} "
                f"(Permission denied). Запусти бота от пользователя-владельца "
                f"файла или выдай права: chmod 644 {os.path.basename(self.path)}. "
                f"Подробности — в консоли."
            ) from first_err
        except OSError as e:
            print(_save_error_console_hint(self.path, e))
            raise RuntimeError(
                f"Не удалось сохранить {os.path.basename(self.path)}: {e}. "
                f"Подробности — в консоли."
            ) from e

    async def get(self, key: str, default: Any = None) -> Any:
        async with self._lock:
            keys = key.split(".")
            val = self._data
            for k in keys:
                if not isinstance(val, dict) or k not in val:
                    return default
                val = val[k]
            return val

    async def set(self, key: str, value: Any):
        async with self._lock:
            keys = key.split(".")
            d = self._data
            for k in keys[:-1]:
                d = d.setdefault(k, {})
            d[keys[-1]] = value
            self._save()

    async def delete(self, key: str):
        async with self._lock:
            keys = key.split(".")
            d = self._data
            for k in keys[:-1]:
                if not isinstance(d, dict) or k not in d:
                    return
                d = d[k]
            d.pop(keys[-1], None)
            self._save()

    async def get_all(self) -> dict:
        async with self._lock:
            return dict(self._data)

    def get_sync(self, key: str, default: Any = None) -> Any:
        """
        Синхронное чтение из уже загруженных данных.
        Нужно там, где нельзя использовать await (например, __getattr__ у текстов
        или построение View/Modal). Безопасно: все записи проходят через set().
        """
        keys = key.split(".")
        val = self._data
        for k in keys:
            if not isinstance(val, dict) or k not in val:
                return default
            val = val[k]
        return val


db = Database()
