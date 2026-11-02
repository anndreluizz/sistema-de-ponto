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


# --- Segurança (auditoria 2026-10) -----------------------------------------


def _criar_funcionario(codigo="QRTESTE123"):
    conexao = db_module.get_db()
    funcionario_id = conexao.execute(
        "INSERT INTO funcionarios (nome, codigo, situacao) VALUES ('Zzz Totem', ?, 'ativo')", (codigo,)
    ).lastrowid
    conexao.commit()
    conexao.close()
    return funcionario_id


def test_qrcode_nao_e_acessivel_sem_login(cliente):
    assert cliente.get("/static/qrcodes/1.png").status_code == 404
    resposta = cliente.get("/admin/arquivos/qrcodes/1.png")
    assert resposta.status_code == 302
    assert "/admin/login" in resposta.headers["Location"]


def test_api_bater_ponto_recusa_aparelho_nao_autorizado(cliente):
    _criar_funcionario()

    resposta = cliente.post("/api/bater-ponto", json={"codigo": "QRTESTE123"})

    assert resposta.status_code == 403
    conexao = db_module.get_db()
    assert conexao.execute("SELECT COUNT(*) c FROM registros").fetchone()["c"] == 0
    conexao.close()


def test_api_bater_ponto_aceita_aparelho_autorizado_por_admin(cliente):
    _criar_funcionario()
    _criar_usuario("admin_totem", "SenhaForte123!", "administrador")
    _login(cliente, "admin_totem", "SenhaForte123!")
    token = _csrf_token(cliente.get("/admin/configuracoes").get_data(as_text=True))
    cliente.post("/admin/totem/autorizar", data={"csrf_token": token})
    cliente.get("/admin/logout")  # o totem continua autorizado sem sessão de admin

    resposta = cliente.post("/api/bater-ponto", json={"codigo": "QRTESTE123"})

    assert resposta.status_code == 200
    assert resposta.get_json()["tipo"] == "entrada"


def test_login_ignora_redirecionamento_para_outro_site(cliente):
    _criar_usuario("admin_redir", "SenhaForte123!", "administrador")
    for destino in ("//site-falso.com/login", r"/\site-falso.com", "http://site-falso.com", "/%09/site-falso.com"):
        cliente.get("/admin/logout")
        token = _csrf_token(cliente.get("/admin/login").get_data(as_text=True))
        resposta = cliente.post(
            "/admin/login?proximo=" + destino,
            data={"csrf_token": token, "usuario": "admin_redir", "senha": "SenhaForte123!"},
        )
        assert resposta.headers["Location"].endswith("/admin/dashboard"), destino


def test_usuario_inativado_perde_acesso_na_hora(cliente):
    _criar_usuario("rh_demitido", "SenhaForte123!", "rh")
    _login(cliente, "rh_demitido", "SenhaForte123!")
    assert cliente.get("/admin/dashboard").status_code == 200

    conexao = db_module.get_db()
    conexao.execute("UPDATE usuarios SET ativo = 0 WHERE usuario = 'rh_demitido'")
    conexao.commit()
    conexao.close()

    resposta = cliente.get("/admin/dashboard")
    assert resposta.status_code == 302
    assert "/admin/login" in resposta.headers["Location"]


def test_senha_padrao_obriga_troca_antes_de_usar_o_sistema(cliente):
    _login(cliente, "admin", "admin123")  # criado pelo init_db

    resposta = cliente.get("/admin/funcionarios")
    assert resposta.status_code == 302
    assert "/admin/senha" in resposta.headers["Location"]

    token = _csrf_token(cliente.get("/admin/senha").get_data(as_text=True))
    cliente.post(
        "/admin/senha",
        data={"csrf_token": token, "senha_atual": "admin123", "nova_senha": "NovaSenha!9", "confirmar_senha": "NovaSenha!9"},
    )
    assert cliente.get("/admin/funcionarios").status_code == 200


# --- Backup automático (roda numa thread, sem requisição) ------------------


