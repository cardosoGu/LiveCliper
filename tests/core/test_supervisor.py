from liveclips.core.supervisor import supervise


async def test_restarts_with_backoff_and_alerts():
    calls = {"n": 0}
    alerts, sleeps = [], []

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")

    async def alert(text):
        alerts.append(text)

    async def sleep(s):
        sleeps.append(s)

    await supervise("pipeline", flaky, alert, sleep, max_restarts=2)
    assert calls["n"] == 3
    assert alerts == ["pipeline caiu: RuntimeError: boom"] * 2
    assert sleeps == [5.0, 10.0, 5.0]  # depois de uma saída limpa, a espera volta a 5 s
