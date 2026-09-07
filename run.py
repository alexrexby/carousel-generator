#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Универсальный генератор карточек каруселей для Instagram / соцсетей.
Размер: 1080x1350 (4:5).

Примеры использования:
    python run.py list                           # Список всех доступных шаблонов
    python run.py themes                         # Список доступных тем оформления
    python run.py render sample_deck             # Собрать карусель decks/sample_deck.json
    python run.py render --json my_deck.json --theme dark
    python run.py serve                          # Запустить веб-интерфейс
"""
import os
import sys
import json
import argparse
from src.theme import Theme
from src.renderer import render_deck

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def get_json_decks():
    decks_dir = os.path.join(BASE_DIR, "decks")
    decks = {}
    if os.path.exists(decks_dir):
        for f in sorted(os.listdir(decks_dir)):
            if f.endswith(".json"):
                name = os.path.splitext(f)[0]
                p = os.path.join(decks_dir, f)
                try:
                    with open(p, "r", encoding="utf-8") as fp:
                        decks[name] = (p, json.load(fp))
                except Exception:
                    pass
    return decks

def cmd_list(args):
    decks = get_json_decks()
    print("==================================================")
    print("ДОСТУПНЫЕ ШАБЛОНЫ КАРУСЕЛЕЙ (decks/*.json):")
    print("==================================================")
    for name, (path, slides) in decks.items():
        title = ""
        if slides and "title" in slides[0]:
            t = slides[0]["title"]
            if isinstance(t, str): title = t
            elif isinstance(t, list): title = " ".join(item[0] if isinstance(item, (list, tuple)) else str(item) for item in t)
        print(f"  • {name:<20} ({len(slides)} слайдов) - {title[:50]}")

def cmd_themes(args):
    config_dir = os.path.join(BASE_DIR, "config")
    print("==================================================")
    print("ДОСТУПНЫЕ ТЕМЫ ОФОРМЛЕНИЯ:")
    print("==================================================")
    for f in sorted(os.listdir(config_dir)):
        if f.endswith(".json"):
            name = f[:-5]
            try:
                with open(os.path.join(config_dir, f), "r", encoding="utf-8") as fp:
                    d = json.load(fp)
                    h = d.get("handle", "")
                print(f"  • {name:<12} (никнейм: {h})")
            except Exception:
                print(f"  • {name}")

def cmd_render(args):
    theme_name = args.theme or "default"
    theme = Theme.load(theme_name)
    assets_dir = os.path.join(BASE_DIR, "assets")
    output_base = args.out or os.path.join(BASE_DIR, "output")
    
    # 1. Custom JSON render
    if args.json:
        json_path = args.json
        if not os.path.isabs(json_path):
            json_path = os.path.join(BASE_DIR, json_path)
            
        if not os.path.isfile(json_path):
            print(f"Ошибка: JSON-файл не найден: {json_path}")
            sys.exit(1)
            
        with open(json_path, "r", encoding="utf-8") as f:
            slides = json.load(f)
            
        base_name = os.path.splitext(os.path.basename(json_path))[0]
        out_dir = os.path.join(output_base, f"karusel_{base_name}")
        total = render_deck(slides, out_dir, theme, assets_dir, make_preview=not args.no_preview)
        print(f"✓ Готово: {total} карточек собрано в {out_dir}")
        print(f"✓ Контакт-лист: {os.path.join(out_dir, 'preview.png')}")
        return

    # 2. Render from decks folder by name
    decks = get_json_decks()
    if args.all:
        names = list(decks.keys())
    elif args.deck:
        names = [args.deck]
    else:
        print("Укажите имя шаблона, --all или --json <path>. См. 'python run.py list'")
        sys.exit(1)

    for name in names:
        if name not in decks:
            # Maybe path was passed?
            if os.path.isfile(name):
                with open(name, "r", encoding="utf-8") as f:
                    slides = json.load(f)
            else:
                print(f"Внимание: шаблон '{name}' не найден в decks/. Проверьте 'python run.py list'")
                continue
        else:
            _, slides = decks[name]
            
        out_dir = os.path.join(output_base, f"karusel_{name}")
        total = render_deck(slides, out_dir, theme, assets_dir, make_preview=not args.no_preview)
        print(f"✓ [{name}]: {total} карточек собрано в {out_dir}")
        print(f"  Превью: {os.path.join(out_dir, 'preview.png')}")

def cmd_serve(args):
    import uvicorn
    port = args.port or 8007
    host = args.host or "0.0.0.0"
    print(f"Запуск веб-сервера на http://{host}:{port}")
    uvicorn.run("web.app:app", host=host, port=port, reload=args.reload)

def main():
    parser = argparse.ArgumentParser(description="Универсальный генератор каруселей 1080x1350")
    subparsers = parser.add_subparsers(dest="command")

    # Command: list
    subparsers.add_parser("list", help="Список доступных колод")

    # Command: themes
    subparsers.add_parser("themes", help="Список доступных тем")

    # Command: render
    p_render = subparsers.add_parser("render", help="Собрать карусель")
    p_render.add_argument("deck", nargs="?", help="Имя колоды (например, sample_deck, content_strategy)")
    p_render.add_argument("--all", action="store_true", help="Собрать все шаблоны")
    p_render.add_argument("--json", help="Путь к внешнему JSON-файлу со слайдами")
    p_render.add_argument("--theme", help="Имя темы или путь к JSON-файлу темы")
    p_render.add_argument("--out", help="Папка вывода")
    p_render.add_argument("--no-preview", action="store_true", help="Не создавать контакт-лист preview.png")

    # Command: serve
    p_serve = subparsers.add_parser("serve", help="Запустить веб-интерфейс")
    p_serve.add_argument("--port", type=int, default=8007, help="Порт сервера (по умолчанию 8007)")
    p_serve.add_argument("--host", default="0.0.0.0", help="Хост сервера")
    p_serve.add_argument("--reload", action="store_true", help="Авто-перезагрузка при изменениях")

    args = parser.parse_args()
    if args.command == "list":
        cmd_list(args)
    elif args.command == "themes":
        cmd_themes(args)
    elif args.command == "render":
        cmd_render(args)
    elif args.command == "serve":
        cmd_serve(args)
    else:
        if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
            args.deck = sys.argv[1]
            args.all = False
            args.json = None
            args.theme = None
            args.out = None
            args.no_preview = False
            cmd_render(args)
        else:
            parser.print_help()

if __name__ == "__main__":
    main()
