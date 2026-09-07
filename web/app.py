# -*- coding: utf-8 -*-
import os
import io
import json
import uuid
import zipfile
import shutil
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel

from src.theme import Theme
from src.renderer import render_deck
from src.vk_poster import VKCarouselPoster, VKAPIError, build_post_text_from_slides
from src.triggers_manager import TriggersManager
from src.extractor import extract_content, ExtractionError
from src.ai_pipeline import structure_content_into_carousel, AIPipelineError
from src.funnels_manager import FunnelsManager
from src.auto_queue import QueueManager

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP_DIR = os.path.join(BASE_DIR, "output", "web_renders")
BOT_TRIGGERS_PATH = "/opt/vk-bot-engine/config/triggers.yaml"
TRIGGERS_PATH = BOT_TRIGGERS_PATH if os.path.exists(BOT_TRIGGERS_PATH) else os.path.join(BASE_DIR, "data", "triggers.yaml")
FUNNELS_PATH = os.path.join(BASE_DIR, "data", "funnels.json")
QUEUE_PATH = os.path.join(BASE_DIR, "data", "queue.json")

os.makedirs(TMP_DIR, exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)

# Секретный токен авторизации
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "karusel_a3a266badc2f91c609cfaac6").strip()

triggers_mgr = TriggersManager(filepath=TRIGGERS_PATH)
funnels_mgr = FunnelsManager(filepath=FUNNELS_PATH, triggers_filepath=TRIGGERS_PATH)
queue_mgr = QueueManager(queue_file=QUEUE_PATH, funnels_mgr=funnels_mgr)

app = FastAPI(title="Carousel Studio & Autopilot", description="AI Content Pipeline: Extract -> Carousel -> Auto-Schedule VK -> Bot Triggers")


def check_auth(request: Request) -> bool:
    """Проверка наличия и валидности токена доступа."""
    if not AUTH_TOKEN:
        return True

    # 1. Query parameter: ?token=...
    token_param = request.query_params.get("token")
    if token_param and token_param.strip() == AUTH_TOKEN:
        return True

    # 2. Cookie: karusel_token=...
    cookie_token = request.cookies.get("karusel_token")
    if cookie_token and cookie_token.strip() == AUTH_TOKEN:
        return True

    # 3. Header: Authorization: Bearer ...
    auth_header = request.headers.get("Authorization")
    if auth_header:
        if auth_header.startswith("Bearer "):
            bearer = auth_header[7:].strip()
            if bearer == AUTH_TOKEN:
                return True
        elif auth_header.strip() == AUTH_TOKEN:
            return True

    # 4. Header: X-Access-Token
    x_token = request.headers.get("X-Access-Token")
    if x_token and x_token.strip() == AUTH_TOKEN:
        return True

    return False


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path

    if path in ["/api/auth/login", "/api/auth/logout", "/openapi.json", "/docs", "/redoc"]:
        return await call_next(request)

    if path == "/":
        token_param = request.query_params.get("token")
        if token_param and token_param.strip() == AUTH_TOKEN:
            response = HTMLResponse(content=HTML_CONTENT)
            response.set_cookie(
                key="karusel_token",
                value=AUTH_TOKEN,
                max_age=2592000,
                path="/",
                httponly=False,
                samesite="lax"
            )
            return response

        if not check_auth(request):
            return HTMLResponse(content=LOGIN_HTML, status_code=200)
        
        return await call_next(request)

    if not check_auth(request):
        return JSONResponse(
            status_code=401,
            content={"detail": "Доступ запрещен. Укажите валидный токен доступа."}
        )

    return await call_next(request)


# =====================================================================
# AUTH ENDPOINTS
# =====================================================================

class LoginRequest(BaseModel):
    token: str


@app.post("/api/auth/login")
def api_login(req: LoginRequest, response: Response):
    entered = req.token.strip()
    if entered != AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Неверный токен доступа")

    response.set_cookie(
        key="karusel_token",
        value=AUTH_TOKEN,
        max_age=2592000,
        path="/",
        httponly=False,
        samesite="lax"
    )
    return {"success": True, "message": "Авторизация успешна"}


@app.get("/api/auth/logout")
@app.post("/api/auth/logout")
def api_logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("karusel_token", path="/")
    return response


# =====================================================================
# CORE STUDIO ENDPOINTS
# =====================================================================

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
    target: str = "user"
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


class AutopilotProcessRequest(BaseModel):
    url_or_text: str
    funnel_id: str
    custom_token: Optional[str] = None


class ExtractPreviewRequest(BaseModel):
    url_or_text: str


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
# AUTOPILOT, FUNNELS & SCHEDULE DASHBOARD ENDPOINTS
# =====================================================================

@app.get("/api/funnels")
def api_list_funnels():
    return {"funnels": funnels_mgr.list_funnels()}


@app.post("/api/funnels")
def api_save_funnel(data: Dict[str, Any]):
    try:
        saved = funnels_mgr.save_funnel(data)
        return {"success": True, "funnel": saved}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка сохранения воронки: {e}")


