"""Gera o TWITCH_BOT_REFRESH_TOKEN (escopo clips:edit) para a conta bot.

Uso (no PC, logado no navegador com a CONTA BOT da Twitch):
    uv run python scripts/twitch_oauth.py <client_id> <client_secret>

Pré-requisito: no app em dev.twitch.tv, cadastre a OAuth Redirect URL http://localhost:3000
"""
from __future__ import annotations

import secrets
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

REDIRECT = "http://localhost:3000"


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    client_id, client_secret = sys.argv[1], sys.argv[2]
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

    print("Abrindo o navegador para autorizar a conta bot...\n", url, "\n")
    webbrowser.open(url)
    HTTPServer(("localhost", 3000), Handler).handle_request()

    if result.get("state") != state or "code" not in result:
        sys.exit(f"Autorização falhou: {result}")
    r = httpx.post("https://id.twitch.tv/oauth2/token", data={
        "client_id": client_id, "client_secret": client_secret, "code": result["code"],
        "grant_type": "authorization_code", "redirect_uri": REDIRECT,
    })
    r.raise_for_status()
    print("Coloque no .env:\nTWITCH_BOT_REFRESH_TOKEN=" + r.json()["refresh_token"])


if __name__ == "__main__":
    main()
