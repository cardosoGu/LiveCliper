# GTA 6 Live Clips — Design

**Data:** 2026-11-16
**Prazo:** rodando antes do lançamento do GTA 6 (quarta-feira, 00:00)
**Custo alvo:** R$ 0 (apenas planos gratuitos)

## 1. Objetivo

Detectar automaticamente os melhores momentos das lives de GTA 6 na Twitch e no Kick, quase em tempo real, transformar em vídeos verticais legendados, avaliar com IA, pedir aprovação humana pelo Telegram e publicar no Instagram Reels, TikTok e YouTube Shorts.

**Sucesso no lançamento:** em até ~5 min após um momento forte numa live grande, o clipe pronto chega no Telegram; com um toque ele é publicado.

### Fora do escopo (v1)
- Gravar lives (streamlink): fica como plano B, não implementado.
- YouTube Live como fonte.
- Postagem automática no Kwai (não há API pública; o arquivo final é entregue no Telegram para postagem manual).
- Postagem 100% automática sem aprovação (vira configuração depois — modo híbrido).
- Painel web.

## 2. Decisões já tomadas

| Tema | Decisão |
|---|---|
| Fonte | Categoria GTA 6 inteira, Twitch + Kick |
| Detecção | Pico de chat + Create Clip da Twitch; mais coleta de clipes criados pelo público |
| Aprovação | Humana, via Telegram (✅/❌) |
| Hospedagem | Oracle Cloud Always Free (VM ARM, 4 OCPU, 24 GB) com Docker Compose |
| IA | Gemini Flash (plano gratuito) — recebe o **vídeo**, não só o texto |
| Transcrição/legenda | Groq Whisper (gratuito); fallback Whisper local na VM |
| Proteção contra denúncias | Crédito ao streamer em todo post, lista de bloqueio, edição transformativa |

## 3. Arquitetura

```
                 Oracle VM (Docker Compose)
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
│ [approval] Telegram: vídeo + nota + legenda + botões            │
│                                   ▼ ✅                          │
│ [publisher] Instagram Reels · TikTok (rascunho) · YT Shorts     │
└───────────────────────────────────────────────────────────────┘
```

Um único app Python 3.12 (asyncio) com módulos isolados, cada um com uma responsabilidade, comunicando pelo banco SQLite (tabela `candidates` com estados). Um módulo falhar não derruba os outros.

### Estados de um candidato
`detected → downloaded → processed → evaluated → pending_approval → approved → published`
Saídas laterais: `rejected_rank`, `rejected_ai`, `rejected_human`, `failed` (com motivo).

## 4. Componentes

### 4.1 discovery
- Na inicialização, resolve o `game_id` da categoria "Grand Theft Auto VI" (Twitch Get Games por nome).
- A cada 10 min: Twitch Get Streams (`game_id`, paginado) → lista dos top `MONITOR_MAX_CHANNELS` (padrão 200) por espectadores, excluindo a lista de bloqueio.
- Kick: lista as lives da categoria, só para o coletor (sem monitor de chat na v1).

### 4.2 chat_monitor
- Conexão IRC anônima (`justinfan…`, somente leitura) aos canais da discovery; entra/sai conforme a lista muda, respeitando o limite de JOIN da Twitch (lotes de 20 a cada 10 s).
- Por canal: contagem de mensagens em janelas de 10 s; linha de base = média móvel exponencial dos últimos 5 min.
- **Pico** quando: `taxa ≥ SPIKE_MULTIPLIER (3×) × base` **e** `taxa ≥ SPIKE_MIN_MSGS` (padrão 15 msgs/10 s).
- Bônus de intensidade para palavras-chave: `KEKW, LUL, OMEGALUL, kkkk, CLIP, PogChamp, W, NAO`, configuráveis.
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
- Registra novos clipes como candidatos (`source=twitch_public`), com `view_count`.

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
  - **Opção SupoClip:** se a Tarefa 0 aprovar o SupoClip (API funcional, roda em ARM, bom enquadramento em GTA), o processor delega a ele via REST API; caso contrário usa o pipeline próprio com FFmpeg acima.
- Legendas: Groq Whisper (`whisper-large-v3`, PT/EN) → SRT → FFmpeg. Se a cota acabar: Whisper local (`faster-whisper small`, CPU ARM). Se a transcrição falhar: segue sem legenda.

### 4.8 evaluator (IA)
- Envia o **vídeo processado** + metadados (canal, espectadores, origem, transcrição) ao Gemini Flash.
- Ver o vídeo resolve o problema do momento épico de gameplay sem fala.
- Saída JSON validada:
  ```json
  {"score": 0-10, "safe": true, "reason": "...", "title": "...",
   "caption": "... (PT-BR)", "hashtags": ["#GTA6", "..."]}
  ```
- `safe=false` (conteúdo sexual, ódio, dados pessoais, menor de idade etc.) → `rejected_ai`, sem chegar ao Telegram.
- `score < AI_MIN_SCORE` (padrão 6) → `rejected_ai`.
- Se a cota do Gemini acabar: vai para o Telegram marcado "sem nota da IA", com legenda-padrão.

### 4.9 approval (Telegram)
- Bot em modo polling (não precisa de URL pública), respondendo só ao `TELEGRAM_ADMIN_CHAT_ID`.
- Mensagem: vídeo + nota + motivo + legenda sugerida + canal + espectadores + origem.
- Botões: **✅ Postar** · **✏️ Editar legenda** (você responde com o novo texto) · **❌ Descartar** · **🚫 Bloquear canal**.
- Fila máxima de pendentes (`APPROVAL_MAX_PENDING`, padrão 15). Acima disso, só entram candidatos com nota maior que o pior pendente, que é descartado.
- Pendentes vencem após 60 min (o momento perde a graça).

