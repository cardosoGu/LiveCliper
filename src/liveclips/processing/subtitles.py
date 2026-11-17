"""Legendas curtas, sincronizadas por palavra, posicionadas fora da área do jogo.

O arquivo é ASS com PlayRes = tamanho real do vídeo (1080x1920), então fonte e
margens são em pixels de verdade. (Com SRT o libass usa uma tela virtual de 288 px
de altura e uma fonte "13" vira ~87 px.)
"""
from __future__ import annotations

from dataclasses import dataclass, field

CANVAS_W, CANVAS_H = 1080, 1920
FONT = "Montserrat"
FONT_SIZE = 72
LINE_H = round(FONT_SIZE * 1.25)
MAX_LINES = 2
GAP_BELOW_VIDEO = 36
SIDE_MARGIN = 70
BOTTOM_SAFE = 140  # se não couber abaixo do jogo, fica sobre a base dele, longe da borda

MAX_WORDS = 3
MAX_CHARS = 20  # 1-2 linhas curtas, estilo Shorts
MAX_CUE_S = 2.6
MAX_GAP_S = 0.6
MIN_CUE_S = 0.35
SENTENCE_END = ".!?…"


HIGHLIGHT = "&H00FF5FC8&"  # roxo/rosa (ASS usa BGR) na palavra que está sendo falada


@dataclass
class Cue:
    start: float
    end: float
    text: str
    words: list = field(default_factory=list)  # [(palavra, início, fim)] para o destaque


def words_from_segments(segments: list[dict]) -> list[dict]:
    """Sem tempo por palavra: reparte o tempo de cada trecho entre as palavras,
    proporcional ao tamanho delas."""
    out: list[dict] = []
    for seg in segments:
        words = seg["text"].split()
        if not words:
            continue
        total = sum(len(w) for w in words)
        t = seg["start"]
        dur = max(seg["end"] - seg["start"], 0.01)
        for w in words:
            d = dur * len(w) / total
            out.append({"word": w, "start": t, "end": t + d})
            t += d
    return out


def build_cues(words: list[dict]) -> list[Cue]:
    """Agrupa palavras em blocos curtos que trocam no ritmo da fala."""
    cues: list[Cue] = []
    cur: list[dict] = []

    def flush() -> None:
        if cur:
            cues.append(Cue(cur[0]["start"], cur[-1]["end"],
                            " ".join(w["word"] for w in cur),
                            [(w["word"], w["start"], w["end"]) for w in cur]))
            cur.clear()

    for w in words:
        text = w["word"].strip()
        if not text:
            continue
        if cur:
            length = len(" ".join(x["word"] for x in cur)) + 1 + len(text)
            if (len(cur) >= MAX_WORDS or length > MAX_CHARS
                    or w["start"] - cur[-1]["end"] > MAX_GAP_S
                    or w["start"] - cur[0]["start"] > MAX_CUE_S):
                flush()
        cur.append({"word": text, "start": w["start"], "end": w["end"]})
        if text[-1] in SENTENCE_END:
            flush()
    flush()

    for i, c in enumerate(cues):  # duração mínima legível, sem sobrepor a próxima
        c.end = max(c.end, c.start + MIN_CUE_S)
        if i + 1 < len(cues):
            c.end = min(c.end, cues[i + 1].start)
    return cues


def caption_position(src_w: int, src_h: int) -> tuple[int, int]:
    """(alinhamento ASS, margem vertical). Normalmente logo abaixo do vídeo
    (alinhado pelo topo); se o vídeo ocupa quase a tela toda, sobre a base dele."""
    video_h = min(round(CANVAS_W * src_h / max(src_w, 1)), CANVAS_H)
    video_bottom = (CANVAS_H + video_h) // 2
    text_top = video_bottom + GAP_BELOW_VIDEO
    if text_top + MAX_LINES * LINE_H <= CANVAS_H - BOTTOM_SAFE:
        return 8, text_top  # 8 = topo-centro; MarginV conta do topo
    return 2, BOTTOM_SAFE  # 2 = base-centro; MarginV conta da base


def _ass_time(t: float) -> str:
    cs = int(round(max(t, 0) * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def _ass_text(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def cues_to_ass(cues: list[Cue], src_w: int, src_h: int) -> str:
    align, margin_v = caption_position(src_w, src_h)
    header = "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {CANVAS_W}",
        f"PlayResY: {CANVAS_H}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{FONT},{FONT_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,"
        f"-1,0,0,0,100,100,0,0,1,6,3,{align},{SIDE_MARGIN},{SIDE_MARGIN},{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ])
    events = []
    for c in cues:
        if not c.words:
            events.append(f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},Default,,0,0,0,,"
                          f"{_ass_text(c.text).upper()}")
            continue
        # estilo karaokê: o bloco inteiro na tela, a palavra falada em destaque
        for i, (_, start, _end) in enumerate(c.words):
            end = c.words[i + 1][1] if i + 1 < len(c.words) else c.end
            parts = []
            for j, (w, _s, _e) in enumerate(c.words):
                w = _ass_text(w).upper()
                parts.append(f"{{\\c{HIGHLIGHT}\\fscx112\\fscy112}}{w}{{\\r}}" if j == i else w)
            events.append(f"Dialogue: 0,{_ass_time(max(start, c.start))},{_ass_time(max(end, start + 0.05))},"
                          f"Default,,0,0,0,,{' '.join(parts)}")
    return header + "\n" + "\n".join(events) + "\n"


def anchor_ass(ass: str, x: int, y: int) -> str:
    """Prende cada fala no ponto (x, y), centrada (\\an5): no render final a legenda
    fica na emenda entre webcam e jogo, qualquer que seja o layout."""
    out = []
    for line in ass.splitlines():
        if line.startswith("Dialogue:"):
            fields = line.split(",", 9)
            if len(fields) == 10:
                fields[9] = f"{{\\an5\\pos({x},{y})}}" + fields[9]
                line = ",".join(fields)
        out.append(line)
    return "\n".join(out) + "\n"
