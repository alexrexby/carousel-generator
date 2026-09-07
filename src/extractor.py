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


def extract_from_youtube_apify(url: str, apify_token: Optional[str] = None) -> Optional[str]:
    """Извлечение субтитров YouTube через Apify Actor (обход блокировок IP датацентров YouTube)."""
    token = apify_token or DEFAULT_APIFY_TOKEN
    if not token:
        return None
    try:
        actor_url = f"https://api.apify.com/v2/acts/pintostudio~youtube-transcript-scraper/run-sync-get-dataset-items?token={token}"
        resp = httpx.post(actor_url, json={"videoUrl": url}, timeout=45.0)
        if resp.status_code in (200, 201):
            items = resp.json()
            if isinstance(items, list) and len(items) > 0:
                first = items[0]
                lines = []
                data_snippets = first.get("data", []) if isinstance(first, dict) else []
                for s in data_snippets:
                    txt = s.get("text", "") if isinstance(s, dict) else ""
                    if txt:
                        lines.append(txt)
                if lines:
                    return " ".join(lines)
    except Exception as e:
        logger.warning(f"Apify YouTube transcript fallback error: {e}")
    return None


def extract_from_youtube(url: str, apify_token: Optional[str] = None) -> Dict[str, Any]:
    """Извлечение субтитров и транскрипта из YouTube (Shorts и длинные видео)."""
    video_id = extract_youtube_video_id(url)
    if not video_id:
        raise ExtractionError(f"Не удалось распознать ID видео YouTube из ссылки: {url}")

    title = get_youtube_title(url)
    full_text = ""

    # 1. Сначала пробуем локальную библиотеку youtube-transcript-api
    if YouTubeTranscriptApi:
        try:
            ytt = YouTubeTranscriptApi()
            transcript_list = None
            if hasattr(ytt, "list"):
                transcript_list = ytt.list(video_id)
            elif hasattr(YouTubeTranscriptApi, "list_transcripts"):
                transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

            if transcript_list:
                transcript = None
                # Ищем русские субтитры напрямую
                try:
                    transcript = transcript_list.find_transcript(['ru'])
                except Exception:
                    pass

                # Если нет, пробуем перевести на русский доступные субтитры
                if not transcript:
                    for t in transcript_list:
                        if getattr(t, 'is_translatable', False):
                            try:
                                transcript = t.translate('ru')
                                break
                            except Exception:
                                pass

                # Если перевод не удался, берем английские или любые доступные
                if not transcript:
                    try:
                        transcript = transcript_list.find_transcript(['en'])
                    except Exception:
                        for t in transcript_list:
                            transcript = t
                            break

                if transcript:
                    data = transcript.fetch()
                    lines = []
                    for item in data:
                        snippet_text = getattr(item, 'text', None) or (item.get('text', '') if isinstance(item, dict) else str(item))
                        if snippet_text:
                            lines.append(snippet_text)
                    full_text = " ".join(lines)
        except Exception as e:
            logger.warning(f"Local youtube-transcript-api error for {video_id}: {e}")

    # 2. Если локально не удалось (IpBlocked или отсутствие метода) — используем Apify
    if not full_text or len(full_text.strip()) < 30:
        logger.info(f"Falling back to Apify YouTube transcript scraper for {url}...")
        apify_text = extract_from_youtube_apify(url, apify_token=apify_token)
        if apify_text:
            full_text = apify_text

    # Очистка текста от служебных меток времени и скобок
    if full_text:
        full_text = re.sub(r"\[.*?\]", "", full_text)
        full_text = re.sub(r"\s+", " ", full_text).strip()

    # 3. Если субтитров совсем нет, но есть заголовок — используем тему видео
    if len(full_text) < 30:
        if title:
            full_text = f"Тема и содержание видео: {title}"
        else:
            raise ExtractionError("У этого видео нет доступных субтитров и не удалось получить описание. Попробуйте вставить тезисы текстом.")

    return {
        "source_type": "youtube",
        "source_url": url,
        "title": title or f"YouTube Video ({video_id})",
        "text": full_text,
        "author": "YouTube Creator"
    }


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
            return extract_from_youtube(raw, apify_token=apify_token)
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