### 4.10 publisher
Ao ✅, publica em cada plataforma habilitada, de forma independente (uma falhar não impede as outras), e reporta o resultado no Telegram com os links.

| Plataforma | Método | Observação |
|---|---|---|
| Instagram Reels | Graph API (conta Business/Creator ligada a uma página do Facebook; app Meta em modo desenvolvimento, sem revisão) | Exige o vídeo em URL pública: servido pela VM em `/media/<token-aleatório>.mp4` por até 1 h, ou por URL pré-assinada do Oracle Object Storage (gratuito). |
| TikTok | Content Posting API — upload como **rascunho** (`video.upload`) | Chega como notificação no app; você publica com um toque. Post direto público só após a auditoria do TikTok. |
| YouTube Shorts | YouTube Data API `videos.insert` | Cota gratuita ≈ 6 uploads/dia; desligado por padrão. |
| Kwai | Manual | O bot reenvia o MP4 final para download. |

**Legenda publicada:** título + legenda + `🎥 Créditos: twitch.tv/<canal>` + hashtags.

### 4.11 blocklist e limpeza
- A tabela `blocklist` (canal + motivo) é respeitada pela discovery, pelos coletores e pelo publisher. Também pode ser editada pelo Telegram (`/block canal`, `/unblock canal`).
- Arquivos de vídeo apagados após 48 h; banco mantido.

## 5. Configuração (`.env`)
`TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `TWITCH_BOT_USER_TOKEN` (+ refresh), `KICK_ENABLED`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_CHAT_ID`, `IG_USER_ID`, `IG_ACCESS_TOKEN`, `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REFRESH_TOKEN`, `YT_ENABLED` (+ credenciais OAuth), `PUBLIC_BASE_URL`, e os parâmetros de ajuste: `MONITOR_MAX_CHANNELS`, `SPIKE_MULTIPLIER`, `SPIKE_MIN_MSGS`, `CLIP_RATE_SECONDS`, `RANK_THRESHOLD`, `AI_DAILY_BUDGET`, `AI_MIN_SCORE`, `APPROVAL_MAX_PENDING`.

## 6. Tratamento de erros
- Cada módulo roda em sua própria task asyncio com supervisor: exceção → log + reinício com backoff; os outros seguem.
- Toda chamada externa com timeout e retentativa limitada; nada fica bloqueado indefinidamente.
- Candidato que falha em qualquer etapa → estado `failed` + motivo, visível com `/status` no Telegram.
- O `/status` mostra: canais monitorados, picos e clipes da última hora, cotas usadas no dia e erros recentes.
- Tokens OAuth (Twitch, TikTok, Instagram) renovados automaticamente. Falha de renovação → alerta no Telegram.

## 7. Testes
- **Unitários (pytest):** detector de pico (sequências sintéticas de chat), dedup, pré-ranking, montagem da legenda final, parsing/validação do JSON do Gemini, máquina de estados.
- **Integração com mocks:** clipper (429/503/permissão), coletores (respostas gravadas), publisher (respostas gravadas de cada API).
- **Ensaio real antes do lançamento (terça):** rodar contra a categoria **GTA V** por 1 a 2 h: medir picos/hora, sucesso do Create Clip, latência ponta a ponta e qualidade do vídeo; publicar 1 clipe de teste em cada plataforma.

## 8. Riscos

| Risco | Mitigação |
|---|---|
| Denúncia/strike por usar conteúdo de terceiros | Crédito ao streamer, bloqueio imediato via Telegram, edição transformativa, aprovação humana. **Risco residual aceito pelo usuário.** |
| Limite/instabilidade do Create Clip | Orçamento global + prioridade por score; o coletor de clipes públicos continua como fonte |
| Conta bot da Twitch punida por automação | Conta dedicada, separada da pessoal; volume de clipes baixo e configurável |
| Kick bloqueia o coletor | Módulo isolado e desligável |
| Cotas gratuitas estouram no lançamento | Pré-ranking filtra antes da IA; fallback "sem nota" e Whisper local; trocar por chave paga = mudar o `.env` |
| SupoClip não roda em ARM ou não tem API adequada | Pipeline próprio com FFmpeg já definido como padrão |
| VM ARM da Oracle "sem capacidade" | Tentar outras regiões; plano B: rodar o mesmo Docker Compose no PC do usuário |

## 9. Pré-requisitos do usuário (fazer já)
1. Conta Oracle Cloud + VM ARM Ampere (4 OCPU / 24 GB, Ubuntu).
2. Conta Twitch **bot** dedicada + app em dev.twitch.tv (client id/secret).
3. Bot do Telegram (@BotFather) + seu chat id.
4. Chaves gratuitas: Google AI Studio (Gemini) e Groq.
5. Instagram Business/Creator ligado a uma página do Facebook + app em developers.facebook.com.
6. App em developers.tiktok.com com Content Posting API (`video.upload`).
7. (Opcional) Projeto no Google Cloud com a YouTube Data API.

## 10. Cronograma
- **Seg (hoje):** Tarefa 0 — testes de viabilidade (Create Clip numa live real, coletor Kick, SupoClip em ARM, Gemini avaliando vídeo); setup das contas.
- **Ter:** discovery, chat_monitor, clipper, coletores, processor, evaluator, Telegram; ensaio real com GTA V.
- **Qua (até 00:00):** publisher, deploy na Oracle, ajuste dos limiares, publicação de teste em cada plataforma.
