"""
Простое JSON-хранилище для данных бота.
Используется для: кулдаунов, авто-ответов, временных банов,
настроек доната, /info, прав доступа и т.д.
"""

import json
import asyncio
import os
from typing import Any

DATA_FILE = "data.json"


class Database:
    def __init__(self, path: str = DATA_FILE):
        self.path = path
        self._lock = asyncio.Lock()
        self._data: dict = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        else:
            self._data = {}

    def _save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

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


db = Database()
