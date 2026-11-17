# GTA 6 Live Clips — Design

**Data:** 2026-11-16
**Prazo:** rodando antes do lançamento do GTA 6 (quarta-feira, 00:00)
**Custo alvo:** fase 1 roda no PC do usuário (R$ 0 de hospedagem); fase 2, se tudo der certo, Railway (~US$ 5–15/mês). APIs de IA e plataformas nos planos gratuitos; o fallback de IA (Claude) é pago, com teto diário

## 1. Objetivo

Detectar automaticamente os melhores momentos das lives **brasileiras** de GTA 6 na Twitch e no Kick, quase em tempo real, transformar em vídeos verticais legendados, avaliar com IA e **publicar automaticamente, sem intervenção humana**, quando a IA julgar que o clipe prende o público.

**Objetivo de negócio:** crescer seguidores e monetizar. Atenção: TikTok Creator Rewards e o Instagram não pagam por conteúdo repostado e reduzem o alcance de perfis agregadores. A monetização realista vem do crescimento: parcerias com streamers, programas de clipagem pagos e publis.

**Sucesso no lançamento:** em até ~5 min após um momento forte numa live BR grande, o clipe está publicado no Instagram Reels, respeitando os limites de aquecimento da conta.

### Fora do escopo (v1)
- Gravar lives (streamlink): fica como plano B, não implementado.
- YouTube Live como fonte; lives em outros idiomas.
- Kwai (não há API pública e não há humano no fluxo para postar manualmente).
- Painel web.

## 2. Decisões já tomadas

| Tema | Decisão |
|---|---|
| Fonte | Categoria GTA 6, **somente lives em português (BR)**, Twitch + Kick |
| Detecção | Pico de chat + Create Clip da Twitch; mais coleta de clipes criados pelo público |
| Aprovação | **Nenhuma humana.** A IA decide; o Telegram serve só para monitorar e para o botão de emergência (pausar, apagar, bloquear) |
| Conta | Perfis antigos, porém zerados (0 seguidores, 0 posts): aquecimento obrigatório com limite de posts |
| Hospedagem | **Fase 1: local** no PC do usuário (Docker Compose + Cloudflare Tunnel). **Fase 2: Railway**, só depois que a fase 1 funcionar. Mesmo container, configurável só por variáveis de ambiente. |
| IA | Gemini Flash (plano gratuito) — recebe o **vídeo**, não só o texto |
| Transcrição/legenda | Groq Whisper (gratuito); fallback Whisper local no container |
| Proteção contra denúncias | Crédito ao streamer em todo post, lista de bloqueio, edição transformativa |

## 3. Arquitetura

```
         Container Docker (Railway ou local)
┌───────────────────────────────────────────────────────────────┐
│ [discovery] top lives GTA6 (Twitch Get Streams, a cada 10 min)│
│      │                                                        │
│      ▼                                                        │
│ [chat_monitor] IRC anônimo em N canais ──pico──► [clipper]     │
│                                    Create Clip (conta bot)    │
│ [collector_twitch] Get Clips da categoria (a cada 3 min) ──┐   │
│ [collector_kick]   clipes da categoria (a cada 5 min) ─────┤   │
│                                                            ▼   │
│                    [candidates] dedup + pré-ranking (SQLite)   │
│                                   │ top K dentro do orçamento   │
│                                   ▼                             │
│ [processor] baixa MP4 → 9:16 + legenda (SupoClip ou FFmpeg)     │
│                                   ▼                             │
│ [evaluator] Gemini: nota, seguro?, título, legenda, hashtags    │
│                                   ▼                             │
│ [scheduler] nota ≥ limiar + seguro + dentro do limite diário    │
│                                   ▼                             │
│ [publisher] Instagram Reels (público) · TikTok · YT Shorts      │
│                                   ▼                             │
│ [monitor] Telegram: aviso de cada post + /pause /delete /block  │
└───────────────────────────────────────────────────────────────┘
```

Um único app Python 3.12 (asyncio) com módulos isolados, cada um com uma responsabilidade, comunicando pelo banco SQLite (tabela `candidates` com estados). Um módulo falhar não derruba os outros.

### Estados de um candidato
`detected → processing (download, legenda, render, IA) → queued → published`
Saídas laterais: `rejected_rank`, `rejected_ai`, `expired` (ficou na fila além do prazo), `deleted` (removido via Telegram), `failed` (com motivo).

## 4. Componentes

