"""Edição de vídeo com FFmpeg: áudio, vertical 9:16 (tela dividida), prévia e frames."""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from ..ai.verdict import FaceCam
from .layout import CAM_H, GAME_H, Layout, split_layout
from .subtitles import anchor_ass

log = logging.getLogger(__name__)

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


class MediaError(Exception):
    pass


async def run(*cmd: str, timeout: float = 300) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise MediaError(f"{cmd[0]}: tempo esgotado")
    if proc.returncode != 0:
        raise MediaError(f"{cmd[0]} falhou ({proc.returncode}): "
                         f"{err.decode(errors='ignore')[-500:]}")
    return out.decode(errors="ignore")


async def probe_duration(path: Path) -> float:
    out = await run("ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=nw=1:nk=1", str(path))
    return float(out.strip())


async def probe_dimensions(path: Path) -> tuple[int, int]:
    out = await run("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                    "stream=width,height", "-of", "csv=p=0", str(path))
    w, h = out.strip().splitlines()[0].split(",")[:2]
    return int(w), int(h)


def build_filter(credit: str, srt_path: str | None) -> str:
    parts = [
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=20:2[bg]",
        "[0:v]scale=1080:-2[fg]",
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[base]",
        f"[base]drawtext=fontfile={FONT}:text='{credit}':fontcolor=white:fontsize=38:"
        "x=(w-text_w)/2:y=h-170:box=1:boxcolor=black@0.55:boxborderw=12[cred]",
    ]
    if srt_path:  # arquivo .ass: estilo, tamanho e posição vêm do próprio arquivo
        parts.append(f"[cred]subtitles=filename='{srt_path}'[v]")
    else:
        parts.append("[cred]null[v]")
    return ";".join(parts)


async def render_vertical(src: Path, dst: Path, credit: str,
                          srt_path: Path | None = None) -> Path:
    await run(
        "ffmpeg", "-y", "-i", str(src),
        "-filter_complex", build_filter(credit, str(srt_path) if srt_path else None),
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-r", "30",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", "-t", "90", str(dst),
        timeout=600,
    )
    return dst


_EMOJI = re.compile("[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE0F\u200D]")
TITLE_FONT = "Montserrat Black"  # cai para Montserrat/DejaVu se não houver
TITLE_SIZE = 86


def wrap_title(title: str, max_chars: int = 18, max_lines: int = 3) -> list[str]:
    """Quebra o título em até 3 linhas curtas, sem emoji (a fonte não desenha emoji)."""
    words = _EMOJI.sub("", title).split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,!?") + "…"
    return lines


TITLE_COLORS = ["&H00FFFFFF&", "&H0000D4FF&"]  # branco, amarelo (ASS usa BGR)
# Tempos do título-gancho (s): entra com "pop", fica e sai junto com o desfoque.
POP_END = 0.3
HOLD_END = 5.0
TITLE_END = 5.4
BLUR_DARKEN = 0.45  # brilho que sobra na área desfocada (escurece ~55%)


def _ms(t: float) -> int:
    return int(round(t * 1000))


def title_ass(title: str, center_y: int) -> str | None:
    """ASS do título-gancho animado: entra com "pop" (escala 60% -> 108% -> 100% em 0,3 s),
    fica até 5 s e some com fade até 5,4 s. Montserrat Black, contorno e sombra, linhas
    alternando branco e amarelo. None se o título ficar vazio."""
    lines = [ln.upper().replace("\\", "").replace("{", "(").replace("}", ")")
             for ln in wrap_title(title)]
    if not lines:
        return None
    text = "\\N".join(f"{{\\c{TITLE_COLORS[i % 2]}}}{ln}" for i, ln in enumerate(lines))
    peak = _ms(POP_END * 2 / 3)
    anim = (f"{{\\an5\\pos(540,{center_y})\\fad(120,{_ms(TITLE_END - HOLD_END)})"
            f"\\fscx60\\fscy60\\t(0,{peak},\\fscx108\\fscy108)"
            f"\\t({peak},{_ms(POP_END)},\\fscx100\\fscy100)}}")
    cs = round(TITLE_END * 100)
    end = f"0:00:{cs // 100:02}.{cs % 100:02}"
    return "\n".join([
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Title,{TITLE_FONT},{TITLE_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,"
        "-1,0,0,0,100,100,0,0,1,8,6,5,40,40,0,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        f"Dialogue: 1,0:00:00.00,{end},Title,,0,0,0,,{anim}{text}", ""])


def _blur_chain(label_in: str, label_out: str, height: int) -> str:
    """Faixa de cima desfocada e escurecida, que entra suave com o título e some com ele.
    Só os primeiros segundos passam pelo boxblur (trim); depois o overlay deixa passar."""
    k = BLUR_DARKEN
    return (f"[{label_in}]trim=end={TITLE_END + 0.1:.1f},crop=1080:{height}:0:0,"
            f"boxblur=28:3,colorchannelmixer=rr={k}:gg={k}:bb={k},format=yuva420p,"
            f"fade=t=in:st=0:d={POP_END}:alpha=1,"
            f"fade=t=out:st={HOLD_END}:d={TITLE_END - HOLD_END:.1f}:alpha=1[{label_out}]")


def split_filter(lay: Layout, credit: str, subs: str | None, title: str | None) -> str:
    """Grafo do vertical final. `subs` e `title` são caminhos de arquivos .ass (ou None)."""
    parts: list[str] = []
    if lay.cam is not None:
        parts += ["[0:v]split=2[c0][g0]",
                  f"[c0]{lay.cam.crop()},scale=1080:{CAM_H},setsar=1[cam]",
                  f"[g0]{lay.game.crop()},scale=1080:{GAME_H},setsar=1[game]"]
        if title:
            parts += ["[cam]split=2[cama][camb]", _blur_chain("camb", "blur", CAM_H),
                      "[cama][blur]overlay=0:0:eof_action=pass[camf]"]
        else:
            parts.append("[cam]null[camf]")
        parts.append("[camf][game]vstack=inputs=2,fps=30[base]")
    else:
        parts.append(f"[0:v]{lay.game.crop()},scale=1080:1920,setsar=1,fps=30[full]")
        if title:
            parts += ["[full]split=2[fa][fb]", _blur_chain("fb", "blur", lay.blur_h),
                      "[fa][blur]overlay=0:0:eof_action=pass[base]"]
        else:
            parts.append("[full]null[base]")
    parts.append(f"[base]drawtext=fontfile={FONT}:text='{credit}':fontcolor=white@0.9:"
                 "fontsize=34:x=(w-text_w)/2:y=h-110:box=1:boxcolor=black@0.45:"
                 "boxborderw=10[cred]")
    last = "cred"
    for name, path in (("subs", subs), ("ttl", title)):
        if path:
            parts.append(f"[{last}]subtitles=filename='{path}'[{name}]")
            last = name
    parts.append(f"[{last}]null[v]")
    return ";".join(parts)


async def render_split(src: Path, dst: Path, credit: str, subs_path: Path | None,
                       title: str, facecam: FaceCam | None) -> Path:
    """Vertical final: webcam em cima e jogo embaixo (ou jogo em tela cheia sem webcam),
    legenda na emenda, crédito no rodapé e título-gancho animado com desfoque."""
    src_w, src_h = await probe_dimensions(src)
    lay = split_layout(src_w, src_h, facecam)
    subs = None
    if subs_path and Path(subs_path).exists():
        subs = dst.with_name("subs_final.ass")
        subs.write_text(anchor_ass(Path(subs_path).read_text(encoding="utf-8"),
                                   540, lay.caption_y), encoding="utf-8")
    ttl = None
    ass = title_ass(title, lay.title_y) if title else None
    if ass:
        ttl = dst.with_name("title.ass")
        ttl.write_text(ass, encoding="utf-8")
    graph = split_filter(lay, credit, subs.as_posix() if subs else None,
                         ttl.as_posix() if ttl else None)
    await run(
        "ffmpeg", "-y", "-i", str(src), "-filter_complex", graph,
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-r", "30",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", "-t", "90", str(dst), timeout=900)
    return dst


async def render_final(src: Path, dst: Path, credit: str, subs_path: Path | None,
                       title: str, facecam: FaceCam | None) -> Path:
    """Render dos clipes aprovados. Se a tela dividida falhar, cai no layout antigo
    (jogo no centro com fundo desfocado) para não perder o clipe."""
    try:
        return await render_split(src, dst, credit, subs_path, title, facecam)
    except MediaError as e:
        log.warning("render dividido falhou, usando o layout simples: %s", e)
        return await render_vertical(src, dst, credit, subs_path)


async def make_preview(src: Path, dst: Path) -> Path:
    """Versão leve (720p, bitrate limitado) para caber no limite de 10 MB do Discord."""
    await run("ffmpeg", "-y", "-i", str(src), "-vf", "scale=-2:1280",
              "-c:v", "libx264", "-preset", "veryfast", "-b:v", "1200k",
              "-maxrate", "1500k", "-bufsize", "3000k", "-c:a", "aac", "-b:a", "96k",
              "-movflags", "+faststart", "-t", "60", str(dst), timeout=300)
    return dst


async def extract_frames(src: Path, out_dir: Path, n: int = 8) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("frame_*.jpg"):  # nunca misturar frames de outro clipe
        old.unlink()
    duration = await probe_duration(src)
    fps = n / max(duration, 1.0)
    await run("ffmpeg", "-y", "-i", str(src), "-vf", f"fps={fps:.4f},scale=-2:720",
              "-frames:v", str(n), str(out_dir / "frame_%02d.jpg"))
    return sorted(out_dir.glob("frame_*.jpg"))
