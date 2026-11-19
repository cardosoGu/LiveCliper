"""A IA lê a transcrição em volta de cada pico e decide onde começa e termina o assunto."""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from ..ai.text import ask_json
from .snap import MIN_POST_ROLL, MIN_PRE_ROLL, sentences

WINDOW_BEFORE = 150
WINDOW_AFTER = 90

TOPICS_PROMPT = """Você é editor de cortes de um perfil brasileiro de clipes (Reels, TikTok, Shorts).
Abaixo estão trechos da transcrição de um vídeo, um por PICO de interesse (risada, chat
explodindo, parte mais repetida pelo público). Cada linha começa com o tempo [m:ss].

Para CADA pico, decida se ali tem um corte que prende quem rola o feed:
- manter=true só se houver um momento claro (história engraçada, treta, revelação, reação
  forte, frase marcante) que se entende sem o resto do vídeo;
- inicio e fim em SEGUNDOS desde o começo do vídeo, cobrindo o ASSUNTO COMPLETO (de ~20 s a
  ~3 min): comece onde o assunto começa e termine depois do desfecho/reação;
- motivo: 1 frase.
Responda só o JSON pedido, com um item por pico (campo "pico" = número do pico).

Vídeo: {title}
Instrução do admin: {instructions}"""


class TopicCut(BaseModel):
    pico: int
    manter: bool
    inicio: float
    fim: float
    motivo: str


class TopicsAnswer(BaseModel):
    cortes: list[TopicCut]


@dataclass
class Topic:
    peak: float
    start: float
    end: float
    why: str


def fmt_time(t: float) -> str:
    m, s = divmod(int(t), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def build_topics_prompt(title: str, words: list[dict], peaks: list[int],
                        instructions: str) -> str:
    sents = sentences(words)
    blocks = []
    for k, p in enumerate(peaks):
        lo, hi = p - WINDOW_BEFORE, p + WINDOW_AFTER
        lines = [f"[{fmt_time(a)}] {txt}" for a, b, txt in sents if b >= lo and a <= hi]
        blocks.append(f"### Pico {k} (em {fmt_time(p)})\n" + ("\n".join(lines) or "(sem fala)"))
    head = TOPICS_PROMPT.format(title=title or "(sem título)",
                                instructions=instructions.strip() or "(nenhuma)")
    return head + "\n\n" + "\n\n".join(blocks)


def parse_topics(answer: TopicsAnswer, peaks: list[int]) -> list[Topic]:
    chosen = []
    for c in answer.cortes:
        if not c.manter or not 0 <= c.pico < len(peaks) or c.fim <= c.inicio:
            continue
        peak = min(max(float(peaks[c.pico]), c.inicio + MIN_PRE_ROLL), c.fim - MIN_POST_ROLL)
        chosen.append(Topic(peak, float(c.inicio), float(c.fim), c.motivo))
    chosen.sort(key=lambda t: t.start)
    merged: list[Topic] = []
    for t in chosen:  # picos no mesmo assunto viram um corte só
        if merged and t.start < merged[-1].end:
            merged[-1].end = max(merged[-1].end, t.end)
        else:
            merged.append(t)
    return merged


async def choose_topics(cfg, title: str, words: list[dict], peaks: list[int],
                        instructions: str, ask=ask_json, db=None) -> list[Topic]:
    if not peaks:
        return []
    answer = await ask(cfg, build_topics_prompt(title, words, peaks, instructions),
                       TopicsAnswer, db=db)
    return parse_topics(answer, peaks)
