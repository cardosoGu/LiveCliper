"""Edição de vídeo com FFmpeg: áudio, vertical 9:16, prévia leve e frames."""
from __future__ import annotations

import asyncio
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
        f"[base]drawtext=fontfile={FONT}:text='{credit}':fontcolor=white:fontsize=44:"
        "x=(w-text_w)/2:y=200:box=1:boxcolor=black@0.55:boxborderw=14[cred]",
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
