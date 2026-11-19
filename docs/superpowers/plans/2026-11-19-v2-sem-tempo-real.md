# V2 · Remover o tempo real e aceitar live em andamento por link

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O Live-Clips passa a ter um só jeito de gerar clipe: o admin cola um link (YouTube, Twitch ou Kick) no canal de links. Todo o clipador em tempo real por pico de chat sai do código, da configuração e da documentação. Link de **live em andamento** deixa de ser recusado: o bot processa do início até o último segundo que já foi ao ar (no máximo as últimas `ONDEMAND_MAX_HOURS` horas).

**Architecture:** Remove-se o pacote `capture/` inteiro, o `AutoController`, o `/testar` (ManualTester), a blocklist e os controles do automático no `#painel`; o `Pipeline` passa a só avaliar candidatos já preparados (os do sob demanda). Para live, um novo `ondemand/source.py` resolve **de onde** baixar e **qual janela** processar: YouTube ao vivo usa `--live-from-start` com `--download-sections`; Twitch ao vivo vira o VOD em andamento (achado pela API da Twitch, que fica só com o token de app); Kick ao vivo é recusada com uma mensagem clara. A transcrição e os sinais trabalham em tempo relativo à janela; os cortes voltam para o tempo absoluto do vídeo.

**Tech Stack:** Python 3.12, yt-dlp (com Deno na imagem), httpx, discord.py 2.x, SQLite, pytest.

## Decisões (combinadas com o usuário em 2026-11-19)

- O tempo real não rendeu clipes (0 com nota ≥ 8 em um dia) e é a parte mais cara de manter: sai **tudo** — código, testes, variáveis do `.env`, `.env.example` e documentação de uso. Os documentos de histórico em `docs/superpowers/` (specs e planos antigos) ficam como estão.
- Entrada só por **link** (vídeo gravado ou live). Upload de arquivo pelo Discord continua fora.
- Live em andamento = "foto até agora": duração conhecida no momento do pedido; para pegar o resto, o admin manda o link de novo.
- Live com mais de `ONDEMAND_MAX_HOURS`: processa **as últimas** N horas e avisa na thread. Vídeo gravado mais longo que o limite continua sendo recusado.
- Chat do replay não é lido em live em andamento (o chat ao vivo do YouTube não termina).
- O banco não perde dados: colunas antigas (`spike_score`, `rank`, `viewers`, …) continuam no esquema; só param de ser usadas.

## Global Constraints

