"""#painel: estado e ações do painel de controle, independente do Discord."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .runtime import PLATFORMS, RuntimeSettings

SCORE_OPTIONS = (5, 6, 7, 8, 9)
INTERVAL_OPTIONS = (15, 30, 45, 60)
LIMIT_OPTIONS = (4, 8, 12, 20)
PLATFORM_LABEL = {"instagram": "Reels", "youtube": "Shorts", "tiktok": "TikTok"}


@dataclass
class PanelState:
    text: str
    paused: bool
    min_score: float
    interval: int
    limit: int
    platforms: list[tuple[str, bool, bool]]  # (nome, ligada, configurada)


class Panel:
    def __init__(self, runtime: RuntimeSettings, configured: list[str],
                 status_lines: Callable[[], list[str]]):
        self.runtime = runtime
        self.configured = configured
        self.status_lines = status_lines

    def state(self) -> PanelState:
        rt = self.runtime
        platforms = [(p, rt.platform_enabled(p), p in self.configured) for p in PLATFORMS]
        on = [PLATFORM_LABEL[p] for p, enabled, conf in platforms if enabled and conf]
        lines = [
            "## 🎛️ Painel Live Clips",
            f"Publicação: {'⏸️ pausada' if rt.paused() else '▶️ ativa'} · "
            f"plataformas: {', '.join(on) or 'nenhuma'} · nota mínima {rt.min_score():g} · "
            f"a cada {rt.post_interval_min()} min · até {rt.post_max_per_day()}/dia",
            *self.status_lines(),
        ]
        return PanelState(text="\n".join(lines), paused=rt.paused(),
                          min_score=rt.min_score(),
                          interval=rt.post_interval_min(), limit=rt.post_max_per_day(),
                          platforms=platforms)

    async def handle(self, action: str, values: list[str]) -> str:
        rt = self.runtime
        try:
            if action == "pause":
                rt.set_paused(not rt.paused())
                return "⏸️ Publicações pausadas." if rt.paused() else "▶️ Publicações retomadas."
            if action == "score":
                rt.set_min_score(float(values[0]))
                return f"🎯 Nota mínima: {rt.min_score():g}"
            if action == "pace":
                interval, limit = values[0].split("|")
                rt.set_post_interval_min(int(interval))
                rt.set_post_max_per_day(int(limit))
                return f"⏱️ A cada {interval} min, até {limit} por dia."
            if action.startswith("plat-"):
                name = action.removeprefix("plat-")
                if name not in PLATFORMS:
                    return "Ação desconhecida."
                if name not in self.configured:
                    return f"{PLATFORM_LABEL[name]} não configurado no .env."
                rt.set_platform(name, not rt.platform_enabled(name))
                on = rt.platform_enabled(name)
                return f"{'🟢' if on else '⚪'} {PLATFORM_LABEL[name]} {'ligado' if on else 'desligado'}."
        except (ValueError, IndexError) as e:
            return f"Valor inválido: {e}" if str(e) else "Valor inválido."
        return "Ação desconhecida."
