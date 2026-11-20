# LiveCliper

Corta os melhores momentos de vídeos e lives (YouTube, Twitch e Kick) a partir de um **link colado
no Discord**, avalia cada corte com IA, entrega tudo pronto num canal de aprovação e publica no
Instagram Reels, YouTube Shorts e TikTok **só o que você aprovar**.

```
link no #gerar-clipes ─► baixa o áudio ─► transcreve ─► acha os picos ─► IA escolhe os assuntos
        ─► corta em fim de frase ─► baixa só os trechos ─► IA avalia ─► vídeo vertical pronto
        ─► #aprovação (✅ ⚡ ✏️ ❌) ─► fila ─► Reels / Shorts / TikTok
```

## O que ele faz

- **Qualquer vídeo gravado** do YouTube, VOD da Twitch ou VOD da Kick, de até `ONDEMAND_MAX_HOURS`
  horas (padrão 4).
- **Live em andamento**: processa do início até o último segundo que já foi ao ar (ou as últimas
  `ONDEMAND_MAX_HOURS` horas, se a live for mais longa). Para pegar o resto, mande o link de novo.
  - YouTube: baixa a janela direto pelos fragmentos da live.
  - Twitch: usa o VOD que está sendo gravado (precisa de `TWITCH_CLIENT_ID`/`SECRET`). Se o canal
    não salva VOD, o bot avisa em vez de cortar o vídeo errado.
  - Kick: só depois que a live acaba (mande o link do VOD).
- **Acha os momentos** combinando três sinais por segundo — volume do áudio (risada, grito,
  aplauso), o "Mais repetidos" do YouTube e as reações no chat do replay — e pede a uma IA de
  texto que marque o começo e o fim de cada assunto.
- **Corta bem**: começa e termina em frase, nunca no meio de uma palavra, espera a risada acabar;
  cada corte tem de 15 a 65 s.
- **Avalia em vídeo** (Gemini): nota de 0 a 10, segurança, título-gancho,
  legenda, hashtags e onde está a webcam.
- **Renderiza o vertical** 1080×1920: webcam em cima e jogo embaixo (ou tela cheia sem webcam),
  legenda palavra a palavra e título-gancho animado.
- **Você decide**: cada corte chega no `#aprovação` com o vídeo e quatro botões. Nada é publicado
  sem o seu ✅.
- **Aprende o seu gosto**: cada ✅ e ❌ (com o motivo que você escrever) vira exemplo para a IA,
  tanto na escolha dos assuntos quanto na nota. Com o tempo ela corta e avalia do seu jeito.
- **Retoma de onde parou**: cada etapa de um pedido fica salva; um reinício no meio continua dali.

## Como usar

1. Cole no canal de links uma mensagem com o link e, se quiser, uma instrução:
   ```
   https://www.youtube.com/watch?v=XXXXXXXXXXX foca nas histórias do convidado
   ```
2. A mensagem ganha ⏳ e uma thread **"Cortes #N"** mostra o progresso (lendo → baixando áudio →
   transcrevendo → picos → IA escolhendo → recortando → avaliando).
3. Cada corte que passa da nota mínima aparece no `#aprovação` com o vídeo, o título, a nota, o
   motivo e a origem `🔗 <título> · <minuto>`.
4. No card:
   - **✅ Aprovar** — entra na fila de publicação (respeita o intervalo e o limite por dia);
   - **⚡ Agora** — publica no próximo minuto (respeita só o limite por dia);
   - **✏️ Editar** — troca o título-gancho e a legenda; o vídeo é refeito;
   - **❌ Descartar** — abre uma caixa para você dizer **por quê** (opcional) e apaga o corte.
5. Depois de publicado, o card mostra os links. Se uma plataforma falhar, aparece **🔁** para
   reenviar só para ela.

A thread termina com ✅ e um resumo ("3 cortes enviados para o #aprovação, 5 descartados pela IA").
Se a IA estiver fora do ar ou o Groq no limite, a thread mostra "⏸️ aguardando…" e o pedido
continua sozinho depois.

### Painel e comandos

O `#painel` é uma mensagem fixa, atualizada a cada minuto, com:
- **Pausar / Retomar** as publicações;
- **nota mínima** da IA (5 a 9) e **ritmo** (intervalo entre posts × limite por dia);
- **Reels / Shorts / TikTok**: liga e desliga cada plataforma;
- status: pedidos na fila, uso da IA hoje, cortes aguardando aprovação, posts do dia.

Slash commands (só o admin): `/status`, `/pause`, `/resume`, `/min_score N`,
`/plataforma nome on|off`.

## Instalação

Requisitos: **Docker** (Docker Desktop no Windows). O túnel da Cloudflare, usado para o Instagram
baixar o vídeo, sobe junto no `docker-compose.yml`.

