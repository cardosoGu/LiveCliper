from liveclips.ai.prompt import build_prompt
from liveclips.ai.taste import taste_block
from liveclips.ondemand.topics import build_topics_prompt


def test_empty_db_gives_no_block(db):
    assert taste_block(db) == ""


def test_block_lists_approved_and_discarded_with_reason(db):
    db.record_feedback(1, "aprovado", "ELE VAI CONSEGUIR?", 7.5, "caos", "olha o carro voando")
    db.record_feedback(2, "descartado", "ESPERA O FINAL", 8.0, "reação", "bla " * 200,
                       note="precisa de contexto, ninguém entende")
    db.record_feedback(3, "descartado", "SEM MOTIVO", 6.0, "ok", "x")
    block = taste_block(db)
    assert "ELE VAI CONSEGUIR?" in block and "olha o carro voando" in block
    assert "precisa de contexto, ninguém entende" in block
    # descartes com motivo vêm antes dos sem motivo; trecho longo é cortado
    assert block.index("ESPERA O FINAL") < block.index("SEM MOTIVO")
    assert len(block) < 2000


def test_limits_how_many_examples(db):
    for i in range(30):
        db.record_feedback(i, "descartado", f"T{i}", 5.0, "r", "x", note=f"motivo {i}")
    block = taste_block(db, discarded=4)
    assert block.count("motivo do admin") == 4 and "motivo 29" in block  # os mais recentes


def test_prompts_include_taste():
    assert "GOSTO" in build_prompt({"transcript": "oi"}, taste="GOSTO DO ADMIN")
    assert "GOSTO" in build_topics_prompt("v", [], [], "", taste="GOSTO DO ADMIN")
    assert "GOSTO" not in build_prompt({"transcript": "oi"})