- Testes: `.venv/Scripts/python.exe -m pytest -q` — a suíte inteira passa ao fim de cada tarefa; nenhum teste usa rede.
- Depois de cada tarefa de remoção, `grep -rn` pelos nomes removidos em `src/`, `tests/`, `.env.example`, `docs/run-local.md` e `pyproject.toml` não acha nada (os nomes de cada tarefa estão na própria tarefa).
- Nunca imprimir valores do `.env` (segredos). A limpeza do `.env` real apaga linhas por nome da chave, sem ler os valores para a saída.
- Textos em português do Brasil.
- Commits terminam com uma linha em branco e `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

---

### Task 1: Remover a captura em tempo real

**Files:**
- Delete: `src/liveclips/capture/` (pacote inteiro: `intake.py`, `loop.py`, `ranking.py`, `spike.py`, `kick/`, `twitch/chat.py`, `twitch/clipper.py`, `twitch/discovery.py`, `twitch/public_clips.py`, `twitch/api.py`)
- Delete: `src/liveclips/control/auto.py`, `src/liveclips/control/manual_test.py`
- Delete: `tests/capture/` (pasta inteira), `tests/control/test_auto.py`, `tests/control/test_manual_test.py`, `tests/fixtures/kick_clips.json`, `scripts/twitch_oauth.py`
- Create: `src/liveclips/ondemand/twitch.py` (API da Twitch só com token de app), `tests/ondemand/test_twitch.py`
- Modify: `src/liveclips/main.py` (tirar descoberta, chat, clipper, coletores, AutoController, ManualTester, `auto_boot`; o `TwitchAPI` novo é criado só se `TWITCH_CLIENT_ID` e `TWITCH_CLIENT_SECRET` existirem)

**Interfaces:**
- Produces (`ondemand/twitch.py`):
  - `class TwitchError(Exception)` com `status: int`
  - `TwitchAPI(client_id: str, client_secret: str, http: httpx.AsyncClient)`
  - `async get_user(login: str) -> dict | None` (`GET /helix/users?login=`)
  - `async get_live_vod(user_id: str) -> dict | None` — `GET /helix/videos?user_id=&type=archive&first=1`, devolve o primeiro item (o VOD da transmissão atual) ou `None`
  - `parse_twitch_duration(text: str) -> float` — `"2h13m5s"` → `7985.0`; `"45m"` → `2700.0`; `"9s"` → `9.0`
  - Token de app (`client_credentials`) guardado em memória; uma resposta 401 renova o token e repete a chamada uma vez.

- [ ] **Step 1: Write the failing test** `tests/ondemand/test_twitch.py`

```python
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
```

- [ ] **Step 2: Run to verify it fails** — `.venv/Scripts/python.exe -m pytest tests/ondemand/test_twitch.py -q` → `ModuleNotFoundError`.

- [ ] **Step 3: Implement** `src/liveclips/ondemand/twitch.py`

```python
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
```

- [ ] **Step 4: Delete the realtime capture and wire `main.py`**

  - Apagar os arquivos e pastas listados em **Files → Delete**.
  - Em `src/liveclips/main.py`: tirar os imports e o uso de `capture.*`, `ChatMonitor`, `SpikeDetector`, `Clipper`, `TokenBucket`, `discovery_loop`, `collector_loop`, `collect_twitch_public`, `collect_kick`, `AutoController`, `ManualTester`, `viewers_cache`, `make_auto_jobs`, o job `auto_boot`, os jobs `kick_clips`, e a linha de status "🔎 Lives monitoradas" do `panel_status`. O `TwitchAPI` passa a vir de `ondemand.twitch` e só é criado se `cfg.twitch_client_id` e `cfg.twitch_client_secret` existirem (senão `None`); por enquanto ele não é passado a ninguém (a Task 4 usa). Em `check_settings`, `TWITCH_CLIENT_ID`/`TWITCH_CLIENT_SECRET` deixam de ser obrigatórios (continua obrigatório ter Gemini ou Anthropic).
  - Onde `main.py` criava o `Monitor` com `channel_count=` e `chat_summary=`, remover esses argumentos (a Task 2 limpa o `Monitor`; até lá eles têm valor padrão).
  - Onde `main.py` criava o `Panel` com o `auto`, passar `None` por enquanto **somente se** o `Panel` aceitar; se não aceitar, fazer esta mudança junto com a Task 2 no mesmo commit (a suíte e o import precisam passar ao fim desta tarefa).

- [ ] **Step 5: Run** — `.venv/Scripts/python.exe -m pytest -q` e `.venv/Scripts/python.exe -c "import liveclips.main"` → ok. `grep -rn "capture\|ManualTester\|AutoController\|ChatMonitor\|SpikeDetector\|Clipper\|discovery_loop\|collect_kick\|collect_twitch_public" src tests` → nada.

- [ ] **Step 6: Commit**

```bash
git add -A src tests scripts
git commit -m "refactor: remove o clipador em tempo real (chat, picos, descoberta, clipper, coletores, /testar); API da Twitch só para o sob demanda"
```

---

### Task 2: Painel, comandos e configurações sem o automático

**Files:**
- Modify: `src/liveclips/control/runtime.py`, `src/liveclips/control/panel.py`, `src/liveclips/control/discord_bot.py`, `src/liveclips/control/monitor.py`, `src/liveclips/core/db.py`
- Test: `tests/control/test_runtime.py`, `tests/control/test_panel.py`, `tests/control/test_panel_view.py`, `tests/control/test_monitor.py`, `tests/core/test_db.py`

**O que sai:**
- `RuntimeSettings`: `auto_on/set_auto_on`, `categories/set_categories`, `known_categories/add_known_category`, `min_viewers/set_min_viewers`, `MAX_KNOWN_CATEGORIES` (e o `import json` se ficar sem uso).
- `Panel`: o parâmetro `auto` e o `resolve`; ações `auto`, `cats`, `viewers`, `addcat`; campos `auto_on`, `auto_running`, `known`, `selected`, `min_viewers` do `PanelState`; constante `VIEWER_OPTIONS`; as linhas "📡 Automático…" e "Categorias…" do texto.
- `discord_bot.py`: `AddCategoryModal`; no `build_panel_view`, o botão do automático, o `➕ Categoria`, os selects de categorias e de espectadores. O painel fica: linha 0 = `⏸ Pausar`/`▶ Retomar`; linha 1 = select de nota; linha 2 = select de ritmo. Os slash commands `/testar`, `/block`, `/unblock` saem. A rota `panel:addcat` sai do `on_interaction`.
- `Monitor`: `run_test`, `tester`, `channel_count`, `chat_summary`, os comandos `block`/`unblock`, a ação `block:` e o botão "🚫 Bloquear canal" do `post_published`; as linhas "Lives monitoradas", "chat…" e "Canais bloqueados" do `status_text`; o `HELP` sem `/testar`, `/block`, `/unblock`.
- `DB`: `block`, `unblock`, `is_blocked`, `blocked_channels` e a tabela `blocklist` do `SCHEMA` (bancos antigos continuam com a tabela, sem problema).

**Interfaces:**
- Produces: `Panel(runtime, configured: list[str], status_lines: Callable[[], list[str]])`; `PanelState(text, paused, min_score, interval, limit, platforms)`; `build_panel_view(s)` com `custom_id`s `panel:pause`, `panel:score`, `panel:pace` nessa ordem.

- [ ] **Step 1: Atualizar os testes primeiro**
  - `tests/control/test_runtime.py`: apagar os testes de automático, categorias e espectadores.
  - `tests/control/test_panel.py`: tirar `FakeAuto` e o `resolve`; `make` vira `Panel(rt, list(configured), lambda: ["📥 Aprovação: 2 aguardando"])`; apagar os testes de automático, categorias, espectadores e `addcat`; manter os de pausa, nota, ritmo, plataformas e valores inválidos (ajustando os asserts que citavam o automático; o texto do estado não tem mais "Automático").
  - `tests/control/test_panel_view.py`: o helper `state()` fica só com `text, paused, min_score, interval, limit, platforms`; o teste da view passa a esperar `["panel:pause", "panel:score", "panel:pace"]` e confere o default do select de ritmo (`"45|8"`) e da nota (`"6"`); o teste das plataformas continua igual.
  - `tests/control/test_monitor.py` e `tests/core/test_db.py`: apagar os testes de `block`/`unblock`, de `run_test` e de "Bloquear canal".

- [ ] **Step 2: Run** — `.venv/Scripts/python.exe -m pytest tests/control tests/core -q` → falham (código antigo).

- [ ] **Step 3: Implement** — remover o que está em **O que sai** e ajustar `main.py` (`Panel(runtime, [p.name for p in publishers], panel_status)`; `Monitor` sem `channel_count`/`chat_summary`).

- [ ] **Step 4: Run** — suíte inteira e import do `liveclips.main` → ok. `grep -rn "auto_on\|known_categories\|min_viewers\|AddCategoryModal\|addcat\|blocked_channels\|run_test\|/testar\|Bloquear canal" src tests` → nada.

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "refactor: painel e comandos sem o automático (sem categorias, espectadores, /testar e bloqueio de canal)"
```

