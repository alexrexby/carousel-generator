"""Модуль управления пресетами воронок контента (Funnels Manager)."""

import json
import logging
import os
import uuid
from typing import Dict, Any, List, Optional
from src.triggers_manager import TriggersManager

logger = logging.getLogger("carousel.funnels")

DEFAULT_FUNNELS = [
    {
        "id": "beauty_service",
        "name": "Бьюти-бизнес: Сервис и стандарты",
        "handle": "@amalia_pro_beauty_",
        "theme": "ocean",
        "vk": {
            "target": "user",
            "group_id": None,
            "target_name": "Личная страница"
        },
        "lead_magnet": {
            "keyword": "СЕРВИС",
            "title": "Регламент работы администратора",
            "url": "https://disk.yandex.ru/d/service_guide",
            "comment_reply": "@{user_screen_name} ({first_name}), регламент отправили вам в ЛС! 🎁\n\nЕсли сообщения закрыты, напишите нам: vk.me/{group_domain}",
            "dm_text": "Здравствуйте, {first_name}! 🎁\n\nВы запросили регламент по кодовому слову «СЕРВИС».\n\nСсылка на скачивание: https://disk.yandex.ru/d/service_guide"
        },
        "schedule": {
            "slots": ["10:00", "14:30", "19:00"],
            "timezone_offset": 3
        }
    }
]


class FunnelsManager:
    def __init__(self, filepath: str = "data/funnels.json", triggers_filepath: Optional[str] = None):
        self.filepath = filepath
        self.triggers_mgr = TriggersManager(filepath=triggers_filepath)
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        if not os.path.exists(self.filepath):
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_FUNNELS, f, ensure_ascii=False, indent=2)

    def list_funnels(self) -> List[Dict[str, Any]]:
        """Получить список всех воронок."""
        self._ensure_file_exists()
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"Failed to read funnels: {e}")
            return []

    def get_funnel(self, funnel_id: str) -> Optional[Dict[str, Any]]:
        """Найти воронку по ID."""
        funnels = self.list_funnels()
        for fn in funnels:
            if fn.get("id") == funnel_id:
                return fn
        return None

    def save_funnel(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Создать или обновить воронку."""
        funnels = self.list_funnels()
        fn_id = data.get("id")
        if not fn_id:
            fn_id = f"fn_{uuid.uuid4().hex[:8]}"
            data["id"] = fn_id

        # Дефолтные 3 слота публикации
        if "schedule" not in data or not data["schedule"].get("slots"):
            data["schedule"] = {
                "slots": ["10:00", "14:30", "19:00"],
                "timezone_offset": 3
            }

        # Обновляем или добавляем в список
        updated = False
        for i, existing in enumerate(funnels):
            if existing.get("id") == fn_id:
                funnels[i] = data
                updated = True
                break

        if not updated:
            funnels.append(data)

        # Сохраняем в JSON
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(funnels, f, ensure_ascii=False, indent=2)

        # Автоматически регистрируем ключевое слово воронки в боте
        lm = data.get("lead_magnet", {})
        kw = lm.get("keyword")
        if kw:
            try:
                self.triggers_mgr.add_or_update_keyword(
                    keyword=kw,
                    lead_magnet_url=lm.get("url"),
                    reply_comment_text=lm.get("comment_reply"),
                    dm_text=lm.get("dm_text")
                )
            except Exception as e:
                logger.warning(f"Failed to sync trigger for funnel {fn_id}: {e}")

        return data

    def delete_funnel(self, funnel_id: str) -> bool:
        """Удалить воронку."""
        funnels = self.list_funnels()
        initial_len = len(funnels)
        funnels = [fn for fn in funnels if fn.get("id") != funnel_id]
        if len(funnels) < initial_len:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(funnels, f, ensure_ascii=False, indent=2)
            return True
        return False
