"""Cliente mínimo da Twitch Helix (token de app + token de usuário do bot)."""
from __future__ import annotations

from datetime import datetime
from typing import Callable

import httpx

HELIX = "https://api.twitch.tv/helix"
OAUTH = "https://id.twitch.tv/oauth2/token"


class TwitchError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"Twitch {status}: {body[:300]}")
        self.status = status
        self.body = body


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class TwitchAPI:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str,
                 http: httpx.AsyncClient, on_refresh: Callable[[str], None] | None = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.http = http
        self.on_refresh = on_refresh
        self._app_token: str | None = None
        self._user_token: str | None = None

    async def _app_auth(self) -> str:
        if self._app_token is None:
            # no corpo, nunca na URL: URLs acabam em logs
            r = await self.http.post(OAUTH, data={
                "client_id": self.client_id, "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            })
            if r.status_code != 200:
                raise TwitchError(r.status_code, r.text)
            self._app_token = r.json()["access_token"]
        return self._app_token

    async def _user_auth(self) -> str:
        if self._user_token is None:
            if not self.refresh_token:
                raise TwitchError(401, "TWITCH_BOT_REFRESH_TOKEN não configurado")
            r = await self.http.post(OAUTH, data={
                "grant_type": "refresh_token", "refresh_token": self.refresh_token,
                "client_id": self.client_id, "client_secret": self.client_secret,
            })
            if r.status_code != 200:
                raise TwitchError(r.status_code, r.text)
            j = r.json()
            self._user_token = j["access_token"]
            new_refresh = j.get("refresh_token")
            if new_refresh and new_refresh != self.refresh_token:
                self.refresh_token = new_refresh
                if self.on_refresh:
                    self.on_refresh(new_refresh)
        return self._user_token

    async def _request(self, method: str, path: str, *, user: bool = False,
                       params: dict | None = None) -> dict:
        for attempt in range(2):
            token = await (self._user_auth() if user else self._app_auth())
            r = await self.http.request(method, HELIX + path, params=params, headers={
                "Client-Id": self.client_id, "Authorization": f"Bearer {token}",
            })
            if r.status_code == 401 and attempt == 0:
                if user:
                    self._user_token = None
                else:
                    self._app_token = None
                continue
            if r.status_code >= 400:
                raise TwitchError(r.status_code, r.text)
            return r.json()
        raise TwitchError(401, "não autorizado após renovar o token")

    async def get_game_id(self, name: str) -> str:
        j = await self._request("GET", "/games", params={"name": name})
        if not j.get("data"):
            raise TwitchError(404, f"jogo não encontrado: {name}")
        return j["data"][0]["id"]

    async def get_game_ids(self, names: list[str]) -> dict[str, str]:
        """{nome: id} das categorias que existem (uma requisição para até 100 nomes).
        Nomes que a Twitch não conhece ficam de fora."""
        j = await self._request("GET", "/games", params={"name": names[:100]})
        return {g["name"]: g["id"] for g in j.get("data", [])}

    async def get_streams(self, game_id: str | list[str], language: str = "pt",
                          max_items: int = 500) -> list[dict]:
        """Lives (mais assistidas primeiro) de uma ou várias categorias juntas."""
        out: list[dict] = []
        cursor = None
        while len(out) < max_items:
            params = {"game_id": game_id, "language": language, "first": 100}
            if cursor:
                params["after"] = cursor
            j = await self._request("GET", "/streams", params=params)
            data = j.get("data", [])
            out.extend(data)
            cursor = j.get("pagination", {}).get("cursor")
            if not cursor or not data:
                break
        return out[:max_items]

    async def get_clips(self, game_id: str, started_at: datetime,
                        ended_at: datetime) -> list[dict]:
        j = await self._request("GET", "/clips", params={
            "game_id": game_id, "started_at": _iso(started_at),
            "ended_at": _iso(ended_at), "first": 100,
        })
        return j.get("data", [])

    async def get_broadcaster_clips(self, broadcaster_id: str, started_at: datetime,
                                    ended_at: datetime) -> list[dict]:
        j = await self._request("GET", "/clips", params={
            "broadcaster_id": broadcaster_id, "started_at": _iso(started_at),
            "ended_at": _iso(ended_at), "first": 100,
        })
        return j.get("data", [])

    async def create_clip(self, broadcaster_id: str) -> str:
        j = await self._request("POST", "/clips", user=True,
                                params={"broadcaster_id": broadcaster_id})
        return j["data"][0]["id"]

    async def get_user(self, login: str) -> dict | None:
        j = await self._request("GET", "/users", params={"login": login.lower()})
        data = j.get("data", [])
        return data[0] if data else None

    async def get_clip(self, clip_id: str) -> dict | None:
        j = await self._request("GET", "/clips", params={"id": clip_id})
        data = j.get("data", [])
        return data[0] if data else None
