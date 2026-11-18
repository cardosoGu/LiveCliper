# Live-Clips V2 — Discord como painel, cortes sob demanda e aprovação humana

Data: 2026-11-17 · Substitui, na parte de vídeos longos, o design `2026-11-17-supoclip-discord-design.md`
(não vamos usar o SupoClip).

## 1. Por que a V2

Diagnóstico das primeiras ~18 h da V1 (banco `data/liveclips.db` e logs):

- 533 candidatos, 433 cortados pelo rank, 62 avaliados pela IA, **nenhum com nota ≥ 8** (máx. 7, mediana 2–3).
- A entrada é o problema, não o filtro: às 18h30 de uma terça só havia **7 lives BR com ≥ 1000 espectadores**
  nas 10 categorias; o detector de pico pegava lives "idle" (comandos `!pokeidle`, `!pix`).
- A IA ficou fora do ar (cota do Gemini esgotada nas 3 chaves, sem reserva do Claude) e os candidatos expiraram.

A V1 recebeu correções pontuais (chat ignora comandos e bots, clipes públicos buscados por canal, espera
crescente quando a IA cai). A V2 muda o modelo: **o humano escolhe a entrada e aprova a saída**, e o
Discord vira o painel de controle de tudo.

## 2. Objetivo e escopo

Duas formas de gerar clipes, uma saída comum:

1. **Sob demanda:** o admin cola um link de vídeo gravado (YouTube, VOD da Twitch, VOD da Kick) e recebe os
   melhores cortes, inclusive séries "Parte 1 / Parte 2".
2. **Automático:** o motor da V1 (lives da Twitch + picos no chat), ligado e desligado por um botão, com
   categorias e mínimo de espectadores escolhidos no painel.
3. **Aprovação:** todo corte cai num canal com ✅/❌. Só o aprovado é publicado em Reels, Shorts e TikTok.

Dentro do MVP:
- Painel no Discord (toggle, seletores, liga/desliga por plataforma, status ao vivo).
- Motor de vídeo longo próprio (sinais + IA de texto + acabamento de bordas + séries).
- Layout vertical para podcast (centrado no rosto), além do layout de jogo (tela dividida) da V1.
- Canal de aprovação com aprovar, publicar agora, editar título/legenda e descartar.
- TikTok em modo rascunho (`TIKTOK_MODE=draft`).

Fora do MVP (V2.1):
- Kick em tempo real (lives + chat da Kick).
- Gravação própria da live (buffer contínuo com `streamlink`), que substitui o "criar clipe" da Twitch e
  acaba com os erros 403.
- Lista de canais permitidos/proibidos para o automático.
- Upload de arquivo pelo Discord e link de live em andamento.

## 3. Estrutura no Discord

O bot cria, na primeira execução, a categoria **Live Clips** no servidor `DISCORD_GUILD_ID` com quatro canais e
grava os IDs na tabela `kv` (`discord_channel:<nome>`). Se um canal for apagado, ele é recriado no próximo início.
Permissões do bot: ver canais, enviar mensagens, anexar arquivos, criar threads, gerenciar canais.

| Canal | Função |
|---|---|
| `#painel` | Uma mensagem fixa com controles e status. Ninguém escreve aqui. |
| `#gerar-clipes` | Admin cola `<link> [instrução]`; cada pedido vira uma thread com o progresso. |
| `#aprovação` | Uma mensagem por corte (ou por série) com os botões de decisão. |
| `#logs` | Erros e avisos que hoje vão para `DISCORD_CHANNEL_ID`. |

Só `DISCORD_ADMIN_USER_ID` pode usar botões, seletores e o `#gerar-clipes`. Interações de outros usuários
recebem uma resposta efêmera "só o admin pode usar isso".
Os slash commands `/status`, `/testar`, `/block`, `/unblock` continuam; `/pause` e `/resume` viram o botão
de pausa do painel (os comandos ficam como atalho).