---

### Task 3: Pipeline, configuração, `.env` e docs só para o sob demanda

**Files:**
- Modify: `src/liveclips/processing/pipeline.py`, `src/liveclips/core/config.py`, `src/liveclips/control/approval.py`, `.env.example`, `docs/run-local.md`, `.env` (do usuário — só apagar linhas por chave)
- Delete: `src/liveclips/processing/download.py` (se ficar sem uso)
- Test: `tests/processing/test_pipeline.py`, `tests/core/test_config.py`, `tests/control/test_approval.py`, `tests/processing/test_media.py`

**Pipeline:** o `Pipeline` passa a só avaliar candidatos já preparados.
- Sai: `MediaOps` (`download`, `extract_audio`, `transcribe`, `probe`, `concat`), `_prepare_media`, o corte por `rank_threshold`, a expiração por `queue_ttl_min`, a rejeição por idioma (o sob demanda já transcreveu), `extra_clip_url`/`concat_clips`.
- Fica: orçamento diário (`AI_DAILY_BUDGET`), espera crescente quando a IA cai, nota mínima, segurança, `AWAITING_APPROVAL`/`TESTED`.
- Regra nova e única para mídia: candidato sem `raw_path` existente ou sem `transcript` → `FAILED` com `"trecho sumiu antes da avaliação"` (vale para qualquer origem, sem baixar nada).
- `Pipeline(db, cfg, evaluator, media_dir, now_fn=utcnow, concurrency=2)` — o `http` só existia para a transcrição; sai se ficar sem uso.
- `processing/ffmpeg.py`: apagar `concat_clips` se ficar sem uso (e o teste dele em `tests/processing/test_media.py`). `extract_audio` continua se algo ainda usar; senão sai também.

