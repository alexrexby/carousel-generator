# -*- coding: utf-8 -*-
import os
import io
import json
import uuid
import zipfile
import shutil
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, Response
from pydantic import BaseModel

from src.theme import Theme
from src.renderer import render_deck
from src.vk_poster import VKCarouselPoster, VKAPIError, build_post_text_from_slides
from src.triggers_manager import TriggersManager

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP_DIR = os.path.join(BASE_DIR, "output", "web_renders")
TRIGGERS_PATH = os.path.join(BASE_DIR, "data", "triggers.yaml")
os.makedirs(TMP_DIR, exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)

triggers_mgr = TriggersManager(filepath=TRIGGERS_PATH)

app = FastAPI(title="Carousel Studio & VK Publisher", description="Web Generator for Social Media Carousels with VK Autoposting")


class RenderRequest(BaseModel):
    slides: List[Dict[str, Any]]
    theme: Optional[str] = "default"


class VKCheckTokenRequest(BaseModel):
    access_token: str


class VKPreviewTextRequest(BaseModel):
    slides: List[Dict[str, Any]]


class VKPublishRequest(BaseModel):
    access_token: str
    render_id: str
    target: str = "user"  # "user" или "group"
    group_id: Optional[int] = None
    message: str = ""
    publish_date: Optional[int] = None
    activate_keyword: bool = False
    keyword: Optional[str] = None
    lead_magnet_url: Optional[str] = None
    reply_comment_text: Optional[str] = None
    dm_text: Optional[str] = None


class AddTriggerRequest(BaseModel):
    keyword: str
    lead_magnet_url: Optional[str] = None
    reply_comment_text: Optional[str] = None
    dm_text: Optional[str] = None


@app.get("/api/themes")
def list_themes():
    cfg_dir = os.path.join(BASE_DIR, "config")
    res = []
    if os.path.exists(cfg_dir):
        for f in sorted(os.listdir(cfg_dir)):
            if f.endswith(".json"):
                name = f[:-5]
                try:
                    with open(os.path.join(cfg_dir, f), "r", encoding="utf-8") as fp:
                        d = json.load(fp)
                        res.append({"id": name, "name": name.title(), "handle": d.get("handle", "")})
                except Exception:
                    pass
    return res


@app.get("/api/templates")
def list_templates():
    decks_dir = os.path.join(BASE_DIR, "decks")
    res = []
    if os.path.exists(decks_dir):
        for f in sorted(os.listdir(decks_dir)):
            if f.endswith(".json"):
                p = os.path.join(decks_dir, f)
                try:
                    with open(p, "r", encoding="utf-8") as fp:
                        slides = json.load(fp)
                        name = f[:-5]
                        title = name.replace("_", " ").title()
                        if slides and "title" in slides[0]:
                            t = slides[0]["title"]
                            if isinstance(t, str): title = t
                            elif isinstance(t, list): title = " ".join(item[0] if isinstance(item, (list, tuple)) else str(item) for item in t)
                        res.append({"id": name, "title": title, "slides": slides})
                except Exception:
                    pass
    return res


@app.post("/api/render")
def api_render(req: RenderRequest):
    if not req.slides:
        raise HTTPException(status_code=400, detail="Slides array cannot be empty")
        
    theme = Theme.load(req.theme)
    render_id = str(uuid.uuid4())[:8]
    out_dir = os.path.join(TMP_DIR, render_id)
    assets_dir = os.path.join(BASE_DIR, "assets")
    
    total = render_deck(req.slides, out_dir, theme, assets_dir=assets_dir, make_preview=True)
    
    slides_urls = [f"/preview/{render_id}/{i:02d}.png" for i in range(1, total + 1)]
    return {
        "success": True,
        "render_id": render_id,
        "total": total,
        "slides": slides_urls,
        "preview_url": f"/preview/{render_id}/preview.png",
        "zip_url": f"/download/{render_id}.zip"
    }


@app.get("/preview/{render_id}/{filename}")
def serve_preview(render_id: str, filename: str):
    file_path = os.path.join(TMP_DIR, render_id, filename)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, media_type="image/png")


