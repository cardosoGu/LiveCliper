import httpx
import respx

from liveclips.ondemand.twitch import TwitchAPI, parse_twitch_duration

TOKEN = "https://id.twitch.tv/oauth2/token"
HELIX = "https://api.twitch.tv/helix"


def test_parse_twitch_duration():
    assert parse_twitch_duration("2h13m5s") == 7985.0
    assert parse_twitch_duration("45m") == 2700.0
    assert parse_twitch_duration("9s") == 9.0


@respx.mock
async def test_user_and_live_vod_with_app_token():
    respx.post(TOKEN).mock(return_value=httpx.Response(200, json={"access_token": "a",
                                                                 "expires_in": 3600}))
    respx.get(f"{HELIX}/users").mock(return_value=httpx.Response(
        200, json={"data": [{"id": "42", "login": "gaules"}]}))
    videos = respx.get(f"{HELIX}/videos").mock(return_value=httpx.Response(
        200, json={"data": [{"id": "999", "duration": "1h0m0s"}]}))
    async with httpx.AsyncClient() as http:
        api = TwitchAPI("cid", "secret", http)
        assert (await api.get_user("gaules"))["id"] == "42"
        assert (await api.get_live_vod("42"))["id"] == "999"
    q = videos.calls[0].request.url.params
    assert q["user_id"] == "42" and q["type"] == "archive" and q["first"] == "1"


@respx.mock
async def test_401_renews_token_once():
    token = respx.post(TOKEN).mock(return_value=httpx.Response(
        200, json={"access_token": "a", "expires_in": 3600}))
    respx.get(f"{HELIX}/users").mock(side_effect=[
        httpx.Response(401, json={"message": "invalid"}),
        httpx.Response(200, json={"data": []})])
    async with httpx.AsyncClient() as http:
        assert await TwitchAPI("cid", "secret", http).get_user("ninguem") is None
    assert token.call_count == 2
