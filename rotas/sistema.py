"""Configurações, usuários, auditoria e backup."""

import io
import os
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from datetime import date, datetime
from pathlib import Path
from flask import abort, flash, redirect, render_template, request, send_file, session, url_for
from PIL import Image, ImageOps
from werkzeug.security import generate_password_hash
import db

import nucleo
from nucleo import (
    BASE_DIR,
    EXTENSOES_IMAGEM_PERMITIDAS,
    NOMES_PERFIL,
    PERFIS_SOMENTE_ADMIN,
    TABELAS_ESPERADAS_BACKUP,
    _datahora_backup_auto,
    app,
    login_obrigatorio,
    montar_backup_zip_bytes,
    perfil_obrigatorio,
    registrar_auditoria,
)


def contar_administradores_ativos(conn, excluir_id=None):
    query = "SELECT COUNT(*) AS n FROM usuarios WHERE perfil = 'administrador' AND ativo = 1"
    params = []
    if excluir_id is not None:
        query += " AND id != ?"
        params.append(excluir_id)
    return conn.execute(query, params).fetchone()["n"]


def processar_logo_upload(arquivo):
    extensao = arquivo.filename.rsplit(".", 1)[-1].lower() if "." in arquivo.filename else ""
    if extensao not in EXTENSOES_IMAGEM_PERMITIDAS:
        raise ValueError("Formato de logo não suportado. Use JPG, PNG ou WEBP.")
    try:
        imagem = Image.open(arquivo.stream)
        imagem = ImageOps.exif_transpose(imagem)
    except Exception:
        raise ValueError("Não foi possível ler o arquivo como imagem.")
    imagem.thumbnail((600, 600))
    nucleo.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    imagem.save(nucleo.UPLOADS_DIR / "logo.png", "PNG")
    return "uploads/logo.png"


