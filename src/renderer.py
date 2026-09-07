# -*- coding: utf-8 -*-
"""
Рендерер слайдов карусели и сборка контакт-листов.
"""
import os
import re
from PIL import Image, ImageDraw
from src.engine import (
    W, H, PAD, TOP, GAP, FOOT_Y, FOOT_H,
    BLACK_, HEAVY, BOLD, SEMI, MED, REG,
    draw_par, draw_hl, h_hl, h_par,
    orb, shadow, card, pill, check, numbox,
    quote_h, quote_box, footer,
    has_photo, photo_bleed_right
)

def bg_white(img, theme):
    ImageDraw.Draw(img).ellipse([W-300, -300, W+300, 300], fill=theme.PRIMARY_TINT+(255,))

def bg_primary(img, theme):
    d = ImageDraw.Draw(img)
    p_start = theme.PRIMARY
    p_end = theme.PRIMARY_NIGHT
    for i in range(H):
        t = i / (H - 1)
        d.line([(0, i), (W, i)], fill=(
            int(p_start[0] + (p_end[0] - p_start[0]) * t),
            int(p_start[1] + (p_end[1] - p_start[1]) * t),
            int(p_start[2] + (p_end[2] - p_start[2]) * t),
            255
        ))
    orb(img, 60, 90, 340, (120, 190, 255), 90, 130)
    orb(img, W - 30, H - 140, 300, (30, 80, 190), 130, 130)

