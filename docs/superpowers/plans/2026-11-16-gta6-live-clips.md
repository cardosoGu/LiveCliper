# GTA 6 Live Clips — Plano de Implementação

> **Para agentes:** use superpowers:subagent-driven-development ou superpowers:executing-plans, tarefa por tarefa. TDD em todo módulo puro; mocks HTTP (respx) nos clientes de API. Um commit por tarefa.

**Objetivo:** app Python que detecta momentos fortes nas lives BR de GTA 6 (Twitch/Kick), gera Reels verticais legendados, avalia com IA (Gemini → fallback Claude) e publica sozinho no Instagram (TikTok/YouTube quando auditados).

**Arquitetura:** um processo asyncio com módulos isolados supervisionados; estado em SQLite; uma imagem Docker configurada só por env. **Fase 1 (este plano): roda no PC** com Docker Compose + Cloudflare quick tunnel. Fase 2 (fora deste plano): Railway, com o mesmo container. Spec: `docs/superpowers/specs/2026-11-16-gta6-live-clips-design.md`.

**Stack:** Python 3.12, httpx, aiohttp (servidor de mídia), pydantic, anthropic, google-genai, google-api-python-client, faster-whisper, yt-dlp, FFmpeg, SQLite, pytest + pytest-asyncio + respx.

## Restrições globais
- Fuso de "dia" para limites/orçamentos: `America/Sao_Paulo`. Datas no banco em UTC ISO.
- Somente lives `language=pt`. Legendas e textos em PT-BR.
- Nada publica sem veredito da IA com `safe=true` e `score >= min_score` (padrão 7; override no kv `min_score`).
- Sem IA disponível → não publica.
- Claude: `claude-opus-5-5` por padrão (`CLAUDE_MODEL`), `effort: low`, structured output, teto `CLAUDE_DAILY_BUDGET_USD` (3.0); `fallbacks: "default"` só para modelos Opus/Sonnet/Fable.
- Aquecimento: 45 min entre posts; 8 posts no dia 1, +2/dia até 20; fila expira em 90 min.
- `DRY_RUN=1`: nunca chama publishers; envia prévia ao Telegram.
- Todos os parâmetros vêm de env (`.env.example` documenta cada um). Nada de caminho, domínio ou credencial no código.
- HTTP escuta em `$PORT` (padrão 8080) com `GET /health`. URL pública, nesta ordem: `PUBLIC_BASE_URL` → `https://$RAILWAY_PUBLIC_DOMAIN` → hostname lido de `TUNNEL_METRICS_URL` (padrão `http://cloudflared:2000/quicktunnel`), relido antes de cada publicação. Refresh tokens rotativos persistem no kv e têm prioridade sobre o env.

## Estrutura de arquivos
```
pyproject.toml, Dockerfile, docker-compose.yml, .env.example
src/liveclips/
  config.py        Settings + load_settings(env)
  timeutil.py      local_day, local_midnight_utc, parse_iso
  models.py        State, Candidate
  db.py            DB (candidates, publications, blocklist, kv)
  spike.py         SpikeDetector, Spike, spike_score
  ranking.py       compute_rank, find_overlap
  intake.py        candidate_from_twitch_clip, admit
  twitch_api.py    TwitchAPI, TwitchError
  chat_monitor.py  parse_privmsg, ChannelInfo, ChatMonitor
  discovery.py     discover_twitch, discovery_loop
  clipper.py       TokenBucket, choose_spike, Clipper
  collectors.py    collect_twitch_public, parse_kick_clips, collect_kick, collector_loop
  media.py         run, download_clip, extract_audio, credit_text, build_filter, render_vertical, extract_frames, cleanup_media
  transcribe.py    segments_to_srt, transcribe_groq, transcribe
  evaluator.py     Verdict, parse_verdict, build_prompt, claude_cost_usd, Evaluator, AIUnavailable
  pipeline.py      Pipeline
  publishers/      __init__ (PublishJob, PostResult, PublishError, build_publishers), caption.py, instagram.py, tiktok.py, youtube.py
  media_server.py  MediaServer
  scheduler.py     daily_limit, can_post_now, pick_next, is_expired, Scheduler
  telegram_bot.py  Telegram, Monitor
  supervisor.py    supervise
  main.py          amain() — liga tudo
scripts/ twitch_oauth.py, calibrate_ai.py
docs/ run-local.md
tests/ test_<módulo>.py, fixtures/kick_clips.json
```

---