**Config:** sair do `Settings` e do `load_settings`: `twitch_game_name` (e a property `twitch_game_names`), `twitch_bot_refresh_token`, `kick_enabled`, `kick_category_slug`, `monitor_max_channels`, `min_viewers`, `spike_multiplier`, `spike_min_msgs`, `clip_rate_seconds`, `clip_followup_s`, `rank_threshold`, `queue_ttl_min`, `post_max_per_day_step`, `post_max_per_day_cap`. Ficam `twitch_client_id`/`twitch_client_secret` (opcionais, para live da Twitch).

**Card do #aprovação:** a origem passa a ser sempre a do sob demanda (`🔗 <título> · <minuto>`); o ramo "📡 Automático … pico no chat" e o aviso de idade "⏰ há N h" (só existia para o automático) saem. Ajustar os testes do `test_approval.py` que criavam candidatos `chat_spike` (passam a ser `source="ondemand"` com `start_s`).

**`.env.example`:** apagar `TWITCH_GAME_NAME`, `TWITCH_BOT_REFRESH_TOKEN` (e o comentário do `twitch_oauth.py`), a seção Kick (`KICK_ENABLED`, `KICK_CATEGORY_SLUG`), `MONITOR_MAX_CHANNELS`, `MIN_VIEWERS`, `SPIKE_MULTIPLIER`, `SPIKE_MIN_MSGS`, `CLIP_RATE_SECONDS`, `CLIP_FOLLOWUP_S`, `RANK_THRESHOLD`, `QUEUE_TTL_MIN`, `POST_MAX_PER_DAY_STEP`, `POST_MAX_PER_DAY_CAP` e os comentários de cada uma. O bloco da Twitch vira:

```
# --- Twitch (opcional: só para cortar live em andamento da Twitch) -----------
TWITCH_CLIENT_ID=
TWITCH_CLIENT_SECRET=
```

**`.env` do usuário:** apagar as mesmas chaves (e as linhas de comentário logo acima de cada uma que só falam delas) com um script que lê o arquivo, filtra por nome da chave e regrava — **sem imprimir valores**. Mostrar no relatório só a lista de chaves removidas.

**`docs/run-local.md`:** tirar tudo sobre tempo real (categorias, chat, picos, `/testar`, Kick, token do bot da Twitch, `twitch_oauth.py`); o fluxo descrito passa a ser: colar link → thread → #aprovação → publicar.

- [ ] **Step 1: Atualizar testes** — `tests/processing/test_pipeline.py`: apagar os testes de rank, expiração, idioma, download/`MediaOps` e `extra_clip_url`; os restantes criam o candidato já com `raw_path` (arquivo existente), `transcript` e `source="ondemand"`; acrescentar `test_missing_media_fails_without_downloading` (sem raw → `FAILED` "trecho sumiu antes da avaliação"). `tests/core/test_config.py`: tirar os asserts das variáveis removidas.
- [ ] **Step 2: Run** → falham.
- [ ] **Step 3: Implement** o que está acima (código, `.env.example`, `.env`, docs).
- [ ] **Step 4: Run** — suíte e import ok; `grep -rn "TWITCH_GAME_NAME\|KICK_ENABLED\|KICK_CATEGORY\|MIN_VIEWERS\|SPIKE_\|CLIP_RATE\|CLIP_FOLLOWUP\|RANK_THRESHOLD\|QUEUE_TTL\|MONITOR_MAX\|TWITCH_BOT_REFRESH\|POST_MAX_PER_DAY_STEP\|POST_MAX_PER_DAY_CAP\|chat_spike\|twitch_public\|kick_public" src tests .env.example docs/run-local.md` → nada; `grep -cE "^(TWITCH_GAME_NAME|KICK_ENABLED|MIN_VIEWERS|RANK_THRESHOLD)=" .env` → 0.
- [ ] **Step 5: Commit** (o `.env` não é versionado)

```bash
git add -A src tests .env.example docs/run-local.md
git commit -m "refactor: pipeline, configuração e docs só para o sob demanda; .env.example sem as variáveis do tempo real"
```

---

### Task 4: Live em andamento — do início até agora

**Files:**
- Create: `src/liveclips/ondemand/source.py`
- Modify: `src/liveclips/ondemand/ingest.py`, `src/liveclips/ondemand/jobs.py`, `src/liveclips/main.py`
- Test: `tests/ondemand/test_source.py` (novo), `tests/ondemand/test_ingest.py`, `tests/ondemand/test_jobs.py`

