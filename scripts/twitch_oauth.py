"""Gera o TWITCH_BOT_REFRESH_TOKEN (escopo clips:edit) para a conta bot.

Uso (lê TWITCH_CLIENT_ID e TWITCH_CLIENT_SECRET do .env e grava o token lá):
    uv run python scripts/twitch_oauth.py

Pré-requisitos:
- no app em dev.twitch.tv, cadastre a OAuth Redirect URL http://localhost:3000
- na página de autorização, esteja logado com a CONTA BOT (use janela anônima)
"""
from __future__ import annotations

import re
import secrets
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

REDIRECT = "http://localhost:3000"
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def write_env_value(path: Path, key: str, value: str) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(lambda _: f"{key}={value}", text)
    else:
        text = text.rstrip("\n") + f"\n{key}={value}\n"
    path.write_text(text, encoding="utf-8")


def main() -> None:
    if len(sys.argv) == 3:  # compatibilidade: credenciais por argumento
        client_id, client_secret = sys.argv[1], sys.argv[2]
    else:
        if not ENV_FILE.exists():
            sys.exit(f"Não achei {ENV_FILE}. Copie .env.example para .env primeiro.")
        env = read_env(ENV_FILE)
        client_id = env.get("TWITCH_CLIENT_ID", "")
        client_secret = env.get("TWITCH_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            sys.exit("Preencha TWITCH_CLIENT_ID e TWITCH_CLIENT_SECRET no .env e rode de novo.")
    state = secrets.token_urlsafe(12)
    url = "https://id.twitch.tv/oauth2/authorize?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": REDIRECT, "response_type": "code",
        "scope": "clips:edit", "state": state, "force_verify": "true",
    })
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result.update({k: v[0] for k, v in qs.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Pronto! Pode fechar esta aba e voltar ao terminal.".encode())

        def log_message(self, *args):
            pass

    print("\n1. Abra uma JANELA ANÔNIMA do navegador e entre na Twitch com a CONTA BOT.")
    print("2. Cole este link nessa janela e clique em 'Autorizar':\n")
    print("   " + url + "\n")
    print("Aguardando a autorização (Ctrl+C para cancelar)...")
    if "--abrir" in sys.argv:
        webbrowser.open(url)
    HTTPServer(("localhost", 3000), Handler).handle_request()

    if result.get("state") != state or "code" not in result:
        sys.exit(f"Autorização falhou: {result.get('error_description') or result}")
    r = httpx.post("https://id.twitch.tv/oauth2/token", data={
        "client_id": client_id, "client_secret": client_secret, "code": result["code"],
        "grant_type": "authorization_code", "redirect_uri": REDIRECT,
    })
    if r.status_code != 200:
        sys.exit(f"Troca do código falhou ({r.status_code}): {r.text[:300]}")
    login = ""
    try:
        v = httpx.get("https://id.twitch.tv/oauth2/validate",
                      headers={"Authorization": f"OAuth {r.json()['access_token']}"})
        login = v.json().get("login", "")
    except Exception:
        pass
    if ENV_FILE.exists():
        write_env_value(ENV_FILE, "TWITCH_BOT_REFRESH_TOKEN", r.json()["refresh_token"])
        print(f"\nPronto! TWITCH_BOT_REFRESH_TOKEN gravado no .env"
              + (f" (conta autorizada: {login})." if login else "."))
        print("Confira se essa é a conta BOT, e não a sua pessoal.")
    else:
        print("Coloque no .env:\nTWITCH_BOT_REFRESH_TOKEN=" + r.json()["refresh_token"])


if __name__ == "__main__":
    main()