### Tarefa 0: Viabilidade e contas (manual, paralelo às tarefas 1–4)
- [ ] Docker Desktop instalado e rodando no Windows; suspensão automática do PC desativada para a noite do lançamento.
- [x] Repositório local já ligado ao GitHub (`origin` = cardosoGu/LiveCliper).
- [ ] App Twitch (redirect `http://localhost:3000`) + conta bot dedicada.
- [ ] `curl -A "Mozilla/5.0" "https://kick.com/api/v2/categories/grand-theft-auto-vi/clips?sort=date&time=day"`: salvar a resposta real em `tests/fixtures/kick_clips.json`. Se retornar 403/HTML (anti-bot), manter `KICK_ENABLED=0` na v1.
- [ ] Gemini: no AI Studio, subir um clipe vertical de 30 s e confirmar que o modelo descreve o vídeo.
- [ ] Bot do Telegram + chat id; chaves Gemini, Groq e Anthropic (créditos).
- [ ] Instagram Business ligado a página FB; app Meta com `instagram_content_publish`; token longo (60 dias).
- [ ] Enviar os pedidos de auditoria do TikTok (Direct Post) e do YouTube API.

### Tarefa 1: Scaffold, config, modelos, banco
**Arquivos:** pyproject.toml, .env.example, config.py, timeutil.py, models.py, db.py, tests/conftest.py, tests/test_db.py, tests/test_config.py
**Produz:** `load_settings(env: Mapping|None) -> Settings` (todos os campos com padrão; segredos `""`; `port` de `PORT`; `public_base_url`, `railway_public_domain` e `tunnel_metrics_url` lidos crus, resolvidos pelo MediaServer); `State` (detected, processing, queued, published, rejected_rank, rejected_ai, expired, deleted, failed); `Candidate` dataclass; `DB(path)` com `insert_candidate(c)->int|None` (None se `clip_id` duplicado), `get`, `update`, `by_state(state, limit)` (ordem rank desc), `channel_candidates_since`, `reset_processing`, `record_publication`, `publications_for`, `publications_count_since`, `last_publication()->(datetime, channel)|None`, `block/unblock/is_blocked/blocked_channels`, `kv_get/kv_set/kv_incr_float`, `counts_since`.
**Testes:** settings padrão e override por env; insert/get ida e volta preserva datas e estado; duplicado retorna None; by_state ordena por rank; last_publication devolve o canal; kv_incr_float acumula; reset_processing volta processing→detected.

### Tarefa 2: Detector de pico (puro)
**Arquivos:** spike.py, tests/test_spike.py
**Produz:** `SpikeDetector(window_s=10, multiplier=3.0, min_msgs=15, cooldown_s=180, warmup_windows=6, alpha=1/30, keywords=None)` com `add_message(ts, text)` e `poll(now)->Spike|None`; `Spike(at, ratio, keyword_bonus, msgs)`; `spike_score(ratio, bonus, viewers) = ratio*(1+bonus)*log10(max(viewers,10))`. Palavras padrão: `k{3,}`, kekw, lul, clipa, clip, mds, não/nao, mano, pogchamp, omegalul, 🤣, 😂.
**Testes:** sem pico durante o warmup; 7 janelas de 2 msgs + janela de 30 msgs com "kkkk" → pico com ratio≈15 e bonus 1.0; janela abaixo de `min_msgs` não dispara; cooldown bloqueia 2º pico em 20 s e libera após 180 s; janelas vazias fecham corretamente após silêncio; valor de `spike_score`.

### Tarefa 3: Ranking, deduplicação e entrada de candidatos
**Arquivos:** ranking.py, intake.py, tests/test_ranking.py
**Produz:** `compute_rank(c, now)` (chat_spike → spike_score; público → `views/idade_min * log10(max(viewers,10))`); `find_overlap(new, existing, window_s=30)`; `candidate_from_twitch_clip(clip, source, viewers, spike_score=0.0)` (clip_id `twitch:<id>`); `admit(db, c, now=None)->bool` (bloqueados recusados; duplicado: substitui o existente se ainda `detected` e com rank menor, senão recusa).
**Testes:** os dois ramos do rank; sobreposição dentro/fora de 30 s e entre canais diferentes; admit recusa canal bloqueado; admit substitui duplicado mais fraco (o antigo vira rejected_rank) e recusa duplicado mais forte.

### Tarefa 4: Política do scheduler (pura)
**Arquivos:** scheduler.py (só as funções), tests/test_scheduler_policy.py
**Produz:** `daily_limit(day_index, start, step, cap)`; `can_post_now(now, last_post_at, posts_today, limit, min_interval_min)`; `pick_next(queue, last_channel)` (maior ai_score, desempate pelo rank, pula o mesmo canal do último post); `is_expired(c, now, ttl_min)` (idade a partir de `created_at`).
**Testes:** rampa 8,10,…,20; intervalo e limite diário; pick_next pula o mesmo canal e retorna None se só houver esse canal; expiração.

