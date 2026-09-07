# -*- coding: utf-8 -*-
"""
Графический и типографический движок для каруселей 1080x1350.
"""
import os
import re
from PIL import Image, ImageDraw, ImageFilter

W, H = 1080, 1350
PAD = 84
TOP = 108
GAP = 58
FOOT_H = 76
FOOT_Y = H - PAD - FOOT_H

BLACK_, HEAVY, BOLD, SEMI, MED, REG = "Black", "Heavy", "Bold", "Semibold", "Medium", "Regular"

_scratch = ImageDraw.Draw(Image.new("RGB", (10, 10)))

PREPS = {
    'в', 'во', 'на', 'с', 'со', 'к', 'ко', 'по', 'о', 'об', 'обо', 'от', 'ото',
    'из', 'изо', 'за', 'под', 'подо', 'над', 'надо', 'до', 'без', 'безо', 'для',
    'про', 'через', 'при', 'у', 'и', 'а', 'но', 'да', 'не', 'ни', 'же', 'ли',
    'бы', 'то', 'как', 'что', 'где', 'кто', 'чем', 'их', 'ее', 'её', 'его',
    'мы', 'вы', 'он', 'она', 'они', 'я', 'ты', 'или'
}

def is_prep_or_orphan(w: str) -> bool:
    clean_w = re.sub(r'^[«"“\(]+|[»"”\),.!?:;]+$', '', str(w)).strip().lower()
    if not clean_w:
        return False
    if clean_w in PREPS or len(clean_w) == 1:
        return True
    if clean_w.isdigit() or re.match(r'^\d+[-%]?$', clean_w):
        return True
    if clean_w == "-":
        return True
    return False

def typograph(text: str) -> str:
    if not text:
        return ""
    t = str(text).replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\xa0", " ").replace("\u202f", " ")
    t = re.sub(r'(\d+)\s+([а-яА-Яa-zA-Z]+)', lambda m: f'{m.group(1)}\u00A0{m.group(2)}', t)
    for _ in range(3):
        t = re.sub(r'(^|[\s\(\[\"«])([а-яА-Яa-zA-Z]{1,3})\s+',
                   lambda m: f'{m.group(1)}{m.group(2)}\u00A0' if m.group(2).lower() in PREPS else m.group(0), t)
    t = re.sub(r'\s+-\s+', lambda m: ' -\u00A0', t)
    return t

def wrap(draw, text, fnt, max_w):
    if not text:
        return []
    lines = []
    paragraphs = str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for para in paragraphs:
        t_clean = typograph(para).strip()
        if not t_clean:
            continue
        words = [w.replace("\n", "").strip() for w in t_clean.split(" ") if w.strip()]
        cur_words = []
        for w_ in words:
            if not w_:
                continue
            test_line = " ".join(cur_words + [w_])
            if draw.textlength(test_line, font=fnt) <= max_w:
                cur_words.append(w_)
            else:
                carry_over = [w_]
                while cur_words and is_prep_or_orphan(cur_words[-1]) and len(cur_words) > 1:
                    carry_over.insert(0, cur_words.pop())

                if cur_words:
                    lines.append(" ".join(cur_words))
                cur_words = carry_over
        if cur_words:
            lines.append(" ".join(cur_words))
    return lines

def draw_par(draw, text, fnt, x, y, max_w, fill, lh=1.28):
    step = int(fnt.size * lh)
    for ln in wrap(draw, text, fnt, max_w):
        draw.text((x, y), ln, font=fnt, fill=fill)
        y += step
    return y

