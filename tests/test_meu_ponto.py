"""Meu Ponto: servidor entra com matrícula + PIN, pede justificativa; chefe do setor/RH decidem."""
import io
import re
from datetime import date

import pytest

import db as db_module
from tests.test_app_rotas import _criar_usuario, _csrf_token, _login

SENHA = "SenhaForte123!"
HOJE = date.today().isoformat()


def _token(cliente, url):
    return _csrf_token(cliente.get(url).get_data(as_text=True))


def _gerar_pin(cliente, funcionario_id):
    html = cliente.post(
        f"/admin/funcionarios/{funcionario_id}/pin-meu-ponto",
        data={"csrf_token": _token(cliente, f"/admin/funcionarios/{funcionario_id}")},
    ).get_data(as_text=True)
    return re.search(r">(\d{6})<", html).group(1)


def _entrar_servidor(cliente, matricula, pin):
    return cliente.post(
        "/meu-ponto/entrar",
        data={"csrf_token": _token(cliente, "/meu-ponto/entrar"), "matricula": matricula, "pin": pin},
    )


def _pedir(cliente, data=HOJE, arquivo=None):
    dados = {"csrf_token": _token(cliente, "/meu-ponto"), "data": data, "tipo": "atestado", "motivo": "Consulta médica"}
    if arquivo:
        dados["arquivo"] = arquivo
    return cliente.post("/meu-ponto/justificativa", data=dados, content_type="multipart/form-data")


@pytest.fixture
def cenario(cliente):
    conn = db_module.get_db()
    escola = conn.execute("INSERT INTO setores (nome) VALUES ('Escola')").lastrowid
    obras = conn.execute("INSERT INTO setores (nome) VALUES ('Obras')").lastrowid
    ana = conn.execute(
        "INSERT INTO funcionarios (nome, codigo, matricula, situacao, setor_id) VALUES ('Ana Escola', 'A1', '0101', 'ativo', ?)",
        (escola,),
    ).lastrowid
    paulo = conn.execute(
        "INSERT INTO funcionarios (nome, codigo, matricula, situacao, setor_id) VALUES ('Paulo Obras', 'P1', '0202', 'ativo', ?)",
        (obras,),
    ).lastrowid
    conn.commit()
    conn.close()
    _criar_usuario("rh1", SENHA, "rh")
    _criar_usuario("chefe_obras", SENHA, "direcao")
    _criar_usuario("chefe_escola", SENHA, "direcao")
    conn = db_module.get_db()
    conn.execute("UPDATE usuarios SET setor_id = ? WHERE usuario = 'chefe_obras'", (obras,))
    conn.execute("UPDATE usuarios SET setor_id = ? WHERE usuario = 'chefe_escola'", (escola,))
    conn.commit()
    conn.close()
    _login(cliente, "rh1", SENHA)
    pin = _gerar_pin(cliente, ana)
    cliente.get("/admin/logout")
    return {"ana": ana, "paulo": paulo, "pin": pin}


def _pedido_unico():
    conn = db_module.get_db()
    try:
        return conn.execute("SELECT * FROM solicitacoes_justificativa").fetchone()
    finally:
        conn.close()


def test_pin_errado_nao_entra_e_bloqueia_apos_5(cliente, cenario):
    pin_errado = "000000" if cenario["pin"] != "000000" else "111111"
    assert "incorretos" in _entrar_servidor(cliente, "0101", pin_errado).get_data(as_text=True)
    for _ in range(4):
        _entrar_servidor(cliente, "0101", pin_errado)
    resposta = _entrar_servidor(cliente, "0101", cenario["pin"])
    assert "Muitas tentativas" in resposta.get_data(as_text=True)
    assert cliente.get("/meu-ponto").status_code == 302


def test_servidor_ve_o_proprio_ponto_e_nao_o_painel(cliente, cenario):
    assert _entrar_servidor(cliente, "0101", cenario["pin"]).status_code == 302
    html = cliente.get("/meu-ponto").get_data(as_text=True)
    assert "Ana Escola" in html and "Paulo Obras" not in html
    assert cliente.get("/admin/funcionarios").status_code == 302  # sessão de servidor não abre o painel


