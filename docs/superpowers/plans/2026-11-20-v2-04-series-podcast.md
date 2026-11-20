# V2 · Plano 4 — Séries "Parte 1/2/3" e layout de podcast

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Assunto longo (mais de 65 s) vira uma série de 2 ou 3 partes (25–58 s cada, cortadas em fim de frase), avaliada uma vez, aprovada em bloco, com "PARTE N/M" no vídeo, aviso "VEJA A PARTE N+1 NO PERFIL" no fim, e publicada da última para a primeira. Vídeo sem jogo na tela (podcast, Just Chatting) passa a ser recortado no rosto de quem fala.

**Architecture:** Cada parte é um `Candidate` com `series_id`, `part` e `parts`. O `OnDemandRunner` corta o assunto inteiro (até 174 s) e divide em partes (`ondemand/series.py`, puro). O `Pipeline` avalia só a Parte 1 (com a transcrição da série inteira) e copia a decisão para as outras partes; a IA também devolve uma frase de contexto (`series_recap`) para as partes 2+, o retângulo do rosto (`subject`) e o `layout` (`game` | `podcast`). O render ganha uma camada ASS da série e o layout de podcast (`layout.podcast_layout`). O `#aprovação` mostra a série num card (botões na Parte 1, as outras partes logo abaixo, sem botões) e as ações valem para a série. O agendador publica a série como um post só, da última parte para a primeira.

**Tech Stack:** Python 3.12, ffmpeg (ASS), pydantic, discord.py 2.x, SQLite, pytest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-11-17-v2-discord-painel-design.md` §5.2 (passo 7), §7, §8 — e as decisões do usuário de 2026-11-17: série aprovada **em bloco**; publicação da **última para a primeira**; Parte 1 com título-gancho e aviso de continuação; partes 2+ com etiqueta "PARTE N" e uma frase de contexto.
- Números: assunto ≤ 65 s → clipe único; maior → 2 ou 3 partes de 25–58 s; assunto > 174 s (3 × 58) é aparado em volta do pico; cortes entre partes em fim de frase/pausa.
- A nota da Parte 1 vale para a série; série insegura ou abaixo da nota mínima é descartada inteira.
- Série conta como **um** post no limite diário e no intervalo; partes publicadas com 120 s entre elas.
- O texto queimado no vídeo não usa emoji (a fonte não desenha): o aviso final é `VEJA A PARTE {n+1} NO PERFIL`.
- Layout `podcast`: recorte 9:16 do quadro inteiro, centrado no rosto (`subject`); sem `subject` válido, centro do quadro.
- Textos em português do Brasil. Testes: `.venv/Scripts/python.exe -m pytest -q` (sem rede) — a suíte inteira passa ao fim de cada tarefa.
- Commits terminam com uma linha em branco e `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

---

### Task 1: Dados e decisão da IA

**Files:** `src/liveclips/core/models.py`, `src/liveclips/core/db.py`, `src/liveclips/ai/verdict.py`, `src/liveclips/ai/prompt.py`; tests `tests/core/test_db.py`, `tests/ai/test_evaluator.py` (ou um novo `tests/ai/test_verdict.py`).

