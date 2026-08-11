import tempfile
from pathlib import Path

import pytest

import db as db_module

# Proteção: app.py chama db.init_db() assim que é importado (efeito colateral de
# nível de módulo). Se algum teste for o primeiro a fazer `import app`, isso não
# pode, nem por um instante, rodar contra o ponto.db real — aponta DB_PATH pra um
# arquivo temporário aqui em cima, antes de qualquer teste (e antes de qualquer
# `import app`) executar. Cada teste que mexe com banco troca isso de novo por um
# arquivo isolado próprio (fixtures `conn`/`cliente` abaixo), então isto é só uma
# rede de segurança contra o import acontecer com o caminho real ainda configurado.
db_module.DB_PATH = Path(tempfile.gettempdir()) / "sistema_ponto_teste_conftest.db"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "teste.db")
    db_module.init_db()
    conexao = db_module.get_db()
    yield conexao
    conexao.close()


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    """Cliente de teste do Flask com banco isolado (arquivo novo por teste, igual à
    fixture `conn`). O import de `app` só acontece aqui dentro (não no topo do
    arquivo), depois do DB_PATH já isolado, então o `db.init_db()` que roda na
    importação do app.py sempre bate num banco de teste, nunca no real."""
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "teste_rotas.db")
    db_module.init_db()
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield client


@pytest.fixture
def cenario_semana(conn):
    """Turno comercial 08h-17h (almoço 12h-13h, tolerância 10min) + 2 funcionários sintéticos
    na semana 2026-01-05 (segunda) a 2026-01-09 (sexta): Ana sempre pontual; Beto com um
    atraso de 15min na terça (sem bater ponto do almoço nesse dia), falta não justificada
    na quarta e uma ausência de férias na quinta (não deve contar como falta)."""
    turno_id = conn.execute(
        """INSERT INTO turnos (nome, horario_entrada, horario_saida, tolerancia_minutos,
                                dias_semana, horario_saida_almoco, horario_retorno_almoco,
                                tolerancia_saida_minutos)
           VALUES ('comercial', '08:00', '17:00', 10, '0,1,2,3,4', '12:00', '13:00', 10)"""
    ).lastrowid

    ana_id = conn.execute(
        "INSERT INTO funcionarios (nome, codigo, situacao, turno_id) VALUES ('Ana Pontual', 'AAA111', 'ativo', ?)",
        (turno_id,),
    ).lastrowid
    beto_id = conn.execute(
        "INSERT INTO funcionarios (nome, codigo, situacao, turno_id) VALUES ('Beto Testado', 'BBB222', 'ativo', ?)",
        (turno_id,),
    ).lastrowid

    def registrar(funcionario_id, data, hora, tipo):
        conn.execute(
            "INSERT INTO registros (funcionario_id, tipo, timestamp) VALUES (?, ?, ?)",
            (funcionario_id, tipo, f"{data} {hora}:00"),
        )

    for dia in ("2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"):
        registrar(ana_id, dia, "08:00", "entrada")
        registrar(ana_id, dia, "12:00", "saida")
        registrar(ana_id, dia, "13:00", "entrada")
        registrar(ana_id, dia, "17:00", "saida")

    registrar(beto_id, "2026-01-05", "08:00", "entrada")
    registrar(beto_id, "2026-01-05", "12:00", "saida")
    registrar(beto_id, "2026-01-05", "13:00", "entrada")
    registrar(beto_id, "2026-01-05", "17:00", "saida")

    registrar(beto_id, "2026-01-06", "08:25", "entrada")
    registrar(beto_id, "2026-01-06", "17:00", "saida")

    # 2026-01-07 (quarta): sem nenhum registro -> falta não justificada
    # 2026-01-08 (quinta): sem registro, mas com ausência -> não conta falta

    conn.execute(
        "INSERT INTO ausencias (funcionario_id, data, tipo, criado_em) VALUES (?, ?, 'ferias', datetime('now'))",
        (beto_id, "2026-01-08"),
    )

    registrar(beto_id, "2026-01-09", "08:00", "entrada")
    registrar(beto_id, "2026-01-09", "12:00", "saida")
    registrar(beto_id, "2026-01-09", "13:00", "entrada")
    registrar(beto_id, "2026-01-09", "17:00", "saida")

    conn.commit()
    return {"ana_id": ana_id, "beto_id": beto_id, "turno_id": turno_id}


@pytest.fixture
def cenario_ranking(conn, cenario_semana):
    """Estende cenario_semana com um terceiro funcionário (Carlos), atrasado 50min na
    segunda — mais que o atraso de 15min do Beto — pra testar ordenação e top_n do ranking."""
    turno_id = cenario_semana["turno_id"]
    carlos_id = conn.execute(
        "INSERT INTO funcionarios (nome, codigo, situacao, turno_id) VALUES ('Carlos Atrasado', 'CCC333', 'ativo', ?)",
        (turno_id,),
    ).lastrowid
    conn.execute(
        "INSERT INTO registros (funcionario_id, tipo, timestamp) VALUES (?, 'entrada', '2026-01-05 09:00:00')",
        (carlos_id,),
    )
    conn.execute(
        "INSERT INTO registros (funcionario_id, tipo, timestamp) VALUES (?, 'saida', '2026-01-05 17:00:00')",
        (carlos_id,),
    )
    conn.commit()
    return {**cenario_semana, "carlos_id": carlos_id}


@pytest.fixture
def cenario_dois_meses(conn):
    """1 funcionário com turno comercial, registros pontuais em 2 dias de janeiro/2026 e
    1 dia de fevereiro/2026 (todos dias de semana), pra testar agregação por mês."""
    turno_id = conn.execute(
        """INSERT INTO turnos (nome, horario_entrada, horario_saida, tolerancia_minutos, dias_semana)
           VALUES ('comercial', '08:00', '17:00', 10, '0,1,2,3,4')"""
    ).lastrowid
    func_id = conn.execute(
        "INSERT INTO funcionarios (nome, codigo, situacao, turno_id) VALUES ('Dani Mensal', 'DDD444', 'ativo', ?)",
        (turno_id,),
    ).lastrowid

    def bater(data, hora, tipo):
        conn.execute(
            "INSERT INTO registros (funcionario_id, tipo, timestamp) VALUES (?, ?, ?)",
            (func_id, tipo, f"{data} {hora}:00"),
        )

    for dia in ("2026-01-05", "2026-01-06"):
        bater(dia, "08:00", "entrada")
        bater(dia, "17:00", "saida")
    bater("2026-02-02", "08:00", "entrada")
    bater("2026-02-02", "17:00", "saida")

    conn.commit()
    return {"func_id": func_id}
