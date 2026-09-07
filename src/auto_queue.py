"""Модуль автопилота контента и умной очереди отложки (3 карусели в день)."""

import datetime
import json
import logging
import os
import time
import uuid
from typing import Dict, Any, List, Optional, Tuple

from src.extractor import extract_content
from src.ai_pipeline import structure_content_into_carousel
from src.renderer import render_deck
from src.theme import Theme
from src.vk_poster import VKCarouselPoster, VKAPIError
from src.funnels_manager import FunnelsManager

logger = logging.getLogger("carousel.queue")

MSK_TZ = datetime.timezone(datetime.timedelta(hours=3))


class QueueManager:
    def __init__(self, queue_file: str = "data/queue.json", funnels_mgr: Optional[FunnelsManager] = None):
        self.queue_file = queue_file
        self.funnels_mgr = funnels_mgr or FunnelsManager()
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        os.makedirs(os.path.dirname(self.queue_file), exist_ok=True)
        if not os.path.exists(self.queue_file):
            with open(self.queue_file, "w", encoding="utf-8") as f:
                json.dump([], f)

    def list_queue(self) -> List[Dict[str, Any]]:
        """Получить список всех запланированных постов, отсортированных по времени."""
        self._ensure_file_exists()
        try:
            with open(self.queue_file, "r", encoding="utf-8") as f:
                items = json.load(f)
                if not isinstance(items, list):
                    return []
                # Сортируем: сначала ближайшие запланированные, затем остальные
                return sorted(items, key=lambda x: x.get("scheduled_time", 0))
        except Exception as e:
            logger.error(f"Failed to read queue: {e}")
            return []

    def save_queue(self, items: List[Dict[str, Any]]):
        """Сохранить элементы очереди."""
        os.makedirs(os.path.dirname(self.queue_file), exist_ok=True)
        with open(self.queue_file, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)

    def delete_item(self, item_id: str, cancel_vk: bool = True) -> bool:
        """Удалить пост из очереди и при необходимости отменить отложку в ВК."""
        items = self.list_queue()
        target_item = None
        for it in items:
            if it.get("id") == item_id:
                target_item = it
                break

        if not target_item:
            return False

        # Отмена в ВК через wall.delete
        if cancel_vk and target_item.get("vk_post_id") and target_item.get("vk_token"):
            try:
                poster = VKCarouselPoster(target_item["vk_token"])
                owner_id = target_item.get("owner_id")
                post_id = target_item.get("vk_post_id")
                poster._api_call("wall.delete", {"owner_id": owner_id, "post_id": post_id})
                logger.info(f"Cancelled postponed post {owner_id}_{post_id} in VK")
            except Exception as e:
                logger.warning(f"Could not delete post from VK wall: {e}")

        items = [it for it in items if it.get("id") != item_id]
        self.save_queue(items)
        return True

    def find_next_free_slot(
        self,
        vk_poster: Optional[VKCarouselPoster],
        target: str,
        group_id: Optional[int],
        slots: Optional[List[str]] = None
    ) -> Tuple[int, str]:
        """
        Умный поиск ближайшего свободного слота (по умолчанию 3 слота: 10:00, 14:30, 19:00 МСК).
        Учитывает как локальную очередь, так и реально отложенные записи в ВК.
        """
        slot_hours = slots or ["10:00", "14:30", "19:00"]
        now = datetime.datetime.now(MSK_TZ)

        # 1. Собираем уже занятые таймстемпы из локальной очереди
        booked_timestamps = set()
        for it in self.list_queue():
            ts = it.get("scheduled_time")
            if ts and ts > now.timestamp():
                booked_timestamps.add(int(ts))

        # 2. Если есть доступ к ВК, получаем список реально отложенных записей
        if vk_poster:
            try:
                params = {"filter": "postponed", "count": 100}
                if target == "group" and group_id:
                    params["owner_id"] = -abs(int(group_id))
                else:
                    me = vk_poster.get_current_user()
                    params["owner_id"] = me["id"]

                postponed = vk_poster._api_call("wall.get", params)
                if postponed and "items" in postponed:
                    for p in postponed["items"]:
                        p_date = p.get("date")
                        if p_date and p_date > now.timestamp():
                            booked_timestamps.add(int(p_date))
            except Exception as e:
                logger.warning(f"Failed to fetch postponed posts from VK: {e}")

        # 3. Перебираем дни и 3 слота в день
        # Кандидат свободен, если нет постов в радиусе 45 минут
        for day_offset in range(0, 45):
            candidate_date = (now + datetime.timedelta(days=day_offset)).date()
            for s in slot_hours:
                try:
                    h, m = map(int, s.strip().split(":"))
                except ValueError:
                    h, m = 12, 0

                candidate_dt = datetime.datetime(
                    candidate_date.year, candidate_date.month, candidate_date.day,
                    h, m, 0, tzinfo=MSK_TZ
                )
                cand_ts = int(candidate_dt.timestamp())

                # Слот должен быть минимум через 20 минут от текущего момента
                if cand_ts < now.timestamp() + 1200:
                    continue

                # Проверяем наложение с уже занятыми (радиус 45 минут = 2700 сек)
                has_collision = False
                for b_ts in booked_timestamps:
                    if abs(b_ts - cand_ts) < 2700:
                        has_collision = True
                        break

                if not has_collision:
                    date_str = candidate_dt.strftime("%d.%m.%Y в %H:%M (МСК)")
                    return cand_ts, date_str

        # Fallback: через 24 часа
        fallback_dt = now + datetime.timedelta(days=1)
        return int(fallback_dt.timestamp()), fallback_dt.strftime("%d.%m.%Y в %H:%M (МСК)")

    def process_autopilot_pipeline(
        self,
        url_or_text: str,
        funnel_id: str,
        base_dir: str,
        custom_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Полный сквозной цикл автопилота:
        1. Извлечение контента
        2. ИИ-структурирование в слайды + текст поста
        3. Рендеринг карусели 1080x1350
        4. Поиск ближайшего из 3 слотов в день
        5. Отложка во ВКонтакте
        6. Регистрация в дашборде очереди
        """
        funnel = self.funnels_mgr.get_funnel(funnel_id)
        if not funnel:
            raise ValueError(f"Воронка с ID '{funnel_id}' не найдена")

        token = custom_token or funnel.get("vk", {}).get("access_token")
        poster = VKCarouselPoster(token) if token else None

        # 1. Извлечение
        extracted = extract_content(url_or_text)
        title = extracted.get("title", "Без названия")
        raw_text = extracted.get("text", "")

        # 2. ИИ упаковка
        ai_res = structure_content_into_carousel(
            text=raw_text,
            title=title,
            funnel=funnel
        )
        slides = ai_res.get("slides", [])
        post_text = ai_res.get("post_text", "")

        # 3. Рендеринг карусели
        theme_id = funnel.get("theme", "default")
        theme = Theme.load(theme_id)
        render_id = uuid.uuid4().hex[:8]
        out_dir = os.path.join(base_dir, "output", "web_renders", render_id)
        assets_dir = os.path.join(base_dir, "assets")

        total = render_deck(slides, out_dir, theme, assets_dir=assets_dir, make_preview=True)

        image_files = sorted([
            os.path.join(out_dir, f)
            for f in os.listdir(out_dir)
            if f.endswith(".png") and f != "preview.png"
        ])

        # 4. Расчет слота публикации (3 слота в день)
        vk_target = funnel.get("vk", {}).get("target", "user")
        vk_group_id = funnel.get("vk", {}).get("group_id")
        slots = funnel.get("schedule", {}).get("slots", ["10:00", "14:30", "19:00"])

        slot_ts, slot_date_str = self.find_next_free_slot(
            vk_poster=poster,
            target=vk_target,
            group_id=vk_group_id,
            slots=slots
        )

        # 5. Отложенная публикация в ВК (если токен задан)
        post_res = {}
        status = "scheduled_local"
        if poster:
            try:
                post_res = poster.post_carousel(
                    image_paths=image_files,
                    message=post_text,
                    target=vk_target,
                    group_id=vk_group_id,
                    publish_date=slot_ts
                )
                status = "scheduled"
            except Exception as e:
                logger.error(f"Failed to post to VK: {e}")
                status = "error_vk"

        # 6. Добавление в очередь
        queue_item = {
            "id": f"q_{render_id}",
            "funnel_id": funnel["id"],
            "funnel_name": funnel["name"],
            "source_url": extracted.get("source_url", ""),
            "source_type": extracted.get("source_type", "raw_text"),
            "title": title,
            "render_id": render_id,
            "slides_count": total,
            "slides": [f"/preview/{render_id}/{i:02d}.png" for i in range(1, total + 1)],
            "preview_url": f"/preview/{render_id}/preview.png",
            "post_text": post_text,
            "status": status,
            "scheduled_time": slot_ts,
            "scheduled_date_str": slot_date_str,
            "vk_post_id": post_res.get("post_id"),
            "owner_id": post_res.get("owner_id"),
            "wall_url": post_res.get("wall_url"),
            "vk_token": token or "",
            "created_at": datetime.datetime.now(MSK_TZ).isoformat()
        }

        items = self.list_queue()
        items.append(queue_item)
        self.save_queue(items)

        return {
            "success": True,
            "item": queue_item
        }
