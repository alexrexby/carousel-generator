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

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP_DIR = os.path.join(BASE_DIR, "output", "web_renders")
os.makedirs(TMP_DIR, exist_ok=True)

app = FastAPI(title="Carousel Studio", description="Web Generator for Social Media Carousels")

class RenderRequest(BaseModel):
    slides: List[Dict[str, Any]]
    theme: Optional[str] = "default"

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
            for f in files:
                if f.endswith(".png") and f != "preview.png":
                    zf.write(os.path.join(root, f), arcname=f)
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=carousel_{render_id}.zip"}
    )

HTML_CONTENT = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Carousel Studio · Генератор каруселей 1080x1350</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #090d16;
      --card-bg: rgba(22, 30, 46, 0.75);
      --card-border: rgba(255, 255, 255, 0.08);
      --accent: #6366f1;
      --accent-hover: #4f46e5;
      --accent-glow: rgba(99, 102, 241, 0.35);
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --success: #10b981;
      --radius: 16px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Plus Jakarta Sans', sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      background-image: 
        radial-gradient(circle at 15% 20%, rgba(99, 102, 241, 0.12) 0%, transparent 40%),
        radial-gradient(circle at 85% 70%, rgba(14, 165, 233, 0.1) 0%, transparent 45%);
      background-attachment: fixed;
    }
    header {
      padding: 20px 40px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid var(--card-border);
      backdrop-filter: blur(12px);
    }
    .logo {
      display: flex;
      align-items: center;
      gap: 12px;
      font-weight: 800;
      font-size: 20px;
      letter-spacing: -0.5px;
    }
    .logo-badge {
      background: linear-gradient(135deg, #6366f1, #0ea5e9);
      color: #fff;
      padding: 4px 10px;
      border-radius: 8px;
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
    }
    .container {
      max-width: 1600px;
      width: 100%;
      margin: 0 auto;
      padding: 32px 40px;
      display: grid;
      grid-template-columns: 480px 1fr;
      gap: 32px;
      flex: 1;
    }
    @media (max-width: 1100px) {
      .container { grid-template-columns: 1fr; }
    }
    .panel {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: var(--radius);
      padding: 24px;
      backdrop-filter: blur(16px);
      display: flex;
      flex-direction: column;
      gap: 20px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.3);
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
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    select, textarea, input {
      background: rgba(10, 15, 26, 0.85);
      border: 1px solid var(--card-border);
      border-radius: 10px;
      color: #fff;
      padding: 12px 16px;
      font-family: inherit;
      font-size: 14px;
      outline: none;
      transition: all 0.2s;
    }
    select:focus, textarea:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px var(--accent-glow);
    }
    textarea {
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
      line-height: 1.5;
      resize: vertical;
      min-height: 400px;
    }
    .btn {
      background: linear-gradient(135deg, var(--accent), #4f46e5);
      color: #fff;
      font-weight: 700;
      font-size: 15px;
      padding: 14px 24px;
      border-radius: 12px;
      border: none;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      box-shadow: 0 4px 16px var(--accent-glow);
      transition: all 0.2s;
    }
    .btn:hover {
      transform: translateY(-2px);
      box-shadow: 0 6px 20px rgba(99, 102, 241, 0.5);
    }
    .btn:active { transform: translateY(0); }
    .btn-secondary {
      background: rgba(255, 255, 255, 0.06);
      border: 1px solid var(--card-border);
      box-shadow: none;
    }
    .btn-secondary:hover {
      background: rgba(255, 255, 255, 0.12);
      box-shadow: none;
    }
    .preview-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
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
      border: 3px solid rgba(255,255,255,0.1);
      border-top: 3px solid var(--accent);
      border-radius: 50%;
      width: 20px;
      height: 20px;
      animation: spin 0.8s linear infinite;
      display: none;
    }
    @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    .loading .spinner { display: inline-block; }
    .loading .btn-text { opacity: 0.7; }
  </style>
</head>
<body>
  <header>
    <div class="logo">
      <span>🎠 Carousel Studio</span>
      <span class="logo-badge">1080x1350</span>
    </div>
    <div style="font-size: 13px; color: var(--text-muted);">
      Авто-верстка · Типографика · Поддержка тем
    </div>
  </header>

  <div class="container">
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

    <div class="panel">
      <div class="preview-header">
        <div class="panel-title">
          <span>Сгенерированные карточки</span>
          <span id="slideCount" style="font-size: 13px; color: var(--text-muted); font-weight: 500;"></span>
        </div>
        <div style="display: flex; gap: 12px;">
          <a id="downloadBtn" href="#" class="btn btn-secondary" style="display: none; text-decoration: none; padding: 8px 16px; font-size: 13px;">
            📥 Скачать ZIP
          </a>
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

  <script>
    const themeSelect = document.getElementById('themeSelect');
    const templateSelect = document.getElementById('templateSelect');
    const jsonInput = document.getElementById('jsonInput');
    const renderBtn = document.getElementById('renderBtn');
    const galleryContainer = document.getElementById('galleryContainer');
    const downloadBtn = document.getElementById('downloadBtn');
    const slideCount = document.getElementById('slideCount');

    let templatesCache = {};

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
        slideCount.textContent = `${data.total} карточек собрано`;
      } catch (e) {
        alert('Ошибка при генерации: ' + e.message);
      } finally {
        renderBtn.classList.remove('loading');
        renderBtn.disabled = false;
      }
    });

    loadInitialData();
  </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(content=HTML_CONTENT)