### 4.1 discovery
- Na inicialização, resolve o `game_id` da categoria "Grand Theft Auto VI" (Twitch Get Games por nome).
- A cada 10 min: Twitch Get Streams (`game_id`, `language=pt`, paginado) → lista dos top `MONITOR_MAX_CHANNELS` (padrão 200) por espectadores, excluindo a lista de bloqueio. Com o filtro BR, é provável que dê para monitorar todas as lives da categoria.
- Kick: lista as lives da categoria em português, só para o coletor (sem monitor de chat na v1).

### 4.2 chat_monitor
- Conexão IRC anônima (`justinfan…`, somente leitura) aos canais da discovery; entra/sai conforme a lista muda, respeitando o limite de JOIN da Twitch (lotes de 20 a cada 10 s).
- Por canal: contagem de mensagens em janelas de 10 s; linha de base = média móvel exponencial dos últimos 5 min.
- **Pico** quando: `taxa ≥ SPIKE_MULTIPLIER (3×) × base` **e** `taxa ≥ SPIKE_MIN_MSGS` (padrão 15 msgs/10 s).
- Bônus de intensidade para palavras-chave do chat BR, configuráveis: `kkkk` (qualquer quantidade de k), `KEKW`, `LUL`, `CLIPA`, `CLIP`, `MDS`, `NÃO`, `MANO`, `PogChamp`, `OMEGALUL`, `🤣`, `😂`.
- Cooldown por canal: 3 min.
- `spike_score = (taxa / base) × (1 + bônus_palavras) × log10(espectadores)`.

### 4.3 clipper
- Recebe picos e chama **Twitch Create Clip** com o token de usuário de uma **conta bot dedicada** (escopo `clips:edit`).
- O Create Clip captura os ~30 s anteriores à chamada; o chat reage com 5 a 15 s de atraso, então o momento fica dentro do clipe.
- **Orçamento global** (token bucket, padrão 1 clipe a cada 20 s): se houver mais picos que orçamento, cria primeiro os de maior `spike_score`; o resto descarta.
- Em 429/503: retentativa com backoff (3 tentativas). Em erro de permissão (canal restringe clipes): marca o canal como `no_clip` por 1 h.
- Depois de criado: consulta Get Clips pelo id até ficar disponível (máx. 30 s) e registra o candidato com `source=chat_spike`.

### 4.4 collector_twitch
- A cada 3 min: Get Clips (`game_id`, `started_at` = últimos 20 min, `first=100`).
- Registra novos clipes como candidatos (`source=twitch_public`), com `view_count`, apenas de clipes com `language=pt`.

### 4.5 collector_kick
- A cada 5 min: busca os clipes recentes da categoria GTA 6 no Kick.
- Isolado atrás de `KICK_ENABLED`. Qualquer erro (bloqueio anti-bot, mudança de formato) é logado e ignorado; não afeta o resto.
- Viabilidade a confirmar na Tarefa 0.

### 4.6 candidates (dedup + pré-ranking)
- **Dedup:** mesmo canal + janelas de tempo sobrepostas (±30 s) = mesmo momento; fica o de maior pontuação.
- **Pré-ranking:** `rank = spike_score` (chat) ou `view_count normalizado × log10(espectadores)` (públicos).
- Só os candidatos com rank acima de `RANK_THRESHOLD` seguem, respeitando o **orçamento diário de IA** (`AI_DAILY_BUDGET`, ajustado à cota gratuita do Gemini). Isso protege as cotas gratuitas no pico do lançamento.

### 4.7 processor
- Baixa o MP4 do clipe com `yt-dlp` (Twitch e Kick).
- Formata para 9:16 (1080×1920):
  - **Layout padrão:** gameplay centralizado sobre fundo desfocado do próprio vídeo, legenda queimada na parte de baixo e o crédito `twitch.tv/<canal>` no topo.
  - **SupoClip:** fora da v1 por causa do prazo (risco de não rodar em ARM e API não verificada). Avaliar depois do lançamento como substituto do render.
- Legendas: Groq Whisper (`whisper-large-v3`, idioma fixo `pt`) → SRT → FFmpeg. Se a cota acabar: Whisper local (`faster-whisper small`, CPU ARM). Se a transcrição falhar: segue sem legenda.