@app.get("/download/{render_id}.zip")
def download_zip(render_id: str):
    render_dir = os.path.join(TMP_DIR, render_id)
    if not os.path.isdir(render_dir):
        raise HTTPException(status_code=404, detail="Render session not found")
        
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(render_dir):
            for f in sorted(files):
                if f.endswith(".png") and f != "preview.png":
                    zf.write(os.path.join(root, f), arcname=f)
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=carousel_{render_id}.zip"}
    )


# =====================================================================
# VK API & KEYWORD TRIGGERS ENDPOINTS
# =====================================================================

@app.post("/api/vk/check-token")
def check_vk_token(req: VKCheckTokenRequest):
    token = req.access_token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Токен не может быть пустым")
    try:
        poster = VKCarouselPoster(access_token=token)
        profile = poster.get_account_profile()
        return {"success": True, "profile": profile}
    except VKAPIError as e:
        raise HTTPException(status_code=400, detail=f"Ошибка VK API: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка проверки токена: {e}")


@app.post("/api/vk/preview-text")
def preview_post_text(req: VKPreviewTextRequest):
    text = build_post_text_from_slides(req.slides)
    cta_word = None
    for s in req.slides:
        if s.get("type") == "cta" and s.get("word"):
            cta_word = s.get("word")
            break
    return {"text": text, "cta_word": cta_word}


@app.post("/api/vk/publish")
def publish_to_vk(req: VKPublishRequest):
    render_dir = os.path.join(TMP_DIR, req.render_id)
    if not os.path.isdir(render_dir):
        raise HTTPException(status_code=404, detail="Сгенерированная карусель не найдена. Сначала выполните рендеринг.")

    image_files = sorted([
        os.path.join(render_dir, f)
        for f in os.listdir(render_dir)
        if f.endswith(".png") and f != "preview.png"
    ])

    if not (2 <= len(image_files) <= 10):
        raise HTTPException(status_code=400, detail=f"Для карусели требуется от 2 до 10 слайдов (найдено: {len(image_files)})")

    try:
        poster = VKCarouselPoster(access_token=req.access_token)
        res = poster.post_carousel(
            image_paths=image_files,
            message=req.message,
            target=req.target,
            group_id=req.group_id,
            publish_date=req.publish_date
        )

        trigger_res = None
        if req.activate_keyword and req.keyword:
            trigger_res = triggers_mgr.add_or_update_keyword(
                keyword=req.keyword,
                lead_magnet_url=req.lead_magnet_url,
                reply_comment_text=req.reply_comment_text,
                dm_text=req.dm_text
            )

        return {
            "success": True,
            "post_id": res.get("post_id"),
            "wall_url": res.get("wall_url"),
            "attachments_count": res.get("attachments_count"),
            "trigger": trigger_res
        }
    except VKAPIError as e:
        raise HTTPException(status_code=400, detail=f"Ошибка публикации VK API: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка публикации: {e}")


@app.get("/api/vk/triggers")
def get_triggers():
    return {"triggers": triggers_mgr.list_triggers()}


@app.post("/api/vk/triggers")
def add_trigger(req: AddTriggerRequest):
    if not req.keyword.strip():
        raise HTTPException(status_code=400, detail="Ключевое слово не указано")
    try:
        res = triggers_mgr.add_or_update_keyword(
            keyword=req.keyword,
            lead_magnet_url=req.lead_magnet_url,
            reply_comment_text=req.reply_comment_text,
            dm_text=req.dm_text
        )
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка сохранения триггера: {e}")


# =====================================================================
# FRONTEND HTML / CSS / JS
# =====================================================================

