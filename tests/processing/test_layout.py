from liveclips.ai.verdict import FaceCam
from liveclips.processing.layout import (CAM_H, GAME_H, Rect, cam_crop, game_crop,
                                         split_layout)


def overlaps(r: Rect, fc: FaceCam, w: int, h: int) -> bool:
    fx, fy, fw, fh = fc.x * w, fc.y * h, fc.w * w, fc.h * h
    return r.x < fx + fw and fx < r.x + r.w and r.y < fy + fh and fy < r.y + r.h


def inside(r: Rect, w: int, h: int) -> bool:
    return r.x >= 0 and r.y >= 0 and r.x + r.w <= w and r.y + r.h <= h


def test_cam_crop_fills_top_band_without_distortion():
    fc = FaceCam(x=0.0, y=0.7, w=0.25, h=0.3)  # 480x324 no canto inferior esquerdo
    r = cam_crop(fc, 1920, 1080)
    assert abs(r.w / r.h - 1080 / CAM_H) < 0.02
    assert inside(r, 1920, 1080) and r.w % 2 == 0 and r.h % 2 == 0
    assert r.h == 324 and r.y == 756  # usa a altura toda, corta as laterais
    assert r.x + r.w / 2 == 240       # centrado na webcam


def test_game_crop_avoids_facecam_and_stays_central():
    fc = FaceCam(x=0.0, y=0.7, w=0.25, h=0.3)
    r = game_crop(1920, 1080, fc, GAME_H)
    assert abs(r.w / r.h - 1080 / GAME_H) < 0.02
    assert inside(r, 1920, 1080) and not overlaps(r, fc, 1920, 1080)
    assert r.h == 1080 and r.x == 480  # empurrado só o necessário para a direita


def test_game_crop_with_wide_bottom_overlay_zooms_above_it():
    fc = FaceCam(x=0.0, y=0.75, w=1.0, h=0.25)
    r = game_crop(1920, 1080, fc, GAME_H)
    assert not overlaps(r, fc, 1920, 1080) and r.y + r.h <= 810
    assert abs(r.x + r.w / 2 - 960) <= 1


def test_game_crop_ignores_facecam_when_the_free_area_is_tiny():
    fc = FaceCam(x=0.2, y=0.2, w=0.6, h=0.6)  # 36% bem no meio
    center = game_crop(1920, 1080, None, GAME_H)
    assert game_crop(1920, 1080, fc, GAME_H) == center


def test_layout_without_facecam_is_full_screen_center_zoom():
    lay = split_layout(1920, 1080, None)
    assert lay.cam is None
    assert lay.game.h == 1080 and abs(lay.game.w / lay.game.h - 1080 / 1920) < 0.01
    assert abs(lay.game.x + lay.game.w / 2 - 960) <= 1  # zoom no centro
    assert lay.blur_h == 640 and lay.title_y == 320  # desfoque no terço de cima


def test_layout_with_facecam_puts_caption_on_the_seam():
    lay = split_layout(1920, 1080, FaceCam(x=0.78, y=0.0, w=0.22, h=0.28))
    assert lay.cam is not None and lay.caption_y == CAM_H and lay.blur_h == CAM_H
    assert lay.title_y == CAM_H // 2


def test_podcast_layout_centers_on_subject_inside_frame():
    from liveclips.processing.layout import FULL_CAPTION_Y, podcast_layout

    lay = podcast_layout(1920, 1080, FaceCam(x=0.6, y=0.2, w=0.2, h=0.4))  # centro x=0.7
    assert lay.cam is None and lay.game.h == 1080 and lay.game.w == 606
    assert abs(lay.game.x + lay.game.w / 2 - 0.7 * 1920) <= 2
    assert inside(lay.game, 1920, 1080)
    assert lay.caption_y == FULL_CAPTION_Y and lay.title_y == 320 and lay.blur_h == 640


def test_podcast_layout_without_subject_is_centered():
    from liveclips.processing.layout import podcast_layout

    lay = podcast_layout(1920, 1080, None)
    assert abs(lay.game.x + lay.game.w / 2 - 960) <= 2


def test_podcast_layout_subject_on_the_edge_is_clamped():
    from liveclips.processing.layout import podcast_layout

    left = podcast_layout(1920, 1080, FaceCam(x=0.0, y=0.2, w=0.1, h=0.3))
    right = podcast_layout(1920, 1080, FaceCam(x=0.9, y=0.2, w=0.1, h=0.3))
    assert left.game.x == 0
    assert right.game.x + right.game.w == 1920