## 4. Painel (`#painel`)

Uma mensagem com embed + componentes, editada pelo bot (nunca reenviada, salvo se for apagada).

**Automático**
- Botão `📡 Automático: DESLIGADO` / `🟢 LIGADO há 1h20`.
- Select múltiplo de categorias (até 25 opções): lista inicial = `TWITCH_GAME_NAME` do `.env`.
  Botão `➕ Categoria` abre um modal com o nome; o bot valida em `/helix/games` e adiciona à lista.
- Select de espectadores mínimos: 300 · 500 · 1000 · 2000 · 5000 · 10000.
- Qualquer mudança dispara uma nova descoberta de lives na hora, sem reiniciar o app.
- Desligado, **não roda nada** do automático: chat, descoberta, coletor de clipes públicos e clipper param.

**Publicação**
- Botões liga/desliga: `Reels`, `Shorts`, `TikTok`. Plataforma sem credenciais no `.env` aparece desabilitada
  com a dica do que falta.
- Selects: nota mínima da IA (5–9, padrão 6), intervalo entre posts (15/30/45/60 min, padrão 45),
  limite por dia (4/8/12/20, padrão 8).
- Botão `⏸ Pausar publicações` / `▶ Retomar`.

**Status** (editado a cada 60 s)
```
📡 Automático: 8 lives monitoradas · 3 picos na última hora
🔗 Sob demanda: processando "Podpah #1049" (renderizando 3/8) · 1 na fila
🧠 IA: 41 avaliações hoje · Gemini ✅ | ⚠️ fora do ar desde 18:20
📥 Aprovação: 6 aguardando
📤 Hoje: 4 publicados · próximo às 21:30
```

**Persistência:** todas as escolhas ficam em `kv` (`panel:auto_on`, `panel:categories`, `panel:min_viewers`,
`panel:platforms`, `panel:min_score`, `panel:post_interval_min`, `panel:post_max_per_day`, `paused`).
O `.env` só fornece os valores iniciais quando a chave ainda não existe.
Um objeto `RuntimeSettings` lê esses valores (com cache curto) e é o único ponto que o resto do código consulta.

## 5. Sob demanda (`#gerar-clipes`)

### 5.1 Entrada
- Mensagem do admin com uma URL `http(s)`; o texto depois da URL (até 500 caracteres) é a instrução para a IA.
- Aceitos: YouTube (vídeo comum, live encerrada), VOD/highlight da Twitch, VOD da Kick — qualquer coisa que o
  `yt-dlp` resolva como vídeo gravado. Live em andamento → ❌ "live em andamento ainda não é suportada (V2.1)".
- Limite: duração ≤ 4 h (`ONDEMAND_MAX_HOURS`, padrão 4). Um job por vez; os outros esperam em fila (FIFO).
- O bot reage ⏳, cria uma thread na mensagem e mantém nela **uma** mensagem de progresso, editada só quando muda.

### 5.2 Etapas
Cada etapa grava o resultado em `data/jobs/<job_id>/` e o passo concluído na tabela `jobs`. No reinício do app,
jobs `running` voltam a `queued` e retomam da última etapa concluída.

1. **Metadados** — `yt-dlp -J`: título, canal, duração, `is_live`, `heatmap` ("Mais repetidos" do YouTube,
   quando existir).
2. **Áudio** — baixa só o áudio (`-f bestaudio`), converte para MP3 mono 16 kHz 48 kbps.
3. **Transcrição** — Groq com timestamps por palavra. O áudio é cortado em pedaços de ~20 min (limite de 25 MB
   do Groq) e os tempos são somados ao juntar.
