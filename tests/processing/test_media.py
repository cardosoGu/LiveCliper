import os
import shutil
import time
from pathlib import Path

import pytest

from liveclips.core.credits import credit_text
from liveclips.processing.cleanup import cleanup_media
from liveclips.processing.ffmpeg import (FONT, build_filter, extract_frames, probe_duration,
                                         render_vertical, run)


def test_credit_text_sanitizes():
    assert credit_text("twitch", "gaules") == "twitch.tv/gaules"
    assert credit_text("kick", "x';rm -rf:/") == "kick.com/xrm-rf"


def test_build_filter_with_and_without_subtitles():
    with_srt = build_filter("twitch.tv/a", "/data/1/subs.ass")
    assert "subtitles=filename='/data/1/subs.ass'[v]" in with_srt
    assert "text='twitch.tv/a'" in with_srt
    assert with_srt.endswith("[v]")
    without = build_filter("twitch.tv/a", None)
    assert "subtitles" not in without and "[cred]null[v]" in without


def test_cleanup_removes_only_old_dirs(tmp_path: Path):
    old, new = tmp_path / "1", tmp_path / "2"
    old.mkdir()
    new.mkdir()
    past = time.time() - 50 * 3600
    os.utime(old, (past, past))
    assert cleanup_media(tmp_path) == 1
    assert not old.exists() and new.exists()


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or not Path(FONT).exists(),
    reason="ffmpeg/fonte não disponíveis (roda no Docker)")


@needs_ffmpeg
async def test_render_vertical_and_frames(tmp_path: Path):
    src = tmp_path / "in.mp4"
    await run("ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=4:size=1280x720:rate=30",
              "-f", "lavfi", "-i", "sine=duration=4", "-shortest", str(src))
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nteste\n", encoding="utf-8")
    out = await render_vertical(src, tmp_path / "v.mp4", "twitch.tv/teste", srt)
    dims = await run("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                     "stream=width,height", "-of", "csv=p=0", str(out))
    assert dims.strip() == "1080,1920"
    assert 3.5 < await probe_duration(out) < 4.5
    frames = await extract_frames(src, tmp_path / "frames", 8)
    assert len(frames) == 8
    fewer = await extract_frames(src, tmp_path / "frames", 3)  # mesma pasta, outro clipe
    assert len(fewer) == 3  # nada de frames que sobraram da extração anterior


def test_wrap_title():
    from liveclips.processing.ffmpeg import wrap_title

    assert wrap_title("Ele caiu do prédio 😂") == ["Ele caiu do prédio"]
    assert wrap_title("Aula de biologia virou experimento maluco") == [
        "Aula de biologia virou", "experimento maluco"]
    assert len(wrap_title("um dois tres quatro cinco seis sete oito nove dez onze doze treze")) == 3
    assert wrap_title("🔥🔥") == []