**Interfaces:**
- `Candidate.series_id: str | None = None`, `Candidate.part: int | None = None`, `Candidate.parts: int | None = None` (colunas `series_id TEXT`, `part INTEGER`, `parts INTEGER`; `_COLS` e `_NEW_COLUMNS`; índice `idx_cand_series` criado no `_migrate` depois das colunas).
- `DB.series_parts(series_id: str) -> list[Candidate]` (ordenado por `part`).
- `Verdict` ganha: `series_recap: str | None = None`, `subject: FaceCam | None = None`, `layout: str = "game"`. `VERDICT_SCHEMA` (Claude) ganha `series_recap` (`string` ou `null`), `subject` (mesmo esquema do `facecam` ou `null`) e `layout` (`enum: ["game", "podcast"]`), todos em `required`.
- `valid_subject(fc) -> FaceCam | None`: aceita retângulo dentro do quadro (mesma tolerância de borda do `valid_facecam`), sem limite de área máxima (um rosto em close pode ser grande), área mínima 1%.
- `parse_verdict` aplica `valid_subject` ao `subject` e força `layout` para `"game"` se vier outro valor.
- `build_prompt(meta)`: se `meta.get("parts")`, acrescenta uma linha `Série: este assunto foi dividido em {parts} partes; a transcrição abaixo é da série inteira. Avalie a série toda.`
- `SYSTEM_PROMPT` ganha (no fim, antes de "facecam"):
  - `series_recap`: quando o prompt disser que é uma série, 1 frase curta (até ~40 caracteres, sem emoji) que situa quem cai direto na Parte 2 ou 3 (ex.: "A HISTÓRIA DO ASSALTO CONTINUA"); senão `null`.
  - `layout`: `"podcast"` quando não há jogo na tela (conversa, podcast, Just Chatting, IRL); senão `"game"`.
  - `subject`: retângulo do rosto de quem mais fala (mesmo formato do facecam), ou `null`.

- [ ] **Step 1 (tests):** `test_series_fields_roundtrip_and_series_parts` (3 partes inseridas fora de ordem → `series_parts` devolve 1, 2, 3); teste de migração (banco antigo sem as colunas) no mesmo estilo do existente; `test_verdict_defaults_and_subject_validation` (`parse_verdict` de um JSON sem os campos novos → `series_recap None`, `subject None`, `layout "game"`; `layout "xyz"` → `"game"`; `subject` fora do quadro → `None`); `test_prompt_mentions_series` (`build_prompt({"parts": 3, ...})` contém "3 partes").
- [ ] **Step 2:** rodar → falham.
- [ ] **Step 3:** implementar.
- [ ] **Step 4:** suíte inteira → passa.
- [ ] **Step 5:** commit `feat: dados de série (series_id/part/parts) e IA devolve series_recap, subject e layout`.

---

### Task 2: Cortar o assunto inteiro e dividir em partes

**Files:** Create `src/liveclips/ondemand/series.py`; Modify `src/liveclips/ondemand/snap.py`, `src/liveclips/ondemand/jobs.py`; tests `tests/ondemand/test_series.py`, `tests/ondemand/test_jobs.py`.

**Interfaces:**
- `snap.snap_clip(words, peak, start, end, rms_db=None, max_len=MAX_LEN)` — o `MAX_LEN` fixo vira parâmetro (padrão 65, comportamento atual inalterado).
- `series.py`:
  - `SINGLE_MAX = 65.0`, `PART_MIN = 25.0`, `PART_MAX = 58.0`, `SERIES_MAX = 174.0`
  - `split_parts(words: list[dict], start: float, end: float) -> list[tuple[float, float]] | None` — `end - start <= SINGLE_MAX` → `[(start, end)]`; senão tenta 2 partes, depois 3, cada uma entre `PART_MIN` e `PART_MAX`, com o corte entre partes num fim de frase/pausa (`snap.sentence_ends`) e a parte seguinte começando no próximo começo de frase (`snap.sentence_starts`); escolhe a divisão com partes de duração mais parecida; nenhuma divisão válida → `None`.

```python
from .snap import sentence_ends, sentence_starts

SINGLE_MAX = 65.0
PART_MIN = 25.0
PART_MAX = 58.0
SERIES_MAX = 174.0


def _next_start(starts: list[float], t: float) -> float | None:
    return next((s for s in starts if s >= t - 1e-6), None)


def split_parts(words, start, end):
    if end - start <= SINGLE_MAX:
        return [(start, end)]
    starts, ends = sentence_starts(words), sentence_ends(words)
    cuts = [e for e in ends if start < e < end]
    best = None
    for k in (2, 3):
        options = []
        if k == 2:
            for c1 in cuts:
                s2 = _next_start(starts, c1)
                if s2 is not None:
                    options.append([(start, c1), (s2, end)])
        else:
            for i, c1 in enumerate(cuts):
                s2 = _next_start(starts, c1)
                if s2 is None:
                    continue
                for c2 in cuts[i + 1:]:
                    s3 = _next_start(starts, c2)
                    if s3 is not None and c2 > s2:
                        options.append([(start, c1), (s2, c2), (s3, end)])
        valid = [o for o in options if all(PART_MIN <= b - a <= PART_MAX for a, b in o)]
        if valid:
            best = min(valid, key=lambda o: max(b - a for a, b in o) - min(b - a for a, b in o))
            break
    return [(round(a, 2), round(b, 2)) for a, b in best] if best else None
```

