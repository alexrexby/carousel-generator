"""Модуль смысловой упаковки контента в карусель через LLM (Polza.ai / Claude 3.5/4.5)."""

import json
import logging
import os
import re
from typing import Dict, Any, List, Optional
import httpx

logger = logging.getLogger("carousel.ai")

POLZA_API_KEY = os.environ.get("POLZA_API_KEY", "")
POLZA_BASE_URL = os.environ.get("POLZA_BASE_URL", "https://api.polza.ai/api/v1")
POLZA_MODEL = os.environ.get("POLZA_MODEL", "anthropic/claude-sonnet-4.5")


class AIPipelineError(Exception):
    pass


SYSTEM_PROMPT = """Ты — ведущий контент-стратег и редактор виральных каруселей для социальных сетей (1080x1350 px).
Твоя задача — взять исходный транскрипт или текст видео/поста и упаковать его в сильную, емкую карусель (от 4 до 7 слайдов) и виральный текст для поста.

ПРАВИЛА УПАКОВКИ:
1. «Упаковка, а не выдумка»: строго сохраняй факты, мысли и инсайты оригинала. Не выдумывай отсебятину.
2. Никакой воды: убери длинные разгоны, приветствия («всем привет, сегодня я расскажу...») и мусор. Сразу бей в суть.
3. Короткие, рубленые фразы. Читатель листает карусель с экрана смартфона за 15 секунд.
4. Заголовки слайдов оформляй массивом пар: [["Текст", "ЦВЕТ"]], где ЦВЕТ:
   - "INK" (основной темный цвет темы)
   - "PRIMARY" (яркий акцентный цвет темы)
   - "WHITE" (белый цвет для темных перебивок break)
5. Типы слайдов:
   - 'cover' (1-й слайд): мощный хук-заголовок (ВИСП: выгода/интрига/срочность/причастность) + краткий интригующий lead (1-2 строки).
   - 'break' (слайд-перебивка на цветном фоне): главный инсайт, парадокс или контрастная мысль.
   - 'list' (структурированный список): 3-4 конкретных шага, ошибки или рекомендации (items: array строк, numbered: true).
   - 'cta' (последний слайд): призыв к действию! ОБЯЗАТЕЛЬНО укажи кодовое слово воронки в поле 'word'.

ФОРМАТ ОТВЕТА:
Верни ТОЛЬКО валидный JSON (без markdown-оберток и комментариев) по следующей схеме:
{
  "slides": [
    {
      "type": "cover",
      "title": [["СЛОВА", "INK"], ["АКЦЕНТ", "PRIMARY"]],
      "lead": "Краткое введение в тему"
    },
    ...
    {
      "type": "cta",
      "tag": "Подарок",
      "title": [["Заберите", "INK"], ["название бонуса", "PRIMARY"]],
      "lead": "Напишите в комментариях кодовое слово, чтобы бот прислал материал в ЛС.",
      "word": "СЛОВО_ВОРОНКИ"
    }
  ],
  "post_text": "Виральный текст для стены ВКонтакте с хуком, краткими тезисами и призывом написать кодовое слово в комментариях."
}
"""


def clean_json_response(raw_text: str) -> dict:
    """Очистка и безопасный парсинг JSON из ответа LLM."""
    text = raw_text.strip()
    # Убираем markdown ```json ... ```
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Попытка найти JSON объект внутри фигурных скобок
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise AIPipelineError(f"LLM не вернул валидный JSON: {raw_text[:300]}")


def structure_content_into_carousel(
    text: str,
    title: str = "",
    funnel: Optional[Dict[str, Any]] = None,
    api_key: Optional[str] = None
) -> Dict[str, Any]:
    """Превращение сырого текста в карусель и пост через Polza AI."""
    key = api_key or POLZA_API_KEY
    if not key:
        raise AIPipelineError("POLZA_API_KEY не настроен")

    # Метаданные воронки
    funnel_data = funnel or {}
    lm = funnel_data.get("lead_magnet", {})
    keyword = lm.get("keyword", "БОНУС").upper().strip()
    lm_title = lm.get("title", "полезные материалы").strip()

    user_prompt = f"""ИСХОДНЫЙ КОНТЕНТ:
Заголовок: {title}
Текст:
{text[:6000]}

ТРЕБОВАНИЯ ВОРОНКИ:
- Кодовое слово для финального CTA-слайда (поле 'word'): «{keyword}»
- Название лид-магнита: «{lm_title}»

Собери виральную карусель (4-6 слайдов) и сильный текст для публикации ВКонтакте. Верни СТРОГО чистый JSON.
"""

    url = f"{POLZA_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": POLZA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.4,
        "max_tokens": 2500
    }

    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=60.0)
        if resp.status_code != 200:
            raise AIPipelineError(f"Polza AI вернула статус {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        raw_reply = data["choices"][0]["message"]["content"]
        result = clean_json_response(raw_reply)

        # Валидация
        if "slides" not in result or not isinstance(result["slides"], list):
            raise AIPipelineError("В ответе LLM отсутствует список 'slides'")

        # Гарантируем, что последнее кодовое слово совпадает с воронкой
        for s in reversed(result["slides"]):
            if s.get("type") == "cta":
                s["word"] = keyword
                break

        return result
    except Exception as e:
        if isinstance(e, AIPipelineError):
            raise
        raise AIPipelineError(f"Ошибка вызова LLM: {e}")
