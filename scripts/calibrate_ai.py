"""Calibra a IA antes de publicar sem humano no meio.

Uso:
    uv run python scripts/calibrate_ai.py <pasta_com_mp4> <labels.csv>

labels.csv (cabeçalho obrigatório):
    arquivo,esperado
    explosao.mp4,publicar
    cutscene_final.mp4,rejeitar

Lê as chaves do ambiente (.env carregado pelo seu shell) e mostra:
- a decisão da IA para cada clipe, com nota e motivo;
- a matriz de confusão;
- os FALSOS "publicar" (o que nunca pode acontecer em produção).
"""
from __future__ import annotations

import asyncio
import csv
import sys
from pathlib import Path

from liveclips.config import load_settings
from liveclips.db import DB
from liveclips.evaluator import Evaluator


async def main(folder: Path, labels: Path) -> None:
    cfg = load_settings()
    ev = Evaluator(cfg, DB(":memory:"))
    rows = list(csv.DictReader(labels.open(encoding="utf-8")))
    matrix = {("publicar", "publicar"): 0, ("publicar", "rejeitar"): 0,
              ("rejeitar", "publicar"): 0, ("rejeitar", "rejeitar"): 0}
    false_publish = []
    for row in rows:
        path = folder / row["arquivo"]
        expected = row["esperado"].strip().lower()
        v = await ev.evaluate(path, path, {"platform": "twitch", "channel": "calibracao",
                                           "viewers": 1000, "source": "calibration",
                                           "transcript": ""})
        decided = "publicar" if v.safe and v.score >= cfg.ai_min_score else "rejeitar"
        matrix[(expected, decided)] += 1
        flag = "  <-- FALSO PUBLICAR" if expected == "rejeitar" and decided == "publicar" else ""
        print(f"{row['arquivo']:<40} esperado={expected:<9} ia={decided:<9} "
              f"nota={v.score:>4.1f} safe={v.safe!s:<5} {v.reason}{flag}")
        if flag:
            false_publish.append(row["arquivo"])
    print("\nMatriz (esperado → IA):")
    for (exp, got), n in matrix.items():
        print(f"  {exp:>9} → {got:<9} {n}")
    print(f"\nNota mínima usada: {cfg.ai_min_score}")
    if false_publish:
        print("❌ Falsos publicar (ajuste o prompt ou suba AI_MIN_SCORE):", ", ".join(false_publish))
    else:
        print("✅ Nenhum falso publicar.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    asyncio.run(main(Path(sys.argv[1]), Path(sys.argv[2])))
