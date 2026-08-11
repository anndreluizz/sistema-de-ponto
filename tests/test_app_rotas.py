"""Testes das rotas HTTP (app.py), usando o cliente de teste do Flask.

Diferente de test_relatorios_service.py (que testa cálculos puros), aqui o alvo é
o comportamento das telas em si: login/bloqueio, proteção por perfil, CSRF, e as
exportações que dependem de várias peças conectadas corretamente (foi um erro
desse tipo — um parâmetro que deixei de repassar entre duas funções — que motivou
este arquivo: existia antes só como teste manual, nunca automatizado).

Cada teste usa a fixture `cliente` (tests/conftest.py), que isola um banco novo
por teste e devolve um app.test_client() já pronto para uso.
"""

import re

from werkzeug.security import generate_password_hash

import db as db_module


def _criar_usuario(usuario, senha, perfil, nome=None):
    conexao = db_module.get_db()
    conexao.execute(
        "INSERT INTO usuarios (nome, usuario, senha_hash, perfil, ativo) VALUES (?, ?, ?, ?, 1)",
        (nome or usuario, usuario, generate_password_hash(senha), perfil),
    )
    conexao.commit()
    conexao.close()


def _csrf_token(html):
    return re.search(r'name="csrf_token" value="([^"]*)"', html).group(1)


def _login(client, usuario, senha):
    resposta = client.get("/admin/login")
    token = _csrf_token(resposta.get_data(as_text=True))
    return client.post("/admin/login", data={"csrf_token": token, "usuario": usuario, "senha": senha})


# --- Login: sucesso, senha errada, bloqueio após 5 tentativas -----------------


def test_login_sucesso_redireciona_para_dashboard(cliente):
    _criar_usuario("admin_teste", "SenhaForte123!", "administrador")

    resposta = _login(cliente, "admin_teste", "SenhaForte123!")

    assert resposta.status_code == 302
    assert "/admin/dashboard" in resposta.headers["Location"]
    # confirma que a sessão realmente foi criada (não só o redirect): uma rota
    # protegida deve responder 200 em vez de mandar de volta pro login.
    assert cliente.get("/admin/dashboard").status_code == 200


def test_login_senha_errada_mostra_mensagem_generica_e_nao_autentica(cliente):
    _criar_usuario("admin_teste2", "SenhaForte123!", "administrador")

    resposta = _login(cliente, "admin_teste2", "senha_errada")

    assert resposta.status_code == 200  # renderiza login.html direto, sem redirect
    assert "Usuário ou senha inválidos" in resposta.get_data(as_text=True)
    resposta_dashboard = cliente.get("/admin/dashboard")
    assert resposta_dashboard.status_code == 302
    assert "/admin/login" in resposta_dashboard.headers["Location"]


def test_login_bloqueia_apos_5_tentativas_falhas_seguidas(cliente):
    _criar_usuario("admin_teste3", "SenhaCerta123!", "administrador")

    for _ in range(4):
        resposta = _login(cliente, "admin_teste3", "senha_errada")
        assert "bloqueada" not in resposta.get_data(as_text=True).lower()

    quinta_resposta = _login(cliente, "admin_teste3", "senha_errada")
    assert "bloqueada" in quinta_resposta.get_data(as_text=True).lower()

    # mesmo com a senha certa agora, a conta continua bloqueada
    resposta_senha_certa = _login(cliente, "admin_teste3", "SenhaCerta123!")
    assert "bloqueada" in resposta_senha_certa.get_data(as_text=True).lower()
    assert cliente.get("/admin/dashboard").status_code == 302


# --- CSRF -----------------------------------------------------------------


def test_post_sem_token_csrf_e_rejeitado(cliente):
    _criar_usuario("admin_teste4", "SenhaForte123!", "administrador")
    _login(cliente, "admin_teste4", "SenhaForte123!")

    resposta = cliente.post(
        "/admin/feriados", data={"data": "2026-09-07", "nome": "Feriado Sem Token"}
    )

    assert resposta.status_code == 400
    conexao = db_module.get_db()
    total = conexao.execute("SELECT COUNT(*) c FROM feriados").fetchone()["c"]
    conexao.close()
    assert total == 0


# --- RH não pode definir salário (só Administrador) ------------------------


def test_rh_nao_consegue_definir_salario_de_funcionario(cliente):
    _criar_usuario("rh_teste", "SenhaForte123!", "rh")
    _login(cliente, "rh_teste", "SenhaForte123!")

    pagina_form = cliente.get("/admin/funcionarios/novo")
    token = _csrf_token(pagina_form.get_data(as_text=True))
    cliente.post(
        "/admin/funcionarios/novo",
        data={"csrf_token": token, "nome": "Zzz Teste Rh Salario", "salario": "5000.00"},
    )

    conexao = db_module.get_db()
    linha = conexao.execute(
        "SELECT salario FROM funcionarios WHERE nome = 'Zzz Teste Rh Salario'"
    ).fetchone()
    conexao.close()
    assert linha is not None, "o funcionário deveria ter sido criado mesmo sem salário"
    assert linha["salario"] is None


def test_administrador_consegue_definir_salario_de_funcionario(cliente):
    _criar_usuario("admin_teste5", "SenhaForte123!", "administrador")
    _login(cliente, "admin_teste5", "SenhaForte123!")

    pagina_form = cliente.get("/admin/funcionarios/novo")
    token = _csrf_token(pagina_form.get_data(as_text=True))
    cliente.post(
        "/admin/funcionarios/novo",
        data={"csrf_token": token, "nome": "Zzz Teste Admin Salario", "salario": "5000.00"},
    )

    conexao = db_module.get_db()
    linha = conexao.execute(
        "SELECT salario FROM funcionarios WHERE nome = 'Zzz Teste Admin Salario'"
    ).fetchone()
    conexao.close()
    assert linha is not None
    assert linha["salario"] == "5000.00"


# --- Perfil "consulta" não acessa telas de gestão --------------------------


def test_perfil_consulta_e_bloqueado_de_tela_de_gestao(cliente):
    _criar_usuario("consulta_teste", "SenhaForte123!", "consulta")
    _login(cliente, "consulta_teste", "SenhaForte123!")

    resposta = cliente.get("/admin/funcionarios/novo")

    assert resposta.status_code == 302
    assert "/admin/dashboard" in resposta.headers["Location"]


# --- Folha de Ponto Mensal: PDF/Excel respondem com conteúdo real ----------
# (Cenário desenhado especificamente pra pegar automaticamente o bug que corrigi
# manualmente antes: dias_semana_abrev deixando de ser repassado entre funções,
# que derrubava esta rota com 500.)


def test_folha_de_ponto_mensal_pdf_responde_200_com_conteudo(cliente):
    _criar_usuario("admin_teste6", "SenhaForte123!", "administrador")
    _login(cliente, "admin_teste6", "SenhaForte123!")

    resposta = cliente.get("/admin/relatorios/dias/exportar.pdf?mes=2026-01")

    assert resposta.status_code == 200
    assert resposta.mimetype == "application/pdf"
    assert len(resposta.data) > 500


def test_folha_de_ponto_mensal_excel_responde_200_com_conteudo(cliente):
    _criar_usuario("admin_teste7", "SenhaForte123!", "administrador")
    _login(cliente, "admin_teste7", "SenhaForte123!")

    resposta = cliente.get("/admin/relatorios/dias/exportar.xlsx?mes=2026-01")

    assert resposta.status_code == 200
    assert resposta.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert len(resposta.data) > 500