def test_novo_pin_derruba_sessao_aberta(cliente, cenario):
    _entrar_servidor(cliente, "0101", cenario["pin"])
    conn = db_module.get_db()
    conn.execute("UPDATE funcionarios SET pin_hash = 'scrypt:outro$hash$qualquercoisa' WHERE id = ?", (cenario["ana"],))
    conn.commit()
    conn.close()
    assert cliente.get("/meu-ponto").status_code == 302


def test_pedido_com_atestado_aprovado_vira_justificativa(cliente, cenario):
    _entrar_servidor(cliente, "0101", cenario["pin"])
    _pedir(cliente, arquivo=(io.BytesIO(b"%PDF-1.4 teste"), "atestado.pdf"))
    _pedir(cliente)  # 2º pedido no mesmo dia é recusado (contagem = 1 lá embaixo)
    pedido = _pedido_unico()
    assert pedido["status"] == "pendente" and pedido["anexo_nome"] == "atestado.pdf"
    cliente.get("/meu-ponto/sair")

    # Chefe de outro setor não vê nem decide.
    _login(cliente, "chefe_obras", SENHA)
    assert "Ana Escola" not in cliente.get("/admin/solicitacoes").get_data(as_text=True)
    token = _token(cliente, "/admin/senha")  # lista vazia não tem formulário
    assert cliente.post(f"/admin/solicitacoes/{pedido['id']}/decidir", data={"csrf_token": token, "acao": "aprovar"}).status_code == 404
    cliente.get("/admin/logout")

    _login(cliente, "chefe_escola", SENHA)
    html = cliente.get("/admin/solicitacoes").get_data(as_text=True)
    assert "Ana Escola" in html
    token = _csrf_token(html)
    cliente.post(f"/admin/solicitacoes/{pedido['id']}/decidir", data={"csrf_token": token, "acao": "aprovar"})
    assert cliente.get(f"/admin/solicitacoes/{pedido['id']}/anexo").status_code == 200

    conn = db_module.get_db()
    try:
        ausencia = conn.execute("SELECT * FROM ausencias WHERE funcionario_id = ?", (cenario["ana"],)).fetchone()
        assert ausencia["data"] == HOJE and ausencia["tipo"] == "atestado"
        assert conn.execute("SELECT COUNT(*) FROM documentos WHERE ausencia_id = ?", (ausencia["id"],)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM solicitacoes_justificativa").fetchone()[0] == 1
    finally:
        conn.close()
    assert _pedido_unico()["status"] == "aprovada"

    # Responder de novo não duplica.
    cliente.post(f"/admin/solicitacoes/{pedido['id']}/decidir", data={"csrf_token": token, "acao": "recusar", "resposta": "x"})
    assert _pedido_unico()["status"] == "aprovada"


def test_recusar_exige_resposta_e_servidor_le(cliente, cenario):
    _entrar_servidor(cliente, "0101", cenario["pin"])
    _pedir(cliente)
    cliente.get("/meu-ponto/sair")
    pedido = _pedido_unico()

    _login(cliente, "rh1", SENHA)
    token = _token(cliente, "/admin/solicitacoes")
    cliente.post(f"/admin/solicitacoes/{pedido['id']}/decidir", data={"csrf_token": token, "acao": "recusar"})
    assert _pedido_unico()["status"] == "pendente"
    cliente.post(f"/admin/solicitacoes/{pedido['id']}/decidir",
                 data={"csrf_token": token, "acao": "recusar", "resposta": "Traga o atestado original"})
    assert _pedido_unico()["status"] == "recusada"
    cliente.get("/admin/logout")

    _entrar_servidor(cliente, "0101", cenario["pin"])
    assert "Traga o atestado original" in cliente.get("/meu-ponto").get_data(as_text=True)


def test_pedido_fora_da_janela_e_recusado(cliente, cenario):
    _entrar_servidor(cliente, "0101", cenario["pin"])
    _pedir(cliente, data="2020-01-01")
    assert _pedido_unico() is None


def test_service_worker_servido_na_raiz(cliente):
    resposta = cliente.get("/sw.js")
    assert resposta.status_code == 200 and "javascript" in resposta.content_type