def test_backup_automatico_roda_sem_requisicao_e_audita_falha(cliente, tmp_path):
    import app  # noqa: F401  (registra as rotas)
    import nucleo as app_module

    app_module.verificar_backup_automatico()
    assert len(list(app_module.BACKUPS_AUTO_DIR.glob("auto-*.zip"))) == 1

    # pasta de destino extra inexistente: backup local continua, falha vai pra auditoria
    conexao = db_module.get_db()
    conexao.execute("UPDATE configuracoes SET backup_pasta_destino = ? WHERE id = 1", (str(tmp_path / "sumiu"),))
    conexao.commit()
    for zip_antigo in app_module.BACKUPS_AUTO_DIR.glob("auto-*.zip"):
        zip_antigo.unlink()
    app_module.verificar_backup_automatico()
    linha = conexao.execute(
        "SELECT usuario FROM auditoria WHERE acao = 'backup_automatico_falhou'"
    ).fetchone()
    conexao.close()
    assert linha["usuario"] == "sistema"
    assert len(list(app_module.BACKUPS_AUTO_DIR.glob("auto-*.zip"))) == 1


def test_restaurar_recusa_backup_corrompido(tmp_path):
    import zipfile

    from rotas.sistema import validar_backup_zip

    caminho_zip = tmp_path / "ruim.zip"
    with zipfile.ZipFile(caminho_zip, "w") as zf:
        zf.writestr("ponto.db", b"isto nao e um banco sqlite" * 100)
    try:
        validar_backup_zip(caminho_zip)
        assert False, "deveria ter recusado"
    except ValueError as erro:
        assert "corrompido" in str(erro)


def test_bater_ponto_duas_vezes_seguidas_registra_so_uma(cliente):
    _criar_funcionario("DUPLO123")
    _criar_usuario("admin_duplo", "SenhaForte123!", "administrador")
    _login(cliente, "admin_duplo", "SenhaForte123!")
    token = _csrf_token(cliente.get("/admin/configuracoes").get_data(as_text=True))
    cliente.post("/admin/totem/autorizar", data={"csrf_token": token})

    assert cliente.post("/api/bater-ponto", json={"codigo": "DUPLO123"}).status_code == 200
    assert cliente.post("/api/bater-ponto", json={"codigo": "DUPLO123"}).status_code == 429
    conexao = db_module.get_db()
    assert conexao.execute("SELECT COUNT(*) c FROM registros").fetchone()["c"] == 1
    conexao.close()


def test_editar_turno_pela_tela_guarda_jornada_antiga(cliente):
    conexao = db_module.get_db()
    turno_id = conexao.execute(
        "INSERT INTO turnos (nome, horario_entrada, horario_saida, tolerancia_minutos, dias_semana) "
        "VALUES ('manha', '08:00', '12:00', 10, '0,1,2,3,4')"
    ).lastrowid
    conexao.execute("INSERT INTO funcionarios (nome, codigo, situacao, turno_id) VALUES ('Zzz Jornada', 'JJJ1', 'ativo', ?)", (turno_id,))
    conexao.commit()
    conexao.close()
    _criar_usuario("admin_jornada", "SenhaForte123!", "administrador")
    _login(cliente, "admin_jornada", "SenhaForte123!")
    token = _csrf_token(cliente.get("/admin/turnos").get_data(as_text=True))

    cliente.post(f"/admin/turnos/{turno_id}/editar", data={
        "csrf_token": token, "nome": "manha", "horario_entrada": "07:00", "horario_saida": "11:00",
        "tolerancia_minutos": "10", "tolerancia_saida_minutos": "10", "dias_semana": ["0", "1", "2", "3", "4"],
    })

    conexao = db_module.get_db()
    linha = conexao.execute("SELECT dados FROM jornada_historico").fetchone()
    conexao.close()
    assert linha is not None and '"horario_entrada": "08:00"' in linha["dados"]


# --- Fechamento do mês ------------------------------------------------------


def _contar(sql):
    conexao = db_module.get_db()
    n = conexao.execute(sql).fetchone()[0]
    conexao.close()
    return n