### Tarefa 5: Cliente Twitch Helix + spike de Create Clip
**Arquivos:** twitch_api.py, scripts/twitch_oauth.py, tests/test_twitch_api.py
**Produz:** `TwitchAPI(client_id, client_secret, refresh_token, http, on_refresh=None)` com `get_game_id(name)`, `get_streams(game_id, language="pt", max_items=500)` (paginado), `get_clips(game_id, started_at, ended_at)`, `create_clip(broadcaster_id)->str` (token de usuário via refresh), `get_clip(id)->dict|None`; `TwitchError(status, body)`; 401 → renova o token e tenta 1 vez.
**Testes (respx):** paginação de streams com `language=pt`; create_clip usa o token de usuário e chama on_refresh quando o refresh token muda; retry após 401; 403 vira TwitchError(403).
**Validação manual:** rodar `scripts/twitch_oauth.py` (conta bot), depois chamar `create_clip` numa live BR real de GTA V 10 vezes com 20 s de intervalo e anotar sucessos, 429 e 503. Ajustar o padrão de `CLIP_RATE_SECONDS` com o resultado.

### Tarefa 6: Monitor de chat IRC
**Arquivos:** chat_monitor.py, tests/test_chat_monitor.py
**Produz:** `parse_privmsg(line)->(channel, text)|None`; `ChannelInfo(login, broadcaster_id, viewers)`; `ChatMonitor(on_spike, detector_factory, host="irc.chat.twitch.tv", port=6697)` com `set_channels(infos)`, `handle_line(line, now)->str|None` (PING→PONG), `check_spikes(now)`, `run()` (conexão anônima justinfan, JOIN em lotes de 20 a cada 10 s, PART dos que saíram, reconexão com espera de 5 s).
**Testes:** parse válido/inválido; PING; mensagens alimentam o detector certo; check_spikes chama on_spike com o ChannelInfo; set_channels remove os detectores de canais que saíram.

### Tarefa 7: Discovery + Clipper
**Arquivos:** discovery.py, clipper.py, tests/test_clipper.py
**Produz:** `discover_twitch(api, game_id, max_channels, blocked)->list[ChannelInfo]`; `discovery_loop(...)` (a cada 600 s; atualiza o monitor e `viewers_cache`); `TokenBucket(rate_seconds, clock)`; `choose_spike(pending, now, max_age=15)`; `Clipper(api, db, bucket, clock, sleep)` com `on_spike`, `tick`, `run` — cria o clipe, espera ficar disponível (até 30 s) e chama `admit(..., source="chat_spike")`; 429/5xx → 3 tentativas com backoff; 400/403 → canal fica `no_clip` por 1 h.
**Testes:** discover filtra bloqueados e ordena; bucket com relógio falso; choose_spike descarta picos velhos e pega o maior; tick insere o candidato; 403 silencia o canal; 503 tenta 3 vezes.

### Tarefa 8: Coletores (Twitch público + Kick)
**Arquivos:** collectors.py, tests/test_collectors.py, tests/fixtures/kick_clips.json
**Produz:** `collect_twitch_public(api, game_id, db, viewers_cache, now)->int` (janela de 20 min, só `language` pt); `parse_kick_clips(payload)->list[Candidate]` (clip_url = `video_url` ou `clip_url`; views de `view_count`/`views`; canal de `channel.slug`); `collect_kick(http, slug, db, now)`; `collector_loop(fn, interval)` (loga erros sem derrubar).
**Testes:** só os clipes pt entram; parse da fixture real (Tarefa 0); erro HTTP do Kick é engolido pelo loop.

### Tarefa 9: Mídia (FFmpeg/yt-dlp)
**Arquivos:** media.py, Dockerfile (ffmpeg + fonts-dejavu-core), tests/test_media.py
**Produz:** `run(*cmd, timeout)` (levanta MediaError); `download_clip(url, dest)`; `probe_duration`; `extract_audio(src, dst)` (mono 16 kHz); `credit_text(platform, channel)` (sanitizado); `build_filter(credit, srt_path)` (fundo desfocado 1080×1920 + gameplay central + crédito no topo + legenda embaixo); `render_vertical(src, dst, credit, srt)` (H.264/AAC, faststart, máx. 90 s); `extract_frames(src, out_dir, n=8)` (720p, do vídeo original); `cleanup_media(media_dir, now, max_age_h=48)`.
**Testes:** credit_text remove caracteres perigosos; build_filter com e sem legenda; cleanup apaga só pastas antigas; integração (pula se não houver ffmpeg/fonte): gera vídeo de teste via lavfi → render sai 1080×1920 → extract_frames devolve 8 jpgs.