### 4.8 evaluator (IA)
- Envia o **vídeo processado** + metadados (canal, espectadores, origem, transcrição) ao Gemini Flash.
- Ver o vídeo resolve o problema do momento épico de gameplay sem fala.
- Saída JSON validada:
  ```json
  {"score": 0-10, "safe": true, "reason": "...", "title": "...",
   "caption": "... (PT-BR)", "hashtags": ["#GTA6", "..."]}
  ```
- Como não há humano no fluxo, a IA é o único filtro, e por isso o critério é rígido:
  - `safe=false` → `rejected_ai`. Inclui: conteúdo sexual, ódio, ofensa a terceiros, dados pessoais, menor de idade, violência real (não do jogo), streamer fora do jogo em situação constrangedora e **spoiler de história** (cutscenes, missões da campanha, final). A preferência é por caos de gameplay, reação do streamer, física e momentos engraçados.
  - `score < AI_MIN_SCORE` (padrão **7**) → `rejected_ai`.
  - O prompt pede explicitamente: "este clipe prende um espectador brasileiro nos primeiros 2 segundos?". O gancho conta mais na nota.
  - Se a fala principal não for português do Brasil → `score = 0`. Isso também filtra os clipes do Kick, cuja API não informa o idioma.
- **Fallback: Claude (API da Anthropic).** Se o Gemini falhar ou a cota acabar, o evaluator usa o Claude com o mesmo prompt e o mesmo JSON de saída:
  - Modelo: `claude-opus-5-5` (configurável em `CLAUDE_MODEL`; `claude-haiku-5-5` custa ~40× menos).
  - O Claude não aceita vídeo: o evaluator extrai **8 frames** do clipe com FFmpeg (1 a cada ~4 s, 720p) e envia as imagens + a transcrição + os metadados.
  - Saída via structured outputs (`output_config.format`) com o mesmo schema do Gemini; `effort: low`.
  - Trata `stop_reason == "refusal"` como `safe=false`.
  - **Não é gratuito:** API paga, com créditos pré-pagos (a assinatura do Claude/Claude Code não cobre a API). Custo estimado por clipe: ~US$ 0,06 (Opus 5.5) ou ~US$ 0,002 (Haiku 5.5). Um teto diário `CLAUDE_DAILY_BUDGET_USD` (padrão US$ 3) para de usar o fallback ao atingir o limite.
- Se Gemini **e** Claude estiverem indisponíveis (ou o teto estourar): **não publica**. O candidato espera na fila até uma IA voltar ou expirar.

### 4.9 scheduler (fila de publicação)
- Candidatos aprovados pela IA entram numa fila ordenada por `score` da IA (desempate por rank).
- **Aquecimento da conta** (os perfis estão zerados; postar em rajada derruba o alcance ou marca como spam):
  - `POST_MIN_INTERVAL_MIN`: intervalo mínimo entre posts por plataforma (padrão 45 min).
  - `POST_MAX_PER_DAY` (padrão 8 no primeiro dia) e rampa de +2 por dia até `POST_MAX_PER_DAY_CAP` (padrão 20).
- Quando abre uma vaga, publica o melhor da fila. Itens com mais de `QUEUE_TTL_MIN` (padrão 90 min) expiram, porque o momento perde a graça e outros perfis já postaram.
- Nunca publica dois clipes do mesmo canal seguidos.

### 4.10 monitor (Telegram) — não aprova, só observa e freia
- Bot em modo polling, respondendo só ao `TELEGRAM_ADMIN_CHAT_ID`.
- Avisa cada post publicado (link, nota, canal) com botões **🗑️ Apagar post** e **🚫 Bloquear canal**.
- Comandos: `/pause` e `/resume` (para e retoma todas as publicações), `/status`, `/block canal`, `/unblock canal`, `/min_score N`.
- Alertas: qualquer módulo que caia (falha de token, erro repetido de plataforma etc.), com no máximo 1 alerta igual a cada 10 min.
- **Modo ensaio (`DRY_RUN=1`):** nada é publicado; o scheduler segue as mesmas regras, mas manda o vídeo final + legenda para o Telegram. Usado no ensaio com GTA V e na calibração.

### 4.11 publisher
Publica em cada plataforma habilitada de forma independente (uma falhar não impede as outras).