4. **Sinais** — curva de 1 ponto por segundo, cada sinal normalizado 0–1 por percentis (p25–p99):
   - `loudness`: subida do volume (RMS em dB via `ffmpeg`) acima da média móvel de 60 s — risada, grito, aplauso;
   - `heatmap`: "Mais repetidos" interpolado (só YouTube);
   - `chat`: densidade de mensagens de reação ("kkkk", "mds", "pqp", emotes de risada) no replay do chat —
     YouTube via `yt-dlp --sub-langs live_chat`, Twitch via API GraphQL não oficial. **Opcional**: qualquer
     erro só remove o sinal.
   - Pontuação = média ponderada dos sinais presentes (pesos iniciais: heatmap 0,45, chat 0,35, loudness 0,20,
     renormalizados quando falta algum) + bônus de 0,15 quando ≥ 2 sinais passam de 0,5 no mesmo trecho.
5. **Picos** — os 20 maiores da curva suavizada (média de 5 s), com ≥ 90 s entre eles.
6. **Escolha dos assuntos (IA de texto)** — uma chamada ao Gemini com, para cada pico, a transcrição de
   150 s antes a 90 s depois (com marcações de tempo), mais a instrução do admin. A IA devolve, por pico:
   `keep` (bool), `start`/`end` do assunto completo (em segundos), `why` (1 frase). Assuntos podem ir de
   ~20 s a ~3 min. Picos que caem no mesmo assunto são unidos.
7. **Acabamento das bordas** (determinístico, sem IA):
   - início e fim ajustados para o fim de frase (`.?!`, sem contar reticências) ou pausa ≥ 0,5 s mais próximos,
     dentro de ±8 s; nunca no meio de uma palavra;
   - o fim estende sobre a reação (risada/aplauso) até o volume voltar a ≤ 2 dB acima da base local, sem invadir
     a próxima palavra;
   - pré-roll mínimo de 6 s antes do pico;
   - assunto com menos de 15 s depois do ajuste é descartado;
   - **séries:** assunto ≤ 65 s → 1 clipe; maior → 2 ou 3 partes de 25–58 s cada, cortadas na melhor fronteira
     de frase/pausa (a que deixa as partes mais equilibradas); assunto > 174 s é aparado em volta do pico.
8. **Download dos trechos** — `yt-dlp --download-sections "*ini-fim" --force-keyframes-at-cuts`, até 1080p,
   com 3 tentativas e espera crescente.
9. **Avaliação em vídeo** — a mesma da V1 (`Evaluator`), com o prompt ampliado: além de `facecam`, devolve
   `subject` (retângulo do rosto principal, para podcast) e `layout` (`game` | `podcast`).
   Série é avaliada pela Parte 1 (com a transcrição da série inteira como contexto); a nota vale para todas.
   Nota < nota mínima do painel ou `safe=false` → descartado.
10. **Render** — ver §7.
11. **Entrega** — cada corte/série vai para o `#aprovação`. A thread termina com ✅ e um resumo
    ("8 cortes enviados (2 séries), 5 descartados pela IA"). Sem nenhum aprovado pela IA: ✅ com
    "nenhum corte passou da nota mínima" e os 3 melhores descartados com nota e motivo.

### 5.3 Falhas
- Link inválido, privado, live em andamento ou longo demais → ❌ na thread com o motivo; job `failed`.
- IA indisponível em qualquer etapa → o job fica `waiting_ai` na mesma etapa; tenta de novo com a espera
  crescente (1, 2, 4, 8, máx. 15 min) que já existe no `Pipeline`; a thread mostra "aguardando a IA".
- Falha de download/render de um corte → aquele corte é pulado e o motivo vai para a thread; os outros seguem.
- Transcrição sem fala (música, silêncio) → ✅ com "nenhuma fala encontrada".

## 6. Automático (motor da V1)

- Mesmo pipeline de hoje (descoberta → chat → pico → criar clipe → baixar → transcrever → avaliar), com
  categorias, mínimo de espectadores e nota mínima vindos do `RuntimeSettings`.