### Tarefa 10: Transcrição
**Arquivos:** transcribe.py, tests/test_transcribe.py
**Produz:** `segments_to_srt(segments)`; `transcribe_groq(audio, api_key, http)` (whisper-large-v3, `language=pt`, verbose_json); `transcribe(audio, groq_key, http)` → Groq, depois faster-whisper `small` int8 local, depois `[]`.
**Testes:** SRT exato (timestamps, numeração, ignora segmentos vazios); Groq com respx; fallback na ordem certa (monkeypatch).

### Tarefa 11: Avaliador de IA
**Arquivos:** evaluator.py, tests/test_evaluator.py
**Produz:** `Verdict` (score, safe, reason, title, caption, hashtags); `VERDICT_SCHEMA` (additionalProperties false); `parse_verdict(text)` (limita a nota a 0–10, garante `#` nas hashtags, máx. 8); `SYSTEM_PROMPT` com as regras da spec 4.8 (gancho, lista de `safe=false` incluindo spoiler, não-PT-BR → 0, tela parada ≤ 3); `build_prompt(meta)`; `claude_cost_usd(model, in, out)`; `Evaluator(cfg, db, gemini_fn=None, claude_fn=None).evaluate(video_path, raw_path, meta)` → Gemini (vídeo inline < 18 MB, senão Files API, `response_schema=Verdict`) → Claude (8 frames base64 + `client.beta.messages.create` com `output_config={"effort":"low","format":{"type":"json_schema","schema":VERDICT_SCHEMA}}`; refusal → `safe=false`) respeitando o teto diário em `kv claude_spend:<dia>` → `AIUnavailable`.
**Testes:** parse_verdict valida e corrige; Gemini ok não chama Claude; Gemini falha → Claude e o gasto é somado; teto estourado → AIUnavailable; sem chaves → AIUnavailable; resposta de refusal vira unsafe.

### Tarefa 12: Pipeline
**Arquivos:** pipeline.py, tests/test_pipeline.py
**Produz:** `Pipeline(db, cfg, evaluator, http, media_dir, ops=None, now_fn=utcnow)` com `tick()` e `run()`. Tick: expira `detected` antigos → para se `ai_evals:<dia>` ≥ `AI_DAILY_BUDGET` → `rank < RANK_THRESHOLD` vira rejected_rank → processa até 2 em paralelo (estado processing): download → áudio → transcrição/SRT → render → avaliação → queued ou rejected_ai. Se já existe vertical_path, pula a mídia. AIUnavailable → volta a `detected` e espera 60 s. Outras exceções → failed com o motivo. `ops` injeta as funções de mídia e transcrição nos testes.
**Testes:** seguro com nota ≥ mínimo → queued; unsafe → rejected_ai; nota baixa → rejected_ai; respeita o override `min_score` do kv; AIUnavailable mantém detected e o vídeo pronto; orçamento esgotado não processa; rank baixo é rejeitado; erro de mídia vira failed.

### Tarefa 13: Publishers + servidor de mídia
**Arquivos:** publishers/{__init__,caption,instagram,tiktok,youtube}.py, media_server.py, tests/test_publishers.py
**Produz:** `PublishJob(video_path, video_url, caption, title)`; `PostResult(platform, post_id, url)`; `PublishError`; `build_caption(verdict, platform, channel)` (título, legenda, `🎥 Créditos: …`, hashtags; máx. 2200); `InstagramPublisher(ig_user_id, token, http, version="v23.0").publish/delete` (container REELS → espera FINISHED → media_publish → permalink); `TikTokPublisher(key, secret, refresh, privacy, http, on_refresh)` (FILE_UPLOAD em 1 chunk → status fetch); `YouTubePublisher(client_id, secret, refresh, privacy).publish/delete`; `build_publishers(cfg, db, http)` (TikTok/YouTube só se o modo ≠ off); `MediaServer(cfg, http, port)` com `async public_base() -> str` (ordem de resolução das restrições globais; levanta `PublishError` se nenhuma estiver disponível), `async expose(path, ttl_s=3600) -> url`, `start()`, e as rotas `GET /media/{token}.mp4` e `GET /health` → 200 "ok".
**Testes (respx):** legenda tem créditos e é truncada; fluxo feliz do Instagram e container com ERROR; TikTok init/upload/status e a privacidade enviada; build_publishers respeita os modos; MediaServer serve o token válido e dá 404 para expirado (aiohttp test client); `public_base` usa o env fixo, depois `RAILWAY_PUBLIC_DOMAIN`, depois `{"hostname": "x.trycloudflare.com"}` do endpoint de métricas (respx).

