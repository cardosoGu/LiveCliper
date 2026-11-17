# Canal SupoClip no Discord — design

Data: 2026-11-17

## Objetivo

Um canal separado no Discord onde o admin cola o link de um vídeo longo (YouTube, VOD da
Twitch/Kick, etc.) e recebe de volta, no próprio Discord, os clipes verticais gerados pelo
[SupoClip](https://github.com/FujiwaraChoki/supoclip) (open source, AGPL-3.0).

O SupoClip roda como serviço separado (caixa-preta) e o bot só conversa com ele pela API HTTP.
Nenhum código do SupoClip é copiado para este repo, então a AGPL não se estende a ele.

## Escopo da v1

Dentro:
- Mensagem do admin com um link no canal novo vira um job no SupoClip.
- O texto que vier depois do link é enviado como `instructions` (até 500 caracteres).
- Acompanhar o progresso e postar os clipes prontos numa thread da mensagem original.
- SupoClip self-hosted no mesmo `docker-compose`.

Fora:
- Publicar esses clipes no TikTok/Instagram/YouTube (os clipes não entram no banco nem no agendador).
- Slash command `/clipar` com opções (`clip_length`, `workflow`).
- Live em andamento (o SupoClip só aceita VOD, clipe ou upload).
- Retomar jobs depois de um restart do app.

## Fluxo

1. O admin manda `<url> [instruções]` no canal `DISCORD_SUPOCLIP_CHANNEL_ID`. Mensagens de outros
   usuários, de outros canais ou sem URL são ignoradas.
2. O bot reage com ⏳, cria uma thread na mensagem e chama
   `POST /tasks/` com `{"source": {"url": url}, "instructions": instruções}` (o campo
   `instructions` só vai quando não estiver vazio). A resposta traz `task_id`.
3. A cada 15 s o bot faz `GET /tasks/{task_id}` e edita uma única mensagem de status na thread com
   `progress`% e `progress_message`. O texto só é editado quando muda.
4. Estados terminais:
   - `completed`: segue para o passo 5.
   - `error` ou `cancelled`: a reação vira ❌ e o bot posta `progress_message`/`error_code`.
   - Timeout total de 2 h (`SUPOCLIP_TIMEOUT_S`, padrão 7200): ❌ com "tempo esgotado".
5. Com o job `completed`, o bot faz `GET /tasks/{task_id}/clips` e processa os clipes em ordem de
   `clip_order`. Para cada um, faz o export (passo 6) e posta o arquivo na thread com esta legenda:
   `#n · start_time–end_time · viral virality_score · reasoning` (o `reasoning` é cortado para caber
   em 2000 caracteres).
6. O export é feito com `POST /tasks/{task_id}/clips/{clip_id}/exports` e `{"preset": "tiktok"}`.
   Depois o bot consulta `GET .../exports/{export_id}` a cada 5 s até ficar `succeeded` ou `failed`,
   e então baixa `GET .../exports/{export_id}/file` (seguindo redirect) para
   `data/supoclip/<task_id>/<clip_id>.mp4`.
7. No fim a reação vira ✅ e o bot posta "N clipes". Se algum clipe falhar, ele avisa na thread e
   segue para o próximo; o ✅ vale quando ao menos um clipe foi entregue.
8. Os arquivos locais são apagados depois do envio.

## Limite de upload do Discord

O limite é `DISCORD_UPLOAD_LIMIT_MB`, com padrão 10. Se o clipe passar disso:
1. Recomprime com ffmpeg, calculando o bitrate de vídeo a partir da duração para caber em 95% do
   limite, com áudio AAC a 96k.
2. Se ainda não couber, publica pelo `MediaServer` existente (link temporário via túnel) e posta o
   link em vez do arquivo.

## Componentes

### `src/liveclips/longform/client.py` — `SupoClipClient`
Wrapper fino de `httpx.AsyncClient` com `base_url=SUPOCLIP_URL` e o header `x-api-key`.
- `create_task(url, instructions) -> str` (task_id)
- `get_task(task_id) -> TaskStatus` (status, progress, progress_message, error_code)
- `list_clips(task_id) -> list[Clip]` (id, clip_order, start_time, end_time, duration,
  virality_score, reasoning)
- `export_clip(task_id, clip_id, dest, preset="tiktok") -> Path`

Erros HTTP viram `SupoClipError(message)` com uma mensagem legível: 401/403 é chave inválida,
422 é link inválido e erro de conexão é "SupoClip fora do ar". É o único arquivo que conhece os
nomes dos campos do SupoClip.

### `src/liveclips/longform/runner.py` — `LongformRunner`
`run(url, instructions, out: Output)`, onde `Output` é um Protocol com
`status(text)`, `file(path, caption)`, `text(msg)` e `done(ok: bool)`.
Orquestra os passos 2 a 8 e não importa nada do `discord`. O intervalo de polling e o relógio são
injetáveis para os testes.

### `src/liveclips/longform/message.py`
`parse_message(content) -> tuple[url, instructions] | None`: pega a primeira URL http(s) e trata o
resto do texto como instruções (sem espaços nas pontas, até 500 caracteres).

### `src/liveclips/control/discord_bot.py`
- `intents.message_content = True`. Isso também exige ligar o **Message Content Intent** no
  Developer Portal.
- Novo `on_message`: filtra pelo canal, pelo admin e por `parse_message`, cria a thread, monta um
  `Output` que escreve na thread e roda `runner.run` em uma task asyncio, sem bloquear o bot.
  Vários vídeos podem rodar ao mesmo tempo; a fila fica com o SupoClip.

### Config (`core/config.py`)
- `DISCORD_SUPOCLIP_CHANNEL_ID` (0 = recurso desligado)
- `SUPOCLIP_URL` (padrão `http://supoclip-backend:8000`, ajustar ao nome real do serviço)
- `SUPOCLIP_API_KEY`
- `SUPOCLIP_TIMEOUT_S` (padrão 7200)
- `DISCORD_UPLOAD_LIMIT_MB` (padrão 10)

### Infra
- O SupoClip é clonado em `vendor/supoclip/`, que entra no `.gitignore`.
- O `docker-compose.yml` faz `include:` do compose do SupoClip, para que tudo rode na mesma rede.
  Os nomes e as portas dos serviços são conferidos ao clonar, e o `.env` do SupoClip fica dentro da
  pasta dele.
- `docs/run-local.md` ganha o passo a passo: clonar, configurar o `.env` do SupoClip (AssemblyAI e
  LLM), criar a conta e verificar o e-mail na instância local, gerar a API key `sk_...` e ligar o
  intent no Discord.

## Testes
- `client`: `httpx.MockTransport` simulando create, status, clips, export (queued → succeeded),
  download com redirect e os erros 401/422/conexão.
- `runner`: client falso e `Output` falso, cobrindo sucesso com 2 clipes, `error`, `cancelled`,
  timeout, um clipe que falha no export enquanto os outros seguem, e clipe grande demais
  (recompressão e fallback de link).
- `message`: URL sozinha, URL com instruções, texto sem URL, instruções acima de 500 caracteres.

## A verificar na integração
Os nomes dos campos vêm do código-fonte do SupoClip (`backend/src/models.py` e
`backend/src/api/routes/tasks.py`, branch `main`), não da documentação. Antes de dar a integração
por pronta, conferir num job real:
- a resposta de `GET /tasks/{id}` (campo `status` e valores `completed`/`error`/`cancelled`);
- os campos de cada clipe;
- a resposta de `POST .../exports` (nome do campo `export_id`).