- `jobs._cuts`: para cada tópico, `snap_clip(..., max_len=SERIES_MAX)`; se a duração passar de `SINGLE_MAX`, `split_parts`; se `split_parts` devolver `None`, cair para `snap_clip(...)` normal (65 s). Uma série gera N candidatos com `series_id = f"{job_id}:{int((source.start + s) * 10)}"` (o início absoluto da série), `part = i`, `parts = N`, e `clip_id` de cada parte pelo seu próprio início absoluto (como hoje). Clipe único: `series_id/part/parts = None`. O progresso fica `✂️ recortando i/N…` contando partes. A retomada de cortes órfãos continua valendo por `clip_id`.

- [ ] **Step 1 (tests):** em `tests/ondemand/test_series.py`, com o helper `make_words` de `test_peaks_snap.py` (frases de 5 palavras, ~3 s cada): assunto de 60 s → 1 parte igual à entrada; assunto de ~100 s → 2 partes, cada uma entre 25 e 58 s, cada início é começo de frase e cada fim é fim de frase, sem sobreposição e em ordem; assunto de ~150 s → 3 partes; texto sem nenhum fim de frase no meio (uma única "palavra" longa) → `None`. Em `test_jobs.py`: um tópico de 30–130 s (fake `topics`) gera 2 candidatos com o mesmo `series_id`, `part` 1 e 2, `parts` 2, e os dois `DETECTED`; o teste atual de clipe único continua com `series_id None`.
- [ ] **Step 2:** rodar → falham. **Step 3:** implementar. **Step 4:** suíte → passa.
- [ ] **Step 5:** commit `feat: assunto longo vira série de 2 ou 3 partes cortadas em fim de frase`.

---

### Task 3: Avaliar a série uma vez

**Files:** `src/liveclips/processing/pipeline.py`; tests `tests/processing/test_pipeline.py`.

**Comportamento:**
- `tick`: só entram no lote candidatos `DETECTED` com `part in (None, 1)`; partes 2+ esperam a Parte 1.
- `process(c)` com `c.series_id`: lê `parts = db.series_parts(c.series_id)`; se alguma parte não tem `raw_path` existente ou `transcript`, a série inteira vai para `FAILED` ("trecho sumiu antes da avaliação"); o `meta` leva `transcript` = transcrições de todas as partes juntas (separadas por `" … "`) e `parts = c.parts`.
- Depois da decisão, cada parte recebe o mesmo `state`, `ai_score` e `reason`; o `ai_json` da Parte 1 é a decisão como veio; o das partes 2+ é a mesma decisão com `title = verdict.series_recap or f"PARTE {n}"`.
- `AIUnavailable` com série: só a Parte 1 volta para `DETECTED` (as outras já estão lá).

- [ ] **Step 1 (tests):** série de 3 partes `DETECTED` com raw/transcript → um `tick` avalia **uma** vez (o fake do evaluator conta chamadas), o `meta` recebido tem `parts == 3` e a transcrição das 3 partes; as 3 ficam `AWAITING_APPROVAL` com a mesma nota; a Parte 2 tem `title == series_recap`. Nota baixa → as 3 `REJECTED_AI`. Parte 2 sem raw → as 3 `FAILED`. Partes 2+ nunca são avaliadas sozinhas (só elas `DETECTED` → `tick` devolve 0).
- [ ] **Step 2–4:** falham → implementar → suíte passa.
- [ ] **Step 5:** commit `feat: série é avaliada uma vez (com a transcrição inteira) e a decisão vale para todas as partes`.

