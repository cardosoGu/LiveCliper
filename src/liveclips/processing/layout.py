"""Cálculo dos recortes do vertical 9:16 em tela dividida (sem FFmpeg, fácil de testar).

Com webcam: webcam ampliada em cima (1080x768, 40%) e jogo embaixo (1080x1152, 60%),
recortado no centro e sem a webcam. Sem webcam: jogo em tela cheia com zoom no centro.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..ai.verdict import FaceCam

CANVAS_W, CANVAS_H = 1080, 1920
CAM_H = 768                  # 40% de cima
GAME_H = CANVAS_H - CAM_H    # 60% de baixo
SEAM_Y = CAM_H               # emenda: onde a legenda fica
FULL_CAPTION_Y = 1280        # legenda sem webcam (terço de baixo, longe do título)
MIN_GAME_FRACTION = 0.5      # recorte do jogo menor que isso (de altura) → ignora a webcam


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    def crop(self) -> str:
        return f"crop={self.w}:{self.h}:{self.x}:{self.y}"


@dataclass(frozen=True)
class Layout:
    cam: Rect | None   # recorte da webcam no vídeo original (None = tela cheia)
    game: Rect         # recorte do jogo no vídeo original
    caption_y: int     # centro vertical da legenda na tela 1080x1920
    title_y: int       # centro vertical do título
    blur_h: int        # altura da faixa de cima que desfoca junto com o título


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(v, hi))


def _fit(region: tuple[float, float, float, float], aspect: float,
         cx: float, cy: float) -> Rect:
    """Maior retângulo com a proporção `aspect` (w/h) dentro de `region`,
    o mais perto possível do ponto (cx, cy)."""
    rx, ry, rw, rh = region
    if rw / rh > aspect:
        h = rh
        w = rh * aspect
    else:
        w = rw
        h = rw / aspect
    w, h = _even(min(w, rw)), _even(min(h, rh))
    x = _clamp(cx - w / 2, rx, rx + rw - w)
    y = _clamp(cy - h / 2, ry, ry + rh - h)
    return Rect(int(x), int(y), w, h)


def facecam_pixels(fc: FaceCam, src_w: int, src_h: int) -> tuple[float, float, float, float]:
    return fc.x * src_w, fc.y * src_h, fc.w * src_w, fc.h * src_h


def cam_crop(fc: FaceCam, src_w: int, src_h: int) -> Rect:
    """Recorte da webcam com a proporção da faixa de cima (1080:768), centrado nela:
    corta as sobras (geralmente as laterais) para preencher sem distorcer."""
    px, py, pw, ph = facecam_pixels(fc, src_w, src_h)
    return _fit((px, py, pw, ph), CANVAS_W / CAM_H, px + pw / 2, py + ph / 2)


def game_crop(src_w: int, src_h: int, fc: FaceCam | None, out_h: int) -> Rect:
    """Recorte do jogo na proporção 1080:out_h, o mais central possível e sem a webcam.
    Testa as quatro áreas livres em volta da webcam e fica com a maior."""
    aspect = CANVAS_W / out_h
    cx, cy = src_w / 2, src_h / 2
    center = _fit((0, 0, src_w, src_h), aspect, cx, cy)
    if fc is None:
        return center
    px, py, pw, ph = facecam_pixels(fc, src_w, src_h)
    free = [(0, 0, px, src_h), (px + pw, 0, src_w - px - pw, src_h),
            (0, 0, src_w, py), (0, py + ph, src_w, src_h - py - ph)]
    options = [_fit(r, aspect, cx, cy) for r in free if r[2] >= 16 and r[3] >= 16]
    if not options:
        return center
    best = max(options, key=lambda r: (r.w * r.h,
                                       -abs(r.x + r.w / 2 - cx) - abs(r.y + r.h / 2 - cy)))
    return best if best.h >= center.h * MIN_GAME_FRACTION else center


def split_layout(src_w: int, src_h: int, fc: FaceCam | None) -> Layout:
    if fc is None:
        return Layout(None, game_crop(src_w, src_h, None, CANVAS_H),
                      FULL_CAPTION_Y, CANVAS_H // 6, CANVAS_H // 3)
    return Layout(cam_crop(fc, src_w, src_h), game_crop(src_w, src_h, fc, GAME_H),
                  SEAM_Y, CAM_H // 2, CAM_H)