def slide_cover(s, i, total, theme, assets_dir=None):
    img = Image.new("RGBA", (W, H), theme.WHITE + (255,))
    bg_white(img, theme)
    f_t, f_l = theme.font(BLACK_, 104), theme.font(MED, 35)
    mw_t, mw_l = W - PAD * 2 - 30, W - PAD * 2 - 60
    qtop = FOOT_Y - 34
    lead_text = s.get("lead", "")
    ch = h_hl(s["title"], f_t, mw_t, 1.02) + (42 + h_par(lead_text, f_l, mw_l, 1.4) if lead_text else 0)
    y = TOP + max(0, (qtop - GAP - TOP - ch) // 2)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([PAD, y - 34, PAD + 86, y - 27], radius=4, fill=theme.PRIMARY + (255,))
    y = draw_hl(d, s["title"], f_t, PAD, y, mw_t, lh=1.02) + 42
    if lead_text:
        draw_par(d, lead_text, f_l, PAD, y, mw_l, theme.GRAY + (255,), lh=1.4)
    footer(img, i, total, False, theme=theme)
    return img.convert("RGB")

def slide_break(s, i, total, theme, assets_dir=None):
    img = Image.new("RGBA", (W, H), theme.PRIMARY + (255,))
    bg_primary(img, theme)
    f_t, f_l = theme.font(BLACK_, 72), theme.font(MED, 33)
    mw = W - PAD * 2 - 10
    has_q = bool(s.get("quote"))
    qtop = (H - PAD - quote_h(s["quote"], theme)) if has_q else (FOOT_Y - 34)
    head_h = 200 if s.get("bignum") else (54 + 44 if s.get("tag") else 0)
    lead_text = s.get("lead", "")
    ch = head_h + h_hl(s["title"], f_t, mw, 1.1) + (32 + h_par(lead_text, f_l, mw - 20, 1.42) if lead_text else 0)
    y = TOP + max(0, (qtop - GAP - TOP - ch) // 2)
    d = ImageDraw.Draw(img)
    if s.get("bignum"):
        d.text((PAD, y - 30), str(s["bignum"]), font=theme.font(BLACK_, 184), fill=theme.WHITE + (255,))
        y += 200
    elif s.get("tag"):
        y = pill(img, s["tag"], PAD, y, True, theme=theme) + 44
    d = ImageDraw.Draw(img)
    y = draw_hl(d, s["title"], f_t, PAD, y, mw, lh=1.1) + 32
    if lead_text:
        draw_par(d, lead_text, f_l, PAD, y, mw - 20, (214, 230, 252, 255), lh=1.42)
    if has_q:
        quote_box(img, s["quote"], True, theme=theme)
    footer(img, i, total, True, theme=theme)
    return img.convert("RGB")

def slide_list(s, i, total, theme, assets_dir=None):
    img = Image.new("RGBA", (W, H), theme.WHITE + (255,))
    bg_white(img, theme)
    f_t, f_lb, f_i = theme.font(BLACK_, 68), theme.font(BOLD, 31), theme.font(SEMI, 34)
    mw_t = W - PAD * 2 - 30
    S = 48
    mw_i = W - PAD * 2 - S - 34
    has_q = bool(s.get("quote"))
    qtop = (H - PAD - quote_h(s["quote"], theme)) if has_q else (FOOT_Y - 34)
    ch = (54 + 34 if s.get("tag") else 0) + h_hl(s["title"], f_t, mw_t, 1.08) + 40 + (64 if s.get("label") else 0)
    items = s.get("items", [])
    items_h = [max(h_par(it, f_i, mw_i, 1.3), S) for it in items]
    free = qtop - GAP - TOP - ch - sum(items_h)
    gap = min(64, max(20, free // max(len(items), 1)))
    ch += sum(items_h) + gap * max(len(items) - 1, 0)
    y = TOP + max(0, (qtop - GAP - TOP - ch) // 2)
    if s.get("tag"):
        y = pill(img, s["tag"], PAD, y, False, theme=theme) + 34
    d = ImageDraw.Draw(img)
    y = draw_hl(d, s["title"], f_t, PAD, y, mw_t, lh=1.08) + 40
    if s.get("label"):
        d.text((PAD, y), s["label"], font=f_lb, fill=theme.INK + (255,))
        y += 64
    for n, it in enumerate(items, 1):
        if s.get("numbered"):
            numbox(img, PAD, y + 3, S, n, theme=theme)
        else:
            check(img, PAD, y + 3, S, theme=theme)
        d = ImageDraw.Draw(img)
        draw_par(d, it, f_i, PAD + S + 26, y, mw_i, theme.INK + (255,), lh=1.3)
        if n < len(items):
            yl = y + items_h[n - 1] + gap // 2 - 2
            d.line([(PAD + S + 26, yl), (W - PAD, yl)], fill=theme.LINE + (255,), width=2)
        y += items_h[n - 1] + gap
    if has_q:
        quote_box(img, s["quote"], False, theme=theme)
    footer(img, i, total, False, theme=theme)
    return img.convert("RGB")

def slide_cta(s, i, total, theme, assets_dir=None):
    img = Image.new("RGBA", (W, H), theme.WHITE + (255,))
    photo_spec = s.get("photo", theme.default_photo)
    ph = photo_bleed_right(img, width=470, feather=104, top_bias=0.06, x_bias=0.30,
                           photo_spec=photo_spec, assets_dir=assets_dir) if has_photo(photo_spec, assets_dir) else False
    if not ph:
        bg_white(img, theme)

    s_word = s.get("word")
    if not s_word:
        all_text = ""
        if isinstance(s.get("title"), str):
            all_text += s["title"] + " "
        elif isinstance(s.get("title"), list):
            all_text += " ".join(t for t, _ in s["title"]) + " "
        all_text += s.get("lead", "")
        
        match = re.search(r'[«"“]([а-яА-Яa-zA-Z]{3,15})[»"”]', all_text)
        s_word = match.group(1).upper() if match else "СТАРТ"

    cx1 = (W - 470 - 26) if ph else (W - PAD)
    PADC = 38
    inner = cx1 - PAD - PADC * 2
    f1 = theme.font(SEMI, 25 if ph else 28)
    f3 = theme.font(SEMI, 24 if ph else 27)
    fs = 86
    scratch = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    while fs > 34:
        f2 = theme.font(BLACK_, fs)
        if scratch.textlength(s_word, font=f2) <= inner:
            break
        fs -= 2
    h1 = int(f1.size * 1.3)
    hw = int(fs * 1.12)
    h3 = int(f3.size * 1.34)
    bh = PADC + h1 + 8 + hw + 14 + 6 + 16 + h3 * 2 + PADC
    y0 = H - PAD - FOOT_H - 30 - bh

    lead_text = s.get("lead", "")
    f_t = theme.font(BLACK_, 66 if ph else 72)
    f_l = theme.font(MED, 31 if ph else 33)
    mw = (W - 470 - PAD - 30) if ph else (W - PAD * 2 - 10)
    ch = (54 + 40 if s.get("tag") else 0) + h_hl(s["title"], f_t, mw, 1.08) + 28 + (h_par(lead_text, f_l, mw, 1.4) if lead_text else 0)
    y = TOP + max(0, (y0 - GAP - TOP - ch) // 2)
    if s.get("tag"):
        y = pill(img, s["tag"], PAD, y, False, theme=theme) + 40
    d = ImageDraw.Draw(img)
    y = draw_hl(d, s["title"], f_t, PAD, y, mw, lh=1.08) + 28
    if lead_text:
        draw_par(d, lead_text, f_l, PAD, y, mw, theme.GRAY + (255,), lh=1.4)

    card(img, [PAD, y0, cx1, y0 + bh], 30, theme.WHITE, blur=40, alpha=60, dy=16, color=(23, 44, 84))
    d = ImageDraw.Draw(img)
    cc = (PAD + cx1) / 2
    yy = y0 + PADC
    t1 = "Напишите в комментариях слово"
    tw = d.textlength(t1, font=f1)
    d.text((cc - tw / 2, yy), t1, font=f1, fill=theme.GRAY + (255,))
    yy += h1 + 8
    tw = d.textlength(s_word, font=f2)
    d.text((cc - tw / 2, yy), s_word, font=f2, fill=theme.PRIMARY + (255,))
    yy += hw + 14
    d.rounded_rectangle([cc - tw / 2, yy, cc + tw / 2, yy + 6], radius=3, fill=theme.PRIMARY_SOFT + (255,))
    yy += 6 + 16
    for t in ("и я пришлю ссылку", "в личные сообщения"):
        tw = d.textlength(t, font=f3)
        d.text((cc - tw / 2, yy), t, font=f3, fill=theme.GRAY + (255,))
        yy += h3
    footer(img, i, total, False, meta=not ph, theme=theme)
    return img.convert("RGB")

RENDER_MAP = {
    "cover": slide_cover,
    "break": slide_break,
    "list": slide_list,
    "cta": slide_cta
}

def normalize_slide(sl, theme):
    sl = dict(sl)
    t = sl.get("title")
    default_white = theme.WHITE
    default_ink = theme.INK
    default_primary = theme.PRIMARY
    
    is_break = (sl.get("type") == "break")
    fallback_col = default_white if is_break else default_ink
    
    if isinstance(t, str):
        sl["title"] = [(t, fallback_col)]
    elif isinstance(t, list):
        normalized_t = []
        for item in t:
            if isinstance(item, str):
                normalized_t.append((item, fallback_col))
            elif isinstance(item, (list, tuple)):
                txt = str(item[0]) if len(item) > 0 else ""
                col = item[1] if len(item) > 1 else "INK"
                if isinstance(col, (list, tuple)):
                    color_val = tuple(col)
                elif col in ("BLUE", "blue", "PRIMARY", "primary"):
                    color_val = default_primary
                elif col in ("WHITE", "white"):
                    color_val = default_white
                elif col in ("INK", "ink"):
                    color_val = default_ink
                else:
                    color_val = default_ink
                normalized_t.append((txt, color_val))
        sl["title"] = normalized_t

    if "lead" in sl and sl["lead"] is not None:
        sl["lead"] = str(sl["lead"])
    if "tag" in sl and sl["tag"] is not None:
        sl["tag"] = str(sl["tag"]).replace("\n", " ").strip()
    if "quote" in sl and sl["quote"] is not None:
        sl["quote"] = str(sl["quote"])
    if "word" in sl and sl["word"] is not None:
        sl["word"] = str(sl["word"]).replace("\n", "").strip()
    if "items" in sl and isinstance(sl["items"], list):
        sl["items"] = [str(it) for it in sl["items"]]
    return sl

def make_contact_sheet(images, out_path, cols=5):
    """Собирает контакт-лист (превью) всех карточек в сетку."""
    if not images:
        return
    count = len(images)
    cols = min(cols, count)
    rows = (count + cols - 1) // cols
    
    tw = 270
    th = 337
    margin = 20
    
    sheet_w = cols * tw + (cols + 1) * margin
    sheet_h = rows * th + (rows + 1) * margin
    
    sheet = Image.new("RGB", (sheet_w, sheet_h), (240, 244, 248))
    for idx, img in enumerate(images):
        r = idx // cols
        c = idx % cols
        x = margin + c * (tw + margin)
        y = margin + r * (th + margin)
        thumb = img.resize((tw, th), Image.LANCZOS)
        sheet.paste(thumb, (x, y))
        
    sheet.save(out_path, "PNG")

def render_deck(slides, output_dir, theme, assets_dir=None, make_preview=True):
    os.makedirs(output_dir, exist_ok=True)
    total = len(slides)
    rendered_images = []
    
    for i, raw_sl in enumerate(slides, 1):
        sl = normalize_slide(raw_sl, theme)
        stype = sl.get("type", "list")
        if stype not in RENDER_MAP:
            stype = "list"
            
        renderer = RENDER_MAP[stype]
        img = renderer(sl, i, total, theme, assets_dir)
        filename = f"{i:02d}.png"
        img.save(os.path.join(output_dir, filename), "PNG")
        rendered_images.append(img)
        
    if make_preview and rendered_images:
        preview_path = os.path.join(output_dir, "preview.png")
        make_contact_sheet(rendered_images, preview_path)
        
    return total