- Um `AutoController` liga/desliga as tarefas `chat`, `discovery`, `clipper` e `twitch_clips` do supervisor.
  `pipeline`, `scheduler` e o restante continuam sempre ativos (servem aos dois modos).
- Mudança de categoria resolve os IDs na Twitch (`get_game_ids`) e reinicia a descoberta.
- Clipes aprovados pela IA deixam de ir para `queued` e passam a `awaiting_approval` (§8).
- Lista de bots do chat ganha `botrix` e `botrixoficial`.

## 7. Render

- **Layout `game`** (V1, sem mudança): webcam 1080×768 em cima, jogo 1080×1152 embaixo; sem webcam, jogo em
  tela cheia.
- **Layout `podcast`** (novo): recorte 9:16 do quadro original centrado horizontalmente no `subject` devolvido
  pela IA (com margem; o recorte nunca sai do quadro). Sem `subject` válido: vídeo inteiro centralizado sobre
  fundo desfocado.
- Título-gancho, legendas e animação da V1 valem para os dois layouts.
- **Séries:** etiqueta `PARTE N/M` no topo durante todo o clipe; nas partes 1 a M-1, cartão final de ~1,5 s
  "PARTE N+1 NO PERFIL 👆"; nas partes 2+, o título-gancho é substituído por uma frase curta de contexto gerada
  na avaliação (`series_recap`).

## 8. Aprovação (`#aprovação`) e publicação

### 8.1 Mensagem
Uma por corte, ou uma por série com todas as partes anexadas:
- vídeo(s); se passar de `DISCORD_UPLOAD_LIMIT_MB` (padrão 10): recomprime para caber; se ainda não couber,
  link temporário pelo `MediaServer`/túnel;
- título-gancho, legenda e hashtags que serão publicados;
- nota e motivo da IA;
- origem: `🔗 <título do vídeo> · 1:23:40` ou `📡 Automático · <canal> · pico 12x`;
- plataformas que vão receber (as ligadas no painel no momento da aprovação).

### 8.2 Botões
- `✅ Aprovar` → `approved`, entra na fila respeitando intervalo e limite diário.
- `⚡ Agora` → `approved` com prioridade; publica no próximo tick ignorando o intervalo (não o limite diário).
- `✏️ Editar` → modal com título-gancho e legenda; salvar refaz o render (o título está queimado no vídeo) e
  atualiza a mensagem.
- `❌ Descartar` → `discarded`, apaga os arquivos.
Cortes pendentes não expiram; os do automático mostram "⏰ há N h" após 2 h.

### 8.3 Publicação
- O `Scheduler` só pega candidatos `approved` (e o `paused` do painel continua valendo).
- Plataformas: Reels (Graph API, público), Shorts (`YT_MODE`: `private` até a auditoria), TikTok
  (`TIKTOK_MODE=draft`: upload para a caixa de entrada via `/post/publish/inbox/video/init/`, escopo
  `video.upload`; `private` e `public` continuam existindo).
- **Séries:** as partes são publicadas em sequência, da última para a primeira, com 2 min entre elas, contando
  como **um** post para intervalo e limite diário. No TikTok (rascunho) a mensagem avisa a ordem em que o admin
  deve publicar.
- A mensagem de aprovação vira o status: `✅ aprovado → na fila (posição 2, ~21:30) → Reels ✅ <link> ·
  Shorts 🔒 privado · TikTok 📥 rascunho`. Falha numa plataforma: ❌ com o motivo e botão `🔁 Tentar de novo`
  só para ela.
- `DRY_RUN` é removido: com todas as plataformas desligadas, aprovar não publica nada.

## 9. Dados

**Tabela nova `jobs`** (sob demanda):
`id, url, instructions, state (queued|running|waiting_ai|done|failed), step, progress_text, title, duration,
thread_id, status_message_id, error, created_at, updated_at`.