def test_mes_fechado_bloqueia_lancamento_manual_e_so_admin_reabre(cliente):
    funcionario_id = _criar_funcionario("FECHA1")
    _criar_usuario("rh_fecha", "SenhaForte123!", "rh")
    _login(cliente, "rh_fecha", "SenhaForte123!")
    token = _csrf_token(cliente.get("/admin/relatorios").get_data(as_text=True))

    cliente.post("/admin/meses/fechar", data={"csrf_token": token, "mes": "2026-01"})
    resposta = cliente.post("/admin/registros/novo", data={
        "csrf_token": token, "funcionario_id": funcionario_id, "tipo": "entrada",
        "timestamp": "2026-01-10T08:00", "motivo": "teste",
    }, follow_redirects=True)
    assert "já foi fechado" in resposta.get_data(as_text=True)
    assert _contar("SELECT COUNT(*) FROM registros") == 0

    # RH não reabre
    cliente.post("/admin/meses/2026-01/reabrir", data={"csrf_token": token})
    assert _contar("SELECT COUNT(*) FROM meses_fechados") == 1

    cliente.get("/admin/logout")
    _criar_usuario("admin_fecha", "SenhaForte123!", "administrador")
    _login(cliente, "admin_fecha", "SenhaForte123!")
    token = _csrf_token(cliente.get("/admin/relatorios").get_data(as_text=True))
    cliente.post("/admin/meses/2026-01/reabrir", data={"csrf_token": token})
    assert _contar("SELECT COUNT(*) FROM meses_fechados") == 0


def test_nao_fecha_mes_corrente(cliente):
    from datetime import date

    _criar_usuario("admin_corrente", "SenhaForte123!", "administrador")
    _login(cliente, "admin_corrente", "SenhaForte123!")
    token = _csrf_token(cliente.get("/admin/relatorios").get_data(as_text=True))

    cliente.post("/admin/meses/fechar", data={"csrf_token": token, "mes": date.today().strftime("%Y-%m")})

    assert _contar("SELECT COUNT(*) FROM meses_fechados") == 0


# --- Foto na batida ---------------------------------------------------------


def _autorizar_totem(cliente, usuario):
    _criar_usuario(usuario, "SenhaForte123!", "administrador")
    _login(cliente, usuario, "SenhaForte123!")
    token = _csrf_token(cliente.get("/admin/configuracoes").get_data(as_text=True))
    cliente.post("/admin/totem/autorizar", data={"csrf_token": token})


def test_batida_guarda_foto_e_so_gestao_ve(cliente):
    import base64
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), "red").save(buffer, "JPEG")
    foto = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()
    _criar_funcionario("FOTO1")
    _autorizar_totem(cliente, "admin_foto")

    assert cliente.post("/api/bater-ponto", json={"codigo": "FOTO1", "foto": foto}).status_code == 200

    conexao = db_module.get_db()
    registro_id = conexao.execute("SELECT id FROM registros").fetchone()["id"]
    conexao.close()
    resposta = cliente.get(f"/admin/registros/{registro_id}/foto")
    assert resposta.status_code == 200 and resposta.data[:2] == b"\xff\xd8"

    cliente.get("/admin/logout")
    assert cliente.get(f"/admin/registros/{registro_id}/foto").status_code == 302  # sem login


def test_batida_sem_foto_ou_foto_invalida_registra_igual(cliente):
    _criar_funcionario("FOTO2")
    _autorizar_totem(cliente, "admin_foto2")

    resposta = cliente.post("/api/bater-ponto", json={"codigo": "FOTO2", "foto": "data:image/jpeg;base64,!!!lixo"})

    assert resposta.status_code == 200
    conexao = db_module.get_db()
    registro_id = conexao.execute("SELECT id FROM registros").fetchone()["id"]
    conexao.close()
    assert cliente.get(f"/admin/registros/{registro_id}/foto").status_code == 404


def test_editar_funcionario_sem_email_nao_mostra_none(cliente):
    # Campo vazio aparecia como "None"; no e-mail isso fazia o navegador barrar o envio.
    _criar_usuario("admin_teste", "SenhaForte123!", "administrador")
    _login(cliente, "admin_teste", "SenhaForte123!")
    conexao = db_module.get_db()
    funcionario_id = conexao.execute(
        "INSERT INTO funcionarios (nome, codigo, situacao) VALUES ('Sem Contato', 'ZZZ999', 'ativo')"
    ).lastrowid
    conexao.commit()
    conexao.close()
    html = cliente.get(f"/admin/funcionarios/{funcionario_id}/editar").get_data(as_text=True)
    assert 'value="None"' not in html
    assert ">None</textarea>" not in html


