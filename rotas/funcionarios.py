"""Funcionários: cadastro, documentos, crachá, histórico, banco de horas e ausências."""

import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import date, datetime
import qrcode
from flask import abort, flash, redirect, render_template, request, send_file, session, url_for
from PIL import Image, ImageOps
from werkzeug.utils import secure_filename
import db
import exportadores
import relatorios_service

import nucleo
from nucleo import (
    EXTENSOES_DOCUMENTO_PERMITIDAS,
    EXTENSOES_IMAGEM_PERMITIDAS,
    NOMES_DIAS_SEMANA_ABREV,
    NOMES_MESES,
    NOMES_PERFIL,
    NOMES_TIPO_AUSENCIA,
    NOMES_VINCULO,
    PERFIS_GESTAO,
    PERFIS_LEITURA,
    _resposta_exportacao,
    app,
    eh_erro_mes_fechado,
    login_obrigatorio,
    perfil_obrigatorio,
    registrar_auditoria,
)


def gerar_codigo_verificacao_espelho(funcionario_id, matricula, mes, total_segundos, extras_segundos):
    mensagem = f"{funcionario_id}|{matricula}|{mes}|{total_segundos}|{extras_segundos}"
    assinatura = hmac.new(app.config["SECRET_KEY"].encode(), mensagem.encode(), hashlib.sha256).hexdigest()
    codigo = assinatura[:8].upper()
    return f"{codigo[:4]}-{codigo[4:]}"


def gerar_qrcode(codigo, funcionario_id):
    nucleo.QRCODES_DIR.mkdir(parents=True, exist_ok=True)
    caminho = nucleo.QRCODES_DIR / f"{funcionario_id}.png"
    imagem = qrcode.make(codigo)
    imagem.save(caminho)


def cpf_valido(cpf):
    cpf = re.sub(r"\D", "", cpf or "")
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for i in (9, 10):
        soma = sum(int(cpf[n]) * ((i + 1) - n) for n in range(i))
        digito = (soma * 10 % 11) % 10
        if digito != int(cpf[i]):
            return False
    return True


def processar_foto_upload(arquivo, funcionario_id):
    if not arquivo or not arquivo.filename:
        return None
    extensao = arquivo.filename.rsplit(".", 1)[-1].lower() if "." in arquivo.filename else ""
    if extensao not in EXTENSOES_IMAGEM_PERMITIDAS:
        raise ValueError("Formato de foto não suportado. Use JPG, PNG ou WEBP.")
    try:
        imagem = Image.open(arquivo.stream)
        imagem = ImageOps.exif_transpose(imagem).convert("RGB")
    except Exception:
        raise ValueError("Não foi possível ler o arquivo como imagem.")
    imagem.thumbnail((480, 480))
    nucleo.FOTOS_DIR.mkdir(parents=True, exist_ok=True)
    imagem.save(nucleo.FOTOS_DIR / f"{funcionario_id}.jpg", "JPEG", quality=85)
    return f"fotos/{funcionario_id}.jpg"


def processar_documento_upload(arquivo, funcionario_id):
    if not arquivo or not arquivo.filename:
        raise ValueError("Nenhum arquivo selecionado.")
    extensao = arquivo.filename.rsplit(".", 1)[-1].lower() if "." in arquivo.filename else ""
    if extensao not in EXTENSOES_DOCUMENTO_PERMITIDAS:
        raise ValueError("Formato não suportado. Use PDF, JPG, PNG ou WEBP.")
    nome_original = arquivo.filename
    nome_seguro = secure_filename(nome_original) or f"documento.{extensao}"
    nome_no_disco = f"{funcionario_id}_{secrets.token_hex(8)}_{nome_seguro}"
    nucleo.DOCUMENTOS_DIR.mkdir(parents=True, exist_ok=True)
    caminho_completo = nucleo.DOCUMENTOS_DIR / nome_no_disco
    arquivo.save(caminho_completo)
    return nome_original, nome_no_disco, caminho_completo.stat().st_size


def proxima_matricula_sugerida(conn):
    linha = conn.execute(
        """SELECT MAX(CAST(matricula AS INTEGER)) AS maior FROM funcionarios
           WHERE matricula GLOB '[0-9]*' AND matricula NOT GLOB '*[^0-9]*'"""
    ).fetchone()
    return f"{(linha['maior'] or 0) + 1:04d}"