HTML_CONTENT = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Carousel Studio & VK Publisher · 1080x1350</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #070a13;
      --card-bg: rgba(16, 24, 40, 0.75);
      --card-border: rgba(255, 255, 255, 0.08);
      --accent: #6366f1;
      --accent-hover: #4f46e5;
      --accent-glow: rgba(99, 102, 241, 0.25);
      --vk-color: #2787f5;
      --vk-hover: #1c6fd1;
      --vk-glow: rgba(39, 135, 245, 0.3);
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --success: #10b981;
      --danger: #ef4444;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg);
      background-image: 
        radial-gradient(at 0% 0%, rgba(99, 102, 241, 0.12) 0px, transparent 50%),
        radial-gradient(at 100% 100%, rgba(39, 135, 245, 0.1) 0px, transparent 50%);
      color: var(--text);
      font-family: 'Plus Jakarta Sans', sans-serif;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }
    header {
      padding: 16px 32px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid var(--card-border);
      backdrop-filter: blur(12px);
      background: rgba(7, 10, 19, 0.8);
      position: sticky;
      top: 0;
      z-index: 50;
    }
    .logo {
      display: flex;
      align-items: center;
      gap: 12px;
      font-size: 18px;
      font-weight: 800;
      letter-spacing: -0.02em;
    }
    .logo-badge {
      font-size: 11px;
      padding: 3px 8px;
      background: rgba(99, 102, 241, 0.15);
      color: #818cf8;
      border: 1px solid rgba(99, 102, 241, 0.3);
      border-radius: 9999px;
      font-weight: 600;
    }
    .header-actions {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .container {
      flex: 1;
      display: grid;
      grid-template-columns: 460px 1fr;
      gap: 24px;
      padding: 24px 32px;
      max-width: 1720px;
      margin: 0 auto;
      width: 100%;
    }
    .panel {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 16px;
      padding: 24px;
      display: flex;
      flex-direction: column;
      gap: 20px;
      backdrop-filter: blur(16px);
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
    }
    .panel-title {
      font-size: 16px;
      font-weight: 700;
      display: flex;
      align-items: center;
      justify-content: space-between;
      color: #fff;
    }
    .field {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    label {
      font-size: 13px;
      font-weight: 600;
      color: var(--text-muted);
    }
    select, textarea, input[type="text"] {
      background: rgba(10, 15, 26, 0.8);
      border: 1px solid var(--card-border);
      border-radius: 10px;
      color: #fff;
      font-family: inherit;
      font-size: 14px;
      padding: 10px 14px;
      outline: none;
      transition: all 0.2s;
    }
    select:focus, textarea:focus, input[type="text"]:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px var(--accent-glow);
    }
    textarea {
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      resize: vertical;
      min-height: 280px;
      line-height: 1.5;
    }
    .btn {
      background: var(--accent);
      color: #fff;
      border: none;
      border-radius: 10px;
      padding: 12px 20px;
      font-weight: 600;
      font-size: 14px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      transition: all 0.2s;
      box-shadow: 0 4px 14px var(--accent-glow);
      text-decoration: none;
    }
    .btn:hover {
      background: var(--accent-hover);
      transform: translateY(-1px);
    }
    .btn:disabled {
      opacity: 0.5;
      cursor: not-allowed;
      transform: none;
    }
    .btn-secondary {
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid var(--card-border);
      box-shadow: none;
      color: #fff;
    }
    .btn-secondary:hover {
      background: rgba(255, 255, 255, 0.14);
      box-shadow: none;
    }
    .btn-vk {
      background: var(--vk-color);
      box-shadow: 0 4px 14px var(--vk-glow);
      color: #fff;
    }
    .btn-vk:hover {
      background: var(--vk-hover);
      transform: translateY(-1px);
    }
    .preview-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 12px;
    }
    .gallery {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      gap: 20px;
      margin-top: 16px;
    }
    .slide-card {
      background: rgba(10, 15, 26, 0.6);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
      position: relative;
    }
    .slide-card:hover {
      transform: translateY(-4px);
      border-color: rgba(99, 102, 241, 0.4);
      box-shadow: 0 12px 28px rgba(0,0,0,0.5);
    }
    .slide-thumb {
      width: 100%;
      aspect-ratio: 4/5;
      object-fit: cover;
      display: block;
      cursor: pointer;
    }
    .slide-meta {
      padding: 10px 14px;
      font-size: 12px;
      font-weight: 600;
      color: var(--text-muted);
      display: flex;
      justify-content: space-between;
      border-top: 1px solid var(--card-border);
    }
    .empty-state {
      padding: 80px 20px;
      text-align: center;
      color: var(--text-muted);
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 16px;
      border: 2px dashed rgba(255,255,255,0.06);
      border-radius: 16px;
    }
    .empty-icon {
      font-size: 44px;
      opacity: 0.6;
    }
    .spinner {
      border: 3px solid rgba(255,255,255,0.15);
      border-top: 3px solid #fff;
      border-radius: 50%;
      width: 18px;
      height: 18px;
      animation: spin 0.8s linear infinite;
      display: none;
    }
    @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    .loading .spinner { display: inline-block; }
    .loading .btn-text { opacity: 0.7; }

    /* MODAL STYLES */
    .modal-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.75);
      backdrop-filter: blur(8px);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 100;
      padding: 20px;
    }
    .modal {
      background: #0f172a;
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 18px;
      width: 100%;
      max-width: 620px;
      max-height: 90vh;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      box-shadow: 0 20px 50px rgba(0,0,0,0.7);
      animation: modalIn 0.25s cubic-bezier(0.16, 1, 0.3, 1);
    }
    @keyframes modalIn {
      from { opacity: 0; transform: scale(0.96) translateY(10px); }
      to { opacity: 1; transform: scale(1) translateY(0); }
    }
    .modal-header {
      padding: 20px 24px;
      border-bottom: 1px solid var(--card-border);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .modal-title {
      font-size: 18px;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .modal-close {
      background: none;
      border: none;
      color: var(--text-muted);
      font-size: 22px;
      cursor: pointer;
      line-height: 1;
      padding: 4px;
    }
    .modal-close:hover { color: #fff; }
    .modal-body {
      padding: 24px;
      display: flex;
      flex-direction: column;
      gap: 18px;
    }
    .modal-footer {
      padding: 16px 24px;
      border-top: 1px solid var(--card-border);
      display: flex;
      justify-content: flex-end;
      gap: 12px;
      background: rgba(10, 15, 26, 0.5);
      border-bottom-left-radius: 18px;
      border-bottom-right-radius: 18px;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      padding: 2px 8px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 600;
    }
    .badge-blue { background: rgba(39, 135, 245, 0.15); color: #60a5fa; }
    .badge-green { background: rgba(16, 185, 129, 0.15); color: #34d399; }
    .box-info {
      background: rgba(39, 135, 245, 0.08);
      border: 1px solid rgba(39, 135, 245, 0.2);
      border-radius: 10px;
      padding: 12px 16px;
      font-size: 13px;
      color: #93c5fd;
      line-height: 1.5;
    }
    .trigger-item {
      background: rgba(10, 15, 26, 0.6);
      border: 1px solid var(--card-border);
      border-radius: 10px;
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
  </style>
</head>
<body>
  <header>
    <div class="logo">
      <span>🎠 Carousel Studio</span>
      <span class="logo-badge">1080x1350</span>
    </div>
    <div class="header-actions">
      <button id="openTriggersBtn" class="btn btn-secondary" style="padding: 8px 14px; font-size: 13px;">
        ⚡️ Ключевые слова бота
      </button>
      <div style="font-size: 13px; color: var(--text-muted); border-left: 1px solid var(--card-border); padding-left: 14px;">
        karusel.launchi.ru
      </div>
    </div>
  </header>

  <div class="container">
    <!-- Левая панель: Параметры -->
    <div class="panel">
      <div class="panel-title">
        <span>Параметры карусели</span>
      </div>

      <div class="field">
        <label>Тема оформления</label>
        <select id="themeSelect">
          <option value="default">Default (Indigo & White)</option>
          <option value="dark">Dark Mode (Emerald & Graphite)</option>
          <option value="minimal">Minimal (High Contrast B&W)</option>
          <option value="ocean">Ocean (Azure Blue)</option>
        </select>
      </div>

      <div class="field">
        <label>Готовый шаблон</label>
        <select id="templateSelect">
          <option value="">-- Выберите шаблон для вставки --</option>
        </select>
      </div>

      <div class="field" style="flex: 1;">
        <label>JSON структура слайдов</label>
        <textarea id="jsonInput" spellcheck="false"></textarea>
      </div>

      <button id="renderBtn" class="btn">
        <div class="spinner"></div>
        <span class="btn-text">✨ Собрать карусель</span>
      </button>
    </div>

    <!-- Правая панель: Результаты -->
    <div class="panel">
      <div class="preview-header">
        <div class="panel-title">
          <span>Сгенерированные карточки</span>
          <span id="slideCount" style="font-size: 13px; color: var(--text-muted); font-weight: 500;"></span>
        </div>
        <div style="display: flex; gap: 10px;">
          <a id="downloadBtn" href="#" class="btn btn-secondary" style="display: none; text-decoration: none; padding: 8px 16px; font-size: 13px;">
            📥 Скачать ZIP
          </a>
          <button id="openVkModalBtn" class="btn btn-vk" style="display: none; padding: 8px 16px; font-size: 13px;">
            📢 Опубликовать в VK
          </button>
        </div>
      </div>

      <div id="galleryContainer" class="gallery">
        <div class="empty-state" style="grid-column: 1 / -1;">
          <div class="empty-icon">🎨</div>
          <div style="font-size: 16px; font-weight: 600; color: #fff;">Здесь появятся ваши слайды</div>
          <div style="font-size: 14px; max-width: 320px;">Выберите шаблон слева и нажмите «Собрать карусель», чтобы сгенерировать карточки 1080x1350 px.</div>
        </div>
      </div>
    </div>
  </div>

  <!-- МОДАЛЬНОЕ ОКНО ПУБЛИКАЦИИ В VK -->
  <div id="vkModal" class="modal-backdrop">
    <div class="modal">
      <div class="modal-header">
        <div class="modal-title">
          <span>📢 Публикация карусели во ВКонтакте</span>
        </div>
        <button class="modal-close" onclick="closeVkModal()">&times;</button>
      </div>
      <div class="modal-body">
        <div class="box-info">
          💡 Для публикации нужен Standalone User Token. Если у вас его нет, получите его в один клик на <a href="https://vkhost.github.io" target="_blank" style="color: #fff; font-weight: 600; text-decoration: underline;">vkhost.github.io</a> (выберите Kate Mobile или VK Admin).
        </div>

        <div class="field">
          <label>VK Access Token</label>
          <div style="display: flex; gap: 8px;">
            <input type="text" id="vkTokenInput" placeholder="vk1.a.your_user_token..." style="flex: 1;">
            <button id="checkTokenBtn" class="btn btn-secondary" style="padding: 8px 14px; font-size: 13px;">
              Проверить
            </button>
          </div>
          <div id="tokenStatus" style="font-size: 12px; margin-top: 4px;"></div>
        </div>

        <div class="field">
          <label>Куда публиковать</label>
          <select id="vkTargetSelect">
            <option value="user">👤 На мою личную страницу</option>
          </select>
        </div>

        <div class="field">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <label>Текст поста</label>
            <button id="generatePostTextBtn" class="btn btn-secondary" style="padding: 4px 10px; font-size: 11px;">
              🪄 Собрать из слайдов
            </button>
          </div>
          <textarea id="vkMessageInput" style="min-height: 120px;" placeholder="Текст, который будет сопровождать карточки..."></textarea>
        </div>

        <!-- БЛОК АКТИВАЦИИ КЛЮЧЕВОГО СЛОВА -->
        <div style="background: rgba(255,255,255,0.03); border: 1px solid var(--card-border); border-radius: 12px; padding: 16px; display: flex; flex-direction: column; gap: 12px;">
          <label style="display: flex; align-items: center; gap: 10px; cursor: pointer; color: #fff; font-size: 14px;">
            <input type="checkbox" id="activateKeywordCheck" checked style="width: 16px; height: 16px; accent-color: var(--vk-color);">
            <span>🤖 Активировать кодовое слово в боте</span>
          </label>
          
          <div id="keywordSettingsBox" style="display: flex; flex-direction: column; gap: 10px;">
            <div class="field">
              <label>Кодовое слово (триггер в комментариях и ЛС)</label>
              <input type="text" id="keywordInput" placeholder="Например: СЕРВИС">
            </div>
            <div class="field">
              <label>Ссылка на лид-магнит / материалы (для отправки в ЛС)</label>
              <input type="text" id="leadMagnetUrlInput" placeholder="https://example.com/materials.pdf">
            </div>
          </div>
        </div>

        <div id="publishStatus" style="display: none; padding: 12px; border-radius: 8px; font-size: 13px;"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-secondary" onclick="closeVkModal()">Отмена</button>
        <button id="doPublishBtn" class="btn btn-vk">
          <div class="spinner"></div>
          <span class="btn-text">🚀 Опубликовать пост</span>
        </button>
      </div>
    </div>
  </div>

  <!-- МОДАЛЬНОЕ ОКНО КЛЮЧЕВЫХ СЛОВ БОТА -->
  <div id="triggersModal" class="modal-backdrop">
    <div class="modal" style="max-width: 720px;">
      <div class="modal-header">
        <div class="modal-title">
          <span>⚡️ Ключевые слова и автоворонка VK Bot</span>
        </div>
        <button class="modal-close" onclick="closeTriggersModal()">&times;</button>
      </div>
      <div class="modal-body">
        <div class="box-info">
          Когда подписчик пишет ключевое слово в комментариях под каруселью или в ЛС группы — VK Bot Engine мгновенно реагирует, отвечает под постом и присылает лид-магнит в диалог.
        </div>

        <div class="panel-title" style="font-size: 14px;">
          <span>Активные триггеры бота</span>
        </div>

        <div id="triggersListContainer" style="display: flex; flex-direction: column; gap: 12px; max-height: 380px; overflow-y: auto;">
          <div style="color: var(--text-muted); font-size: 13px;">Загрузка триггеров...</div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-secondary" onclick="closeTriggersModal()">Закрыть</button>
      </div>
    </div>
  </div>

  <script>
    const themeSelect = document.getElementById('themeSelect');
    const templateSelect = document.getElementById('templateSelect');
    const jsonInput = document.getElementById('jsonInput');
    const renderBtn = document.getElementById('renderBtn');
    const galleryContainer = document.getElementById('galleryContainer');
    const downloadBtn = document.getElementById('downloadBtn');
    const openVkModalBtn = document.getElementById('openVkModalBtn');
    const slideCount = document.getElementById('slideCount');

    // VK elements
    const vkModal = document.getElementById('vkModal');
    const vkTokenInput = document.getElementById('vkTokenInput');
    const checkTokenBtn = document.getElementById('checkTokenBtn');
    const tokenStatus = document.getElementById('tokenStatus');
    const vkTargetSelect = document.getElementById('vkTargetSelect');
    const vkMessageInput = document.getElementById('vkMessageInput');
    const generatePostTextBtn = document.getElementById('generatePostTextBtn');
    const activateKeywordCheck = document.getElementById('activateKeywordCheck');
    const keywordSettingsBox = document.getElementById('keywordSettingsBox');
    const keywordInput = document.getElementById('keywordInput');
    const leadMagnetUrlInput = document.getElementById('leadMagnetUrlInput');
    const doPublishBtn = document.getElementById('doPublishBtn');
    const publishStatus = document.getElementById('publishStatus');

    // Triggers elements
    const triggersModal = document.getElementById('triggersModal');
    const openTriggersBtn = document.getElementById('openTriggersBtn');
    const triggersListContainer = document.getElementById('triggersListContainer');

    let currentRenderId = null;
    let templatesCache = {};

    // Load saved token from localStorage
    const savedToken = localStorage.getItem('vk_user_token');
    if (savedToken) {
      vkTokenInput.value = savedToken;
    }

    async function loadInitialData() {
      try {
        const [themesRes, templatesRes] = await Promise.all([
          fetch('/api/themes'),
          fetch('/api/templates')
        ]);
        
        const themes = await themesRes.json();
        themeSelect.innerHTML = themes.map(t => `<option value="${t.id}">${t.name} (${t.handle})</option>`).join('');
        
        const templates = await templatesRes.json();
        templateSelect.innerHTML = '<option value="">-- Выберите шаблон --</option>' + 
          templates.map(tpl => {
            templatesCache[tpl.id] = tpl.slides;
            return `<option value="${tpl.id}">${tpl.title}</option>`;
          }).join('');

        if (templates.length > 0) {
          templateSelect.value = templates[0].id;
          jsonInput.value = JSON.stringify(templates[0].slides, null, 2);
        }
      } catch (e) {
        console.error('Failed to load initial data:', e);
      }
    }

    templateSelect.addEventListener('change', () => {
      const id = templateSelect.value;
      if (id && templatesCache[id]) {
        jsonInput.value = JSON.stringify(templatesCache[id], null, 2);
      }
    });

    renderBtn.addEventListener('click', async () => {
      let slides;
      try {
        slides = JSON.parse(jsonInput.value);
        if (!Array.isArray(slides)) throw new Error('Root must be an array of slides');
      } catch (err) {
        alert('Ошибка в JSON формате: ' + err.message);
        return;
      }

      renderBtn.classList.add('loading');
      renderBtn.disabled = true;

      try {
        const res = await fetch('/api/render', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            slides: slides,
            theme: themeSelect.value
          })
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Failed to render');

        currentRenderId = data.render_id;

        galleryContainer.innerHTML = data.slides.map((url, idx) => `
          <div class="slide-card">
            <a href="${url}" target="_blank">
              <img class="slide-thumb" src="${url}" alt="Slide ${idx+1}">
            </a>
            <div class="slide-meta">
              <span>Слайд ${idx+1}</span>
              <a href="${url}" download style="color: var(--accent); text-decoration: none;">1080x1350</a>
            </div>
          </div>
        `).join('');

        downloadBtn.href = data.zip_url;
        downloadBtn.style.display = 'inline-flex';
        openVkModalBtn.style.display = 'inline-flex';
        slideCount.textContent = `${data.total} карточек собрано`;
      } catch (e) {
        alert('Ошибка при генерации: ' + e.message);
      } finally {
        renderBtn.classList.remove('loading');
        renderBtn.disabled = false;
      }
    });

    // VK Modal Logic
    openVkModalBtn.addEventListener('click', async () => {
      if (!currentRenderId) return;
      vkModal.style.display = 'flex';
      publishStatus.style.display = 'none';

      // Auto-extract post text and keyword from current slides
      try {
        const slides = JSON.parse(jsonInput.value);
        const res = await fetch('/api/vk/preview-text', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ slides })
        });
        const data = await res.json();
        if (!vkMessageInput.value) {
          vkMessageInput.value = data.text;
        }
        if (data.cta_word) {
          keywordInput.value = data.cta_word;
        }
      } catch (e) {}

      // Auto verify token if exists
      if (vkTokenInput.value.trim() && vkTargetSelect.options.length <= 1) {
        checkToken();
      }
    });

    function closeVkModal() {
      vkModal.style.display = 'none';
    }

    generatePostTextBtn.addEventListener('click', async () => {
      try {
        const slides = JSON.parse(jsonInput.value);
        const res = await fetch('/api/vk/preview-text', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ slides })
        });
        const data = await res.json();
        vkMessageInput.value = data.text;
      } catch (e) {
        alert('Ошибка при сборке текста: ' + e.message);
      }
    });

    activateKeywordCheck.addEventListener('change', () => {
      keywordSettingsBox.style.display = activateKeywordCheck.checked ? 'flex' : 'none';
    });

    async function checkToken() {
      const token = vkTokenInput.value.trim();
      if (!token) {
        tokenStatus.innerHTML = '<span style="color: var(--danger)">Введите токен</span>';
        return;
      }

      checkTokenBtn.disabled = true;
      tokenStatus.innerHTML = '<span style="color: var(--text-muted)">Проверка...</span>';

      try {
        const res = await fetch('/api/vk/check-token', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ access_token: token })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Неверный токен');

        localStorage.setItem('vk_user_token', token);
        const u = data.profile.user;
        tokenStatus.innerHTML = `<span style="color: var(--success)">✓ Авторизован: <b>${u.first_name} ${u.last_name}</b> (ID: ${u.id})</span>`;

        // Fill targets
        vkTargetSelect.innerHTML = `<option value="user">👤 Личная страница: ${u.first_name} ${u.last_name}</option>`;
        if (data.profile.groups && data.profile.groups.length > 0) {
          data.profile.groups.forEach(g => {
            vkTargetSelect.innerHTML += `<option value="group_${g.id}">👥 Группа: ${g.name} (ID: ${g.id})</option>`;
          });
        }
      } catch (e) {
        tokenStatus.innerHTML = `<span style="color: var(--danger)">✗ ${e.message}</span>`;
      } finally {
        checkTokenBtn.disabled = false;
      }
    }

    checkTokenBtn.addEventListener('click', checkToken);

    doPublishBtn.addEventListener('click', async () => {
      const token = vkTokenInput.value.trim();
      if (!token) {
        alert('Пожалуйста, укажите VK Access Token');
        return;
      }

      const targetVal = vkTargetSelect.value;
      let target = 'user';
      let groupId = null;

      if (targetVal.startsWith('group_')) {
        target = 'group';
        groupId = parseInt(targetVal.replace('group_', ''), 10);
      }

      doPublishBtn.classList.add('loading');
      doPublishBtn.disabled = true;
      publishStatus.style.display = 'block';
      publishStatus.style.background = 'rgba(255,255,255,0.05)';
      publishStatus.style.color = 'var(--text-muted)';
      publishStatus.innerHTML = '⏳ Загрузка слайдов карусели в альбом ВКонтакте...';

      try {
        const res = await fetch('/api/vk/publish', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            access_token: token,
            render_id: currentRenderId,
            target: target,
            group_id: groupId,
            message: vkMessageInput.value,
            activate_keyword: activateKeywordCheck.checked,
            keyword: keywordInput.value.trim(),
            lead_magnet_url: leadMagnetUrlInput.value.trim()
          })
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Ошибка публикации');

        publishStatus.style.background = 'rgba(16, 185, 129, 0.15)';
        publishStatus.style.color = '#34d399';
        let html = `🎉 <b>Карусель успешно опубликована!</b><br>
                    🔗 <a href="${data.wall_url}" target="_blank" style="color: #fff; font-weight: 700; text-decoration: underline;">Открыть пост во ВКонтакте</a>`;
        if (data.trigger) {
          html += `<br><span style="font-size: 12px; color: #a7f3d0;">✓ Кодовое слово «${data.trigger.keyword.toUpperCase()}» активировано в боте</span>`;
        }
        publishStatus.innerHTML = html;
      } catch (e) {
        publishStatus.style.background = 'rgba(239, 68, 68, 0.15)';
        publishStatus.style.color = '#f87171';
        publishStatus.innerHTML = `❌ Ошибка: ${e.message}`;
      } finally {
        doPublishBtn.classList.remove('loading');
        doPublishBtn.disabled = false;
      }
    });

    // Triggers Modal Logic
    openTriggersBtn.addEventListener('click', async () => {
      triggersModal.style.display = 'flex';
      triggersListContainer.innerHTML = '<div style="color: var(--text-muted); font-size: 13px;">Загрузка...</div>';
      try {
        const res = await fetch('/api/vk/triggers');
        const data = await res.json();
        if (!data.triggers || data.triggers.length === 0) {
          triggersListContainer.innerHTML = '<div style="color: var(--text-muted); font-size: 13px;">Нет активных триггеров</div>';
          return;
        }

        triggersListContainer.innerHTML = data.triggers.map(t => `
          <div class="trigger-item">
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <span style="font-weight: 700; font-size: 14px; color: #fff;">${t.name}</span>
              <span class="badge ${t.type === 'comment' ? 'badge-blue' : 'badge-green'}">${t.type === 'comment' ? 'Комментарии под постом' : 'Сообщения в ЛС'}</span>
            </div>
            <div style="font-size: 12px; color: var(--text-muted);">
              Ключевые слова: <b style="color: #cbd5e1;">${t.keywords.join(', ')}</b> (${t.match_type})
            </div>
            ${t.reply_text ? `<div style="font-size: 12px; background: rgba(0,0,0,0.3); padding: 8px; border-radius: 6px; white-space: pre-wrap;">💬 <b>Ответ:</b> ${t.reply_text}</div>` : ''}
            ${t.dm_text ? `<div style="font-size: 12px; background: rgba(0,0,0,0.3); padding: 8px; border-radius: 6px; white-space: pre-wrap;">📩 <b>В ЛС:</b> ${t.dm_text}</div>` : ''}
          </div>
        `).join('');
      } catch (e) {
        triggersListContainer.innerHTML = `<div style="color: var(--danger); font-size: 13px;">Ошибка: ${e.message}</div>`;
      }
    });

    function closeTriggersModal() {
      triggersModal.style.display = 'none';
    }

    loadInitialData();
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(content=HTML_CONTENT)