```bash
cp .env.example .env        # preencha (veja abaixo)
docker compose up -d --build
docker compose logs -f app  # deve aparecer "Discord conectado como ... canais: painel, gerar-clipes, aprovação, logs"
```

Os dados ficam em `data/` (banco SQLite, vídeos temporários, cookies). Vídeos com mais de 48 h são
apagados sozinhos, exceto os que ainda aguardam aprovação ou publicação.

## Configuração (`.env`)

O `.env.example` traz todas as variáveis com comentários. As principais:

| Variável | Para quê |
|---|---|
| `DISCORD_BOT_TOKEN`, `DISCORD_ADMIN_USER_ID` | o bot e quem pode mandar nele |
| `DISCORD_CHANNEL_ID` | o canal `#logs` (avisos e erros) |
| `DISCORD_APPROVAL_CHANNEL_ID`, `DISCORD_PANEL_CHANNEL_ID`, `DISCORD_LINKS_CHANNEL_ID` | os outros canais; vazios = o bot cria |
| `GEMINI_API_KEY1..3`, `GEMINI_MODEL` | a IA (tenta cada modelo com cada chave) |
| `GROQ_API_KEY` | transcrição (Whisper) |
| `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` | opcional: só para live da Twitch em andamento |
| `IG_*`, `TIKTOK_*`, `YT_*` | plataformas de publicação |
| `AI_MIN_SCORE`, `POST_MIN_INTERVAL_MIN`, `POST_MAX_PER_DAY`, `AI_DAILY_BUDGET` | valores iniciais (depois, ajuste pelo painel) |
| `ONDEMAND_MAX_HOURS` | duração máxima de um vídeo; numa live, a janela processada |
| `DRY_RUN` | `1` = todas as plataformas começam desligadas |

### Discord
1. Crie o app e o bot em discord.com/developers e convide-o para o servidor.
2. Na aba **Bot**, ligue **Message Content Intent** — sem isso o bot não lê o canal de links (ele
   avisa no log e o resto continua funcionando).
3. Crie os canais (ou deixe o bot criar, com a permissão **Gerenciar canais**) e copie os IDs.

### Vídeos com restrição de idade
Muita gameplay (GTA, por exemplo) exige login no YouTube. Exporte os cookies de uma conta do
YouTube (de preferência secundária) no formato Netscape — por exemplo com a extensão
"Get cookies.txt LOCALLY" — e salve em `data/youtube-cookies.txt`. Não precisa reiniciar.
O mesmo arquivo resolve quando o YouTube pede "confirme que você não é um robô".

### Plataformas
| Plataforma | Situação sem auditoria | Variáveis |
|---|---|---|
| Instagram Reels | publica público (conta Profissional ligada a uma Página) | `IG_USER_ID`, `IG_ACCESS_TOKEN` |
| TikTok | `TIKTOK_MODE=draft`: vai como **rascunho** no app (escopo `video.upload`) | `TIKTOK_CLIENT_KEY`, `_SECRET`, `_REFRESH_TOKEN` |
| YouTube Shorts | sobe como privado até a auditoria do Google | `YT_CLIENT_ID`, `_SECRET`, `_REFRESH_TOKEN` |

Passo a passo de cada credencial em [`docs/run-local.md`](docs/run-local.md).

## Estrutura do código

```
src/liveclips/
  main.py              liga tudo e mantém cada parte viva (supervisor)
  core/                configuração, banco (SQLite), modelos, crédito ao criador
  ondemand/            o fluxo por link
    ingest.py            yt-dlp: metadados, áudio, trechos, chat; cookies; erros em português
    source.py            de onde baixar e qual janela (vídeo gravado ou live)
    ytlive.py            janela de live do YouTube pelos fragmentos numerados
    twitch.py            API da Twitch (VOD da live em andamento)
    longtranscribe.py    transcrição em pedaços (Groq), com cache por pedaço
    signals.py, peaks.py volume + mais repetidos + chat → picos
    topics.py            IA de texto escolhe começo e fim de cada assunto
    snap.py              bordas em frase/pausa, sem cortar palavra nem risada
    jobs.py              a fila de pedidos, em etapas retomáveis
  ai/                  avaliação em vídeo (Gemini), prompt, IA de texto, gosto do admin
  processing/          pipeline de avaliação, ffmpeg (render vertical), legendas, limpeza
  publishing/          agendador, Instagram, TikTok, YouTube, servidor de mídia
  control/             bot do Discord, #aprovação, #painel, configurações do painel
```

## Desenvolvimento

```bash
uv sync
uv run pytest -q
```

Os testes não usam rede (yt-dlp, APIs e Discord são simulados). Specs e planos de cada etapa
ficam em `docs/superpowers/`.