| Plataforma | Método | Automático e público? |
|---|---|---|
| Instagram Reels | Graph API (`instagram_content_publish`). Conta Business/Creator ligada a uma página do Facebook; app Meta em modo desenvolvimento com a conta como testadora. Exige o vídeo em URL pública: servido pelo próprio app em `/media/<token-aleatório>.mp4` por até 1 h (Cloudflare Tunnel na fase 1; domínio do Railway na fase 2). | **Sim**, desde o dia 1. Limite da API: ~50 posts/24 h, muito acima do nosso aquecimento. |
| TikTok | Content Posting API (Direct Post) | **Não**, até a auditoria. App sem auditoria só consegue postar como **privado** (`SELF_ONLY`). A auditoria leva de dias a semanas e deve ser pedida **hoje**. Até lá: `TIKTOK_MODE=off` ou `private`. |
| YouTube Shorts | Data API `videos.insert` | **Não**, até a auditoria. Projetos de API não verificados têm uploads travados como **privado**. Pedir a auditoria hoje; até lá, desligado. |
| Kwai | — | Fora do escopo. |

**Legenda publicada:** título + legenda + `🎥 Créditos: twitch.tv/<canal>` + hashtags.

**Legenda publicada:** título + legenda + `🎥 Créditos: twitch.tv/<canal>` + hashtags.

### 4.12 blocklist e limpeza
- A tabela `blocklist` (canal + motivo) é respeitada pela discovery, pelos coletores e pelo publisher. É editada pelo Telegram.
- Arquivos de vídeo apagados após 48 h; banco mantido.

### 4.13 Hospedagem e portabilidade
- **Uma única imagem Docker**, configurada 100% por variáveis de ambiente. Nenhum caminho, domínio ou credencial fica no código.
- **Fase 1 — local (foco atual):** `docker compose up` lê o `.env` e sobe dois serviços: `app` e `cloudflared` (quick tunnel gratuito, sem conta). A URL do quick tunnel muda a cada reinício, então o app **descobre sozinho** a URL atual consultando o endpoint `/quicktunnel` das métricas do cloudflared (`TUNNEL_METRICS_URL`, padrão `http://cloudflared:2000/quicktunnel`). `PUBLIC_BASE_URL` fixo no env tem prioridade. Banco e mídias ficam em `./data` no PC.
- **Fase 2 — Railway (depois):** o deploy detecta o Dockerfile; variáveis ficam em *Variables*; um volume em `/data`; o HTTP escuta em `$PORT`, com `GET /health` para o healthcheck; `PUBLIC_BASE_URL` vazio vira `https://$RAILWAY_PUBLIC_DOMAIN`. O código já respeita `PORT`, `/health` e o fallback de domínio desde a fase 1; o deploy em si fica fora deste plano.
- **Credenciais que mudam sozinhas** (refresh tokens do Twitch e do TikTok) são gravadas no kv do SQLite e têm prioridade sobre o valor do env. Assim sobrevivem a reinícios sem editar variáveis.
- Migrar de um lugar para o outro = copiar as variáveis (e, se quiser manter o histórico, o arquivo `/data/liveclips.db`).