**Interfaces:**
- `VideoMeta` ganha `live_start: float | None = None` (epoch em segundos do início da live: `info["release_timestamp"]` ou, se faltar, `info["timestamp"]`; só preenchido quando `is_live`).
- `source.py`:
  - `class SourceError(Exception)` (mensagem já em português, vira ❌ na thread)
  - `@dataclass Source(url: str, live: bool, start: float, end: float, note: str = "")` — `url` é o que o yt-dlp baixa; `live=True` só para YouTube ao vivo (usa `--live-from-start`); `[start, end]` em segundos desde o início do vídeo/live; `note` vai para a thread (ex.: aviso das últimas 4 h).
  - `async resolve_source(meta: VideoMeta, now_ts: float, max_s: float, twitch=None) -> Source`
- `ingest.download_audio(url, dest, run=run, start=None, end=None, live=False)` — com `start`/`end` acrescenta `--download-sections "*{start:.2f}-{end:.2f}"`; com `live=True` acrescenta `--live-from-start`. Sem eles, comando igual ao de hoje.
- `ingest.download_section(url, start, end, dest, run=run, live=False)` — com `live=True` acrescenta `--live-from-start`.
- Runner: `OnDemandRunner(..., twitch=None)`; `meta.json` passa a guardar também `source` (a `Source` resolvida); transcrição, sinais e assuntos ficam em tempo **relativo** a `source.start`; os candidatos guardam `start_s/end_s` **absolutos** (`source.start + relativo`) e o trecho é baixado de `source.url` com `live=source.live`.

**Regras do `resolve_source`:**

| Caso | Resultado |
|---|---|
| Não é live, `duration <= 0` | `SourceError("não consegui ler a duração do vídeo")` |
| Não é live, `duration > max_s` | `SourceError(f"vídeo longo demais (máximo {max_s/3600:g} h)")` |
| Não é live | `Source(meta.url, False, 0, duration)` |
| YouTube ao vivo, sem `live_start` | `SourceError("não consegui saber há quanto tempo a live está no ar")` |
| YouTube ao vivo | `elapsed = now_ts - live_start`; `Source(meta.url, True, max(0, elapsed - max_s), elapsed, note)` |
| Twitch ao vivo, sem `twitch` (sem credenciais) | `SourceError("para cortar live da Twitch em andamento, configure TWITCH_CLIENT_ID e TWITCH_CLIENT_SECRET")` |
| Twitch ao vivo | login = primeiro trecho do caminho da URL; `user = get_user(login)`; `vod = get_live_vod(user["id"])`; sem user ou sem VOD → `SourceError("essa live não está salvando VOD (ou o VOD é só para inscritos); mande o link quando ela acabar")`; senão `elapsed = parse_twitch_duration(vod["duration"])` e `Source(f"https://www.twitch.tv/videos/{vod['id']}", False, max(0, elapsed - max_s), elapsed, note)` |
| Kick ao vivo | `SourceError("live da Kick em andamento ainda não dá: mande o link do VOD quando a live acabar")` |
| Outro site ao vivo | `SourceError("live em andamento só funciona no YouTube e na Twitch")` |

`note`: vazio quando a janela começa em 0; senão `f"🔴 live com {fmt_time(elapsed)}: vou cortar as últimas {max_s/3600:g} h ({fmt_time(start)} → {fmt_time(elapsed)})"`. Para live sem corte: `f"🔴 live em andamento: vou cortar do início até agora ({fmt_time(elapsed)})"`.

- [ ] **Step 1: Write the failing tests**

`tests/ondemand/test_source.py`:

```python
import pytest

from liveclips.ondemand.ingest import VideoMeta
from liveclips.ondemand.source import SourceError, resolve_source

H = 3600.0


def meta(**kw):
    base = dict(url="https://www.youtube.com/watch?v=x", title="t", channel="c",
                platform="youtube", duration=600.0, is_live=False, heatmap=None,
                video_id="x", credit_hint="c", live_start=None)
    base.update(kw)
    return VideoMeta(**base)


class FakeTwitch:
    def __init__(self, user=True, vod=True):
        self.user, self.vod = user, vod

    async def get_user(self, login):
        assert login == "gaules"
        return {"id": "42"} if self.user else None

    async def get_live_vod(self, user_id):
        return {"id": "999", "duration": "5h0m0s"} if self.vod else None


async def test_recorded_video():
    s = await resolve_source(meta(), now_ts=0, max_s=4 * H)
    assert (s.url, s.live, s.start, s.end, s.note) == (meta().url, False, 0, 600.0, "")
    with pytest.raises(SourceError, match="longo demais"):
        await resolve_source(meta(duration=5 * H), now_ts=0, max_s=4 * H)
    with pytest.raises(SourceError, match="duração"):
        await resolve_source(meta(duration=0), now_ts=0, max_s=4 * H)


async def test_youtube_live_from_start_until_now():
    s = await resolve_source(meta(is_live=True, duration=0, live_start=1000.0),
                             now_ts=1000.0 + 2 * H, max_s=4 * H)
    assert s.live and (s.start, s.end) == (0, 2 * H) and "do início até agora" in s.note
    long = await resolve_source(meta(is_live=True, duration=0, live_start=0.0),
                                now_ts=6 * H, max_s=4 * H)
    assert (long.start, long.end) == (2 * H, 6 * H) and "últimas 4 h" in long.note
    with pytest.raises(SourceError, match="há quanto tempo"):
        await resolve_source(meta(is_live=True, duration=0), now_ts=10, max_s=4 * H)


async def test_twitch_live_becomes_the_running_vod():
    m = meta(platform="twitch", is_live=True, url="https://www.twitch.tv/gaules", duration=0)
    s = await resolve_source(m, now_ts=0, max_s=4 * H, twitch=FakeTwitch())
    assert s.url == "https://www.twitch.tv/videos/999" and not s.live
    assert (s.start, s.end) == (1 * H, 5 * H)
    with pytest.raises(SourceError, match="VOD"):
        await resolve_source(m, now_ts=0, max_s=4 * H, twitch=FakeTwitch(vod=False))
    with pytest.raises(SourceError, match="TWITCH_CLIENT_ID"):
        await resolve_source(m, now_ts=0, max_s=4 * H, twitch=None)


async def test_kick_and_other_lives_are_refused():
    with pytest.raises(SourceError, match="Kick"):
        await resolve_source(meta(platform="kick", is_live=True), now_ts=0, max_s=4 * H)
    with pytest.raises(SourceError, match="YouTube e na Twitch"):
        await resolve_source(meta(platform="outro", is_live=True), now_ts=0, max_s=4 * H)
```

Em `tests/ondemand/test_ingest.py`, acrescentar:

```python
def test_live_start_is_read_only_for_lives():
    m = parse_metadata(info(is_live=True, release_timestamp=1700000000), "u")
    assert m.live_start == 1700000000.0
    assert parse_metadata(info(is_live=True, timestamp=5), "u").live_start == 5.0
    assert parse_metadata(info(release_timestamp=9), "u").live_start is None


async def test_window_and_live_flags_in_downloads(tmp_path):
    calls = []

    async def run(*cmd, timeout=300):
        calls.append(cmd)
        out = Path(cmd[cmd.index("-o") + 1].replace("%(ext)s", "mp3"))
        out.write_bytes(b"x")
        return ""

    await download_audio("u", tmp_path / "a.mp3", run=run, start=0, end=7200.5, live=True)
    assert "*0.00-7200.50" in calls[-1] and "--live-from-start" in calls[-1]
    await download_audio("u", tmp_path / "b.mp3", run=run)
    assert "--download-sections" not in calls[-1] and "--live-from-start" not in calls[-1]
    await download_section("u", 10, 40, tmp_path / "raw.mp4", run=run, live=True)
    assert "--live-from-start" in calls[-1]
    assert calls[-1][-2:] == ("--", "u")
```

Em `tests/ondemand/test_jobs.py`:
- o `make()` passa a aceitar `meta=` com `live_start` (o `VideoMeta` ganhou o campo com padrão `None`, então os testes atuais continuam valendo);
- o fake `download_audio(url, dest, start=None, end=None, live=False)` e o fake `download_section(url, s, e, dest, live=False)` registram os argumentos;
- substituir `test_live_and_too_long_are_refused` por `test_too_long_recorded_video_is_refused` (só a parte do vídeo de 5 h) e acrescentar:

```python
async def test_youtube_live_is_cut_from_start_until_now(db, tmp_path):
    live = VideoMeta("u", "Live", "c", "youtube", 0.0, True, None, "x", "c", live_start=1000.0)
    r, notes, calls = make(db, tmp_path, meta=live)
    r.now_fn = lambda: datetime.fromtimestamp(1000.0 + 120.0, tz=timezone.utc)
    jid = r.enqueue("u", "", None, None)
    await r.process(db.get_job(jid))
    assert db.get_job(jid)["state"] == "evaluating"
    assert any("do início até agora" in t for _, t in notes.progress)
    audio_call = next(c for c in calls if isinstance(c, tuple) and c[0] == "audio")
    assert audio_call[1:] == (0, 120.0, True)  # start, end, live
    [c] = db.job_candidates(jid)
    section = next(x for x in calls if isinstance(x, tuple) and x[0] == "section")
    assert section[3] is True  # o trecho também usa --live-from-start


async def test_live_window_offsets_cuts_to_absolute_time(db, tmp_path):
    live = VideoMeta("u", "Live", "c", "youtube", 0.0, True, None, "x", "c", live_start=0.0)
    r, notes, calls = make(db, tmp_path, meta=live)
    r.cfg = load_settings({"ONDEMAND_MAX_HOURS": "0.02"})  # 72 s: janela [48, 120]
    r.now_fn = lambda: datetime.fromtimestamp(120.0, tz=timezone.utc)
    jid = r.enqueue("u", "", None, None)
    await r.process(db.get_job(jid))
    [c] = db.job_candidates(jid)
    assert c.start_s >= 48.0  # o tempo relativo da janela voltou a ser absoluto
```

  (ajustar os registros do fake: `calls.append(("audio", start, end, live))` e `calls.append(("section", s, e, live))`; os testes antigos que procuravam `"audio"` como string passam a procurar a tupla. Importar `datetime`, `timezone` e `load_settings` no topo do arquivo de teste.)
  > O `make()` atual devolve um tópico fixo `Topic(peak=40, start=30, end=60)`; na janela `[48, 120]` (72 s de áudio) esses tempos são **relativos**, então o corte absoluto fica em `48 + 30 = 78 s` ou mais — por isso o assert `c.start_s >= 48`.

- [ ] **Step 2: Run to verify they fail** — `.venv/Scripts/python.exe -m pytest tests/ondemand -q`.

- [ ] **Step 3: Implement**

`src/liveclips/ondemand/source.py`:

```python
"""De onde baixar e qual trecho processar (vídeo gravado ou live em andamento)."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from .ingest import VideoMeta
from .topics import fmt_time
from .twitch import parse_twitch_duration


class SourceError(Exception):
    """Motivo (em português) para recusar o link."""


@dataclass
class Source:
    url: str
    live: bool  # YouTube ao vivo: baixar com --live-from-start
    start: float  # segundos desde o início do vídeo/live
    end: float
    note: str = ""


def _live_window(elapsed: float, max_s: float) -> tuple[float, str]:
    if elapsed <= max_s:
        return 0.0, f"🔴 live em andamento: vou cortar do início até agora ({fmt_time(elapsed)})"
    start = elapsed - max_s
    return start, (f"🔴 live com {fmt_time(elapsed)}: vou cortar as últimas {max_s / 3600:g} h "
                   f"({fmt_time(start)} → {fmt_time(elapsed)})")


async def resolve_source(meta: VideoMeta, now_ts: float, max_s: float, twitch=None) -> Source:
    if not meta.is_live:
        if meta.duration <= 0:
            raise SourceError("não consegui ler a duração do vídeo")
        if meta.duration > max_s:
            raise SourceError(f"vídeo longo demais (máximo {max_s / 3600:g} h)")
        return Source(meta.url, False, 0.0, meta.duration)
    if meta.platform == "youtube":
        if meta.live_start is None:
            raise SourceError("não consegui saber há quanto tempo a live está no ar")
        elapsed = max(0.0, now_ts - meta.live_start)
        start, note = _live_window(elapsed, max_s)
        return Source(meta.url, True, start, elapsed, note)
    if meta.platform == "twitch":
        if twitch is None:
            raise SourceError("para cortar live da Twitch em andamento, configure "
                              "TWITCH_CLIENT_ID e TWITCH_CLIENT_SECRET")
        login = urlparse(meta.url).path.strip("/").split("/")[0].lower()
        user = await twitch.get_user(login) if login else None
        vod = await twitch.get_live_vod(user["id"]) if user else None
        if not vod:
            raise SourceError("essa live não está salvando VOD (ou o VOD é só para inscritos); "
                              "mande o link quando ela acabar")
        elapsed = parse_twitch_duration(vod["duration"])
        start, note = _live_window(elapsed, max_s)
        return Source(f"https://www.twitch.tv/videos/{vod['id']}", False, start, elapsed, note)
    if meta.platform == "kick":
        raise SourceError("live da Kick em andamento ainda não dá: mande o link do VOD quando "
                          "a live acabar")
    raise SourceError("live em andamento só funciona no YouTube e na Twitch")
```

