"""Модуль извлечения контента по ссылкам: YouTube, Instagram Reels/Карусели, Threads."""

import json
import logging
import os
import re
from typing import Dict, Any, Optional
from urllib.parse import urlparse
import httpx

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:
    YouTubeTranscriptApi = None

logger = logging.getLogger("carousel.extractor")

DEFAULT_APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")


class ExtractionError(Exception):
    """Ошибка при извлечении контента из внешнего источника."""
    pass


def extract_youtube_video_id(url: str) -> Optional[str]:
    """Извлечение ID видео из ссылки YouTube (обычное видео, Shorts, youtu.be)."""
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11}).*",
        r"youtu\.be\/([0-9A-Za-z_-]{11})",
        r"youtube\.com\/shorts\/([0-9A-Za-z_-]{11})",
        r"youtube\.com\/embed\/([0-9A-Za-z_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def get_youtube_title(url: str) -> str:
    """Получить заголовок видео через публичный oEmbed."""
    try:
        oembed_url = f"https://www.youtube.com/oembed?url={url}&format=json"
        resp = httpx.get(oembed_url, timeout=10.0)
        if resp.status_code == 200:
            return resp.json().get("title", "")
    except Exception:
        pass
    return ""


def extract_from_youtube(url: str) -> Dict[str, Any]:
    """Извлечение субтитров и транскрипта из YouTube (Shorts и длинные видео)."""
    video_id = extract_youtube_video_id(url)
    if not video_id:
        raise ExtractionError(f"Не удалось распознать ID видео YouTube из ссылки: {url}")

    if not YouTubeTranscriptApi:
        raise ExtractionError("Библиотека youtube-transcript-api не установлена")

    title = get_youtube_title(url)

    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        transcript = None
        
        # 1. Сначала ищем русские субтитры (ручные или сгенерированные)
        try:
            transcript = transcript_list.find_transcript(['ru'])
        except Exception:
            pass

        # 2. Если русских нет, ищем английские
        if not transcript:
            try:
                transcript = transcript_list.find_transcript(['en'])
            except Exception:
                pass

        # 3. Берем любые доступные субтитры
        if not transcript:
            for t in transcript_list:
                transcript = t
                break

        if not transcript:
            raise ExtractionError("У этого видео нет доступных субтитров.")

        data = transcript.fetch()
        full_text = " ".join([item.get("text", "") for item in data]).strip()
        
        full_text = re.sub(r"\[.*?\]", "", full_text)
        full_text = re.sub(r"\s+", " ", full_text).strip()

        if len(full_text) < 40:
            raise ExtractionError("Субтитры видео слишком короткие для упаковки в карусель.")

        return {
            "source_type": "youtube",
            "source_url": url,
            "title": title or f"YouTube Video ({video_id})",
            "text": full_text,
            "author": "YouTube Creator"
        }
    except Exception as e:
        if isinstance(e, ExtractionError):
            raise
        raise ExtractionError(f"Ошибка загрузки субтитров YouTube: {e}")


def extract_from_instagram(url: str, apify_token: Optional[str] = None) -> Dict[str, Any]:
    """Извлечение текста Reels или карусели Instagram через Apify."""
    token = apify_token or DEFAULT_APIFY_TOKEN
    if not token:
        raise ExtractionError("Не указан APIFY_TOKEN для парсинга Instagram")

    actor = "apify~instagram-scraper"
    api_url = f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items?token={token}"

    try:
        resp = httpx.post(
            api_url,
            json={"directUrls": [url]},
            timeout=45.0
        )
        if resp.status_code not in (200, 201):
            raise ExtractionError(f"Apify Instagram вернул статус {resp.status_code}: {resp.text[:200]}")

        items = resp.json()
        if not items:
            raise ExtractionError("Apify не вернул данных по этому посту Instagram.")

        item = items[0]
        caption = item.get("caption", "").strip()
        author = item.get("ownerUsername", "Instagram Creator")

        child_texts = []
        if "childPosts" in item and isinstance(item["childPosts"], list):
            for cp in item["childPosts"]:
                if cp.get("caption"):
                    child_texts.append(cp["caption"])

        combined_text = caption
        if child_texts:
            combined_text += "\n\n" + "\n\n".join(child_texts)

        if not combined_text or len(combined_text) < 30:
            raise ExtractionError("В данном посте Instagram не найден текст описания или субтитров.")

        first_line = combined_text.split("\n")[0][:100].strip()

        return {
            "source_type": "instagram",
            "source_url": url,
            "title": first_line or f"Instagram Post (@{author})",
            "text": combined_text,
            "author": f"@{author}"
        }
    except Exception as e:
        if isinstance(e, ExtractionError):
            raise
        raise ExtractionError(f"Ошибка парсинга Instagram: {e}")


