"""Модуль управления триггерами и ключевыми словами бота ВКонтакте."""

import os
import yaml
from typing import Dict, Any, List, Optional


DEFAULT_TRIGGERS_YAML = """# Конфигурация сценариев, триггеров и кнопок VK Bot Engine
message_triggers:
  - id: "welcome_start"
    name: "Приветствие"
    match_type: "exact"
    keywords:
      - "начать"
      - "старт"
      - "start"
      - "меню"
    response:
      text: |
        👋 Здравствуйте, {first_name}!
        Добро пожаловать в наше сообщество.
        Напишите интересующий вас вопрос или кодовое слово из карусели!

comment_triggers:
  - id: "wall_bonus_comment"
    name: "Кодовое слово под постом"
    match_type: "fuzzy"
    keywords:
      - "бонус"
      - "подарок"
      - "гайд"
    reply_comment:
      text: |
        @{user_screen_name} ({first_name}), мы подготовили ваш бонус! 🎁
        Заберите его в личных сообщениях прямо сейчас 👉 vk.me/{group_domain}?ref=bonus
    send_direct_message: true
    direct_message:
      text: |
        Привет, {first_name}! 🎁
        Вы оставили комментарий под постом. Вот ваш обещанный материал: https://example.com/bonus.pdf

fallback_response:
  text: |
    {first_name}, я бот-ассистент сообщества 🤖
    Я пока не знаю такое слово. Напишите «Старт» или кодовое слово из нашего последнего поста.
"""


def get_default_triggers_path() -> str:
    bot_path = "/opt/vk-bot-engine/config/triggers.yaml"
    if os.path.exists(bot_path):
        return bot_path
    return "data/triggers.yaml"


class TriggersManager:
    def __init__(self, filepath: Optional[str] = None):
        self.filepath = filepath or get_default_triggers_path()
        self.filepath = filepath
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        if not os.path.exists(self.filepath):
            with open(self.filepath, "w", encoding="utf-8") as f:
                f.write(DEFAULT_TRIGGERS_YAML)

    def load_data(self) -> Dict[str, Any]:
        """Загрузить все триггеры."""
        self._ensure_file_exists()
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                return data or {}
        except Exception as e:
            print(f"Error loading triggers YAML: {e}")
            return {}

    def save_data(self, data: Dict[str, Any]):
        """Сохранить изменения в YAML."""
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        with open(self.filepath, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

    def list_triggers(self) -> List[Dict[str, Any]]:
        """Получить удобный список триггеров для UI."""
        data = self.load_data()
        result = []

        comment_tr = data.get("comment_triggers", [])
        for ct in comment_tr:
            result.append({
                "id": ct.get("id"),
                "name": ct.get("name", "Триггер комментария"),
                "type": "comment",
                "match_type": ct.get("match_type", "fuzzy"),
                "keywords": ct.get("keywords", []),
                "reply_text": ct.get("reply_comment", {}).get("text", ""),
                "dm_text": ct.get("direct_message", {}).get("text", "") if ct.get("send_direct_message") else None
            })

        message_tr = data.get("message_triggers", [])
        for mt in message_tr:
            result.append({
                "id": mt.get("id"),
                "name": mt.get("name", "Триггер ЛС"),
                "type": "message",
                "match_type": mt.get("match_type", "fuzzy"),
                "keywords": mt.get("keywords", []),
                "reply_text": mt.get("response", {}).get("text", ""),
                "dm_text": None
            })

        return result

    def add_or_update_keyword(
        self,
        keyword: str,
        lead_magnet_url: Optional[str] = None,
        reply_comment_text: Optional[str] = None,
        dm_text: Optional[str] = None
    ) -> Dict[str, Any]:
        """Добавить или обновить триггер на ключевое слово из карусели."""
        kw_clean = keyword.strip().lower()
        if not kw_clean:
            raise ValueError("Ключевое слово не может быть пустым")

        data = self.load_data()
        comment_triggers = data.setdefault("comment_triggers", [])
        message_triggers = data.setdefault("message_triggers", [])

        trigger_id = f"auto_{kw_clean}"

        # 1. Текст ответа в комментарий
        if not reply_comment_text:
            reply_comment_text = (
                "@{user_screen_name} ({first_name}), мы отправили ваш бонус в личные сообщения! 🎁\n\n"
                "Если сообщения закрыты, откройте диалог по ссылке: vk.me/{group_domain}"
            )

        # 2. Текст выдачи в ЛС
        if not dm_text:
            dm_text = f"Здравствуйте, {{first_name}}! 🎁\n\nВы запросили материал по кодовому слову «{keyword.upper()}».\n"
            if lead_magnet_url:
                dm_text += f"\nВот ваша ссылка на материалы: {lead_magnet_url}\n"
            dm_text += "\nЕсли у вас есть вопросы — напишите прямо в этот диалог!"

        # Поиск существующего триггера в comment_triggers
        found_ct = None
        for ct in comment_triggers:
            if ct.get("id") == trigger_id or kw_clean in [k.lower() for k in ct.get("keywords", [])]:
                found_ct = ct
                break

        if found_ct:
            if kw_clean not in [k.lower() for k in found_ct.get("keywords", [])]:
                found_ct.setdefault("keywords", []).append(kw_clean)
            found_ct["reply_comment"] = {"text": reply_comment_text}
            found_ct["send_direct_message"] = True
            found_ct["direct_message"] = {"text": dm_text}
        else:
            comment_triggers.append({
                "id": trigger_id,
                "name": f"Автовыдача: {keyword.upper()}",
                "match_type": "fuzzy",
                "keywords": [kw_clean, f"хочу {kw_clean}"],
                "reply_comment": {"text": reply_comment_text},
                "send_direct_message": True,
                "direct_message": {"text": dm_text}
            })

        # Поиск существующего триггера в message_triggers (для ЛС)
        found_mt = None
        for mt in message_triggers:
            if mt.get("id") == trigger_id or kw_clean in [k.lower() for k in mt.get("keywords", [])]:
                found_mt = mt
                break

        if found_mt:
            if kw_clean not in [k.lower() for k in found_mt.get("keywords", [])]:
                found_mt.setdefault("keywords", []).append(kw_clean)
            found_mt["response"] = {"text": dm_text}
        else:
            message_triggers.append({
                "id": trigger_id,
                "name": f"Автовыдача в ЛС: {keyword.upper()}",
                "match_type": "fuzzy",
                "keywords": [kw_clean, f"хочу {kw_clean}"],
                "response": {"text": dm_text}
            })

        self.save_data(data)
        return {
            "success": True,
            "trigger_id": trigger_id,
            "keyword": kw_clean,
            "reply_comment_text": reply_comment_text,
            "dm_text": dm_text
        }