### Tarefa 14: Scheduler (loop de publicação)
**Arquivos:** scheduler.py (classe), tests/test_scheduler.py
**Produz:** `Scheduler(db, cfg, publishers, media, notifier, now_fn)` com `tick()`, `publish(c, now)` e `run()` (a cada 30 s). Respeita o kv `paused`, expira a fila, aplica daily_limit/can_post_now/pick_next, publica em cada plataforma de forma independente, grava publications, marca `first_post_day`, published ou failed, e notifica. DRY_RUN: não chama publishers, registra a plataforma `dry_run` e chama `notifier.dry_run_preview`.
**Testes:** pausado não publica; o intervalo bloqueia; uma plataforma falhando não impede a outra; todas falhando → failed; dry run não chama publishers e envia prévia; a rampa usa `first_post_day`.

### Tarefa 15: Telegram (monitor e freio)
**Arquivos:** telegram_bot.py, tests/test_telegram.py
**Produz:** `Telegram(token, chat_id, http)` com `send(text, buttons)`, `send_video(path, caption)`, `updates(offset)`, `answer_callback`; `Monitor(db, tg, publishers_by_name, cfg, channel_count, now_fn, clock)` com `alert` (dedup de 10 min), `post_published`, `dry_run_preview`, `handle_command` (/pause /resume /status /block /unblock /min_score), `handle_callback` (`del:<id>` apaga via publisher ou pede apagar manualmente; `block:<canal>`), `run()` (ignora outros chats).
**Testes:** cada comando altera o kv/blocklist e responde; /min_score inválido; del chama publisher.delete e marca deleted; plataforma sem delete → instrução manual; alert deduplica; chat estranho ignorado.

### Tarefa 16: Supervisor, main, Docker e execução local
**Arquivos:** supervisor.py, main.py, Dockerfile, docker-compose.yml (serviço `app` com `env_file: .env`, volume `./data:/data`, `restart: unless-stopped`; serviço `cloudflared` com `cloudflare/cloudflared` rodando `tunnel --no-autoupdate --metrics 0.0.0.0:2000 --url http://app:8080`), docs/run-local.md, tests/test_supervisor.py
**Produz:** `supervise(name, factory, alert, sleep=asyncio.sleep, backoff_max=300)`; `amain()` liga: DB (reset_processing), TwitchAPI (refresh token persistido no kv), game_id, ChatMonitor→Clipper, discovery, coletores, Pipeline, Scheduler, MediaServer, Monitor, limpeza de mídia de hora em hora; mensagem de "iniciado" no Telegram.
**Testes:** supervise reinicia após exceção, alerta e aumenta o backoff.
**docs/run-local.md cobre:** copiar `.env.example` para `.env`; `docker compose up -d --build`; `docker compose logs -f app`; parar e atualizar; como obter cada token (Twitch via `scripts/twitch_oauth.py`, token longo do Instagram, TikTok/YouTube quando auditados); cuidados para a noite do lançamento (PC sem suspensão, cabo de rede).
**Validação:** `DRY_RUN=1` + `TWITCH_GAME_NAME="Grand Theft Auto V"` → mensagem de início no Telegram, `/status` com canais monitorados, `curl https://<x>.trycloudflare.com/health` responde "ok"; reiniciar o compose preserva `./data`.

### Tarefa 17: Ensaio, calibração e lançamento (terça/quarta)
**Arquivos:** scripts/calibrate_ai.py
- [ ] `calibrate_ai.py <pasta> labels.csv` (arquivo, esperado publicar|rejeitar) roda o Evaluator e imprime a matriz de confusão e os falsos "publicar".
- [ ] Juntar ~30 clipes de GTA V BR (incluindo spoiler, conteúdo impróprio, inglês e chatos) e calibrar o prompt e a nota até **zero falsos publicar**.
- [ ] Rodar local com DRY_RUN=1 + GTA V por 1–2 h: medir picos/h, sucesso do Create Clip, latência ponta a ponta e qualidade visual; ajustar SPIKE_*, RANK_THRESHOLD e CLIP_RATE_SECONDS.
- [ ] Publicar 1 Reel de teste real (DRY_RUN=0) e apagar pelo Telegram.
- [ ] Quarta 23:30: `TWITCH_GAME_NAME="Grand Theft Auto VI"`, DRY_RUN=0, `/status`.