def draw_hl(draw, parts, fnt, x, y, max_w, lh=1.06):
    flat = []
    for txt, col in parts:
        txt_clean = str(txt).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        txt_typo = typograph(txt_clean)
        for w_ in txt_typo.split(" "):
            w_ = w_.replace("\n", "").strip()
            if w_:
                flat.append((w_, col))

    step = int(fnt.size * lh)
    space = draw.textlength(" ", font=fnt)
    
    layout_lines = []
    cur_line = []
    cur_w = 0

    for w_, col in flat:
        ww = draw.textlength(w_, font=fnt)
        add = ww if not cur_line else ww + space
        if cur_w + add <= max_w or not cur_line:
            cur_line.append((w_, col, ww))
            cur_w += add
        else:
            carry_over = [(w_, col, ww)]
            while cur_line and is_prep_or_orphan(cur_line[-1][0]) and len(cur_line) > 1:
                carry_over.insert(0, cur_line.pop())

            layout_lines.append(cur_line)
            cur_line = carry_over
            cur_w = sum(item[2] for item in cur_line) + space * max(0, len(cur_line) - 1)

    if cur_line:
        layout_lines.append(cur_line)

    for line in layout_lines:
        cx = x
        for w_, col, ww in line:
            draw.text((cx, y), w_, font=fnt, fill=col)
            cx += ww + space
        y += step

    return y

def h_hl(parts, fnt, max_w, lh=1.06):
    flat = []
    for txt, col in parts:
        txt_clean = str(txt).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        txt_typo = typograph(txt_clean)
        for w_ in txt_typo.split(" "):
            w_ = w_.replace("\n", "").strip()
            if w_:
                flat.append((w_, col))

    space = _scratch.textlength(" ", font=fnt)
    layout_lines = []
    cur_line = []
    cur_w = 0

    for w_, col in flat:
        ww = _scratch.textlength(w_, font=fnt)
        add = ww if not cur_line else ww + space
        if cur_w + add <= max_w or not cur_line:
            cur_line.append((w_, col, ww))
            cur_w += add
        else:
            carry_over = [(w_, col, ww)]
            while cur_line and is_prep_or_orphan(cur_line[-1][0]) and len(cur_line) > 1:
                carry_over.insert(0, cur_line.pop())

            layout_lines.append(cur_line)
            cur_line = carry_over
            cur_w = sum(item[2] for item in cur_line) + space * max(0, len(cur_line) - 1)

    if cur_line:
        layout_lines.append(cur_line)

    return len(layout_lines) * int(fnt.size * lh)

def h_par(text, fnt, max_w, lh=1.28):
    return len(wrap(_scratch, text, fnt, max_w)) * int(fnt.size * lh)

# ---------- глубина ----------
def orb(img, cx, cy, r, color, alpha=255, blur=90):
    lay = Image.new("RGBA", (W, H), (0,0,0,0))
    ImageDraw.Draw(lay).ellipse([cx-r, cy-r, cx+r, cy+r], fill=color+(alpha,))
    lay = lay.filter(ImageFilter.GaussianBlur(blur))
    img.alpha_composite(lay)

def shadow(img, box, radius, blur=28, alpha=42, dy=14, color=(23,44,84)):
    lay = Image.new("RGBA", (W, H), (0,0,0,0))
    x0,y0,x1,y1 = box
    ImageDraw.Draw(lay).rounded_rectangle([x0, y0+dy, x1, y1+dy], radius=radius, fill=color+(alpha,))
    img.alpha_composite(lay.filter(ImageFilter.GaussianBlur(blur)))

def card(img, box, radius, fill, outline=None, sh=True, **kw):
    if sh:
        shadow(img, box, radius, **kw)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, radius=radius, fill=fill+(255,) if len(fill)==3 else fill,
                        outline=outline+(255,) if outline else None, width=2 if outline else 0)

# ---------- визуальные блоки ----------
def footer(img, i, total, dark, meta=True, theme=None):
    d = ImageDraw.Draw(img)
    handle = theme.handle if theme else "@username"
    primary = theme.PRIMARY if theme else (47, 128, 237)
    ink = theme.INK if theme else (18, 25, 40)
    white = theme.WHITE if theme else (255, 255, 255)
    line = theme.LINE if theme else (230, 236, 245)
    gray_light = theme.GRAY_LIGHT if theme else (163, 172, 187)
    
    d.rounded_rectangle([PAD, FOOT_Y+14, PAD+50, FOOT_Y+19], radius=3,
                        fill=(white if dark else primary))
    f_handle = theme.font(BOLD, 27) if theme else _scratch.font
    d.text((PAD, FOOT_Y + 30), handle, font=f_handle,
           fill=(white+(240,)) if dark else ink+(255,))
    if not meta:
        return
    f_ = theme.font(BOLD, 22) if theme else _scratch.font
    t = f"{i}/{total}"
    tw = d.textlength(t, font=f_)
    bw = 132
    bx = W - PAD - bw
    by = FOOT_Y + 46
    d.rounded_rectangle([bx, by, bx+bw, by+6], radius=3,
                        fill=(255,255,255,70) if dark else line+(255,))
    d.rounded_rectangle([bx, by, bx+bw*i/total, by+6], radius=3, fill=(white if dark else primary))
    d.text((W-PAD-tw, FOOT_Y + 10), t, font=f_,
           fill=(255,255,255,220) if dark else gray_light+(255,))