def test_consulta_nao_baixa_qrcode_nem_foto(cliente):
    # Com o QR Code de um colega, alguém do perfil Consulta bateria o ponto por ele.
    _criar_usuario("consulta_teste", "SenhaForte123!", "consulta")
    _login(cliente, "consulta_teste", "SenhaForte123!")
    for pasta in ("qrcodes", "fotos"):
        resposta = cliente.get(f"/admin/arquivos/{pasta}/1.png")
        assert resposta.status_code == 302
        assert resposta.headers["Location"].endswith("/admin/dashboard")


# --- Senha nos backups que saem do computador --------------------------------


def test_copia_externa_do_backup_vai_com_senha(cliente, tmp_path):
    import app  # noqa: F401  (registra as rotas)
    import nucleo

    destino = tmp_path / "onedrive"
    destino.mkdir()
    conexao = db_module.get_db()
    conexao.execute("UPDATE configuracoes SET backup_pasta_destino = ?, backup_senha = ? WHERE id = 1",
                    (str(destino), "senha-forte-1"))
    conexao.commit()
    conexao.close()

    nucleo.verificar_backup_automatico()
    [local] = nucleo.BACKUPS_AUTO_DIR.glob("auto-*.zip")
    [externo] = destino.glob("auto-*.zip")
    assert not nucleo.backup_tem_senha(local)  # no próprio PC fica sem senha
    assert nucleo.backup_tem_senha(externo)

    aberto = nucleo.decifrar_zip(externo, "senha-forte-1", tmp_path / "aberto.zip")
    from rotas.sistema import validar_backup_zip
    validar_backup_zip(aberto)
    try:
        nucleo.decifrar_zip(externo, "errada", tmp_path / "x.zip")
        assert False, "deveria recusar a senha errada"
    except ValueError as erro:
        assert "incorreta" in str(erro)


