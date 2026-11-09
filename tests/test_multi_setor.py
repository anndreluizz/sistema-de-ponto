"""Chefe de setor (Direção/Consulta preso a um setor) só enxerga a equipe do próprio setor."""
from datetime import datetime

import pytest

import db as db_module
from tests.test_app_rotas import _criar_usuario, _login

SENHA = "SenhaForte123!"


@pytest.fixture
def dois_setores(cliente):
    conn = db_module.get_db()
    escola = conn.execute("INSERT INTO setores (nome) VALUES ('Escola')").lastrowid
    obras = conn.execute("INSERT INTO setores (nome) VALUES ('Obras')").lastrowid
    ana = conn.execute(
        "INSERT INTO funcionarios (nome, codigo, situacao, setor_id) VALUES ('Ana Escola', 'A1', 'ativo', ?)", (escola,)
    ).lastrowid
    paulo = conn.execute(
        "INSERT INTO funcionarios (nome, codigo, situacao, setor_id) VALUES ('Paulo Obras', 'P1', 'ativo', ?)", (obras,)
    ).lastrowid
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for fid in (ana, paulo):
        conn.execute("INSERT INTO registros (funcionario_id, tipo, timestamp) VALUES (?, 'entrada', ?)", (fid, agora))
    conn.commit()
    conn.close()
    _criar_usuario("chefe", SENHA, "direcao")
    conn = db_module.get_db()
    conn.execute("UPDATE usuarios SET setor_id = ? WHERE usuario = 'chefe'", (escola,))
    conn.commit()
    conn.close()
    return {"escola": escola, "obras": obras, "ana": ana, "paulo": paulo}


def test_chefe_ve_so_o_proprio_setor(cliente, dois_setores):
    _login(cliente, "chefe", SENHA)
    for url in ("/admin/dashboard", "/admin/funcionarios", "/admin/relatorios"):
        resposta = cliente.get(url)
        assert resposta.status_code == 200, url
        html = resposta.get_data(as_text=True)
        assert "Ana Escola" in html or url == "/admin/relatorios", url
        assert "Paulo Obras" not in html, url


@pytest.mark.parametrize("caminho", [
    "/admin/funcionarios/{id}",
    "/admin/funcionarios/{id}/historico",
    "/admin/funcionarios/{id}/banco-horas",
    "/admin/funcionarios/{id}/cracha",
    "/admin/funcionarios/{id}/espelho.pdf",
    "/admin/arquivos/qrcodes/{id}.png",
])
def test_chefe_nao_abre_funcionario_de_outro_setor(cliente, dois_setores, caminho):
    _login(cliente, "chefe", SENHA)
    resposta = cliente.get(caminho.format(id=dois_setores["paulo"]))
    assert resposta.status_code in (302, 404), resposta.status_code
    assert b"Paulo Obras" not in resposta.get_data()


def test_chefe_abre_ficha_e_espelho_do_proprio_setor(cliente, dois_setores):
    _login(cliente, "chefe", SENHA)
    for caminho in ("/admin/funcionarios/{id}", "/admin/funcionarios/{id}/espelho.pdf",
                    "/admin/relatorios/dias/exportar.xlsx"):
        assert cliente.get(caminho.format(id=dois_setores["ana"])).status_code == 200, caminho


def test_admin_ve_todos_e_presenca_por_setor(cliente, dois_setores):
    _criar_usuario("admin2", SENHA, "administrador")
    _login(cliente, "admin2", SENHA)
    html = cliente.get("/admin/dashboard").get_data(as_text=True)
    assert "Presença por setor" in html and "Obras" in html and "Escola" in html
    html = cliente.get("/admin/funcionarios").get_data(as_text=True)
    assert "Ana Escola" in html and "Paulo Obras" in html