def pill(img, text, x, y, dark, theme=None):
    d = ImageDraw.Draw(img)
    f_ = theme.font(HEAVY, 21) if theme else _scratch.font
    tw = d.textlength(text.upper(), font=f_)
    w_, h_ = tw + 48, 54
    soft = theme.PRIMARY_SOFT if theme else (234, 242, 254)
    dark_col = theme.PRIMARY_DARK if theme else (31, 95, 196)
    d.rounded_rectangle([x, y, x+w_, y+h_], radius=27,
                        fill=(255,255,255,235) if dark else soft+(255,))
    d.text((x+24, y+16), text.upper(), font=f_, fill=dark_col)
    return y + h_

def check(img, x, y, s, dark=False, theme=None):
    d = ImageDraw.Draw(img)
    primary = theme.PRIMARY if theme else (47, 128, 237)
    primary_dark = theme.PRIMARY_DARK if theme else (31, 95, 196)
    white = theme.WHITE if theme else (255, 255, 255)
    
    if not dark:
        shadow(img, [x, y, x+s, y+s], 13, blur=12, alpha=60, dy=5, color=primary)
        d = ImageDraw.Draw(img)
    d.rounded_rectangle([x, y, x+s, y+s], radius=13, fill=(white if dark else primary)+(255,))
    c = primary_dark if dark else white
    p = s*0.27
    d.line([(x+p, y+s*0.52), (x+s*0.43, y+s-p*0.95), (x+s-p*0.8, y+p*0.95)],
           fill=c+(255,), width=max(3, s//9), joint="curve")

def numbox(img, x, y, s, n, theme=None):
    primary = theme.PRIMARY if theme else (47, 128, 237)
    white = theme.WHITE if theme else (255, 255, 255)
    shadow(img, [x, y, x+s, y+s], 13, blur=12, alpha=55, dy=5, color=primary)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([x, y, x+s, y+s], radius=13, fill=primary+(255,))
    f_ = theme.font(HEAVY, int(s*0.5)) if theme else _scratch.font
    t = str(n)
    tw = d.textlength(t, font=f_)
    bb = f_.getbbox(t)
    d.text((x+(s-tw)/2, y+(s-(bb[3]-bb[1]))/2 - bb[1]), t, font=f_, fill=white+(255,))

def quote_h(text, theme=None):
    f_ = theme.font(BOLD, 33) if theme else _scratch.font
    return int(f_.size*1.34)*len(wrap(_scratch, text, f_, W-PAD*2-118)) + 84 + FOOT_H + 34

def quote_box(img, text, dark, bottom=None, theme=None):
    if bottom is None:
        bottom = PAD + FOOT_H + 34
    f_ = theme.font(BOLD, 33) if theme else _scratch.font
    lines = wrap(_scratch, text, f_, W-PAD*2-118)
    step = int(f_.size*1.34)
    bh = step*len(lines) + 84
    y0 = H - bottom - bh
    box = [PAD, y0, W-PAD, y0+bh]
    
    primary = theme.PRIMARY if theme else (47, 128, 237)
    primary_soft = theme.PRIMARY_SOFT if theme else (234, 242, 254)
    white = theme.WHITE if theme else (255, 255, 255)
    ink = theme.INK if theme else (18, 25, 40)
    
    if dark:
        lay = Image.new("RGBA", (W, H), (0,0,0,0))
        ImageDraw.Draw(lay).rounded_rectangle(box, radius=28, fill=(255,255,255,44))
        img.alpha_composite(lay)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle(box, radius=28, outline=(255,255,255,70), width=2)
        qc, tc = (255,255,255,110), white+(255,)
    else:
        card(img, box, 28, white, blur=26, alpha=34, dy=12)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([PAD, y0, PAD+8, y0+bh], radius=4, fill=primary+(255,))
        qc, tc = primary_soft+(255,), ink+(255,)
    d = ImageDraw.Draw(img)
    fq = theme.font(BLACK_, 96) if theme else _scratch.font
    d.text((PAD+34, y0+6), "\u201C", font=fq, fill=qc)
    yy = y0+42
    for ln in lines:
        d.text((PAD+72, yy), ln, font=f_, fill=tc)
        yy += step
    cq = "\u201D"
    cw = d.textlength(cq, font=fq)
    d.text((W-PAD-34-cw, y0+bh-98), cq, font=fq, fill=qc)
    return y0

# ---------- работа с фото ----------
def get_photo_path(photo_spec=None, assets_dir=None, default_photo="photo.jpg"):
    if photo_spec and os.path.isabs(str(photo_spec)) and os.path.exists(str(photo_spec)):
        return str(photo_spec)
        
    search_dirs = []
    if assets_dir:
        search_dirs.append(os.path.join(assets_dir, "photos"))
        search_dirs.append(assets_dir)
        
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    search_dirs.append(os.path.join(base_dir, "assets", "photos"))
    search_dirs.append(os.path.join(base_dir, "assets"))

    if photo_spec:
        for d in search_dirs:
            p = os.path.join(d, str(photo_spec))
            if os.path.exists(p):
                return p

    for d in search_dirs:
        if os.path.isdir(d):
            exts = (".jpg", ".jpeg", ".png", ".webp")
            files = sorted([os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(exts)])
            if files:
                if isinstance(photo_spec, int):
                    return files[photo_spec % len(files)]
                return files[0]

    return None

def has_photo(photo_spec=None, assets_dir=None, default_photo="photo.jpg"):
    return get_photo_path(photo_spec, assets_dir, default_photo) is not None

def photo_slot(img, box, radius, shadow_on=True, top_bias=0.0, photo_spec=None, assets_dir=None, default_photo="photo.jpg"):
    p = get_photo_path(photo_spec, assets_dir, default_photo)
    if not p:
        return False
    x0,y0,x1,y1 = box
    bw, bh = int(x1-x0), int(y1-y0)
    im = Image.open(p).convert("RGB")
    s = max(bw/im.width, bh/im.height)
    im = im.resize((max(int(im.width*s)+1, bw), max(int(im.height*s)+1, bh)), Image.LANCZOS)
    ox = (im.width-bw)//2
    oy = int((im.height-bh)*top_bias)
    im = im.crop((ox, oy, ox+bw, oy+bh))
    if shadow_on:
        shadow(img, box, radius, blur=34, alpha=95, dy=18, color=(4,20,54))
    m = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(m).rounded_rectangle([0,0,bw,bh], radius=radius, fill=255)
    img.paste(im, (int(x0),int(y0)), m)
    return True

def photo_bleed_right(img, width=470, feather=170, top_bias=0.0, x_bias=0.5, photo_spec=None, assets_dir=None, default_photo="photo.jpg"):
    p = get_photo_path(photo_spec, assets_dir, default_photo)
    if not p:
        return False
    im = Image.open(p).convert("RGB")
    s = max(width/im.width, H/im.height)
    im = im.resize((max(int(im.width*s)+1, width), max(int(im.height*s)+1, H)), Image.LANCZOS)
    ox = int((im.width-width)*x_bias)
    oy = int((im.height-H)*top_bias)
    im = im.crop((ox, oy, ox+width, oy+H))
    m = Image.new("L", (width, H), 255)
    md = ImageDraw.Draw(m)
    for i in range(feather):
        md.line([(i,0),(i,H)], fill=int(255*i/feather))
    img.paste(im, (W-width, 0), m)
    return True