def test_restaurar_backup_com_senha_pede_a_senha(cliente, tmp_path):
    import io
    import nucleo

    _criar_usuario("admin_teste", "SenhaForte123!", "administrador")
    _login(cliente, "admin_teste", "SenhaForte123!")
    cifrado = nucleo.cifrar_zip(nucleo.montar_backup_zip_bytes(), "senha-forte-1")
    token = _csrf_token(cliente.get("/admin/backup").get_data(as_text=True))
    resposta = cliente.post("/admin/backup/restaurar", data={
        "csrf_token": token, "confirmacao": "RESTAURAR",
        "arquivo": (io.BytesIO(cifrado), "backup.zip"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert "Este backup tem senha" in resposta.get_data(as_text=True)


def test_restaurar_backup_com_senha_certa_funciona(cliente, tmp_path, monkeypatch):
    import io
    import nucleo
    import rotas.sistema

    monkeypatch.setattr(rotas.sistema, "BASE_DIR", tmp_path)  # a cópia de segurança vai pra cá

    _criar_usuario("admin_teste", "SenhaForte123!", "administrador")
    _login(cliente, "admin_teste", "SenhaForte123!")
    cifrado = nucleo.cifrar_zip(nucleo.montar_backup_zip_bytes(), "senha-forte-1")
    token = _csrf_token(cliente.get("/admin/backup").get_data(as_text=True))
    resposta = cliente.post("/admin/backup/restaurar", data={
        "csrf_token": token, "confirmacao": "RESTAURAR", "senha_backup": "senha-forte-1",
        "arquivo": (io.BytesIO(cifrado), "backup.zip"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert "Backup restaurado com sucesso" in resposta.get_data(as_text=True)


def test_bloqueio_de_login_e_por_aparelho(cliente):
    # Antes, errar a senha do admin 5 vezes de qualquer aparelho travava a conta para todos.
    _criar_usuario("admin_teste", "SenhaForte123!", "administrador")

    def tentar(senha, ip):
        token = _csrf_token(cliente.get("/admin/login", environ_base={"REMOTE_ADDR": ip}).get_data(as_text=True))
        return cliente.post("/admin/login", data={"csrf_token": token, "usuario": "admin_teste", "senha": senha},
                            environ_base={"REMOTE_ADDR": ip})

    for _ in range(5):
        resposta = tentar("errada", "10.0.0.66")
    assert "bloqueada" in resposta.get_data(as_text=True).lower()
    assert "bloqueada" in tentar("SenhaForte123!", "10.0.0.66").get_data(as_text=True).lower()

    resposta_outro_aparelho = tentar("SenhaForte123!", "10.0.0.10")
    assert resposta_outro_aparelho.status_code == 302
    assert "/admin/dashboard" in resposta_outro_aparelho.headers["Location"]


# --- Itens menores da auditoria de segurança ----------------------------------


def test_trocar_senha_derruba_sessao_de_outro_aparelho(cliente):
    import app as app_module

    _criar_usuario("admin_teste", "SenhaForte123!", "administrador")
    outro_aparelho = app_module.app.test_client()
    _login(outro_aparelho, "admin_teste", "SenhaForte123!")
    _login(cliente, "admin_teste", "SenhaForte123!")
    assert outro_aparelho.get("/admin/dashboard").status_code == 200

    token = _csrf_token(cliente.get("/admin/senha").get_data(as_text=True))
    cliente.post("/admin/senha", data={"csrf_token": token, "senha_atual": "SenhaForte123!",
                                       "nova_senha": "OutraSenha!9", "confirmar_senha": "OutraSenha!9"})
    assert cliente.get("/admin/dashboard").status_code == 200  # quem trocou continua
    resposta = outro_aparelho.get("/admin/dashboard")
    assert resposta.status_code == 302 and "/admin/login" in resposta.headers["Location"]


def test_senha_resetada_pelo_admin_obriga_troca(cliente):
    import app as app_module

    _criar_usuario("admin_teste", "SenhaForte123!", "administrador")
    _criar_usuario("rh_teste", "SenhaForte123!", "rh")
    _login(cliente, "admin_teste", "SenhaForte123!")
    conexao = db_module.get_db()
    rh_id = conexao.execute("SELECT id FROM usuarios WHERE usuario = 'rh_teste'").fetchone()["id"]
    conexao.close()
    token = _csrf_token(cliente.get(f"/admin/usuarios/{rh_id}/editar").get_data(as_text=True))
    cliente.post(f"/admin/usuarios/{rh_id}/resetar-senha", data={"csrf_token": token, "nova_senha": "Provisoria1"})

    rh = app_module.app.test_client()
    _login(rh, "rh_teste", "Provisoria1")
    resposta = rh.get("/admin/funcionarios")
    assert resposta.status_code == 302 and "/admin/senha" in resposta.headers["Location"]


def test_csv_neutraliza_formula_no_nome(cliente):
    _criar_usuario("admin_teste", "SenhaForte123!", "administrador")
    _login(cliente, "admin_teste", "SenhaForte123!")
    conexao = db_module.get_db()
    fid = conexao.execute(
        "INSERT INTO funcionarios (nome, codigo, situacao) VALUES ('=HYPERLINK(\"x\")', 'FFF111', 'ativo')"
    ).lastrowid
    conexao.execute("INSERT INTO registros (funcionario_id, tipo, timestamp) VALUES (?, 'entrada', '2026-10-05 07:00:00')", (fid,))
    conexao.commit()
    conexao.close()
    csv = cliente.get("/admin/relatorios/exportar.csv?data_inicio=2026-10-01&data_fim=2026-10-31").get_data(as_text=True)
    assert "'=HYPERLINK" in csv


def test_paginas_tem_cabecalhos_de_seguranca(cliente):
    resposta = cliente.get("/admin/login")
    assert resposta.headers["X-Frame-Options"] == "DENY"
    assert resposta.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in resposta.headers["Content-Security-Policy"]


def test_restaurar_nao_poe_html_na_pasta_publica(cliente, tmp_path):
    import zipfile
    import nucleo
    from rotas.sistema import extrair_arquivos_backup

    caminho = tmp_path / "b.zip"
    with zipfile.ZipFile(caminho, "w") as zf:
        zf.writestr("static/uploads/logo.png", b"png")
        zf.writestr("static/uploads/pagina.html", b"<script>alert(1)</script>")
    extrair_arquivos_backup(caminho)
    assert (nucleo.UPLOADS_DIR / "logo.png").exists()
    assert not (nucleo.UPLOADS_DIR / "pagina.html").exists()
