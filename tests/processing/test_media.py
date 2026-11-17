import os
import shutil
import time
from pathlib import Path

import pytest

from liveclips.core.credits import credit_text
from liveclips.processing.subtitles import build_cues, cues_to_ass
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
        "Aula de biologia", "virou experimento", "maluco"]
    assert len(wrap_title("um dois tres quatro cinco seis sete oito nove dez onze doze treze")) == 3
    assert wrap_title("🔥🔥") == []


def test_title_ass_pop_hold_and_fade():
    from liveclips.processing.ffmpeg import title_ass

    ass = title_ass("Ele vai conseguir sair dessa? 😱", 384)
    [line] = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    assert line.startswith("Dialogue: 1,0:00:00.00,0:00:05.40,Title,")
    assert r"\an5\pos(540,384)" in line
    assert r"\fscx60\fscy60\t(0,200,\fscx108\fscy108)\t(200,300,\fscx100\fscy100)" in line
    assert r"\fad(120,400)" in line  # sai entre 5,0 e 5,4 s
    assert r"{\c&H00FFFFFF&}ELE VAI CONSEGUIR\N{\c&H0000D4FF&}SAIR DESSA?" in line
    assert "Montserrat Black" in ass and "😱" not in ass
    assert title_ass("🔥", 384) is None


def test_split_filter_with_facecam_blurs_only_the_webcam_band():
    from liveclips.ai.verdict import FaceCam
    from liveclips.processing.ffmpeg import split_filter
    from liveclips.processing.layout import split_layout

    lay = split_layout(1920, 1080, FaceCam(x=0.0, y=0.7, w=0.25, h=0.3))
    g = split_filter(lay, "twitch.tv/a", "/w/subs_final.ass", "/w/title.ass")
    assert f"[c0]{lay.cam.crop()},scale=1080:768" in g
    assert f"[g0]{lay.game.crop()},scale=1080:1152" in g
    assert "[camb]trim=end=5.5,crop=1080:768:0:0,boxblur" in g
    assert "colorchannelmixer=rr=0.45" in g  # escurece ~55%
    assert "fade=t=in:st=0:d=0.3:alpha=1" in g and "fade=t=out:st=5.0:d=0.4:alpha=1" in g
    assert "overlay=0:0:eof_action=pass[camf]" in g and "vstack=inputs=2" in g
    assert "text='twitch.tv/a'" in g and "y=h-110" in g
    assert g.index("subs_final.ass") < g.index("title.ass") and g.endswith("[v]")


def test_split_filter_without_facecam_or_title():
    from liveclips.processing.ffmpeg import split_filter
    from liveclips.processing.layout import split_layout

    lay = split_layout(1920, 1080, None)
    with_title = split_filter(lay, "twitch.tv/a", None, "/w/title.ass")
    assert "scale=1080:1920" in with_title and "vstack" not in with_title
    assert "crop=1080:640:0:0,boxblur" in with_title  # terço de cima do jogo
    plain = split_filter(lay, "twitch.tv/a", None, None)
    assert "boxblur" not in plain and "subtitles" not in plain


@needs_ffmpeg
@pytest.mark.parametrize("with_cam", [True, False])
async def test_render_split_real_ffmpeg(tmp_path: Path, with_cam):
    from liveclips.ai.verdict import FaceCam
    from liveclips.processing.ffmpeg import extract_frames, render_split

    src = tmp_path / "in.mp4"
    await run("ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=8:size=1920x1080:rate=30",
              "-f", "lavfi", "-i", "sine=duration=8", "-shortest", str(src))
    subs = tmp_path / "subs.ass"
    subs.write_text(cues_to_ass(build_cues([{"word": "olha", "start": 0.5, "end": 1.0}]),
                                1920, 1080), encoding="utf-8")
    fc = FaceCam(x=0.0, y=0.7, w=0.25, h=0.3) if with_cam else None
    out = await render_split(src, tmp_path / "final.mp4", "twitch.tv/teste", subs,
                             "Ele vai conseguir?", fc)
    dims = await run("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                     "stream=width,height", "-of", "csv=p=0", str(out))
    assert dims.strip() == "1080,1920"
    assert 7.5 < await probe_duration(out) < 8.5  # o vídeo inteiro, sem cortar no desfoque
    frames = await run("ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                       "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(out))
    assert int(frames.strip()) >= 235  # ~30 fps até o fim: nada congelado
    assert len(await extract_frames(out, tmp_path / "f", 4)) == 4
