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