---

### Task 4: Render — camada da série e layout de podcast

**Files:** `src/liveclips/processing/ffmpeg.py`, `src/liveclips/processing/layout.py`, `src/liveclips/publishing/scheduler.py` (`ensure_rendered`); tests `tests/processing/test_layout.py`, `tests/processing/test_media.py`, `tests/publishing/test_scheduler.py`.

**Interfaces:**
- `layout.podcast_layout(src_w, src_h, subject: FaceCam | None) -> Layout` — `Layout(cam=None, game=Rect)` com o maior recorte 9:16 do quadro, centrado no centro horizontal do `subject` (ou do quadro), limitado às bordas; `caption_y = FULL_CAPTION_Y`, `title_y = CANVAS_H // 6`, `blur_h = CANVAS_H // 3` (mesmos de "tela cheia").
- `ffmpeg.series_ass(part: int, parts: int, duration: float) -> str` — ASS 1080×1920 com dois eventos (estilo próprio, fonte do título, contorno): `PARTE {part}/{parts}` no topo (y≈110) durante o clipe inteiro; e, só se `part < parts`, `VEJA A PARTE {part+1} NO PERFIL` centralizado (y≈960) nos últimos 1,5 s (`max(0, duration-1.5)` até `duration`), com fade de entrada.
- `render_split(src, dst, credit, subs_path, title, facecam, *, series: tuple[int, int] | None = None, layout: str = "game", subject: FaceCam | None = None)`: `layout == "podcast"` usa `podcast_layout` (e ignora `facecam`); com `series`, mede a duração (`probe_duration`), grava `series.ass` ao lado e aplica como mais um filtro `subtitles` depois do título (estender `split_filter` para aceitar uma lista de camadas ASS extras, mantendo a assinatura atual compatível). `render_final` repassa os mesmos argumentos nomeados (o fallback `render_vertical` ignora série e layout).
- `Scheduler.ensure_rendered` passa `series=(c.part, c.parts) if c.series_id else None`, `layout=verdict.layout`, `subject=verdict.subject`.

- [ ] **Step 1 (tests):** `podcast_layout(1920, 1080, subject no terço direito)` → recorte 9:16 de altura 1080, centrado no rosto e dentro do quadro; sem subject → centro; subject na borda → encostado na borda. `series_ass(1, 3, 40.0)` contém `PARTE 1/3` e `VEJA A PARTE 2 NO PERFIL` começando em `0:00:38.50`; `series_ass(3, 3, 40.0)` não tem o aviso. `split_filter` com camada extra inclui os dois `subtitles=`. Teste do `ensure_rendered`: o `render_fn` falso recebe `series=(2, 3)` e `layout="podcast"` para uma parte de série com veredito de podcast. (Os testes de render com ffmpeg real, se existirem, seguem o padrão `skipif` atual.)
- [ ] **Step 2–4:** falham → implementar → suíte passa.
- [ ] **Step 5:** commit `feat: vídeo mostra PARTE N/M e o aviso da próxima parte; layout de podcast recortado no rosto`.

---

### Task 5: `#aprovação` — série num card só

**Files:** `src/liveclips/control/approval.py`; tests `tests/control/test_approval.py`.