@app.delete("/api/funnels/{funnel_id}")
def api_delete_funnel(funnel_id: str):
    ok = funnels_mgr.delete_funnel(funnel_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Воронка не найдена")
    return {"success": True}


@app.get("/api/queue")
def api_list_queue():
    items = queue_mgr.list_queue()
    return {"queue": items, "total": len(items)}


@app.delete("/api/queue/{item_id}")
def api_delete_queue_item(item_id: str):
    ok = queue_mgr.delete_item(item_id, cancel_vk=True)
    if not ok:
        raise HTTPException(status_code=404, detail="Элемент очереди не найден")
    return {"success": True}


@app.post("/api/extractor/preview")
def api_extract_preview(req: ExtractPreviewRequest):
    try:
        extracted = extract_content(req.url_or_text)
        return {"success": True, "extracted": extracted}
    except ExtractionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка извлечения: {e}")


@app.post("/api/autopilot/process")
def api_process_autopilot(req: AutopilotProcessRequest):
    try:
        res = queue_mgr.process_autopilot_pipeline(
            url_or_text=req.url_or_text,
            funnel_id=req.funnel_id,
            base_dir=BASE_DIR,
            custom_token=req.custom_token
        )
        return res
    except (ExtractionError, AIPipelineError, VKAPIError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка автопилота: {e}")


# =====================================================================
# LOGIN PAGE HTML
# =====================================================================

LOGIN_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Вход по токену · Carousel Studio</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #070a13;
      --card-bg: rgba(16, 24, 40, 0.85);
      --card-border: rgba(255, 255, 255, 0.08);
      --accent: #6366f1;
      --accent-hover: #4f46e5;
      --accent-glow: rgba(99, 102, 241, 0.3);
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --danger: #ef4444;
      --danger-bg: rgba(239, 68, 68, 0.12);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg);
      background-image: 
        radial-gradient(at 20% 20%, rgba(99, 102, 241, 0.15) 0px, transparent 50%),
        radial-gradient(at 80% 80%, rgba(39, 135, 245, 0.12) 0px, transparent 50%);
      color: var(--text);
      font-family: 'Plus Jakarta Sans', sans-serif;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }
    .auth-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 20px;
      padding: 40px 36px;
      width: 100%;
      max-width: 440px;
      box-shadow: 0 25px 60px rgba(0, 0, 0, 0.6);
      backdrop-filter: blur(20px);
      display: flex;
      flex-direction: column;
      gap: 22px;
      animation: fadeIn 0.3s ease-out;
    }
    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(12px); }
      to { opacity: 1; transform: translateY(0); }
    }
    .logo {
      display: flex;
      align-items: center;
      gap: 12px;
      font-size: 20px;
      font-weight: 800;
    }
    .badge {
      font-size: 11px;
      padding: 3px 9px;
      background: rgba(99, 102, 241, 0.15);
      color: #818cf8;
      border: 1px solid rgba(99, 102, 241, 0.3);
      border-radius: 9999px;
      font-weight: 600;
    }
    .title {
      font-size: 22px;
      font-weight: 800;
      letter-spacing: -0.02em;
      color: #fff;
    }
    .subtitle {
      font-size: 14px;
      color: var(--text-muted);
      line-height: 1.5;
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
    .input-wrap {
      position: relative;
      display: flex;
      align-items: center;
    }
    input[type="password"], input[type="text"] {
      width: 100%;
      background: rgba(10, 15, 26, 0.8);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      color: #fff;
      font-family: 'JetBrains Mono', monospace;
      font-size: 14px;
      padding: 14px 44px 14px 16px;
      outline: none;
      transition: all 0.2s;
    }
    input:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px var(--accent-glow);
    }
    .toggle-eye {
      position: absolute;
      right: 14px;
      background: none;
      border: none;
      color: var(--text-muted);
      cursor: pointer;
      font-size: 16px;
      padding: 4px;
    }
    .toggle-eye:hover { color: #fff; }
    .btn {
      background: var(--accent);
      color: #fff;
      border: none;
      border-radius: 12px;
      padding: 14px 20px;
      font-weight: 700;
      font-size: 15px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      transition: all 0.2s;
      box-shadow: 0 4px 16px var(--accent-glow);
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
    .error-box {
      background: var(--danger-bg);
      border: 1px solid rgba(239, 68, 68, 0.3);
      color: #fca5a5;
      padding: 12px 14px;
      border-radius: 10px;
      font-size: 13px;
      display: none;
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
    .hint {
      font-size: 12px;
      color: var(--text-muted);
      text-align: center;
      line-height: 1.5;
      border-top: 1px solid var(--card-border);
      padding-top: 18px;
    }
  </style>
</head>
<body>
  <div class="auth-card">
    <div style="display: flex; align-items: center; justify-content: space-between;">
      <div class="logo">
        <span>🎠 Carousel</span>
      </div>
      <span class="badge">🔒 Приватный доступ</span>
    </div>

    <div>
      <div class="title">Вход по токену</div>
      <div class="subtitle" style="margin-top: 6px;">
        Для доступа к Carousel Studio и автопостингу укажите ваш секретный токен доступа.
      </div>
    </div>

    <div id="errorBox" class="error-box"></div>

    <form id="authForm" style="display: flex; flex-direction: column; gap: 18px;">
      <div class="field">
        <label>Токен доступа (Access Token)</label>
        <div class="input-wrap">
          <input type="password" id="tokenInput" placeholder="karusel_..." autocomplete="current-password" autofocus required>
          <button type="button" id="toggleEye" class="toggle-eye" title="Показать токен">👁️</button>
        </div>
      </div>

      <button type="submit" id="submitBtn" class="btn">
        <div class="spinner"></div>
        <span class="btn-text">Войти в студию →</span>
      </button>
    </form>

    <div class="hint">
      💡 Вы также можете входить напрямую по ссылке с параметром: <br>
      <code style="color: #cbd5e1; font-family: 'JetBrains Mono', monospace; font-size: 11px;">https://karusel.launchi.ru/?token=ТОКЕН</code>
    </div>
  </div>

  <script>
    const authForm = document.getElementById('authForm');
    const tokenInput = document.getElementById('tokenInput');
    const submitBtn = document.getElementById('submitBtn');
    const errorBox = document.getElementById('errorBox');
    const toggleEye = document.getElementById('toggleEye');

    toggleEye.addEventListener('click', () => {
      if (tokenInput.type === 'password') {
        tokenInput.type = 'text';
        toggleEye.textContent = '🙈';
      } else {
        tokenInput.type = 'password';
        toggleEye.textContent = '👁️';
      }
    });

    authForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const token = tokenInput.value.trim();
      if (!token) return;

      submitBtn.classList.add('loading');
      submitBtn.disabled = true;
      errorBox.style.display = 'none';

      try {
        const res = await fetch('/api/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token })
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Неверный токен');

        window.location.href = '/';
      } catch (err) {
        errorBox.textContent = '❌ ' + err.message;
        errorBox.style.display = 'block';
      } finally {
        submitBtn.classList.remove('loading');
        submitBtn.disabled = false;
      }
    });
  </script>
</body>
</html>
"""


# =====================================================================
# MAIN DASHBOARD & STUDIO HTML
# =====================================================================

HTML_CONTENT = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Carousel Studio & Autopilot · 1080x1350</title>
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
      --warning: #f59e0b;
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
      padding: 14px 32px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid var(--card-border);
      backdrop-filter: blur(12px);
      background: rgba(7, 10, 19, 0.85);
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
    
    /* NAV TABS */
    .nav-tabs {
      display: flex;
      background: rgba(255, 255, 255, 0.05);
      padding: 4px;
      border-radius: 12px;
      border: 1px solid var(--card-border);
      gap: 4px;
    }
    .nav-tab {
      padding: 8px 16px;
      border-radius: 8px;
      border: none;
      background: none;
      color: var(--text-muted);
      font-family: inherit;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 8px;
      transition: all 0.2s;
    }
    .nav-tab:hover { color: #fff; background: rgba(255, 255, 255, 0.04); }
    .nav-tab.active {
      background: var(--accent);
      color: #fff;
      box-shadow: 0 2px 10px var(--accent-glow);
    }

    .header-actions {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    
    .tab-content {
      display: none;
      flex: 1;
      width: 100%;
      max-width: 1720px;
      margin: 0 auto;
      padding: 24px 32px;
    }
    .tab-content.active { display: block; }

    /* STUDIO GRID */
    .studio-grid {
      display: grid;
      grid-template-columns: 460px 1fr;
      gap: 24px;
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
    select, textarea, input[type="text"], input[type="url"] {
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
    select:focus, textarea:focus, input[type="text"]:focus, input[type="url"]:focus {
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
    .btn-danger {
      background: rgba(239, 68, 68, 0.15);
      border: 1px solid rgba(239, 68, 68, 0.3);
      color: #f87171;
      box-shadow: none;
    }
    .btn-danger:hover {
      background: rgba(239, 68, 68, 0.25);
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

    /* DASHBOARD / SCHEDULE STYLES */
    .schedule-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
    }
    .stats-row {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 16px;
      margin-bottom: 24px;
    }
    .stat-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      padding: 18px 20px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .stat-value {
      font-size: 26px;
      font-weight: 800;
      color: #fff;
    }
    .stat-label {
      font-size: 13px;
      color: var(--text-muted);
      font-weight: 500;
    }
    .queue-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
      gap: 20px;
    }
    .queue-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 16px;
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 14px;
      position: relative;
      transition: all 0.2s;
    }
    .queue-card:hover {
      border-color: rgba(99, 102, 241, 0.3);
      transform: translateY(-2px);
    }
    .slot-pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: rgba(99, 102, 241, 0.15);
      border: 1px solid rgba(99, 102, 241, 0.3);
      color: #a5b4fc;
      border-radius: 8px;
      padding: 4px 10px;
      font-size: 12px;
      font-weight: 700;
      font-family: 'JetBrains Mono', monospace;
    }
    .queue-preview-strip {
      display: flex;
      gap: 8px;
      overflow-x: auto;
      padding-bottom: 4px;
    }
    .queue-thumb {
      width: 70px;
      aspect-ratio: 4/5;
      object-fit: cover;
      border-radius: 6px;
      border: 1px solid var(--card-border);
      flex-shrink: 0;
    }
    .funnel-tag {
      background: rgba(39, 135, 245, 0.12);
      border: 1px solid rgba(39, 135, 245, 0.25);
      color: #60a5fa;
      padding: 2px 8px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 600;
    }

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
      max-width: 640px;
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
    .funnel-card {
      background: rgba(10, 15, 26, 0.6);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
  </style>
</head>
<body>
  <header>
    <div class="logo">
      <span>🎠 Carousel Studio</span>
      <span class="logo-badge">1080x1350</span>
    </div>

    <!-- TABS -->
    <div class="nav-tabs">
      <button class="nav-tab active" onclick="switchTab('studio')">🎨 Студия</button>
      <button class="nav-tab" onclick="switchTab('autopilot')">🚀 Автопилот по ссылкам</button>
      <button class="nav-tab" onclick="switchTab('schedule')">📅 Дашборд отложки (<span id="scheduleTabCount">0</span>)</button>
      <button class="nav-tab" onclick="switchTab('funnels')">⚙️ Воронки</button>
    </div>

    <div class="header-actions">
      <button id="openTriggersBtn" class="btn btn-secondary" style="padding: 8px 14px; font-size: 13px;">
        ⚡️ Ключевые слова бота
      </button>
      <div style="display: flex; align-items: center; gap: 10px; border-left: 1px solid var(--card-border); padding-left: 14px;">
        <span style="font-size: 12px; color: var(--success); display: flex; align-items: center; gap: 4px;">
          ● Доступ активен
        </span>
        <a href="/api/auth/logout" style="color: var(--text-muted); font-size: 12px; text-decoration: none; padding: 4px 8px; border-radius: 6px; background: rgba(255,255,255,0.06); transition: all 0.2s;" onmouseover="this.style.color='#fff'" onmouseout="this.style.color='var(--text-muted)'">
          Выйти 🚪
        </a>
      </div>
    </div>
  </header>

  <!-- ============================================================= -->
  <!-- TAB 1: STUDIO (MANUAL GENERATOR) -->
  <!-- ============================================================= -->
  <div id="tab-studio" class="tab-content active">
    <div class="studio-grid">
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
  </div>

  <!-- ============================================================= -->
  <!-- TAB 2: AUTOPILOT (ZERO-TOUCH LINK INPUT) -->
  <!-- ============================================================= -->
  <div id="tab-autopilot" class="tab-content">
    <div style="max-width: 900px; margin: 0 auto; display: flex; flex-direction: column; gap: 24px;">
      <div class="panel">
        <div class="panel-title">
          <span>🚀 Автопилот: Генерация карусели и автоотложка в ВК</span>
          <span class="badge badge-green">3 поста в день (10:00, 14:30, 19:00)</span>
        </div>

        <div class="box-info">
          Вставьте ссылку на YouTube (Shorts или видео), Instagram (Reels или карусель), Threads — система сама извлечет контент, упакует через ИИ в 1080x1350 слайды, найдет свободный слот и поставит пост в отложку ВКонтакте с привязкой лид-магнита в боте.
        </div>

        <div class="field">
          <label>Ссылка на контент или сырой текст</label>
          <input type="url" id="autopilotUrlInput" placeholder="https://youtube.com/shorts/... или https://instagram.com/reel/... или тред Threads" style="padding: 14px 16px; font-size: 15px;">
        </div>

        <div class="field">
          <label>Выберите воронку (определяет визуал, группу ВК, кодовое слово и лид-магнит)</label>
          <select id="autopilotFunnelSelect" style="padding: 12px 14px;">
            <option value="">Загрузка воронок...</option>
          </select>
        </div>

        <button id="runAutopilotBtn" class="btn" style="padding: 16px 24px; font-size: 16px;">
          <div class="spinner"></div>
          <span class="btn-text">⚡️ Запустить в автопилот (Создать & Поставить в отложку)</span>
        </button>

        <!-- БЛОК СТАТУСА ВЫПОЛНЕНИЯ -->
        <div id="autopilotStatusBox" style="display: none; padding: 18px; border-radius: 12px; background: rgba(10, 15, 26, 0.6); border: 1px solid var(--card-border); flex-direction: column; gap: 12px;">
          <div id="autopilotStepText" style="font-size: 14px; font-weight: 600; color: #fff;"></div>
          <div id="autopilotResultCard" style="display: none; flex-direction: column; gap: 10px; margin-top: 10px;"></div>
        </div>
      </div>
    </div>
  </div>

  <!-- ============================================================= -->
  <!-- TAB 3: SCHEDULE DASHBOARD (3 POSTS PER DAY) -->
  <!-- ============================================================= -->
  <div id="tab-schedule" class="tab-content">
    <div class="schedule-header">
      <div>
        <h2 style="font-size: 22px; font-weight: 800; color: #fff;">📅 Дашборд запланированного контента</h2>
        <div style="font-size: 13px; color: var(--text-muted); margin-top: 4px;">
          Сетка отложенных каруселей ВКонтакте: 3 публикации в день (10:00 · 14:30 · 19:00 МСК)
        </div>
      </div>
      <button class="btn btn-secondary" onclick="loadQueueData()">
        🔄 Обновить сетку
      </button>
    </div>

    <!-- STATS -->
    <div class="stats-row">
      <div class="stat-card">
        <div class="stat-value" id="statTotalQueued">0</div>
        <div class="stat-label">Всего в отложке ВК</div>
      </div>
      <div class="stat-card">
        <div class="stat-value" id="statSlotsPerDay">3</div>
        <div class="stat-label">Слотов в день (10:00, 14:30, 19:00)</div>
      </div>
      <div class="stat-card">
        <div class="stat-value" id="statDaysCovered">0 дн.</div>
        <div class="stat-label">Заполненный горизонт</div>
      </div>
      <div class="stat-card">
        <div class="stat-value" id="statNextSlot">--:--</div>
        <div class="stat-label">Ближайший слот</div>
      </div>
    </div>

    <!-- QUEUE GRID -->
    <div id="queueGridContainer" class="queue-grid">
      <div class="empty-state" style="grid-column: 1 / -1;">
        <div class="empty-icon">📅</div>
        <div style="font-size: 16px; font-weight: 600; color: #fff;">Очередь отложки пуста</div>
        <div style="font-size: 14px; max-width: 340px;">Вставьте ссылку во вкладке «Автопилот», и карусели автоматически займут слоты в сетке.</div>
      </div>
    </div>
  </div>

  <!-- ============================================================= -->
  <!-- TAB 4: FUNNELS (PRESETS & LEAD MAGNETS) -->
  <!-- ============================================================= -->
  <div id="tab-funnels" class="tab-content">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px;">
      <div>
        <h2 style="font-size: 22px; font-weight: 800; color: #fff;">⚙️ Пресеты воронок</h2>
        <div style="font-size: 13px; color: var(--text-muted); margin-top: 4px;">
          Настройте визуал, токен ВК, кодовое слово и лид-магнит один раз — автопилот применит их ко всем видео
        </div>
      </div>
      <button class="btn" onclick="openNewFunnelModal()">
        + Создать воронку
      </button>
    </div>

    <div id="funnelsListContainer" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(440px, 1fr)); gap: 20px;">
      <!-- Карточки воронок -->
    </div>
  </div>

  <!-- ============================================================= -->
  <!-- MODAL: CREATE / EDIT FUNNEL -->
  <!-- ============================================================= -->
  <div id="funnelModal" class="modal-backdrop">
    <div class="modal">
      <div class="modal-header">
        <div class="modal-title">
          <span id="funnelModalTitle">⚙️ Новая воронка</span>
        </div>
        <button class="modal-close" onclick="closeFunnelModal()">&times;</button>
      </div>
      <div class="modal-body">
        <input type="hidden" id="editFunnelId">

        <div class="field">
          <label>Название воронки</label>
          <input type="text" id="fnNameInput" placeholder="Например: Бьюти-бизнес: Сервис и стандарты" required>
        </div>

        <div class="field">
          <label>Визуальная тема каруселей</label>
          <select id="fnThemeSelect">
            <option value="ocean">Ocean (Azure Blue)</option>
            <option value="default">Default (Indigo & Slate)</option>
            <option value="dark">Dark Mode (Emerald & Graphite)</option>
            <option value="minimal">Minimal (Black & White)</option>
          </select>
        </div>

        <div style="border-top: 1px solid var(--card-border); padding-top: 14px; display: flex; flex-direction: column; gap: 14px;">
          <span style="font-size: 14px; font-weight: 700; color: #fff;">📢 Публикация ВКонтакте</span>
          
          <div class="field">
            <label>VK Access Token</label>
            <input type="text" id="fnVkTokenInput" placeholder="vk1.a.your_token..." style="font-size: 12px; font-family: 'JetBrains Mono', monospace;">
          </div>

          <div class="field">
            <label>Куда публиковать</label>
            <select id="fnVkTargetSelect">
              <option value="user">👤 Личная страница</option>
              <option value="group">👥 Сообщество / Группа</option>
            </select>
          </div>

          <div class="field" id="fnGroupIdField" style="display: none;">
            <label>ID группы ВКонтакте (только положительное число)</label>
            <input type="text" id="fnGroupIdInput" placeholder="123456789">
          </div>
        </div>

        <div style="border-top: 1px solid var(--card-border); padding-top: 14px; display: flex; flex-direction: column; gap: 14px;">
          <span style="font-size: 14px; font-weight: 700; color: #fff;">🎁 Лид-магнит и бот выдачи</span>

          <div class="field">
            <label>Кодовое слово (триггер в комментариях и ЛС)</label>
            <input type="text" id="fnKeywordInput" placeholder="Например: СЕРВИС" required>
          </div>

          <div class="field">
            <label>Название материала</label>
            <input type="text" id="fnLmTitleInput" placeholder="Регламент работы администратора">
          </div>

          <div class="field">
            <label>Ссылка на скачивание лид-магнита</label>
            <input type="text" id="fnLmUrlInput" placeholder="https://disk.yandex.ru/d/... или Telegram-канал">
          </div>
        </div>

        <div style="border-top: 1px solid var(--card-border); padding-top: 14px; display: flex; flex-direction: column; gap: 14px;">
          <span style="font-size: 14px; font-weight: 700; color: #fff;">⏰ Сетка расписания (слоты часов МСК)</span>
          <div class="field">
            <label>3 слота в день (через запятую)</label>
            <input type="text" id="fnSlotsInput" value="10:00, 14:30, 19:00">
          </div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-secondary" onclick="closeFunnelModal()">Отмена</button>
        <button class="btn" onclick="saveFunnel()">Сохранить воронку</button>
      </div>
    </div>
  </div>

  <!-- ============================================================= -->
  <!-- MODAL: VK PUBLISHER (STUDIO MANUAL) -->
  <!-- ============================================================= -->
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
          💡 Для публикации нужен Standalone User Token. Получите его в один клик на <a href="https://vkhost.github.io" target="_blank" style="color: #fff; font-weight: 600; text-decoration: underline;">vkhost.github.io</a> (выберите Kate Mobile или VK Admin).
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

  <!-- ============================================================= -->
  <!-- MODAL: TRIGGERS LIST -->
  <!-- ============================================================= -->
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
    // Tab switching
    function switchTab(tabId) {
      document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

      const activeBtn = Array.from(document.querySelectorAll('.nav-tab')).find(t => t.getAttribute('onclick') && t.getAttribute('onclick').includes(tabId));
      if (activeBtn) activeBtn.classList.add('active');

      const targetContent = document.getElementById('tab-' + tabId);
      if (targetContent) targetContent.classList.add('active');

      if (tabId === 'schedule') loadQueueData();
      if (tabId === 'funnels') loadFunnelsData();
      if (tabId === 'autopilot') loadAutopilotDropdowns();
    }

    // Studio Elements
    const themeSelect = document.getElementById('themeSelect');
    const templateSelect = document.getElementById('templateSelect');
    const jsonInput = document.getElementById('jsonInput');
    const renderBtn = document.getElementById('renderBtn');
    const galleryContainer = document.getElementById('galleryContainer');
    const downloadBtn = document.getElementById('downloadBtn');
    const openVkModalBtn = document.getElementById('openVkModalBtn');
    const slideCount = document.getElementById('slideCount');

    // VK Elements
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

    // Triggers Elements
    const triggersModal = document.getElementById('triggersModal');
    const openTriggersBtn = document.getElementById('openTriggersBtn');
    const triggersListContainer = document.getElementById('triggersListContainer');

    // Autopilot Elements
    const autopilotUrlInput = document.getElementById('autopilotUrlInput');
    const autopilotFunnelSelect = document.getElementById('autopilotFunnelSelect');
    const runAutopilotBtn = document.getElementById('runAutopilotBtn');
    const autopilotStatusBox = document.getElementById('autopilotStatusBox');
    const autopilotStepText = document.getElementById('autopilotStepText');
    const autopilotResultCard = document.getElementById('autopilotResultCard');

    // Queue Elements
    const scheduleTabCount = document.getElementById('scheduleTabCount');
    const statTotalQueued = document.getElementById('statTotalQueued');
    const statDaysCovered = document.getElementById('statDaysCovered');
    const statNextSlot = document.getElementById('statNextSlot');
    const queueGridContainer = document.getElementById('queueGridContainer');

    // Funnels Elements
    const funnelsListContainer = document.getElementById('funnelsListContainer');
    const funnelModal = document.getElementById('funnelModal');
    const fnVkTargetSelect = document.getElementById('fnVkTargetSelect');
    const fnGroupIdField = document.getElementById('fnGroupIdField');

    let currentRenderId = null;
    let templatesCache = {};
    let funnelsCache = [];

    // LocalStorage token
    const savedToken = localStorage.getItem('vk_user_token');
    if (savedToken) {
      vkTokenInput.value = savedToken;
    }

    async function loadInitialData() {
      try {
        const [themesRes, templatesRes, funnelsRes, queueRes] = await Promise.all([
          fetch('/api/themes'),
          fetch('/api/templates'),
          fetch('/api/funnels'),
          fetch('/api/queue')
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

        const fData = await funnelsRes.json();
        funnelsCache = fData.funnels || [];
        loadAutopilotDropdowns();

        const qData = await queueRes.json();
        updateQueueStats(qData.queue || []);
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

    // =============================================================
    // AUTOPILOT LOGIC
    // =============================================================
    function loadAutopilotDropdowns() {
      if (!funnelsCache || funnelsCache.length === 0) {
        autopilotFunnelSelect.innerHTML = '<option value="">Нет созданных воронок (создайте во вкладке Воронки)</option>';
        return;
      }
      autopilotFunnelSelect.innerHTML = funnelsCache.map(fn => `
        <option value="${fn.id}">${fn.name} · Тема: ${fn.theme} · Кодовое слово: «${fn.lead_magnet.keyword}»</option>
      `).join('');
    }

    runAutopilotBtn.addEventListener('click', async () => {
      const url = autopilotUrlInput.value.trim();
      if (!url) {
        alert('Пожалуйста, укажите ссылку на YouTube, Reels, Threads или вставьте текст.');
        return;
      }

      const funnelId = autopilotFunnelSelect.value;
      if (!funnelId) {
        alert('Пожалуйста, выберите воронку.');
        return;
      }

      runAutopilotBtn.classList.add('loading');
      runAutopilotBtn.disabled = true;
      autopilotStatusBox.style.display = 'flex';
      autopilotStepText.innerHTML = '⏳ <b>Шаг 1/5:</b> Извлечение контента и субтитров...';
      autopilotResultCard.style.display = 'none';

      try {
        const res = await fetch('/api/autopilot/process', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url_or_text: url,
            funnel_id: funnelId,
            custom_token: localStorage.getItem('vk_user_token') || undefined
          })
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Сбой автопилота');

        const item = data.item;
        autopilotStepText.innerHTML = '✅ <b>Готово!</b> Карусель упакована и поставлена в отложку ВКонтакте.';
        autopilotResultCard.style.display = 'flex';
        autopilotResultCard.innerHTML = `
          <div style="background: rgba(16, 185, 129, 0.12); border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 12px; padding: 16px; display: flex; flex-direction: column; gap: 8px;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <span class="slot-pill">⏰ ${item.scheduled_date_str}</span>
              <a href="${item.wall_url}" target="_blank" style="color: #60a5fa; font-weight: 700; text-decoration: underline; font-size: 13px;">Открыть запись в ВК ↗</a>
            </div>
            <div style="font-weight: 700; color: #fff; font-size: 15px; margin-top: 4px;">${item.title}</div>
            <div style="font-size: 13px; color: var(--text-muted);">${item.slides_count} слайдов · Воронка: ${item.funnel_name}</div>
            <div class="queue-preview-strip" style="margin-top: 6px;">
              ${item.slides.map(s => `<img class="queue-thumb" src="${s}">`).join('')}
            </div>
          </div>
        `;

        autopilotUrlInput.value = '';
        loadQueueData();
      } catch (err) {
        autopilotStepText.innerHTML = `<span style="color: var(--danger)">❌ Ошибка: ${err.message}</span>`;
      } finally {
        runAutopilotBtn.classList.remove('loading');
        runAutopilotBtn.disabled = false;
      }
    });

    // =============================================================
    // QUEUE / SCHEDULE LOGIC
    // =============================================================
    async function loadQueueData() {
      try {
        const res = await fetch('/api/queue');
        const data = await res.json();
        const items = data.queue || [];
        updateQueueStats(items);

        if (items.length === 0) {
          queueGridContainer.innerHTML = `
            <div class="empty-state" style="grid-column: 1 / -1;">
              <div class="empty-icon">📅</div>
              <div style="font-size: 16px; font-weight: 600; color: #fff;">Очередь отложки пуста</div>
              <div style="font-size: 14px; max-width: 340px;">Вставьте ссылку во вкладке «Автопилот», и карусели автоматически займут слоты в сетке.</div>
            </div>
          `;
          return;
        }

        queueGridContainer.innerHTML = items.map(item => `
          <div class="queue-card">
            <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 10px;">
              <span class="slot-pill">⏰ ${item.scheduled_date_str}</span>
              <button class="btn btn-danger" style="padding: 4px 8px; font-size: 11px;" onclick="cancelQueueItem('${item.id}')" title="Отменить публикацию">✕</button>
            </div>

            <div style="font-weight: 700; color: #fff; font-size: 15px; line-height: 1.4;">${item.title}</div>
            
            <div style="display: flex; gap: 8px; align-items: center;">
              <span class="funnel-tag">${item.funnel_name || 'Воронка'}</span>
              <span style="font-size: 12px; color: var(--text-muted);">${item.slides_count} слайдов</span>
            </div>

            <div class="queue-preview-strip">
              ${(item.slides || []).map(s => `<a href="${s}" target="_blank"><img class="queue-thumb" src="${s}"></a>`).join('')}
            </div>

            <div style="background: rgba(0,0,0,0.3); padding: 10px; border-radius: 8px; font-size: 12px; color: var(--text-muted); max-height: 60px; overflow-y: hidden; text-overflow: ellipsis; white-space: pre-wrap;">
              ${item.post_text}
            </div>

            <div style="display: flex; justify-content: space-between; align-items: center; border-top: 1px solid var(--card-border); padding-top: 12px; margin-top: 4px;">
              <span class="badge badge-green">● Отложено в ВК</span>
              <a href="${item.wall_url}" target="_blank" style="color: var(--vk-color); font-weight: 600; text-decoration: none; font-size: 12px;">Пост в ВК ↗</a>
            </div>
          </div>
        `).join('');
      } catch (e) {
        console.error('Failed to load queue:', e);
      }
    }

    function updateQueueStats(items) {
      scheduleTabCount.textContent = items.length;
      statTotalQueued.textContent = items.length;

      const daysCount = Math.ceil(items.length / 3);
      statDaysCovered.textContent = `${daysCount} дн.`;

      if (items.length > 0) {
        statNextSlot.textContent = items[0].scheduled_date_str.split(' в ')[1] || items[0].scheduled_date_str;
      } else {
        statNextSlot.textContent = '--:--';
      }
    }

    async function cancelQueueItem(id) {
      if (!confirm('Отменить эту публикацию и удалить из отложки ВК?')) return;
      try {
        const res = await fetch(`/api/queue/${id}`, { method: 'DELETE' });
        if (res.ok) {
          loadQueueData();
        } else {
          alert('Не удалось отменить запись');
        }
      } catch (e) {
        alert('Ошибка: ' + e.message);
      }
    }

    // =============================================================
    // FUNNELS PRESETS LOGIC
    // =============================================================
    async function loadFunnelsData() {
      try {
        const res = await fetch('/api/funnels');
        const data = await res.json();
        funnelsCache = data.funnels || [];
        loadAutopilotDropdowns();

        if (funnelsCache.length === 0) {
          funnelsListContainer.innerHTML = '<div style="color: var(--text-muted)">Нет созданных воронок. Нажмите «+ Создать воронку».</div>';
          return;
        }

        funnelsListContainer.innerHTML = funnelsCache.map(fn => `
          <div class="funnel-card">
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <span style="font-size: 16px; font-weight: 700; color: #fff;">${fn.name}</span>
              <div style="display: flex; gap: 8px;">
                <button class="btn btn-secondary" style="padding: 4px 10px; font-size: 12px;" onclick="editFunnel('${fn.id}')">Редактировать</button>
                <button class="btn btn-danger" style="padding: 4px 8px; font-size: 12px;" onclick="deleteFunnel('${fn.id}')">✕</button>
              </div>
            </div>

            <div style="display: flex; gap: 10px; flex-wrap: wrap;">
              <span class="badge badge-blue">Тема: ${fn.theme}</span>
              <span class="badge badge-green">Кодовое слово: «${fn.lead_magnet.keyword}»</span>
              <span class="badge" style="background: rgba(255,255,255,0.08);">Слоты: ${(fn.schedule.slots || []).join(', ')}</span>
            </div>

            <div style="font-size: 13px; color: var(--text-muted); display: flex; flex-direction: column; gap: 4px; background: rgba(0,0,0,0.25); padding: 10px; border-radius: 8px;">
              <div>🎁 <b>Бонус:</b> ${fn.lead_magnet.title}</div>
              <div>🔗 <b>Ссылка:</b> <a href="${fn.lead_magnet.url}" target="_blank" style="color: #60a5fa;">${fn.lead_magnet.url}</a></div>
              <div>📢 <b>Цель ВК:</b> ${fn.vk.target === 'group' ? 'Группа (ID ' + fn.vk.group_id + ')' : 'Личная страница'}</div>
            </div>
          </div>
        `).join('');
      } catch (e) {
        console.error('Failed to load funnels:', e);
      }
    }

    fnVkTargetSelect.addEventListener('change', () => {
      fnGroupIdField.style.display = fnVkTargetSelect.value === 'group' ? 'flex' : 'none';
    });

    function openNewFunnelModal() {
      document.getElementById('funnelModalTitle').textContent = '⚙️ Новая воронка';
      document.getElementById('editFunnelId').value = '';
      document.getElementById('fnNameInput').value = '';
      document.getElementById('fnThemeSelect').value = 'ocean';
      document.getElementById('fnVkTokenInput').value = localStorage.getItem('vk_user_token') || '';
      document.getElementById('fnVkTargetSelect').value = 'user';
      document.getElementById('fnGroupIdInput').value = '';
      fnGroupIdField.style.display = 'none';
      document.getElementById('fnKeywordInput').value = '';
      document.getElementById('fnLmTitleInput').value = '';
      document.getElementById('fnLmUrlInput').value = '';
      document.getElementById('fnSlotsInput').value = '10:00, 14:30, 19:00';
      funnelModal.style.display = 'flex';
    }

    function editFunnel(id) {
      const fn = funnelsCache.find(f => f.id === id);
      if (!fn) return;

      document.getElementById('funnelModalTitle').textContent = '⚙️ Редактировать воронку';
      document.getElementById('editFunnelId').value = fn.id;
      document.getElementById('fnNameInput').value = fn.name || '';
      document.getElementById('fnThemeSelect').value = fn.theme || 'ocean';
      document.getElementById('fnVkTokenInput').value = fn.vk?.access_token || localStorage.getItem('vk_user_token') || '';
      document.getElementById('fnVkTargetSelect').value = fn.vk?.target || 'user';
      document.getElementById('fnGroupIdInput').value = fn.vk?.group_id || '';
      fnGroupIdField.style.display = fn.vk?.target === 'group' ? 'flex' : 'none';
      document.getElementById('fnKeywordInput').value = fn.lead_magnet?.keyword || '';
      document.getElementById('fnLmTitleInput').value = fn.lead_magnet?.title || '';
      document.getElementById('fnLmUrlInput').value = fn.lead_magnet?.url || '';
      document.getElementById('fnSlotsInput').value = (fn.schedule?.slots || ['10:00', '14:30', '19:00']).join(', ');
      funnelModal.style.display = 'flex';
    }

    function closeFunnelModal() {
      funnelModal.style.display = 'none';
    }

    async function saveFunnel() {
      const name = document.getElementById('fnNameInput').value.trim();
      const keyword = document.getElementById('fnKeywordInput').value.trim().toUpperCase();
      if (!name || !keyword) {
        alert('Укажите название воронки и кодовое слово');
        return;
      }

      const id = document.getElementById('editFunnelId').value;
      const theme = document.getElementById('fnThemeSelect').value;
      const vkToken = document.getElementById('fnVkTokenInput').value.trim();
      const target = document.getElementById('fnVkTargetSelect').value;
      const groupId = document.getElementById('fnGroupIdInput').value.trim();
      const lmTitle = document.getElementById('fnLmTitleInput').value.trim();
      const lmUrl = document.getElementById('fnLmUrlInput').value.trim();
      const slotsStr = document.getElementById('fnSlotsInput').value.trim();
      const slots = slotsStr.split(',').map(s => s.trim()).filter(Boolean);

      const payload = {
        id: id || undefined,
        name: name,
        theme: theme,
        vk: {
          access_token: vkToken,
          target: target,
          group_id: target === 'group' && groupId ? parseInt(groupId, 10) : null
        },
        lead_magnet: {
          keyword: keyword,
          title: lmTitle || 'Материалы',
          url: lmUrl,
          comment_reply: `@{user_screen_name} ({first_name}), мы отправили ${lmTitle} в личные сообщения! 🎁\n\nЕсли сообщения закрыты, напишите нам: vk.me/{group_domain}`,
          dm_text: `Здравствуйте, {first_name}! 🎁\n\nВы запросили «${lmTitle}» по кодовому слову «${keyword}».\n\nСсылка на материалы: ${lmUrl}`
        },
        schedule: {
          slots: slots.length > 0 ? slots : ['10:00', '14:30', '19:00'],
          timezone_offset: 3
        }
      };

      try {
        const res = await fetch('/api/funnels', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });

        if (!res.ok) throw new Error('Failed to save funnel');
        closeFunnelModal();
        loadFunnelsData();
      } catch (e) {
        alert('Ошибка сохранения: ' + e.message);
      }
    }

    async function deleteFunnel(id) {
      if (!confirm('Удалить этот пресет воронки?')) return;
      try {
        await fetch(`/api/funnels/${id}`, { method: 'DELETE' });
        loadFunnelsData();
      } catch (e) {
        alert('Ошибка удаления: ' + e.message);
      }
    }

    // =============================================================
    // VK PUBLISHER MODAL (STUDIO MANUAL)
    // =============================================================
    openVkModalBtn.addEventListener('click', async () => {
      if (!currentRenderId) return;
      vkModal.style.display = 'flex';
      publishStatus.style.display = 'none';

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
        alert('Ошибка сборки текста: ' + e.message);
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
        alert('Укажите VK Access Token');
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
        loadQueueData();
      } catch (e) {
        publishStatus.style.background = 'rgba(239, 68, 68, 0.15)';
        publishStatus.style.color = '#f87171';
        publishStatus.innerHTML = `❌ Ошибка: ${e.message}`;
      } finally {
        doPublishBtn.classList.remove('loading');
        doPublishBtn.disabled = false;
      }
    });

    // =============================================================
    // TRIGGERS MODAL LOGIC
    // =============================================================
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
          <div class="trigger-item" style="background: rgba(10, 15, 26, 0.6); border: 1px solid var(--card-border); border-radius: 10px; padding: 14px; display: flex; flex-direction: column; gap: 8px;">
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
