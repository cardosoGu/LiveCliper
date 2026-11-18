import asyncio

from liveclips.control.auto import AutoController
from liveclips.control.runtime import RuntimeSettings
from liveclips.core.config import load_settings


def make(db, found=None, **env):
    rt = RuntimeSettings(db, load_settings({"TWITCH_GAME_NAME": "Just Chatting, Nada", **env}))
    started, stopped, alerts = [], [], []
    found = {"Just Chatting": "509658"} if found is None else found

    async def resolve(names):
        return {n: gid for n, gid in found.items() if n in names}

    def make_jobs(game_ids, min_viewers):
        async def forever():
            started.append((tuple(game_ids), min_viewers))
            await asyncio.Event().wait()
        return {"chat": forever, "discovery": forever}

    async def alert(text):
        alerts.append(text)

    auto = AutoController(rt, resolve, make_jobs, alert, on_stop=lambda: stopped.append(1))
    return auto, rt, started, stopped


async def test_apply_starts_jobs_with_resolved_categories(db):
    auto, rt, started, _ = make(db, MIN_VIEWERS="2000")
    msg = await auto.apply()
    await asyncio.sleep(0)
    assert auto.running and "LIGADO" in auto.status_line()
    assert started == [(("509658",), 2000)] * 2
    assert auto.missing == ["Nada"] and "Nada" in msg
    await auto.stop()


async def test_turning_off_stops_everything(db):
    auto, rt, started, stopped = make(db)
    await auto.apply()
    rt.set_auto_on(False)
    msg = await auto.apply()
    assert not auto.running and "DESLIGADO" in auto.status_line()
    assert stopped and "desligado" in msg.lower()


async def test_apply_restarts_with_new_categories(db):
    auto, rt, started, _ = make(db, found={"Just Chatting": "1", "GTA V": "2"})
    await auto.apply()
    await asyncio.sleep(0)
    rt.set_categories(["GTA V"])
    await auto.apply()
    await asyncio.sleep(0)
    assert started[-1] == (("2",), rt.min_viewers())
    await auto.stop()


async def test_no_category_found_does_not_start(db):
    auto, rt, started, _ = make(db, found={})
    msg = await auto.apply()
    assert not auto.running and "nenhuma categoria" in msg.lower()


async def test_ensure_running_retries_until_twitch_answers(db):
    auto, rt, started, _ = make(db)
    real_resolve, calls, sleeps = auto.resolve, [], []

    async def flaky(names):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("Twitch fora do ar")
        return await real_resolve(names)

    async def fake_sleep(s):
        sleeps.append(s)

    auto.resolve = flaky
    msg = await auto.ensure_running(sleep=fake_sleep)
    assert auto.running and "ligado" in msg.lower()
    assert sleeps == [30] and len(calls) == 2
    await auto.stop()


async def test_ensure_running_returns_when_auto_is_off(db):
    auto, rt, *_ = make(db)
    rt.set_auto_on(False)

    async def no_sleep(s):
        raise AssertionError("não deveria esperar")

    assert await auto.ensure_running(sleep=no_sleep) is None
    assert not auto.running