**Tabela `candidates`** (reaproveitada para os dois modos):
- `source` ganha `ondemand`; `platform` ganha `youtube`;
- colunas novas: `job_id`, `series_id` (texto, igual para todas as partes), `part`, `parts`, `start_s`,
  `end_s` (posição no vídeo original), `approval_message_id`;
- estados novos: `awaiting_approval`, `approved`, `discarded`. `queued` deixa de ser usado por código novo.

**Tabela `publications`**: sem mudança (uma linha por parte por plataforma).

Migração no mesmo estilo do `_migrate()` atual (`ALTER TABLE ... ADD COLUMN` quando faltar).

## 10. Organização do código

```
src/liveclips/
  control/
    discord_bot.py      # passa a registrar views persistentes e o listener do #gerar-clipes
    channels.py         # cria/recupera a categoria e os 4 canais
    runtime.py          # RuntimeSettings (kv + defaults do .env)
    panel.py            # embed + componentes do #painel, atualização a cada 60 s
    approval.py         # mensagens e botões do #aprovação
    auto.py             # AutoController (liga/desliga tarefas do automático)
  ondemand/
    jobs.py             # fila, estados, retomada, mensagens de progresso na thread
    ingest.py           # metadados, áudio, chat replay, download de trechos
    signals.py          # loudness, heatmap, chat → curva 1 Hz
    peaks.py            # escolha dos picos
    topics.py           # IA de texto: início/fim dos assuntos
    snap.py             # bordas em frase/pausa, reação, séries
  processing/layout.py  # + layout podcast
  ai/prompt.py, verdict.py  # + subject, layout, series_recap; prompt de assuntos
  publishing/scheduler.py   # só approved; séries; status na mensagem de aprovação
  publishing/platforms/tiktok.py  # + modo draft
```
Cada módulo de `ondemand/` é puro onde der (recebe arrays/listas e devolve listas), para testar sem rede.

## 11. Configuração nova (`.env`)

`DISCORD_GUILD_ID`, `DISCORD_UPLOAD_LIMIT_MB=10`, `ONDEMAND_MAX_HOURS=4`, `TIKTOK_MODE=off|draft|private|public`.
Removidos: `DRY_RUN`. `TWITCH_GAME_NAME`, `MIN_VIEWERS`, `AI_MIN_SCORE`, `POST_MIN_INTERVAL_MIN`,
`POST_MAX_PER_DAY` passam a ser só valores iniciais do painel.

## 12. Testes

- `signals`, `peaks`, `snap`: dados sintéticos (curvas e listas de palavras) com métricas fixas —
  0% de cortes no meio de palavra, 100% das bordas em fim de frase/pausa, pico entre 35% e 75% do clipe,
  partes de série entre 25 e 58 s.
- `topics`: IA falsa devolvendo JSON fixo; união de picos no mesmo assunto; JSON inválido → erro tratado.
- `jobs`: retomada após reinício em cada etapa; fila FIFO; `waiting_ai`.
- `approval`/`panel`: handlers chamados com interações falsas; só admin; estados e kv atualizados.
- `scheduler`: só `approved`; ordem das partes; série conta como 1 post; `⚡ Agora` ignora intervalo.
- `tiktok`: modo `draft` chama o endpoint de inbox (respx).
- Um teste manual documentado com um podcast real do YouTube antes de dar a V2 como pronta.

## 13. Riscos

| Risco | Mitigação |
|---|---|
| API GraphQL do chat da Twitch muda | Sinal opcional; falha só remove o chat |
| Cota do Gemini | 1 chamada de texto + ~10–15 vídeos curtos por vídeo longo; espera crescente; reserva do Claude se `ANTHROPIC_API_KEY` existir |
| YouTube bloqueia o `yt-dlp` | Manter `yt-dlp` atualizado na imagem; erro claro na thread |
| Upload do Discord pequeno | Recompressão e, em último caso, link pelo túnel |
| Shorts/TikTok sem auditoria | Privado/rascunho até a auditoria; Reels publica público desde já |