def validar_backup_zip(caminho_zip):
    if not zipfile.is_zipfile(caminho_zip):
        raise ValueError("O arquivo enviado não é um .zip válido.")
    with zipfile.ZipFile(caminho_zip) as zf:
        if "ponto.db" not in zf.namelist():
            raise ValueError("O .zip não contém ponto.db — não parece ser um backup deste sistema.")
        with tempfile.TemporaryDirectory() as pasta_tmp:
            caminho_extraido = zf.extract("ponto.db", path=pasta_tmp)
            conn_teste = sqlite3.connect(caminho_extraido)
            try:
                try:
                    integro = conn_teste.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                except sqlite3.DatabaseError:
                    integro = False
                if not integro:
                    raise ValueError("O banco dentro do backup está corrompido — escolha outro arquivo.")
                tabelas = {
                    linha[0]
                    for linha in conn_teste.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
            finally:
                conn_teste.close()
            faltando = TABELAS_ESPERADAS_BACKUP - tabelas
            if faltando:
                raise ValueError(f"Estrutura inesperada no backup (faltam tabelas: {', '.join(sorted(faltando))}).")


def _caminho_seguro_dentro_de(pasta_base, nome_relativo):
    destino = (pasta_base / nome_relativo).resolve()
    if pasta_base.resolve() not in destino.parents and destino != pasta_base.resolve():
        return None
    return destino


def extrair_arquivos_backup(caminho_zip):
    with zipfile.ZipFile(caminho_zip) as zf:
        for prefixo, pasta_destino in (
            ("static/fotos/", nucleo.FOTOS_DIR),
            ("static/qrcodes/", nucleo.QRCODES_DIR),
            ("static/uploads/", nucleo.UPLOADS_DIR),
            ("uploads_privados/documentos/", nucleo.DOCUMENTOS_DIR),
        ):
            pasta_destino.mkdir(parents=True, exist_ok=True)
            for nome in zf.namelist():
                if not nome.startswith(prefixo) or nome.endswith("/"):
                    continue
                nome_relativo = nome[len(prefixo):]
                destino = _caminho_seguro_dentro_de(pasta_destino, nome_relativo)
                if destino is None:
                    continue
                destino.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(nome) as origem, open(destino, "wb") as saida:
                    saida.write(origem.read())


@app.route("/admin/configuracoes", methods=["GET", "POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_SOMENTE_ADMIN)
def configuracoes():
    conn = db.get_db()
    try:
        if request.method == "POST":
            nome_instituicao = request.form.get("nome_instituicao", "").strip()
            rodape_cracha = request.form.get("rodape_cracha", "").strip()
            cor_primaria = request.form.get("cor_primaria", "#1E40AF").strip()
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", cor_primaria):
                cor_primaria = "#1E40AF"

            try:
                backup_frequencia_horas = int(request.form.get("backup_frequencia_horas", "24"))
            except ValueError:
                backup_frequencia_horas = 24
            if backup_frequencia_horas not in (6, 12, 24, 48, 168):
                backup_frequencia_horas = 24

            backup_pasta_destino_input = request.form.get("backup_pasta_destino", "").strip()
            if backup_pasta_destino_input and not Path(backup_pasta_destino_input).is_dir():
                flash(
                    f'A pasta "{backup_pasta_destino_input}" não foi encontrada — confira o caminho. '
                    "O destino do backup automático não foi alterado.", "erro",
                )
                linha_atual = conn.execute(
                    "SELECT backup_pasta_destino FROM configuracoes WHERE id = 1"
                ).fetchone()
                backup_pasta_destino = linha_atual["backup_pasta_destino"] if linha_atual else None
            else:
                backup_pasta_destino = backup_pasta_destino_input or None

            conn.execute(
                """UPDATE configuracoes
                   SET nome_instituicao = ?, rodape_cracha = ?, cor_primaria = ?,
                       backup_frequencia_horas = ?, backup_pasta_destino = ?
                   WHERE id = 1""",
                (nome_instituicao, rodape_cracha, cor_primaria,
                 backup_frequencia_horas, backup_pasta_destino),
            )
            conn.commit()

            arquivo_logo = request.files.get("logo")
            logo_trocado = bool(arquivo_logo and arquivo_logo.filename)
            if arquivo_logo and arquivo_logo.filename:
                try:
                    caminho_logo = processar_logo_upload(arquivo_logo)
                    conn.execute("UPDATE configuracoes SET logo_path = ? WHERE id = 1", (caminho_logo,))
                    conn.commit()
                except ValueError as erro_logo:
                    flash(str(erro_logo), "erro")

            registrar_auditoria(
                conn, "editar", "configuracoes", 1,
                f"nome_instituicao={nome_instituicao}; cor_primaria={cor_primaria}; logo_trocado={'sim' if logo_trocado else 'nao'}; "
                f"backup_frequencia_horas={backup_frequencia_horas}; backup_pasta_destino={backup_pasta_destino or '(nenhuma)'}",
            )
            flash("Configurações salvas.", "sucesso")
            return redirect(url_for("configuracoes"))

        config = conn.execute("SELECT * FROM configuracoes WHERE id = 1").fetchone()
        return render_template("configuracoes.html", config=config)
    finally:
        conn.close()


@app.route("/admin/auditoria")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_SOMENTE_ADMIN)
def auditoria():
    conn = db.get_db()
    try:
        pagina = max(1, int(request.args.get("pagina", "1") or "1"))
        por_pagina = 50
        usuario_filtro = request.args.get("usuario", "").strip()
        acao_filtro = request.args.get("acao", "").strip()
        entidade_filtro = request.args.get("entidade", "").strip()
        hoje = date.today()
        data_inicio = request.args.get("data_inicio") or hoje.replace(day=1).isoformat()
        data_fim = request.args.get("data_fim") or hoje.isoformat()

        condicoes = ["timestamp >= date(?) AND timestamp < date(?, '+1 day')"]
        params = [data_inicio, data_fim]
        if usuario_filtro:
            condicoes.append("usuario = ?")
            params.append(usuario_filtro)
        if acao_filtro:
            condicoes.append("acao = ?")
            params.append(acao_filtro)
        if entidade_filtro:
            condicoes.append("entidade = ?")
            params.append(entidade_filtro)
        where_sql = " AND ".join(condicoes)

        total = conn.execute(f"SELECT COUNT(*) AS n FROM auditoria WHERE {where_sql}", params).fetchone()["n"]
        total_paginas = max(1, (total + por_pagina - 1) // por_pagina)
        pagina = min(pagina, total_paginas)
        offset = (pagina - 1) * por_pagina

        registros = conn.execute(
            f"SELECT * FROM auditoria WHERE {where_sql} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            [*params, por_pagina, offset],
        ).fetchall()

        usuarios_distintos = [r["usuario"] for r in conn.execute("SELECT DISTINCT usuario FROM auditoria ORDER BY usuario")]
        acoes_distintas = [r["acao"] for r in conn.execute("SELECT DISTINCT acao FROM auditoria ORDER BY acao")]
        entidades_distintas = [r["entidade"] for r in conn.execute("SELECT DISTINCT entidade FROM auditoria ORDER BY entidade")]

        return render_template(
            "auditoria.html",
            registros=registros,
            pagina=pagina,
            total_paginas=total_paginas,
            total=total,
            usuario_filtro=usuario_filtro,
            acao_filtro=acao_filtro,
            entidade_filtro=entidade_filtro,
            data_inicio=data_inicio,
            data_fim=data_fim,
            usuarios_distintos=usuarios_distintos,
            acoes_distintas=acoes_distintas,
            entidades_distintas=entidades_distintas,
        )
    finally:
        conn.close()


@app.route("/admin/usuarios")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_SOMENTE_ADMIN)
def usuarios():
    conn = db.get_db()
    try:
        lista = conn.execute("SELECT * FROM usuarios ORDER BY ativo DESC, nome").fetchall()
        return render_template("usuarios.html", usuarios=lista, nomes_perfil=NOMES_PERFIL)
    finally:
        conn.close()


@app.route("/admin/usuarios/novo", methods=["GET", "POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_SOMENTE_ADMIN)
def novo_usuario():
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        usuario = request.form.get("usuario", "").strip()
        perfil = request.form.get("perfil", "").strip()
        senha = request.form.get("senha", "")

        erro = None
        if not nome or not usuario:
            erro = "Informe nome e usuário."
        elif perfil not in db.PERFIS_VALIDOS:
            erro = "Perfil inválido."
        elif len(senha) < 6:
            erro = "A senha precisa ter pelo menos 6 caracteres."

        if erro:
            flash(erro, "erro")
            return redirect(url_for("novo_usuario"))

        conn = db.get_db()
        try:
            cursor = conn.execute(
                "INSERT INTO usuarios (nome, usuario, senha_hash, perfil, ativo) VALUES (?, ?, ?, ?, 1)",
                (nome, usuario, generate_password_hash(senha), perfil),
            )
            conn.commit()
            registrar_auditoria(conn, "criar", "usuario", cursor.lastrowid, f"usuario={usuario}; perfil={perfil}")
            flash("Usuário criado.", "sucesso")
            return redirect(url_for("usuarios"))
        except sqlite3.IntegrityError:
            flash("Já existe um usuário com esse login.", "erro")
            return redirect(url_for("novo_usuario"))
        finally:
            conn.close()

    return render_template("usuario_form.html", usuario=None, perfis_validos=db.PERFIS_VALIDOS, nomes_perfil=NOMES_PERFIL)


@app.route("/admin/usuarios/<int:usuario_id>/editar", methods=["GET", "POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_SOMENTE_ADMIN)
def editar_usuario(usuario_id):
    conn = db.get_db()
    try:
        alvo = conn.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
        if not alvo:
            abort(404)

        if request.method == "POST":
            nome = request.form.get("nome", "").strip()
            perfil = request.form.get("perfil", "").strip()

            if not nome:
                flash("Informe o nome.", "erro")
                return redirect(url_for("editar_usuario", usuario_id=usuario_id))
            if perfil not in db.PERFIS_VALIDOS:
                flash("Perfil inválido.", "erro")
                return redirect(url_for("editar_usuario", usuario_id=usuario_id))

            if alvo["perfil"] == "administrador" and perfil != "administrador":
                if contar_administradores_ativos(conn, excluir_id=usuario_id) == 0:
                    flash("Não é possível rebaixar: esse é o último administrador ativo.", "erro")
                    return redirect(url_for("editar_usuario", usuario_id=usuario_id))

            conn.execute("UPDATE usuarios SET nome = ?, perfil = ? WHERE id = ?", (nome, perfil, usuario_id))
            conn.commit()
            registrar_auditoria(conn, "editar", "usuario", usuario_id, f"nome={nome}; perfil={perfil}")
            flash("Usuário atualizado.", "sucesso")
            return redirect(url_for("usuarios"))

        return render_template(
            "usuario_form.html", usuario=alvo, perfis_validos=db.PERFIS_VALIDOS, nomes_perfil=NOMES_PERFIL
        )
    finally:
        conn.close()


@app.route("/admin/usuarios/<int:usuario_id>/resetar-senha", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_SOMENTE_ADMIN)
def resetar_senha_usuario(usuario_id):
    nova_senha = request.form.get("nova_senha", "")
    if len(nova_senha) < 6:
        flash("A nova senha precisa ter pelo menos 6 caracteres.", "erro")
        return redirect(url_for("editar_usuario", usuario_id=usuario_id))
    conn = db.get_db()
    try:
        conn.execute(
            """UPDATE usuarios
               SET senha_hash = ?, tentativas_falhas = 0, bloqueado_ate = NULL
               WHERE id = ?""",
            (generate_password_hash(nova_senha), usuario_id),
        )
        conn.commit()
        registrar_auditoria(conn, "resetar_senha", "usuario", usuario_id)
    finally:
        conn.close()
    flash("Senha redefinida.", "sucesso")
    return redirect(url_for("usuarios"))


@app.route("/admin/usuarios/<int:usuario_id>/situacao", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_SOMENTE_ADMIN)
def mudar_situacao_usuario(usuario_id):
    ativo = request.form.get("ativo") == "1"
    conn = db.get_db()
    try:
        alvo = conn.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
        if not alvo:
            abort(404)
        if not ativo and alvo["perfil"] == "administrador":
            if contar_administradores_ativos(conn, excluir_id=usuario_id) == 0:
                flash("Não é possível inativar: esse é o último administrador ativo.", "erro")
                return redirect(url_for("usuarios"))
        conn.execute("UPDATE usuarios SET ativo = ? WHERE id = ?", (1 if ativo else 0, usuario_id))
        conn.commit()
        registrar_auditoria(conn, "mudar_situacao", "usuario", usuario_id, f"ativo={ativo}")
    finally:
        conn.close()
    flash("Situação atualizada.", "sucesso")
    return redirect(url_for("usuarios"))


@app.route("/admin/backup")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_SOMENTE_ADMIN)
def backup():
    nucleo.BACKUPS_AUTO_DIR.mkdir(parents=True, exist_ok=True)
    arquivos = sorted(
        nucleo.BACKUPS_AUTO_DIR.glob("auto-*.zip"),
        key=lambda a: _datahora_backup_auto(a) or datetime.min,
        reverse=True,
    )
    backups_auto = [
        {
            "nome": arquivo.name,
            "tamanho_kb": round(arquivo.stat().st_size / 1024),
            "quando": (_datahora_backup_auto(arquivo) or datetime.min).strftime("%d/%m/%Y %H:%M"),
        }
        for arquivo in arquivos
    ]
    conn = db.get_db()
    try:
        config = conn.execute(
            "SELECT backup_frequencia_horas, backup_pasta_destino FROM configuracoes WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    return render_template("backup.html", backups_auto=backups_auto, config=config)


@app.route("/admin/backup/gerar")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_SOMENTE_ADMIN)
def backup_gerar():
    conn = db.get_db()
    try:
        registrar_auditoria(conn, "gerar_backup", "sistema")
    finally:
        conn.close()
    zip_bytes = montar_backup_zip_bytes()
    nome_arquivo = f"backup-ponto-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    return send_file(
        io.BytesIO(zip_bytes), mimetype="application/zip", as_attachment=True, download_name=nome_arquivo
    )


@app.route("/admin/backup/auto/<nome_arquivo>")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_SOMENTE_ADMIN)
def backup_auto_download(nome_arquivo):
    if not re.fullmatch(r"auto-(\d{4}-\d{2}-\d{2}|\d{8}-\d{6})\.zip", nome_arquivo):
        abort(404)
    caminho = nucleo.BACKUPS_AUTO_DIR / nome_arquivo
    if not caminho.exists():
        abort(404)
    return send_file(caminho, mimetype="application/zip", as_attachment=True, download_name=nome_arquivo)


@app.route("/admin/backup/restaurar", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_SOMENTE_ADMIN)
def backup_restaurar():
    confirmacao = request.form.get("confirmacao", "").strip()
    arquivo = request.files.get("arquivo")

    if confirmacao != "RESTAURAR":
        flash('Digite exatamente "RESTAURAR" para confirmar.', "erro")
        return redirect(url_for("backup"))

    if not arquivo or not arquivo.filename:
        flash("Selecione um arquivo de backup (.zip).", "erro")
        return redirect(url_for("backup"))

    with tempfile.TemporaryDirectory() as pasta_tmp:
        caminho_zip = Path(pasta_tmp) / "enviado.zip"
        arquivo.save(caminho_zip)

        try:
            validar_backup_zip(caminho_zip)
        except ValueError as erro_validacao:
            flash(str(erro_validacao), "erro")
            return redirect(url_for("backup"))

        carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
        caminho_seguranca = BASE_DIR / f"ponto.db.bak-antes-restaurar-{carimbo}"
        origem = sqlite3.connect(db.DB_PATH)
        destino_seguranca = sqlite3.connect(caminho_seguranca)
        try:
            with destino_seguranca:
                origem.backup(destino_seguranca)
        finally:
            origem.close()
            destino_seguranca.close()

        with zipfile.ZipFile(caminho_zip) as zf:
            caminho_novo_db = zf.extract("ponto.db", path=pasta_tmp)

        caminho_tmp_troca = BASE_DIR / "ponto.db.tmp-restaurar"
        shutil.copyfile(caminho_novo_db, caminho_tmp_troca)

        sucesso_troca = False
        for _tentativa in range(5):
            try:
                os.replace(caminho_tmp_troca, db.DB_PATH)
                sucesso_troca = True
                break
            except PermissionError:
                time.sleep(0.5)

        if not sucesso_troca:
            caminho_tmp_troca.unlink(missing_ok=True)
            flash("Não foi possível substituir o banco (arquivo em uso). Tente novamente em alguns segundos.", "erro")
            return redirect(url_for("backup"))

        extrair_arquivos_backup(caminho_zip)
        db.init_db()

        conn = db.get_db()
        try:
            registrar_auditoria(
                conn, "restaurar_backup", "sistema",
                detalhes=f"arquivo={arquivo.filename}; backup_seguranca={caminho_seguranca.name}",
            )
        finally:
            conn.close()

    flash("Backup restaurado com sucesso. Faça login novamente.", "sucesso")
    session.clear()
    return redirect(url_for("login"))
