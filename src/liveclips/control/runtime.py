"""Configurações que o admin muda pelo Discord, guardadas na tabela kv.

O .env só dá o valor inicial: depois que o admin muda algo, vale o que está no banco
(sobrevive a reinícios).
"""
from __future__ import annotations

import json

from ..core.config import Settings
from ..core.db import DB

PLATFORMS = ("instagram", "youtube", "tiktok")
MAX_KNOWN_CATEGORIES = 25


class RuntimeSettings:
    def __init__(self, db: DB, cfg: Settings):
        self.db = db
        self.cfg = cfg

    # nota mínima da IA (mesma chave que o /min_score da V1 usava)
    def min_score(self) -> float:
        return float(self.db.kv_get("min_score", str(self.cfg.ai_min_score)))

    def set_min_score(self, value: float) -> None:
        if not 0 <= value <= 10:
            raise ValueError("a nota precisa estar entre 0 e 10")
        self.db.kv_set("min_score", str(float(value)))

    # ritmo de publicação
    def post_interval_min(self) -> int:
        return int(self.db.kv_get("panel:post_interval_min",
                                  str(self.cfg.post_min_interval_min)))

    def set_post_interval_min(self, value: int) -> None:
        if value < 0:
            raise ValueError("o intervalo não pode ser negativo")
        self.db.kv_set("panel:post_interval_min", str(int(value)))

    def post_max_per_day(self) -> int:
        return int(self.db.kv_get("panel:post_max_per_day", str(self.cfg.post_max_per_day)))

    def set_post_max_per_day(self, value: int) -> None:
        if value < 1:
            raise ValueError("o limite precisa ser pelo menos 1")
        self.db.kv_set("panel:post_max_per_day", str(int(value)))

    # pausa geral das publicações (mesma chave do /pause da V1)
    def paused(self) -> bool:
        return self.db.kv_get("paused") == "1"

    def set_paused(self, on: bool) -> None:
        self.db.kv_set("paused", "1" if on else "0")

    # liga/desliga por plataforma (substitui o DRY_RUN)
    def platform_enabled(self, name: str) -> bool:
        default = "0" if self.cfg.dry_run else "1"
        return self.db.kv_get(f"panel:platform:{name}", default) == "1"

    def set_platform(self, name: str, on: bool) -> None:
        if name not in PLATFORMS:
            raise ValueError(f"plataforma desconhecida: {name}")
        self.db.kv_set(f"panel:platform:{name}", "1" if on else "0")

    # automático (motor da V1): ligado por padrão para não mudar quem já roda a V1
    def auto_on(self) -> bool:
        return self.db.kv_get("panel:auto_on", "1") == "1"

    def set_auto_on(self, on: bool) -> None:
        self.db.kv_set("panel:auto_on", "1" if on else "0")

    def categories(self) -> list[str]:
        saved = self.db.kv_get("panel:categories")
        return json.loads(saved) if saved else list(self.cfg.twitch_game_names)

    def set_categories(self, names: list[str]) -> None:
        names = [n.strip() for n in names if n.strip()]
        if not names:
            raise ValueError("escolha pelo menos uma categoria")
        self.db.kv_set("panel:categories", json.dumps(names, ensure_ascii=False))

    def known_categories(self) -> list[str]:
        saved = self.db.kv_get("panel:known_categories")
        return json.loads(saved) if saved else list(self.cfg.twitch_game_names)

    def add_known_category(self, name: str) -> None:
        name = name.strip()
        known = self.known_categories()
        if not name or name.lower() in {k.lower() for k in known}:
            return
        if len(known) >= MAX_KNOWN_CATEGORIES:
            raise ValueError(f"o painel comporta no máximo {MAX_KNOWN_CATEGORIES} categorias")
        known.append(name)
        self.db.kv_set("panel:known_categories", json.dumps(known, ensure_ascii=False))

    def min_viewers(self) -> int:
        return int(self.db.kv_get("panel:min_viewers", str(self.cfg.min_viewers)))

    def set_min_viewers(self, value: int) -> None:
        if value < 0:
            raise ValueError("o mínimo de espectadores não pode ser negativo")
        self.db.kv_set("panel:min_viewers", str(int(value)))