**Comportamento:**
- `tick`: para candidato com `series_id`, só age pela Parte 1 e só quando **todas** as partes estão `AWAITING_APPROVAL` e sem card; renderiza todas (se alguma falhar, a série toda vai para `FAILED`); posta o card da Parte 1 com botões e, em seguida, cada parte 2+ como mensagem própria **sem botões** (cada uma guarda o seu `approval_message_id`). Partes 2+ nunca geram card próprio sozinhas.
- `card_text` da Parte 1 ganha a linha `🎞️ Série em {parts} partes — os botões valem para todas`; o das partes 2+ é curto: `🎞️ Parte {part}/{parts} — {título}`.
- `handle` (aprovar/agora/descartar) numa parte de série aplica a todas as partes (mesmo `state`/`priority`; descartar apaga a mídia de todas) e atualiza o status de cada mensagem.
- `edit` numa série: troca o título-gancho só da Parte 1 e a legenda de todas; apaga os `final.mp4`, zera `vertical_path` e `approval_message_id` de todas e marca as mensagens antigas como "✏️ Editado"; o próximo `tick` refaz e reposta a série.
- `status_line` de uma série publicada: cada mensagem mostra o status da própria parte; a da Parte 1 acrescenta, se o TikTok estiver entre as publicações sem link (rascunho), `📌 TikTok: publique as partes do app na ordem {parts} → 1`.

- [ ] **Step 1 (tests):** série de 2 partes `AWAITING_APPROVAL` → um `tick` posta 2 mensagens (a 1ª com botões, a 2ª sem) e não reposta no próximo; com só a Parte 1 pronta e a 2 ainda `DETECTED` → não posta nada; `approve` na Parte 1 → as 2 `APPROVED`; `discard` → as 2 `DISCARDED` e pastas apagadas; `edit` → as 2 com `approval_message_id None`, a Parte 1 com o título novo e a Parte 2 mantendo o `series_recap`; texto do card da Parte 1 contém "Série em 2 partes".
- [ ] **Step 2–4:** falham → implementar → suíte passa.
- [ ] **Step 5:** commit `feat: #aprovação mostra a série num card, com as ações valendo para todas as partes`.

---

### Task 6: Publicar a série da última para a primeira

**Files:** `src/liveclips/publishing/scheduler.py`, `src/liveclips/publishing/caption.py`, `src/liveclips/core/db.py`; tests `tests/publishing/test_scheduler.py`, `tests/core/test_db.py`.

**Comportamento:**
- `DB.publications_count_since`: conta séries como um post — `COUNT(DISTINCT COALESCE(c.series_id, CAST(p.candidate_id AS TEXT)))` com `JOIN candidates c ON c.id = p.candidate_id`.
- `Scheduler.tick`: o escolhido (urgente ou o primeiro aprovado) que tiver `series_id` só é publicado quando **todas** as partes estão `APPROVED`; então publica as partes na ordem `parts, parts-1, …, 1`, com `await self.sleep(SERIES_GAP_S)` (120 s; `sleep` injetável no `__init__`, padrão `asyncio.sleep`) entre elas. Uma falha total numa parte não impede as outras; o backoff de falha total vale como hoje.
- `build_caption(verdict, platform, channel, part=None, parts=None)`: com série, a primeira linha vira `({part}/{parts}) {title}` e o corpo termina com `Veja todas as partes no perfil.`

- [ ] **Step 1 (tests):** série de 3 partes `APPROVED` → um `tick` publica na ordem 3, 2, 1 (o publisher falso registra a ordem), com 2 chamadas ao `sleep(120)` falso, e conta como **1** post no limite diário; série com uma parte ainda `AWAITING_APPROVAL` → nada é publicado; legenda da parte 2 começa com `(2/3)`. Em `test_db.py`: 2 publicações de partes da mesma série + 1 clipe único hoje → `publications_count_since == 2`.
- [ ] **Step 2–4:** falham → implementar → suíte passa.
- [ ] **Step 5:** commit `feat: série publicada como um post, da última parte para a primeira, com 2 min entre elas`.

---

### Task 7: Subir e testar de verdade

- [ ] `docker compose up -d --build app`; log sem `Traceback`.
- [ ] Dentro do container, renderizar uma parte de série de um vídeo curto qualquer (pode ser um `raw.mp4` gerado com `ffmpeg -f lavfi`) com `series=(1, 2)` e `layout="podcast"` e conferir com `ffprobe` que sai 1080×1920.
- [ ] Com o usuário: colar um podcast com uma história longa e conferir a série no `#aprovação` (card com "Série em N partes", partes abaixo, "PARTE 1/N" e o aviso no fim do vídeo).
