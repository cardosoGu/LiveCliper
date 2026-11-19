"""API da Twitch só para o sob demanda: achar o VOD de uma live em andamento.

Usa só o token de app (client credentials); nada de token de usuário/bot.
"""
from __future__ import annotations

import re

import httpx

HELIX = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_DUR = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")


class TwitchError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"Twitch {status}: {body[:200]}")
        self.status = status


def parse_twitch_duration(text: str) -> float:
    m = _DUR.fullmatch(text.strip())
    if not m:
        return 0.0
    h, mi, s = (int(x or 0) for x in m.groups())
    return float(h * 3600 + mi * 60 + s)


class TwitchAPI:
    def __init__(self, client_id: str, client_secret: str, http: httpx.AsyncClient):
        self.client_id = client_id
        self.client_secret = client_secret
        self.http = http
        self._token: str | None = None

    async def _app_token(self) -> str:
        r = await self.http.post(TOKEN_URL, data={
            "client_id": self.client_id, "client_secret": self.client_secret,
            "grant_type": "client_credentials"})
        if r.status_code != 200:
            raise TwitchError(r.status_code, r.text)
        self._token = r.json()["access_token"]
        return self._token

    async def _get(self, path: str, params: dict) -> dict:
        for attempt in (1, 2):
            token = self._token or await self._app_token()
            r = await self.http.get(HELIX + path, params=params, headers={
                "Client-Id": self.client_id, "Authorization": f"Bearer {token}"})
            if r.status_code == 401 and attempt == 1:
                self._token = None  # token vencido: pede outro e repete uma vez
                continue
            if r.status_code != 200:
                raise TwitchError(r.status_code, r.text)
            return r.json()
        raise TwitchError(401, "token recusado duas vezes")

    async def get_user(self, login: str) -> dict | None:
        data = (await self._get("/users", {"login": login})).get("data", [])
        return data[0] if data else None

    async def get_live_vod(self, user_id: str) -> dict | None:
        data = (await self._get("/videos", {"user_id": user_id, "type": "archive",
                                             "first": "1"})).get("data", [])
        return data[0] if data else None
