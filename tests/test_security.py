"""Барьеры безопасности веб-интерфейса (security-ревью 05.11.2026, PR #1).

1. Токен доступа живёт в cookie, которую JS прочитать не может (HttpOnly) и которая
   уходит только по HTTPS (Secure): иначе любой XSS на странице уносит AUTH_TOKEN.
2. Ни одна подстановка `${...}` в шаблонных строках интерфейса не попадает в HTML
   сырой: данные из внешних источников (подпись поста Instagram/Threads → заголовок в
   очереди, ответы VK API, ошибки) обязаны проходить через esc() / safeUrl().
   Проверка по ФОРМЕ скрипта, а не по списку мест — новая подстановка попадёт под неё сама.
3. esc()/safeUrl() из самой страницы выполняются в node на враждебных строках.
"""
import os
import re
import shutil
import subprocess

import pytest

os.environ.setdefault("AUTH_TOKEN", "test-token-123")

from fastapi.testclient import TestClient  # noqa: E402

from web import app as webapp  # noqa: E402

TOKEN = os.environ["AUTH_TOKEN"]


def _script() -> str:
    html = webapp.HTML_CONTENT
    start = html.index("<script>", html.index("<body"))
    return html[start: html.index("</script>", start)]


def _cookie_header(resp) -> str:
    vals = [v for k, v in resp.headers.multi_items() if k.lower() == "set-cookie"]
    hdr = "; ".join(v for v in vals if v.startswith("karusel_token="))
    assert hdr, "karusel_token не выставлен"
    return hdr.lower()


@pytest.fixture()
def client():
    return TestClient(webapp.app, base_url="https://testserver")


def test_cookie_from_query_token_is_httponly_secure(client):
    h = _cookie_header(client.get(f"/?token={TOKEN}"))
    assert "httponly" in h
    assert "secure" in h
    assert "samesite=lax" in h


def test_cookie_from_login_is_httponly_secure(client):
    h = _cookie_header(client.post("/api/auth/login", json={"token": TOKEN}))
    assert "httponly" in h
    assert "secure" in h
    assert "samesite=lax" in h


def test_frontend_does_not_read_cookie():
    assert "document.cookie" not in webapp.HTML_CONTENT


def test_protected_path_requires_token(client):
    assert client.get("/api/__accept_probe").status_code == 401


# --- подстановки в шаблонных строках --------------------------------------------

INNER = re.compile(r"\$\{([^{}`]*)\}")  # самая внутренняя подстановка, без вложенных шаблонов


def test_every_innermost_interpolation_is_escaped():
    raw = []
    for m in INNER.finditer(_script()):
        expr = m.group(1).strip()
        if re.match(r"^(esc|safeUrl|jsArg)\(", expr):
            continue
        raw.append(expr)
    assert not raw, f"сырые подстановки в HTML ({len(raw)}): {raw[:10]}"


def test_url_attributes_use_safe_url():
    bad = re.findall(r"""(?:href|src)=["']\$\{(?!safeUrl\()([^}]*)\}""", _script())
    assert not bad, f"href/src без safeUrl: {bad}"


def test_inline_handlers_use_js_arg():
    # esc() внутри onclick="f('...')" не защищает: &#39; раскодируется в ' до разбора JS
    bad = re.findall(r"""\bon[a-z]+="[^"]*\$\{(?!jsArg\()([^}]*)\}""", _script())
    assert not bad, f"подстановки в on*-обработчиках без jsArg: {bad}"


@pytest.mark.skipif(shutil.which("node") is None, reason="нет node")
def test_esc_and_safe_url_behaviour():
    s = _script()
    a = s.find("function esc(")
    b = s.find("function safeUrl(")
    assert a >= 0 and b > a, "esc()/safeUrl() не найдены в скрипте страницы"
    c = s.index("\n    }\n", b) + len("\n    }\n")
    j = s.find("function jsArg(")
    assert j >= 0, "jsArg() не найдена"
    jsarg = s[j: s.index("\n    }\n", j) + len("\n    }\n")]
    js = s[a:c] + jsarg + r"""
const out = {
  esc: esc('<img src=x onerror="alert(1)">\'`&'),
  escNull: esc(null),
  urlJs: safeUrl('javascript:alert(1)'),
  urlData: safeUrl('data:text/html,<b>'),
  urlProto: safeUrl('//evil.example/x'),
  urlHttps: safeUrl('https://vk.com/wall1_2?a=1&b=2'),
  urlRel: safeUrl('/preview/abc/1.png'),
  jsArg: jsArg("x');alert(1);//"),
};
console.log(JSON.stringify(out));
"""
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    import json
    o = json.loads(r.stdout)
    assert "<" not in o["esc"] and '"' not in o["esc"] and "'" not in o["esc"] and "`" not in o["esc"]
    assert o["esc"].startswith("&lt;img")
    assert o["escNull"] == ""
    assert o["urlJs"] == "#" and o["urlData"] == "#" and o["urlProto"] == "#"
    assert o["urlHttps"] == "https://vk.com/wall1_2?a=1&amp;b=2"
    assert o["urlRel"] == "/preview/abc/1.png"
    assert o["jsArg"] == "&quot;x&#39;);alert(1);//&quot;"
