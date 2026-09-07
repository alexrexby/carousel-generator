# -*- coding: utf-8 -*-
import os
import json
from typing import Optional
from PIL import ImageFont

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MAC_FONTS = {
    "Black": "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "Heavy": "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "Bold": "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "Semibold": "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "Medium": "/System/Library/Fonts/Supplemental/Arial.ttf",
    "Regular": "/System/Library/Fonts/Supplemental/Arial.ttf"
}

class Theme:
    def __init__(self, data=None):
        data = data or {}
        self.name = data.get("name", "default")
        self.handle = data.get("handle", "@username")
        
        c = data.get("colors", {})
        self.PRIMARY = tuple(c.get("primary", [47, 128, 237]))
        self.PRIMARY_DARK = tuple(c.get("primary_dark", [31, 95, 196]))
        self.PRIMARY_DEEP = tuple(c.get("primary_deep", [14, 52, 120]))
        self.PRIMARY_NIGHT = tuple(c.get("primary_night", [9, 34, 82]))
        self.PRIMARY_SOFT = tuple(c.get("primary_soft", [234, 242, 254]))
        self.PRIMARY_TINT = tuple(c.get("primary_tint", [245, 249, 255]))
        self.INK = tuple(c.get("ink", [18, 25, 40]))
        self.GRAY = tuple(c.get("gray", [108, 117, 133]))
        self.GRAY_LIGHT = tuple(c.get("gray_light", [163, 172, 187]))
        self.LINE = tuple(c.get("line", [230, 236, 245]))
        self.WHITE = tuple(c.get("white", [255, 255, 255]))
        
        self.default_photo = data.get("default_photo", "photo.jpg")
        self._font_cache = {}

    def font(self, style, size):
        cache_key = (style, size)
        if cache_key in self._font_cache:
            return self._font_cache[cache_key]

        # 1. Custom font in assets/fonts/
        custom_p = os.path.join(BASE_DIR, "assets", "fonts", f"{style}.ttf")
        if os.path.exists(custom_p):
            fnt = ImageFont.truetype(custom_p, size)
            self._font_cache[cache_key] = fnt
            return fnt

        # 2. Linux font path
        linux_p = f"/usr/share/fonts/truetype/lato/Lato-{style}.ttf"
        if os.path.exists(linux_p):
            fnt = ImageFont.truetype(linux_p, size)
            self._font_cache[cache_key] = fnt
            return fnt

        # 3. macOS font path
        mac_p = MAC_FONTS.get(style, "/System/Library/Fonts/Supplemental/Arial.ttf")
        if os.path.exists(mac_p):
            fnt = ImageFont.truetype(mac_p, size)
            self._font_cache[cache_key] = fnt
            return fnt

        # 4. Default
        fnt = ImageFont.load_default()
        self._font_cache[cache_key] = fnt
        return fnt

    @classmethod
    def load(cls, name_or_path="default", custom_handle: Optional[str] = None):
        theme_obj = None
        if not name_or_path:
            name_or_path = "default"
            
        # If direct file path exists
        if os.path.isfile(name_or_path):
            with open(name_or_path, "r", encoding="utf-8") as f:
                theme_obj = cls(json.load(f))
                
        # If in config dir
        if not theme_obj:
            cfg_path = os.path.join(BASE_DIR, "config", f"{name_or_path}.json")
            if os.path.isfile(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    theme_obj = cls(json.load(f))
                
        # Fallback to default
        if not theme_obj:
            default_path = os.path.join(BASE_DIR, "config", "default.json")
            if os.path.isfile(default_path):
                with open(default_path, "r", encoding="utf-8") as f:
                    theme_obj = cls(json.load(f))
                
        if not theme_obj:
            theme_obj = cls()

        if custom_handle and str(custom_handle).strip():
            theme_obj.handle = str(custom_handle).strip()

        return theme_obj
