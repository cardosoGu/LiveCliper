"""Edição de vídeo com FFmpeg: áudio, vertical 9:16, prévia leve e frames."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

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


async def concat_clips(parts: list[Path], dst: Path) -> Path:
    """Junta clipes seguidos do mesmo canal, sempre recodificando na resolução do
    primeiro: copiar sem recodificar deixava o vídeo congelado após o 1º clipe
    (carimbos de tempo do 2º clipe não continuam os do 1º)."""
    w, h = await probe_dimensions(parts[0])
    inputs, chains, pads = [], [], ""
    for i, p in enumerate(parts):
        inputs += ["-i", str(p)]
        chains.append(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                      f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{i}];"
                      f"[{i}:a]aresample=48000[a{i}]")
        pads += f"[v{i}][a{i}]"
    graph = ";".join(chains) + f";{pads}concat=n={len(parts)}:v=1:a=1[v][a]"
    await run("ffmpeg", "-y", *inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac",
              str(dst), timeout=600)
    return dst


async def extract_audio(src: Path, dst: Path) -> Path:
    await run("ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
              "-b:a", "48k", str(dst))
    return dst


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
TITLE_SIZE = 82
TITLE_TOP = 170  # topo do bloco de título; o jogo começa em ~656


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


async def add_hook_title(src: Path, dst: Path, title: str) -> Path:
    """Título-gancho fixo no topo: Montserrat grossa, contorno preto e sombra,
    linhas alternando branco e amarelo."""
    lines = [ln.upper() for ln in wrap_title(title)]
    if not lines:
        return src
    colors = ["&H00FFFFFF&", "&H0000D4FF&"]  # branco, amarelo (BGR)
    text = "\\N".join(f"{{\\c{colors[i % 2]}}}{ln.replace('{', '(').replace('}', ')')}"
                       for i, ln in enumerate(lines))
    ass = dst.with_name("title.ass")
    ass.write_text("\n".join([
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Title,{TITLE_FONT},{TITLE_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,"
        "-1,0,0,0,100,100,0,0,1,8,6,8,60,60,{TITLE_TOP},1".replace("{TITLE_TOP}", str(TITLE_TOP)),
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        f"Dialogue: 0,0:00:00.00,9:59:59.00,Title,,0,0,0,,{text}", ""]), encoding="utf-8")
    await run("ffmpeg", "-y", "-i", str(src), "-vf", f"subtitles=filename='{ass.as_posix()}'",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
              "-c:a", "copy", "-movflags", "+faststart", str(dst), timeout=600)
    return dst


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