> Ciclo de import: `source.py` importa `ingest`, `topics` e `twitch`; nenhum deles importa
> `source`. Conferir com `.venv/Scripts/python.exe -c "import liveclips.ondemand.source"`.

`ingest.py`:
- `VideoMeta`: `live_start: float | None = None` (último campo);
- `parse_metadata`: `live_start = float(info.get("release_timestamp") or info.get("timestamp") or 0) or None` só quando `is_live`, senão `None`;
- `download_audio(url, dest, run=run, start=None, end=None, live=False)`: montar a lista de opções; se `start is not None and end is not None`, acrescentar `"--download-sections", f"*{start:.2f}-{end:.2f}"`; se `live`, acrescentar `"--live-from-start"`; o resto (inclusive o `"--", url` no fim e o `_yt(...)` dos cookies) igual;
- `download_section(url, start, end, dest, run=run, live=False)`: se `live`, acrescentar `"--live-from-start"` antes do `"--"`.

`jobs.py`:
- `OnDemandRunner.__init__(..., twitch=None)` guarda `self.twitch`;
- `_meta`: depois do `probe`, trocar as checagens de live/duração por `source = await resolve_source(meta, self.now_fn().timestamp(), self.cfg.ondemand_max_hours * 3600, self.twitch)` (um `SourceError` vira `JobFailed(str(e))`); gravar `meta.json` com `{"meta": asdict(meta), "source": asdict(source)}`; se `source.note`, mandar como progresso; devolver `(meta, source)`. Ler o cache no mesmo formato (um `meta.json` antigo, sem `"source"`, é tratado como vídeo gravado: `Source(meta.url, False, 0, meta.duration)`);
- `_audio`: `self.ops.download_audio(source.url, path, start=source.start if (source.live or source.start > 0) else None, end=source.end if (source.live or source.start > 0) else None, live=source.live)`;
- `_signals`: `n = max(rms.size, int(source.end - source.start))`; heatmap: só quando `source.start == 0 and not source.live` (o heatmap é do vídeo inteiro); chat: só quando `meta.platform == "youtube" and not meta.is_live`;
- `_cuts`/`_make_candidate`/`_finish_cut`: os tempos `s, e` que saem do `snap_clip` são relativos; o candidato grava `start_s = source.start + s`, `end_s = source.start + e`; `clip_id` usa o tempo absoluto; o trecho é baixado com `self.ops.download_section(source.url, source.start + s, source.start + e, folder / "raw.mp4", live=source.live)`; as palavras da legenda continuam relativas ao corte (`w["start"] - s` com `s` relativo);
- `process`: passa `source` adiante nas chamadas acima.

`main.py`: criar o `OnDemandRunner(..., twitch=twitch)` com o `TwitchAPI` da Task 1 (ou `None`).

- [ ] **Step 4: Run** — `.venv/Scripts/python.exe -m pytest -q` e `.venv/Scripts/python.exe -c "import liveclips.main"` → ok.

- [ ] **Step 5: Commit**

```bash
git add src/liveclips/ondemand src/liveclips/main.py tests/ondemand
git commit -m "feat: link de live em andamento corta do início até agora (YouTube com --live-from-start, Twitch pelo VOD em andamento; Kick recusa com aviso)"
```

---

### Task 5: Subir e testar de verdade

- [ ] **Step 1:** `docker compose up -d --build app`; no log: `Discord conectado ...`, sem `Traceback`.
- [ ] **Step 2 (container):** com `docker compose exec app`, rodar um `yt-dlp -J` numa live do YouTube que esteja no ar e conferir `is_live` e `release_timestamp`; rodar `yt-dlp --live-from-start --download-sections "*0-60" -f bestaudio -x` e conferir que sai ~60 s de áudio do **começo** da live.
- [ ] **Step 3 (com o usuário):** colar no canal de links (a) um vídeo gravado curto, (b) uma live do YouTube no ar, (c) uma live da Twitch no ar (se houver credenciais), (d) uma live da Kick (deve recusar com a mensagem). Conferir a thread (nota "🔴 live…"), os cortes no `#aprovação` com o minuto absoluto, e o `#painel` sem os controles do automático.
- [ ] **Step 4:** se algum passo do container mostrar que o `yt-dlp` não aceita `--live-from-start` junto com `--download-sections` para YouTube, trocar para baixar sem `--download-sections` com `--live-from-start` e cortar a janela depois com `ffmpeg -ss/-to` — registrar a decisão no commit.