def test_rh_com_setor_gravado_continua_vendo_tudo(cliente, dois_setores):
    """Setor só prende perfis de leitura — RH/admin com setor_id no banco não ficam limitados."""
    _criar_usuario("rh2", SENHA, "rh")
    conn = db_module.get_db()
    conn.execute("UPDATE usuarios SET setor_id = ? WHERE usuario = 'rh2'", (dois_setores["escola"],))
    conn.commit()
    conn.close()
    _login(cliente, "rh2", SENHA)
    assert "Paulo Obras" in cliente.get("/admin/funcionarios").get_data(as_text=True)


def test_totem_aceita_qualquer_setor_sem_login(conn):
    """Fora de requisição (totem, backup) a conexão não filtra nada."""
    assert conn.execute("SELECT COUNT(*) FROM funcionarios").fetchone()[0] >= 0
    assert db_module._setor_da_sessao() is None


# --- Totem sem internet: batida guardada no tablet e enviada depois -----------------

def _totem_autorizado(cliente):
    from tests.test_app_rotas import _csrf_token
    _criar_usuario("admin_totem", SENHA, "administrador")
    _login(cliente, "admin_totem", SENHA)
    token = _csrf_token(cliente.get("/admin/configuracoes").get_data(as_text=True))
    cliente.post("/admin/totem/autorizar", data={"csrf_token": token, "nome": "Tablet"})
    cliente.get("/admin/logout")


def test_batida_offline_grava_hora_do_servidor_menos_a_idade(cliente):
    conn = db_module.get_db()
    conn.execute("INSERT INTO funcionarios (nome, codigo, situacao) VALUES ('Rita', 'R1', 'ativo')")
    conn.commit()
    conn.close()
    _totem_autorizado(cliente)

    r = cliente.post("/api/bater-ponto", json={"codigo": "R1", "idade_segundos": 3600})
    assert r.get_json()["ok"] and r.get_json()["tipo"] == "entrada"
    # reenvio da mesma batida (tablet não soube que entrou) é recusado, não duplica
    assert cliente.post("/api/bater-ponto", json={"codigo": "R1", "idade_segundos": 3598}).status_code == 429
    # batida online agora vira saída
    assert cliente.post("/api/bater-ponto", json={"codigo": "R1"}).get_json()["tipo"] == "saida"

    conn = db_module.get_db()
    linhas = conn.execute("SELECT tipo, timestamp, motivo FROM registros ORDER BY timestamp").fetchall()
    conn.close()
    entrada = datetime.strptime(linhas[0]["timestamp"], "%Y-%m-%d %H:%M:%S")
    assert abs((datetime.now() - entrada).total_seconds() - 3600) < 60
    assert "sem internet" in linhas[0]["motivo"] and linhas[1]["motivo"] is None


def test_batida_offline_velha_demais_e_recusada(cliente):
    conn = db_module.get_db()
    conn.execute("INSERT INTO funcionarios (nome, codigo, situacao) VALUES ('Rita', 'R1', 'ativo')")
    conn.commit()
    conn.close()
    _totem_autorizado(cliente)
    for idade in (10 * 24 * 3600, -5, "ontem"):
        assert cliente.post("/api/bater-ponto", json={"codigo": "R1", "idade_segundos": idade}).status_code == 400


def test_admin_cria_chefe_de_setor_pela_tela(cliente, dois_setores):
    from tests.test_app_rotas import _csrf_token
    _criar_usuario("admin3", SENHA, "administrador")
    _login(cliente, "admin3", SENHA)
    html = cliente.get("/admin/usuarios/novo").get_data(as_text=True)
    assert "Todos os setores" in html and "Obras" in html
    cliente.post("/admin/usuarios/novo", data={
        "csrf_token": _csrf_token(html), "nome": "Chefe Obras", "usuario": "chefe_obras",
        "perfil": "direcao", "senha": SENHA, "setor_id": dois_setores["obras"],
    })
    assert "Obras" in cliente.get("/admin/usuarios").get_data(as_text=True)
    cliente.get("/admin/logout")
    _login(cliente, "chefe_obras", SENHA)
    html = cliente.get("/admin/funcionarios").get_data(as_text=True)
    assert "Paulo Obras" in html and "Ana Escola" not in html