@app.route("/admin/funcionarios")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)
def funcionarios():
    conn = db.get_db()
    try:
        lista = conn.execute(
            f"""SELECT funcionarios.*, setores.nome AS setor_nome,
                      {relatorios_service.SQL_COLUNAS_TURNO_EFETIVO},
                      (SELECT MAX(timestamp) FROM registros WHERE registros.funcionario_id = funcionarios.id) AS ultimo_timestamp
               FROM funcionarios
               LEFT JOIN setores ON setores.id = funcionarios.setor_id
               {relatorios_service.SQL_JOIN_TURNO_EFETIVO}
               ORDER BY CASE funcionarios.situacao
                            WHEN 'ativo' THEN 0 WHEN 'ferias' THEN 1 WHEN 'afastado' THEN 2 ELSE 3
                        END, funcionarios.nome"""
        ).fetchall()
        setores_lista = conn.execute("SELECT * FROM setores ORDER BY nome").fetchall()
        turnos_lista = conn.execute("SELECT * FROM turnos ORDER BY nome").fetchall()
        return render_template("funcionarios.html", funcionarios=lista, setores=setores_lista, turnos=turnos_lista)
    finally:
        conn.close()


def salvar_funcionario(conn, funcionario_id):
    nome = request.form.get("nome", "").strip()
    cargo = request.form.get("cargo", "").strip()
    cpf_bruto = request.form.get("cpf", "").strip()
    matricula = request.form.get("matricula", "").strip()
    setor_id = request.form.get("setor_id") or None
    telefone = request.form.get("telefone", "").strip()
    email = request.form.get("email", "").strip()
    data_admissao = request.form.get("data_admissao", "").strip()
    situacao = request.form.get("situacao", "ativo").strip()
    observacoes = request.form.get("observacoes", "").strip()
    banco_horas_inicio = request.form.get("banco_horas_inicio", "").strip() or None
    rg = request.form.get("rg", "").strip()
    salario = request.form.get("salario", "").strip()
    vinculo = request.form.get("vinculo", "").strip() or None
    categoria = request.form.get("categoria", "").strip() or None

    modo_agenda = request.form.get("modo_agenda", "nenhum")
    turno_id = request.form.get("turno_id") or None
    escala_id = request.form.get("escala_id") or None
    escala_data_referencia = request.form.get("escala_data_referencia", "").strip() or None

    erro = None
    if not nome:
        erro = "Informe o nome do funcionário."
    elif situacao not in db.SITUACOES_VALIDAS:
        erro = "Situação inválida."
    elif modo_agenda == "escala" and (not escala_id or not escala_data_referencia):
        erro = "Selecione a escala e a data de referência do ciclo."
    elif vinculo and vinculo not in NOMES_VINCULO:
        erro = "Vínculo inválido."
    elif categoria and categoria not in relatorios_service.NOMES_CATEGORIA:
        erro = "Categoria inválida."

    if modo_agenda == "turno":
        escala_id = None
        escala_data_referencia = None
    elif modo_agenda == "escala":
        turno_id = None
    else:
        turno_id = None
        escala_id = None
        escala_data_referencia = None

    cpf_normalizado = re.sub(r"\D", "", cpf_bruto) if cpf_bruto else ""
    if not erro and cpf_normalizado and not cpf_valido(cpf_normalizado):
        erro = "CPF inválido. Confira os números digitados."

    if erro:
        flash(erro, "erro")
        destino = "editar_funcionario" if funcionario_id else "novo_funcionario"
        return redirect(url_for(destino, funcionario_id=funcionario_id) if funcionario_id else url_for(destino))

    campos = [
        "nome", "cargo", "situacao", "cpf", "matricula", "setor_id", "turno_id", "escala_id",
        "escala_data_referencia", "banco_horas_inicio", "telefone", "email", "data_admissao",
        "observacoes", "rg", "vinculo", "categoria",
    ]
    valores = [
        nome, cargo, situacao, cpf_normalizado or None, matricula or None, setor_id, turno_id,
        escala_id, escala_data_referencia, banco_horas_inicio, telefone or None, email or None,
        data_admissao or None, observacoes or None, rg or None, vinculo, categoria,
    ]
    if session.get("usuario_perfil") == "administrador":
        # Salário só é lido/gravado quando quem está salvando é Administrador — RH nunca
        # toca nessa coluna, então editar um funcionário como RH nunca apaga um salário
        # que o Administrador já tinha definido (a coluna simplesmente não entra no SET/INSERT).
        campos.append("salario")
        valores.append(salario or None)

    try:
        if funcionario_id is None:
            codigo = db.gerar_codigo()
            campos_insert = ["codigo"] + campos
            valores_insert = [codigo] + valores
            marcadores = ", ".join("?" * len(valores_insert))
            cursor = conn.execute(
                f"INSERT INTO funcionarios ({', '.join(campos_insert)}) VALUES ({marcadores})",
                valores_insert,
            )
            conn.commit()
            funcionario_id = cursor.lastrowid
            gerar_qrcode(codigo, funcionario_id)
            registrar_auditoria(conn, "criar", "funcionario", funcionario_id, f"nome={nome}")
        else:
            set_clause = ", ".join(f"{campo}=?" for campo in campos)
            jornadas_antes = relatorios_service.jornadas_atuais(conn)
            conn.execute(
                f"UPDATE funcionarios SET {set_clause} WHERE id=?",
                valores + [funcionario_id],
            )
            relatorios_service.registrar_mudancas_de_jornada(conn, jornadas_antes)
            conn.commit()
            registrar_auditoria(conn, "editar", "funcionario", funcionario_id, f"nome={nome}")
    except sqlite3.IntegrityError:
        flash("Já existe um funcionário com essa matrícula.", "erro")
        destino = "editar_funcionario" if funcionario_id else "novo_funcionario"
        return redirect(url_for(destino, funcionario_id=funcionario_id) if funcionario_id else url_for(destino))

    arquivo_foto = request.files.get("foto")
    if arquivo_foto and arquivo_foto.filename:
        try:
            caminho_foto = processar_foto_upload(arquivo_foto, funcionario_id)
            conn.execute("UPDATE funcionarios SET foto = ? WHERE id = ?", (caminho_foto, funcionario_id))
            conn.commit()
        except ValueError as erro_foto:
            flash(str(erro_foto), "erro")

    flash("Funcionário salvo com sucesso.", "sucesso")
    return redirect(url_for("funcionarios"))