## 5. Configuração (variáveis de ambiente / `.env`)
`DATA_DIR`, `PORT`, `TUNNEL_METRICS_URL`, `TWITCH_GAME_NAME`, `DRY_RUN`, `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `TWITCH_BOT_REFRESH_TOKEN`, `KICK_ENABLED`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `CLAUDE_MODEL`, `CLAUDE_DAILY_BUDGET_USD`, `GROQ_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_CHAT_ID`, `IG_USER_ID`, `IG_ACCESS_TOKEN`, `TIKTOK_MODE` (`off`/`private`/`public`), `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REFRESH_TOKEN`, `YT_MODE` (`off`/`private`/`public`, + credenciais OAuth), `PUBLIC_BASE_URL`, e os parâmetros de ajuste: `MONITOR_MAX_CHANNELS`, `SPIKE_MULTIPLIER`, `SPIKE_MIN_MSGS`, `CLIP_RATE_SECONDS`, `RANK_THRESHOLD`, `AI_DAILY_BUDGET`, `AI_MIN_SCORE`, `POST_MIN_INTERVAL_MIN`, `POST_MAX_PER_DAY`, `POST_MAX_PER_DAY_CAP`, `QUEUE_TTL_MIN`.

## 6. Tratamento de erros
- Cada módulo roda em sua própria task asyncio com supervisor: exceção → log + reinício com backoff; os outros seguem.
- Toda chamada externa com timeout e retentativa limitada; nada fica bloqueado indefinidamente.
- Candidato que falha em qualquer etapa → estado `failed` + motivo, visível com `/status` no Telegram.
- O `/status` mostra: canais monitorados, picos e clipes da última hora, cotas usadas no dia e erros recentes.
- Tokens OAuth (Twitch, TikTok, Instagram) renovados automaticamente. Falha de renovação → alerta no Telegram.

## 7. Testes
- **Unitários (pytest):** detector de pico (sequências sintéticas de chat), dedup, pré-ranking, scheduler (intervalo, rampa diária, TTL, mesmo canal seguido), montagem da legenda final, parsing/validação do JSON do Gemini, máquina de estados.
- **Avaliação da IA (antes do lançamento):** rodar o evaluator em ~30 clipes reais de GTA V BR, incluindo casos que **devem** ser rejeitados (spoiler, conteúdo impróprio, clipe chato). Calibrar `AI_MIN_SCORE` e o prompt até zero falsos "seguros". Como não há aprovação humana, esse é o teste mais importante.
- **Integração com mocks:** clipper (429/503/permissão), coletores (respostas gravadas), publisher (respostas gravadas de cada API).
- **Ensaio real antes do lançamento (terça):** rodar contra a categoria **GTA V** por 1 a 2 h: medir picos/hora, sucesso do Create Clip, latência ponta a ponta e qualidade do vídeo; publicar 1 clipe de teste em cada plataforma.

## 8. Riscos

| Risco | Mitigação |
|---|---|
| Denúncia/strike por usar conteúdo de terceiros | Crédito ao streamer, bloqueio e apagar via Telegram, edição transformativa. **Sem aprovação humana, o risco é maior; risco residual aceito pelo usuário.** |
| IA publica algo impróprio ou spoiler | Critério `safe` rígido, nota mínima 7, calibração prévia com casos negativos, `/pause` e apagar pelo Telegram |
| Rockstar/Take-Two derruba conteúdo do lançamento | Filtro de spoiler de história; foco em gameplay e reações |
| Conta zerada marcada como spam | Aquecimento: intervalo mínimo e rampa diária de posts |
| TikTok e YouTube sem auditoria = posts privados | Pedir as auditorias no dia 1; no lançamento, só o Instagram publica automaticamente |
| Modelo de repost não monetiza direto | Objetivo de curto prazo é crescer; monetização via parcerias e publis |
| Limite/instabilidade do Create Clip | Orçamento global + prioridade por score; o coletor de clipes públicos continua como fonte |
| Conta bot da Twitch punida por automação | Conta dedicada, separada da pessoal; volume de clipes baixo e configurável |
| Kick bloqueia o coletor | Módulo isolado e desligável |
| Cotas gratuitas estouram no lançamento | Pré-ranking filtra antes da IA; fallback para o Claude (pago, com teto diário) e Whisper local |
| SupoClip não roda em ARM ou não tem API adequada | Pipeline próprio com FFmpeg já definido como padrão |
| PC desligado, sem internet ou dormindo durante o lançamento | Desativar suspensão do Windows; `restart: unless-stopped` no compose; fase 2 (Railway) se o local se mostrar instável |
| Quick tunnel da Cloudflare cai ou muda de URL | O app relê a URL atual antes de cada publicação; falha no Instagram vira alerta no Telegram |

## 9. Pré-requisitos do usuário (fazer já)
1. Docker Desktop instalado no PC (Windows). Repositório no GitHub já existe (fase 2: conectar ao Railway).
2. Conta Twitch **bot** dedicada + app em dev.twitch.tv (client id/secret).
3. Bot do Telegram (@BotFather) + seu chat id.
4. Chaves gratuitas: Google AI Studio (Gemini) e Groq. Chave da API da Anthropic (platform.claude.com) com créditos (mínimo ~US$ 5) para o fallback.
5. Instagram Business/Creator ligado a uma página do Facebook + app em developers.facebook.com.
6. App em developers.tiktok.com com a Content Posting API (Direct Post) e **pedido de auditoria enviado hoje**.
7. Projeto no Google Cloud com a YouTube Data API e **formulário de auditoria enviado hoje**.

## 10. Cronograma
- **Seg (hoje):** Tarefa 0 — testes de viabilidade (Create Clip numa live real, coletor Kick, SupoClip em ARM, Gemini avaliando vídeo); setup das contas.
- **Ter:** discovery, chat_monitor, clipper, coletores, processor, evaluator, scheduler, monitor Telegram; ensaio real com GTA V BR; calibração da IA.
- **Qua (até 00:00):** publisher, rodando local no PC 24h, ajuste dos limiares, publicação de teste no Instagram.