def extract_from_threads(url: str, apify_token: Optional[str] = None) -> Dict[str, Any]:
    """Извлечение текста поста и комментариев автора из Threads через Apify."""
    token = apify_token or DEFAULT_APIFY_TOKEN
    if not token:
        raise ExtractionError("Не указан APIFY_TOKEN для парсинга Threads")

    actor = "logical_scrapers~threads-post-scraper"
    api_url = f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items?token={token}"

    try:
        resp = httpx.post(
            api_url,
            json={"startUrls": [{"url": url}]},
            timeout=45.0
        )
        if resp.status_code not in (200, 201):
            raise ExtractionError(f"Apify Threads вернул статус {resp.status_code}")

        items = resp.json()
        if not items:
            raise ExtractionError("Apify не нашел тред по указанной ссылке.")

        item = items[0]
        thread = item.get("thread", {})
        author = thread.get("username", "Threads Author")
        head_text = (thread.get("text") or "").strip()

        replies = item.get("replies", [])
        author_replies = []
        for r in replies:
            if r.get("username") == author and r.get("text"):
                author_replies.append(r.get("text").strip())

        full_thread = head_text
        if author_replies:
            full_thread += "\n\n" + "\n\n".join(author_replies)

        if not full_thread or len(full_thread) < 30:
            raise ExtractionError("Текст в треде Threads пуст или слишком короткий.")

        first_line = full_thread.split("\n")[0][:100].strip()

        return {
            "source_type": "threads",
            "source_url": url,
            "title": first_line or f"Threads (@{author})",
            "text": full_thread,
            "author": f"@{author}"
        }
    except Exception as e:
        if isinstance(e, ExtractionError):
            raise
        raise ExtractionError(f"Ошибка парсинга Threads: {e}")


def extract_content(url_or_text: str, apify_token: Optional[str] = None) -> Dict[str, Any]:
    """Единая точка входа для извлечения контента."""
    raw = url_or_text.strip()
    if not raw:
        raise ExtractionError("Входной текст или URL не может быть пустым.")

    parsed = urlparse(raw)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        netloc = parsed.netloc.lower()
        if "youtube.com" in netloc or "youtu.be" in netloc:
            return extract_from_youtube(raw)
        elif "instagram.com" in netloc:
            return extract_from_instagram(raw, apify_token=apify_token)
        elif "threads.net" in netloc or "threads.com" in netloc:
            return extract_from_threads(raw, apify_token=apify_token)
        else:
            raise ExtractionError(f"Неподдерживаемый источник контента: {netloc}. Поддерживаются YouTube, Instagram, Threads.")

    if len(raw) >= 40:
        lines = [l.strip() for l in raw.split("\n") if l.strip()]
        title = lines[0][:100] if lines else "Пользовательский текст"
        return {
            "source_type": "raw_text",
            "source_url": "",
            "title": title,
            "text": raw,
            "author": "Автор"
        }

    raise ExtractionError("Укажите ссылку на YouTube, Instagram, Threads или вставьте текст статьи/транскрипта.")