@app.route("/admin/funcionarios/novo", methods=["GET", "POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def novo_funcionario():
    conn = db.get_db()
    try:
        if request.method == "POST":
            return salvar_funcionario(conn, funcionario_id=None)
        setores_lista = conn.execute("SELECT * FROM setores ORDER BY nome").fetchall()
        turnos_lista = conn.execute("SELECT * FROM turnos ORDER BY nome").fetchall()
        escalas_lista = conn.execute("SELECT * FROM escalas ORDER BY nome").fetchall()
        matricula_sugerida = proxima_matricula_sugerida(conn)
        return render_template(
            "funcionario_form.html",
            funcionario=None,
            setores=setores_lista,
            turnos=turnos_lista,
            escalas=escalas_lista,
            matricula_sugerida=matricula_sugerida,
        )
    finally:
        conn.close()


@app.route("/admin/funcionarios/<int:funcionario_id>/editar", methods=["GET", "POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def editar_funcionario(funcionario_id):
    conn = db.get_db()
    try:
        funcionario = conn.execute("SELECT * FROM funcionarios WHERE id = ?", (funcionario_id,)).fetchone()
        if not funcionario:
            abort(404)
        if request.method == "POST":
            return salvar_funcionario(conn, funcionario_id=funcionario_id)
        setores_lista = conn.execute("SELECT * FROM setores ORDER BY nome").fetchall()
        turnos_lista = conn.execute("SELECT * FROM turnos ORDER BY nome").fetchall()
        escalas_lista = conn.execute("SELECT * FROM escalas ORDER BY nome").fetchall()
        documentos_lista = conn.execute(
            "SELECT * FROM documentos WHERE funcionario_id = ? AND ausencia_id IS NULL ORDER BY criado_em DESC",
            (funcionario_id,),
        ).fetchall()
        return render_template(
            "funcionario_form.html",
            funcionario=funcionario,
            setores=setores_lista,
            turnos=turnos_lista,
            escalas=escalas_lista,
            documentos=documentos_lista,
            matricula_sugerida=None,
        )
    finally:
        conn.close()


@app.route("/admin/funcionarios/<int:funcionario_id>/documentos/nova", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def nova_documento_funcionario(funcionario_id):
    conn = db.get_db()
    try:
        funcionario = conn.execute("SELECT id FROM funcionarios WHERE id = ?", (funcionario_id,)).fetchone()
        if not funcionario:
            abort(404)
        tipo = request.form.get("tipo", "").strip() or None
        arquivo = request.files.get("arquivo")
        try:
            nome_original, nome_no_disco, tamanho = processar_documento_upload(arquivo, funcionario_id)
        except ValueError as erro_arquivo:
            flash(str(erro_arquivo), "erro")
            return redirect(url_for("editar_funcionario", funcionario_id=funcionario_id))

        conn.execute(
            """INSERT INTO documentos (funcionario_id, ausencia_id, tipo, nome_arquivo, caminho_arquivo,
               tamanho_bytes, criado_por, criado_em)
               VALUES (?, NULL, ?, ?, ?, ?, ?, datetime('now'))""",
            (funcionario_id, tipo, nome_original, nome_no_disco, tamanho, session.get("usuario_login")),
        )
        conn.commit()
        registrar_auditoria(conn, "criar", "documento", funcionario_id, f"nome_arquivo={nome_original}")
        flash("Documento anexado.", "sucesso")
    finally:
        conn.close()
    return redirect(url_for("editar_funcionario", funcionario_id=funcionario_id))


@app.route("/admin/ausencias/<int:ausencia_id>/documentos/nova", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def nova_documento_ausencia(ausencia_id):
    conn = db.get_db()
    try:
        ausencia = conn.execute("SELECT * FROM ausencias WHERE id = ?", (ausencia_id,)).fetchone()
        if not ausencia:
            abort(404)
        funcionario_id = ausencia["funcionario_id"]
        arquivo = request.files.get("arquivo")
        try:
            nome_original, nome_no_disco, tamanho = processar_documento_upload(arquivo, funcionario_id)
        except ValueError as erro_arquivo:
            flash(str(erro_arquivo), "erro")
            return redirect(url_for("ajustes_ponto", funcionario_id=funcionario_id))

        conn.execute(
            """INSERT INTO documentos (funcionario_id, ausencia_id, tipo, nome_arquivo, caminho_arquivo,
               tamanho_bytes, criado_por, criado_em)
               VALUES (?, ?, 'atestado', ?, ?, ?, ?, datetime('now'))""",
            (funcionario_id, ausencia_id, nome_original, nome_no_disco, tamanho, session.get("usuario_login")),
        )
        conn.commit()
        registrar_auditoria(conn, "criar", "documento", ausencia_id, f"nome_arquivo={nome_original}; ausencia_id={ausencia_id}")
        flash("Atestado anexado.", "sucesso")
    finally:
        conn.close()
    return redirect(url_for("ajustes_ponto", funcionario_id=funcionario_id))


@app.route("/admin/documentos/<int:documento_id>/baixar")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def baixar_documento(documento_id):
    conn = db.get_db()
    try:
        documento = conn.execute("SELECT * FROM documentos WHERE id = ?", (documento_id,)).fetchone()
        if not documento:
            abort(404)
        caminho = nucleo.DOCUMENTOS_DIR / documento["caminho_arquivo"]
        if not caminho.exists():
            abort(404)
        registrar_auditoria(
            conn, "baixar_documento", "documento", documento_id, f"funcionario_id={documento['funcionario_id']}"
        )
        return send_file(caminho, as_attachment=True, download_name=documento["nome_arquivo"])
    finally:
        conn.close()


@app.route("/admin/documentos/<int:documento_id>/excluir", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def excluir_documento(documento_id):
    conn = db.get_db()
    try:
        documento = conn.execute("SELECT * FROM documentos WHERE id = ?", (documento_id,)).fetchone()
        if not documento:
            flash("Documento não encontrado.", "erro")
            return redirect(url_for("funcionarios"))
        funcionario_id = documento["funcionario_id"]
        ausencia_id = documento["ausencia_id"]
        conn.execute("DELETE FROM documentos WHERE id = ?", (documento_id,))
        conn.commit()
        registrar_auditoria(conn, "excluir", "documento", documento_id, f"nome_arquivo={documento['nome_arquivo']}")
        caminho = nucleo.DOCUMENTOS_DIR / documento["caminho_arquivo"]
        try:
            caminho.unlink(missing_ok=True)
        except OSError:
            pass
        flash("Documento excluído.", "sucesso")
    finally:
        conn.close()
    if ausencia_id:
        return redirect(url_for("ajustes_ponto", funcionario_id=funcionario_id))
    return redirect(url_for("editar_funcionario", funcionario_id=funcionario_id))


@app.route("/admin/funcionarios/<int:funcionario_id>/historico-alteracoes")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def historico_alteracoes_funcionario(funcionario_id):
    conn = db.get_db()
    try:
        funcionario = conn.execute("SELECT * FROM funcionarios WHERE id = ?", (funcionario_id,)).fetchone()
        if not funcionario:
            abort(404)
        linhas = conn.execute(
            """SELECT * FROM auditoria WHERE entidade = 'funcionario' AND entidade_id = ?
               ORDER BY timestamp DESC""",
            (funcionario_id,),
        ).fetchall()
        return render_template(
            "historico_alteracoes.html", funcionario=funcionario, linhas=linhas, nomes_perfil=NOMES_PERFIL
        )
    finally:
        conn.close()


@app.route("/admin/funcionarios/<int:funcionario_id>/situacao", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def mudar_situacao_funcionario(funcionario_id):
    situacao = request.form.get("situacao", "").strip()
    if situacao not in db.SITUACOES_VALIDAS:
        flash("Situação inválida.", "erro")
        return redirect(url_for("funcionarios"))
    conn = db.get_db()
    try:
        funcionario = conn.execute("SELECT nome, situacao FROM funcionarios WHERE id = ?", (funcionario_id,)).fetchone()
        conn.execute("UPDATE funcionarios SET situacao = ? WHERE id = ?", (situacao, funcionario_id))
        conn.commit()
        if funcionario:
            registrar_auditoria(
                conn, "mudar_situacao", "funcionario", funcionario_id,
                f"nome={funcionario['nome']}; {funcionario['situacao']} -> {situacao}",
            )
    finally:
        conn.close()
    flash("Situação atualizada.", "sucesso")
    return redirect(url_for("funcionarios"))


@app.route("/admin/funcionarios/<int:funcionario_id>/cracha")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)
def cracha_funcionario(funcionario_id):
    conn = db.get_db()
    try:
        funcionario = conn.execute(
            """SELECT funcionarios.*, setores.nome AS setor_nome
               FROM funcionarios LEFT JOIN setores ON setores.id = funcionarios.setor_id
               WHERE funcionarios.id = ?""",
            (funcionario_id,),
        ).fetchone()
        config = conn.execute("SELECT * FROM configuracoes WHERE id = 1").fetchone()
    finally:
        conn.close()

    if not funcionario:
        abort(404)

    return render_template("cracha.html", funcionarios=[funcionario], config=config)


@app.route("/admin/funcionarios/crachas")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)
def crachas_em_massa():
    """Crachás de todos os ativos (ou de uma categoria), em folhas A4."""
    categoria = request.args.get("categoria", "").strip()
    conn = db.get_db()
    try:
        funcionarios = conn.execute(
            """SELECT funcionarios.*, setores.nome AS setor_nome
               FROM funcionarios LEFT JOIN setores ON setores.id = funcionarios.setor_id
               WHERE funcionarios.situacao = 'ativo' AND (? = '' OR COALESCE(funcionarios.categoria, 'outro') = ?)
               ORDER BY funcionarios.nome""",
            (categoria, categoria),
        ).fetchall()
        config = conn.execute("SELECT * FROM configuracoes WHERE id = 1").fetchone()
    finally:
        conn.close()

    if not funcionarios:
        flash("Nenhum funcionário ativo para imprimir crachá.", "erro")
        return redirect(url_for("funcionarios"))
    return render_template("cracha.html", funcionarios=funcionarios, config=config)


@app.route("/admin/funcionarios/<int:funcionario_id>/historico")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)
def historico_funcionario(funcionario_id):
    conn = db.get_db()
    try:
        hoje = date.today()
        data_inicio = request.args.get("data_inicio") or hoje.replace(day=1).isoformat()
        data_fim = request.args.get("data_fim") or hoje.isoformat()
        funcionario, linhas = relatorios_service.montar_historico_funcionario(
            conn, funcionario_id, data_inicio, data_fim
        )
        if not funcionario:
            abort(404)
        config = conn.execute("SELECT * FROM configuracoes WHERE id = 1").fetchone()
        return render_template(
            "historico_funcionario.html",
            funcionario=funcionario,
            linhas=linhas,
            data_inicio=data_inicio,
            data_fim=data_fim,
            nomes_tipo_ausencia=NOMES_TIPO_AUSENCIA,
            config=config,
            mes_atual=hoje.strftime("%Y-%m"),
        )
    finally:
        conn.close()


def _colunas_linhas_historico(linhas, incluir_dia_semana=False):
    colunas = [
        "Data", "Entrada", "Saída (almoço)", "Retorno", "Saída", "Horas trabalhadas", "Horas extras", "Justificativa",
    ]
    linhas_tabela = []
    for linha in linhas:
        data_str = linha["data"].strftime("%d/%m/%Y")
        if incluir_dia_semana:
            data_str = f"{data_str} ({NOMES_DIAS_SEMANA_ABREV[linha['data'].weekday()]})"
        linhas_tabela.append(
            [
                data_str,
                linha["entrada"]["timestamp"][11:16] if linha["entrada"] else "-",
                linha["saida_almoco"]["timestamp"][11:16] if linha["saida_almoco"] else "-",
                linha["retorno"]["timestamp"][11:16] if linha["retorno"] else "-",
                linha["saida"]["timestamp"][11:16] if linha["saida"] else "-",
                linha["total_horas"],
                linha["horas_extras"],
                NOMES_TIPO_AUSENCIA.get(linha["ausencia"]["tipo"], "") if linha["ausencia"] else "",
            ]
        )
    return colunas, linhas_tabela


def _dados_exportacao_historico(conn, funcionario_id, req):
    hoje = date.today()
    data_inicio = req.args.get("data_inicio") or hoje.replace(day=1).isoformat()
    data_fim = req.args.get("data_fim") or hoje.isoformat()
    funcionario, linhas = relatorios_service.montar_historico_funcionario(conn, funcionario_id, data_inicio, data_fim)
    if not funcionario:
        abort(404)
    colunas, linhas_tabela = _colunas_linhas_historico(linhas)
    cabecalho = exportadores.obter_dados_cabecalho(conn)
    titulo = f"Histórico - {funcionario['nome']}"
    subtitulo = f"{data_inicio} a {data_fim}"
    return cabecalho, titulo, subtitulo, colunas, linhas_tabela


@app.route("/admin/funcionarios/<int:funcionario_id>/historico/exportar.pdf")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)
def historico_exportar_pdf(funcionario_id):
    conn = db.get_db()
    try:
        dados = _dados_exportacao_historico(conn, funcionario_id, request)
    finally:
        conn.close()
    return _resposta_exportacao("pdf", *dados, f"historico_{funcionario_id}.pdf")


@app.route("/admin/funcionarios/<int:funcionario_id>/historico/exportar.xlsx")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)
def historico_exportar_excel(funcionario_id):
    conn = db.get_db()
    try:
        dados = _dados_exportacao_historico(conn, funcionario_id, request)
    finally:
        conn.close()
    return _resposta_exportacao("xlsx", *dados, f"historico_{funcionario_id}.xlsx")


@app.route("/admin/funcionarios/<int:funcionario_id>/espelho.pdf")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)
def espelho_ponto_pdf(funcionario_id):
    hoje = date.today()
    mes_param = request.args.get("mes") or hoje.strftime("%Y-%m")
    try:
        ano, mes = (int(parte) for parte in mes_param.split("-"))
        date(ano, mes, 1)
    except (ValueError, TypeError):
        ano, mes = hoje.year, hoje.month
        mes_param = hoje.strftime("%Y-%m")

    conn = db.get_db()
    try:
        funcionario, linhas, totais = relatorios_service.montar_espelho_mensal(conn, funcionario_id, ano, mes)
        if not funcionario:
            abort(404)
        colunas, linhas_tabela = _colunas_linhas_historico(linhas, incluir_dia_semana=True)
        cabecalho = exportadores.obter_dados_cabecalho(conn)
        mes_rotulo = f"{NOMES_MESES[mes]}/{ano}"
        codigo_verificacao = gerar_codigo_verificacao_espelho(
            funcionario_id,
            funcionario["matricula"],
            mes_param,
            totais["total_segundos"],
            totais["total_extras_segundos"],
        )
        registrar_auditoria(conn, "gerar_espelho", "funcionario", funcionario_id, f"mes={mes_param}")
    finally:
        conn.close()

    pdf_bytes = exportadores.gerar_pdf_espelho(
        cabecalho, funcionario, mes_rotulo, colunas, linhas_tabela, totais, codigo_verificacao
    )
    return app.response_class(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=espelho_{funcionario_id}_{mes_param}.pdf"},
    )


@app.route("/admin/funcionarios/<int:funcionario_id>/banco-horas")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)
def banco_horas_funcionario(funcionario_id):
    conn = db.get_db()
    try:
        hoje = date.today()
        data_inicio = request.args.get("data_inicio") or hoje.replace(day=1).isoformat()
        data_fim = request.args.get("data_fim") or hoje.isoformat()
        funcionario, saldo_anterior_seg, linhas, saldo_final_seg = relatorios_service.montar_extrato_banco_horas(
            conn, funcionario_id, data_inicio, data_fim
        )
        if not funcionario:
            abort(404)
        config = conn.execute("SELECT * FROM configuracoes WHERE id = 1").fetchone()
        return render_template(
            "banco_horas_funcionario.html",
            funcionario=funcionario,
            banco_ativo=funcionario["banco_horas_inicio"] is not None,
            saldo_anterior=relatorios_service.formatar_horas(saldo_anterior_seg) if saldo_anterior_seg is not None else None,
            linhas=linhas,
            saldo_final=relatorios_service.formatar_horas(saldo_final_seg) if saldo_final_seg is not None else None,
            data_inicio=data_inicio,
            data_fim=data_fim,
            nomes_tipo_ausencia=NOMES_TIPO_AUSENCIA,
            config=config,
        )
    finally:
        conn.close()


@app.route("/admin/funcionarios/<int:funcionario_id>/banco-horas/compensacoes/nova", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def nova_compensacao_banco_horas(funcionario_id):
    data_compensacao = request.form.get("data", "").strip()
    horas_texto = request.form.get("horas", "").strip()
    tipo = request.form.get("tipo", "credito").strip()
    motivo = request.form.get("motivo", "").strip()

    conn = db.get_db()
    try:
        erro = None
        if not data_compensacao or not horas_texto or not motivo:
            erro = "Preencha a data, a quantidade de horas e o motivo."

        segundos = None
        if not erro:
            try:
                partes = horas_texto.split(":")
                horas_valor = int(partes[0])
                minutos_valor = int(partes[1]) if len(partes) > 1 else 0
                segundos = horas_valor * 3600 + minutos_valor * 60
                if segundos <= 0:
                    raise ValueError
            except (ValueError, IndexError):
                erro = "Informe as horas no formato HH:MM (ex: 02:30)."

        if erro:
            flash(erro, "erro")
        else:
            if tipo == "debito":
                segundos = -segundos
            conn.execute(
                """INSERT INTO compensacoes_banco_horas (funcionario_id, data, segundos, motivo, criado_por, criado_em)
                   VALUES (?, ?, ?, ?, ?, datetime('now'))""",
                (funcionario_id, data_compensacao, segundos, motivo, session.get("usuario_login")),
            )
            conn.commit()
            registrar_auditoria(
                conn, "criar", "compensacao_banco_horas", funcionario_id,
                f"data={data_compensacao}; segundos={segundos}; motivo={motivo}",
            )
            flash("Compensação registrada.", "sucesso")
    finally:
        conn.close()
    return redirect(url_for("banco_horas_funcionario", funcionario_id=funcionario_id))


@app.route("/admin/compensacoes-banco-horas/<int:compensacao_id>/excluir", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def excluir_compensacao_banco_horas(compensacao_id):
    conn = db.get_db()
    try:
        compensacao = conn.execute(
            "SELECT * FROM compensacoes_banco_horas WHERE id = ?", (compensacao_id,)
        ).fetchone()
        if not compensacao:
            flash("Compensação não encontrada.", "erro")
            return redirect(url_for("funcionarios"))
        conn.execute("DELETE FROM compensacoes_banco_horas WHERE id = ?", (compensacao_id,))
        conn.commit()
        registrar_auditoria(
            conn, "excluir", "compensacao_banco_horas", compensacao_id,
            f"funcionario_id={compensacao['funcionario_id']}",
        )
        flash("Compensação excluída.", "sucesso")
        return redirect(url_for("banco_horas_funcionario", funcionario_id=compensacao["funcionario_id"]))
    finally:
        conn.close()


@app.route("/admin/ausencias/nova", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def nova_ausencia():
    funcionario_id = request.form.get("funcionario_id", "").strip()
    data_ausencia = request.form.get("data", "").strip()
    tipo = request.form.get("tipo", "").strip()
    observacao = request.form.get("observacao", "").strip()

    if not funcionario_id or not data_ausencia or tipo not in db.TIPOS_AUSENCIA_VALIDOS:
        flash("Preencha funcionário, data e tipo da justificativa.", "erro")
        return redirect(request.referrer or url_for("ajustes_ponto"))

    conn = db.get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO ausencias (funcionario_id, data, tipo, observacao, criado_por, criado_em) VALUES (?, ?, ?, ?, ?, ?)",
            (
                funcionario_id,
                data_ausencia,
                tipo,
                observacao or None,
                session.get("usuario_login"),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
        registrar_auditoria(
            conn, "criar", "ausencia", cursor.lastrowid, f"funcionario_id={funcionario_id}; data={data_ausencia}; tipo={tipo}"
        )
        flash("Justificativa registrada.", "sucesso")
    except sqlite3.IntegrityError as erro_integridade:
        if eh_erro_mes_fechado(erro_integridade):
            raise
        flash("Já existe uma justificativa para esse funcionário nessa data. Edite a existente.", "erro")
    finally:
        conn.close()
    return redirect(request.referrer or url_for("ajustes_ponto"))


@app.route("/admin/ausencias/<int:ausencia_id>/editar", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def editar_ausencia(ausencia_id):
    tipo = request.form.get("tipo", "").strip()
    observacao = request.form.get("observacao", "").strip()
    if tipo not in db.TIPOS_AUSENCIA_VALIDOS:
        flash("Tipo de justificativa inválido.", "erro")
        return redirect(request.referrer or url_for("ajustes_ponto"))
    conn = db.get_db()
    try:
        conn.execute("UPDATE ausencias SET tipo = ?, observacao = ? WHERE id = ?", (tipo, observacao or None, ausencia_id))
        conn.commit()
        registrar_auditoria(conn, "editar", "ausencia", ausencia_id, f"tipo={tipo}")
    finally:
        conn.close()
    flash("Justificativa atualizada.", "sucesso")
    return redirect(request.referrer or url_for("ajustes_ponto"))


@app.route("/admin/ausencias/<int:ausencia_id>/excluir", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def excluir_ausencia(ausencia_id):
    conn = db.get_db()
    try:
        ausencia = conn.execute(
            """SELECT ausencias.*, funcionarios.nome AS funcionario_nome
               FROM ausencias JOIN funcionarios ON funcionarios.id = ausencias.funcionario_id
               WHERE ausencias.id = ?""",
            (ausencia_id,),
        ).fetchone()

        # Checa o mês fechado ANTES de apagar arquivos do disco (o trigger só barraria depois).
        if ausencia and conn.execute(
            "SELECT 1 FROM meses_fechados WHERE mes = ?", (ausencia["data"][:7],)
        ).fetchone():
            raise sqlite3.IntegrityError("mes_fechado")

        # Apaga os documentos anexados antes (FK bloquearia o DELETE de ausencias senão).
        documentos_anexados = conn.execute(
            "SELECT * FROM documentos WHERE ausencia_id = ?", (ausencia_id,)
        ).fetchall()
        for documento in documentos_anexados:
            caminho = nucleo.DOCUMENTOS_DIR / documento["caminho_arquivo"]
            try:
                caminho.unlink(missing_ok=True)
            except OSError:
                pass
        conn.execute("DELETE FROM documentos WHERE ausencia_id = ?", (ausencia_id,))

        conn.execute("DELETE FROM ausencias WHERE id = ?", (ausencia_id,))
        conn.commit()
        if ausencia:
            registrar_auditoria(
                conn, "excluir", "ausencia", ausencia_id,
                f"funcionario={ausencia['funcionario_nome']}; data={ausencia['data']}; tipo={ausencia['tipo']}",
            )
    finally:
        conn.close()
    flash("Justificativa excluída.", "sucesso")
    return redirect(request.referrer or url_for("ajustes_ponto"))
