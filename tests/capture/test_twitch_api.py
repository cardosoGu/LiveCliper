import httpx
import pytest
import respx

from liveclips.capture.twitch.api import HELIX, OAUTH, TwitchAPI, TwitchError


def token_handler(request: httpx.Request) -> httpx.Response:
    if b"grant_type=refresh_token" in request.content:
        return httpx.Response(200, json={"access_token": "user", "refresh_token": "rt2"})
    return httpx.Response(200, json={"access_token": "app"})


@respx.mock
async def test_get_streams_paginates_with_language():
    respx.post(OAUTH).mock(side_effect=token_handler)
    route = respx.get(f"{HELIX}/streams").mock(side_effect=[
        httpx.Response(200, json={"data": [{"user_login": "a"}], "pagination": {"cursor": "c1"}}),
        httpx.Response(200, json={"data": [{"user_login": "b"}], "pagination": {}}),
    ])
    async with httpx.AsyncClient() as http:
        api = TwitchAPI("cid", "sec", "rt", http)
        streams = await api.get_streams("g1")
    assert [s["user_login"] for s in streams] == ["a", "b"]
    assert route.calls[0].request.url.params["language"] == "pt"
    assert route.calls[1].request.url.params["after"] == "c1"


@respx.mock
async def test_create_clip_uses_user_token_and_reports_rotation():
    respx.post(OAUTH).mock(side_effect=token_handler)
    route = respx.post(f"{HELIX}/clips").mock(
        return_value=httpx.Response(202, json={"data": [{"id": "clip1", "edit_url": "x"}]}))
    rotated = []
    async with httpx.AsyncClient() as http:
        api = TwitchAPI("cid", "sec", "rt", http, on_refresh=rotated.append)
        assert await api.create_clip("123") == "clip1"
    assert route.calls[0].request.headers["Authorization"] == "Bearer user"
    assert route.calls[0].request.url.params["broadcaster_id"] == "123"
    assert rotated == ["rt2"]


@respx.mock
async def test_retries_once_after_401():
    tokens = respx.post(OAUTH).mock(side_effect=token_handler)
    respx.get(f"{HELIX}/games").mock(side_effect=[
        httpx.Response(401, json={}),
        httpx.Response(200, json={"data": [{"id": "999"}]}),
    ])
    async with httpx.AsyncClient() as http:
        api = TwitchAPI("cid", "sec", "rt", http)
        assert await api.get_game_id("Grand Theft Auto VI") == "999"
    assert tokens.call_count == 2


@respx.mock
async def test_error_status_is_raised():
    respx.post(OAUTH).mock(side_effect=token_handler)
    respx.post(f"{HELIX}/clips").mock(return_value=httpx.Response(403, text="forbidden"))
    async with httpx.AsyncClient() as http:
        api = TwitchAPI("cid", "sec", "rt", http)
        with pytest.raises(TwitchError) as exc:
            await api.create_clip("123")
    assert exc.value.status == 403


@respx.mock
async def test_get_user():
    respx.post(OAUTH).mock(side_effect=token_handler)
    route = respx.get(f"{HELIX}/users").mock(side_effect=[
        httpx.Response(200, json={"data": [{"id": "9", "login": "gaules"}]}),
        httpx.Response(200, json={"data": []}),
    ])
    async with httpx.AsyncClient() as http:
        api = TwitchAPI("cid", "sec", "rt", http)
        assert (await api.get_user("Gaules"))["id"] == "9"
        assert await api.get_user("ninguem") is None
    assert route.calls[0].request.url.params["login"] == "gaules"


@respx.mock
async def test_several_categories_in_one_request():
    respx.post(OAUTH).mock(side_effect=token_handler)
    games = respx.get(f"{HELIX}/games").mock(return_value=httpx.Response(200, json={"data": [
        {"id": "509658", "name": "Just Chatting"}, {"id": "27471", "name": "Minecraft"}]}))
    streams = respx.get(f"{HELIX}/streams").mock(
        return_value=httpx.Response(200, json={"data": [], "pagination": {}}))
    async with httpx.AsyncClient() as http:
        api = TwitchAPI("cid", "sec", "rt", http)
        found = await api.get_game_ids(["Just Chatting", "Minecraft", "Jogo Que Não Existe"])
        await api.get_streams(list(found.values()))
    assert found == {"Just Chatting": "509658", "Minecraft": "27471"}
    assert games.calls[0].request.url.params.get_list("name") == [
        "Just Chatting", "Minecraft", "Jogo Que Não Existe"]
    assert streams.calls[0].request.url.params.get_list("game_id") == ["509658", "27471"]
