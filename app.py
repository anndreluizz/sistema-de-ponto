import base64
import csv
import hashlib
import hmac
import io
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import time
import zipfile
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path

import qrcode
from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from flask_wtf import CSRFProtect
from PIL import Image, ImageOps
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import db
import exportadores
import relatorios_service

BASE_DIR = Path(__file__).resolve().parent
SECRET_KEY_PATH = BASE_DIR / ".secret_key"
# Fotos e QR Codes ficam fora de static/ (que é servida sem login): o QR contém o código
# do crachá, e com ele qualquer um na rede bateria ponto no nome de outra pessoa.
QRCODES_DIR = BASE_DIR / "uploads_privados" / "qrcodes"
FOTOS_DIR = BASE_DIR / "uploads_privados" / "fotos"
UPLOADS_DIR = BASE_DIR / "static" / "uploads"
DOCUMENTOS_DIR = BASE_DIR / "uploads_privados" / "documentos"
BACKUPS_AUTO_DIR = BASE_DIR / "backups" / "auto"
BACKUPS_AUTO_MANTER = 14

COOLDOWN_SEGUNDOS = 8
TOP_N_RANKING_ATRASOS = 5
QUANTIDADE_MESES_COMPARATIVO = 6
EXTENSOES_IMAGEM_PERMITIDAS = {"png", "jpg", "jpeg", "webp"}
EXTENSOES_DOCUMENTO_PERMITIDAS = {"pdf", "png", "jpg", "jpeg", "webp"}
CORES_AVATAR = ["#1E40AF", "#0EA5E9", "#16A34A", "#CA8A04", "#DC2626", "#7C3AED", "#0D9488", "#DB2777"]

PERFIS_GESTAO = ("administrador", "rh")
PERFIS_LEITURA = ("administrador", "rh", "direcao")
PERFIS_SOMENTE_ADMIN = ("administrador",)
NOMES_PERFIL = {
    "administrador": "Administrador",
    "rh": "RH",
    "direcao": "Direção",
    "consulta": "Consulta",
}
TABELAS_ESPERADAS_BACKUP = {"funcionarios", "registros", "usuarios", "configuracoes", "setores", "turnos"}

ICONES_SETOR_DISPONIVEIS = [
    ("bi-mortarboard", "Pedagógico"),
    ("bi-book", "Biblioteca"),
    ("bi-building", "Administração"),
    ("bi-briefcase", "Diretoria"),
    ("bi-cash-stack", "Financeiro"),
    ("bi-house-door", "Portaria"),
    ("bi-heart-pulse", "Saúde/Enfermagem"),
    ("bi-tools", "Manutenção"),
    ("bi-cup-hot", "Cozinha/Cantina"),
    ("bi-broom", "Limpeza"),
    ("bi-shield-check", "Segurança"),
    ("bi-people", "Recursos Humanos"),
]

NAVEGACAO = {
    "dashboard": {"titulo": "Dashboard", "trilha": []},
    "funcionarios": {"titulo": "Funcionários", "trilha": []},
    "novo_funcionario": {"titulo": "Novo funcionário", "trilha": [("Funcionários", "funcionarios")]},
    "editar_funcionario": {"titulo": "Editar funcionário", "trilha": [("Funcionários", "funcionarios")]},
    "historico_funcionario": {"titulo": "Histórico de ponto", "trilha": [("Funcionários", "funcionarios")]},
    "banco_horas_funcionario": {"titulo": "Banco de horas", "trilha": [("Funcionários", "funcionarios")]},
    "historico_alteracoes_funcionario": {"titulo": "Histórico de alterações", "trilha": [("Funcionários", "funcionarios")]},
    "relatorios": {"titulo": "Central de Relatórios", "trilha": []},
    "ajustes_ponto": {"titulo": "Ajustes de Ponto", "trilha": []},
    "setores": {"titulo": "Setores", "trilha": [("Funcionários", "funcionarios")]},
    "turnos": {"titulo": "Turnos", "trilha": [("Funcionários", "funcionarios")]},
    "escalas": {"titulo": "Escalas", "trilha": [("Funcionários", "funcionarios")]},
    "feriados": {"titulo": "Feriados", "trilha": [("Funcionários", "funcionarios")]},
    "configuracoes": {"titulo": "Configurações", "trilha": []},
    "trocar_senha": {"titulo": "Trocar senha", "trilha": []},
    "usuarios": {"titulo": "Usuários", "trilha": []},
    "novo_usuario": {"titulo": "Novo usuário", "trilha": [("Usuários", "usuarios")]},
    "editar_usuario": {"titulo": "Editar usuário", "trilha": [("Usuários", "usuarios")]},
    "auditoria": {"titulo": "Auditoria", "trilha": []},
    "backup": {"titulo": "Backup", "trilha": []},
}

NOMES_VISAO = {
    "frequencia": "Frequência",
    "faltas": "Faltas",
    "atrasos": "Atrasos",
    "horas_extras": "Horas extras",
    "banco_horas": "Banco de horas",
    "ausencias": "Ausências",
}
NOMES_TIPO_AUSENCIA = {
    "atestado": "Atestado",
    "licenca": "Licença",
    "ferias": "Férias",
    "folga": "Folga",
    "licenca_maternidade_paternidade": "Licença maternidade/paternidade",
    "falecimento": "Falecimento na família (nojo)",
    "abono_legal": "Doação de sangue / serviço eleitoral",
    "outro": "Outro",
}
NOMES_VINCULO = {"efetivo": "Efetivo", "contratado": "Contratado"}
NOMES_DIAS_SEMANA_ABREV = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
NOMES_MESES = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril", 5: "Maio", 6: "Junho",
    7: "Julho", 8: "Agosto", 9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}


def carregar_ou_criar_secret_key():
    if SECRET_KEY_PATH.exists():
        return SECRET_KEY_PATH.read_text().strip()
    chave = secrets.token_hex(32)
    SECRET_KEY_PATH.write_text(chave)
    return chave


app = Flask(__name__)
app.config["SECRET_KEY"] = carregar_ou_criar_secret_key()
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=60)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

csrf = CSRFProtect(app)

db.init_db()


def _mover_arquivos_de_static():
    """Migração única: fotos/QR gerados antes da mudança para uploads_privados/."""
    for antiga, nova in ((BASE_DIR / "static" / "fotos", FOTOS_DIR), (BASE_DIR / "static" / "qrcodes", QRCODES_DIR)):
        if not antiga.is_dir():
            continue
        nova.mkdir(parents=True, exist_ok=True)
        for arquivo in antiga.iterdir():
            if arquivo.is_file() and not (nova / arquivo.name).exists():
                shutil.move(str(arquivo), nova / arquivo.name)
        if not any(antiga.iterdir()):
            antiga.rmdir()


_mover_arquivos_de_static()


@app.context_processor
def injetar_navegacao():
    info = NAVEGACAO.get(request.endpoint, {"titulo": "Administração", "trilha": []})
    return {"titulo_pagina": info["titulo"], "trilha_breadcrumb": info["trilha"]}


@app.context_processor
def injetar_cor_primaria():
    if not session.get("usuario_id"):
        return {"cor_primaria": None, "cor_primaria_rgb": None}
    conn = db.get_db()
    try:
        config = conn.execute("SELECT cor_primaria FROM configuracoes WHERE id = 1").fetchone()
    finally:
        conn.close()
    cor = config["cor_primaria"] if config else None
    return {"cor_primaria": cor, "cor_primaria_rgb": hex_para_rgb(cor) if cor else None}


LIMIAR_DIAS_UTEIS_SEM_PONTO = 3
JANELA_VARREDURA_SEM_PONTO_DIAS = 60


def _dias_uteis_sem_ponto(f, ultimo_registro_data, ausencias_datas, hoje):
    """Conta dias úteis (agenda própria do funcionário) desde o último registro/admissão até
    ontem, ignorando dias com ausência justificada — parado assim que atinge o limiar."""
    piso = ultimo_registro_data or date.min
    if f["data_admissao"]:
        try:
            piso = max(piso, datetime.strptime(f["data_admissao"], "%Y-%m-%d").date())
        except ValueError:
            pass
    contagem = 0
    dia = hoje - timedelta(days=1)
    varridos = 0
    while contagem < LIMIAR_DIAS_UTEIS_SEM_PONTO and varridos < JANELA_VARREDURA_SEM_PONTO_DIAS and dia > piso:
        if relatorios_service.dia_e_util_para_funcionario(f, dia) and dia.isoformat() not in ausencias_datas:
            contagem += 1
        dia -= timedelta(days=1)
        varridos += 1
    return contagem


@app.context_processor
def injetar_notificacoes():
    """Avisos calculados na hora a cada carregamento de página — sem tabela de 'lida/não lida',
    é um retrato do que está pegando agora. Só administrador/RH veem (only PERFIS_GESTAO tem o que
    fazer com esses avisos); cada aviso só aparece pra quem pode agir nele."""
    perfil = session.get("usuario_perfil")
    if perfil not in PERFIS_GESTAO:
        return {"notificacoes": []}

    conn = db.get_db()
    try:
        avisos = []

        if perfil == "administrador":
            limite_backup = (datetime.now() - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
            falhas_backup = conn.execute(
                "SELECT COUNT(*) AS n FROM auditoria WHERE acao = 'backup_automatico_falhou' AND timestamp >= ?",
                (limite_backup,),
            ).fetchone()["n"]
            if falhas_backup:
                avisos.append({
                    "icone": "bi-exclamation-octagon-fill",
                    "texto": "O backup automático falhou recentemente — confira em Backup.",
                    "link": url_for("backup"),
                })

            limite_login = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
            logins_bloqueados = conn.execute(
                "SELECT COUNT(*) AS n FROM auditoria WHERE acao = 'login_bloqueado' AND timestamp >= ?",
                (limite_login,),
            ).fetchone()["n"]
            if logins_bloqueados:
                avisos.append({
                    "icone": "bi-shield-exclamation",
                    "texto": f"{logins_bloqueados} login(s) bloqueado(s) por tentativas erradas nas últimas 24h.",
                    "link": url_for("auditoria"),
                })

        hoje = date.today()
        ativos = conn.execute(
            f"""SELECT funcionarios.*, {relatorios_service.SQL_COLUNAS_TURNO_EFETIVO}
                FROM funcionarios
                {relatorios_service.SQL_JOIN_TURNO_EFETIVO}
                WHERE funcionarios.situacao = 'ativo'"""
        ).fetchall()
        ultimos = {
            r["funcionario_id"]: datetime.strptime(r["ultimo"], "%Y-%m-%d %H:%M:%S").date()
            for r in conn.execute(
                "SELECT funcionario_id, MAX(timestamp) AS ultimo FROM registros GROUP BY funcionario_id"
            ).fetchall()
        }
        data_minima = (hoje - timedelta(days=JANELA_VARREDURA_SEM_PONTO_DIAS)).isoformat()
        ausencias_por_funcionario = {}
        for r in conn.execute(
            "SELECT funcionario_id, data FROM ausencias WHERE data >= ?", (data_minima,)
        ).fetchall():
            ausencias_por_funcionario.setdefault(r["funcionario_id"], set()).add(r["data"])

        feriados = relatorios_service.obter_feriados_no_periodo(conn, data_minima, hoje.isoformat())

        sem_ponto = []
        for f in ativos:
            if f["horario_entrada"] is None:
                continue
            contagem = _dias_uteis_sem_ponto(
                f, ultimos.get(f["id"]), ausencias_por_funcionario.get(f["id"], set()) | feriados, hoje
            )
            if contagem >= LIMIAR_DIAS_UTEIS_SEM_PONTO:
                sem_ponto.append(f["nome"])

        if sem_ponto:
            nomes = ", ".join(sem_ponto[:5])
            if len(sem_ponto) > 5:
                nomes += f" e mais {len(sem_ponto) - 5}"
            avisos.append({
                "icone": "bi-person-x-fill",
                "texto": f"Sem bater ponto há {LIMIAR_DIAS_UTEIS_SEM_PONTO}+ dias úteis: {nomes}.",
                "link": url_for("funcionarios"),
            })

        return {"notificacoes": avisos}
    finally:
        conn.close()


@app.template_filter("iniciais")
def filtro_iniciais(nome):
    partes = (nome or "").strip().split()
    if not partes:
        return "?"
    if len(partes) == 1:
        return partes[0][:2].upper()
    return (partes[0][0] + partes[-1][0]).upper()


@app.template_filter("cor_avatar")
def filtro_cor_avatar(nome):
    indice = sum(ord(c) for c in (nome or "")) % len(CORES_AVATAR)
    return CORES_AVATAR[indice]


@app.errorhandler(413)
def erro_arquivo_grande(erro):
    flash("Arquivo muito grande. O limite é 100 MB.", "erro")
    return redirect(request.referrer or url_for("dashboard"))


def login_obrigatorio(f):
    @wraps(f)
    def decorado(*args, **kwargs):
        if not session.get("usuario_id"):
            return redirect(url_for("login", proximo=request.path))
        # Reconsulta o banco a cada página: inativar ou rebaixar alguém vale na hora,
        # não só no próximo login.
        conn = db.get_db()
        try:
            usuario = conn.execute(
                "SELECT perfil FROM usuarios WHERE id = ? AND ativo = 1", (session["usuario_id"],)
            ).fetchone()
        finally:
            conn.close()
        if not usuario:
            session.clear()
            return redirect(url_for("login"))
        session["usuario_perfil"] = usuario["perfil"]
        if session.get("senha_padrao") and request.endpoint not in ("trocar_senha", "logout"):
            flash("Por segurança, troque a senha padrão antes de continuar.", "erro")
            return redirect(url_for("trocar_senha"))
        verificar_backup_automatico()
        return f(*args, **kwargs)

    return decorado


def perfil_obrigatorio(*perfis_permitidos):
    def decorador(f):
        @wraps(f)
        def decorado(*args, **kwargs):
            if session.get("usuario_perfil") not in perfis_permitidos:
                flash("Você não tem permissão para acessar essa página.", "erro")
                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)

        return decorado

    return decorador


def interpretar_user_agent(texto):
    if not texto:
        return None, None

    if re.search(r"Edg(e|A|iOS)?/", texto):
        navegador = "Edge"
    elif "OPR/" in texto or "Opera" in texto:
        navegador = "Opera"
    elif "Chrome/" in texto:
        navegador = "Chrome"
    elif "Firefox/" in texto:
        navegador = "Firefox"
    elif "Safari/" in texto:
        navegador = "Safari"
    elif "MSIE" in texto or "Trident/" in texto:
        navegador = "Internet Explorer"
    else:
        navegador = "Outro"

    if "Windows NT 10.0" in texto:
        sistema = "Windows 10/11"
    elif "Windows NT" in texto:
        sistema = "Windows"
    elif "Android" in texto:
        sistema = "Android"
    elif re.search(r"iPhone|iPad|iPod", texto):
        sistema = "iOS"
    elif "Mac OS X" in texto:
        sistema = "macOS"
    elif "Linux" in texto:
        sistema = "Linux"
    else:
        sistema = "Outro"

    return navegador, sistema


def registrar_auditoria(conn, acao, entidade, entidade_id=None, detalhes=None):
    navegador, sistema_operacional = interpretar_user_agent(request.user_agent.string)
    conn.execute(
        """INSERT INTO auditoria (usuario, perfil, acao, entidade, entidade_id, detalhes, ip, timestamp,
                                   navegador, sistema_operacional)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session.get("usuario_login", "?"),
            session.get("usuario_perfil", "?"),
            acao,
            entidade,
            entidade_id,
            detalhes,
            request.remote_addr,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            navegador,
            sistema_operacional,
        ),
    )
    conn.commit()


def contar_administradores_ativos(conn, excluir_id=None):
    query = "SELECT COUNT(*) AS n FROM usuarios WHERE perfil = 'administrador' AND ativo = 1"
    params = []
    if excluir_id is not None:
        query += " AND id != ?"
        params.append(excluir_id)
    return conn.execute(query, params).fetchone()["n"]


def hex_para_rgb(cor_hex):
    cor_hex = (cor_hex or "").lstrip("#")
    if len(cor_hex) != 6:
        return "30, 64, 175"
    try:
        return f"{int(cor_hex[0:2], 16)}, {int(cor_hex[2:4], 16)}, {int(cor_hex[4:6], 16)}"
    except ValueError:
        return "30, 64, 175"


def gerar_codigo_verificacao_espelho(funcionario_id, matricula, mes, total_segundos, extras_segundos):
    mensagem = f"{funcionario_id}|{matricula}|{mes}|{total_segundos}|{extras_segundos}"
    assinatura = hmac.new(app.config["SECRET_KEY"].encode(), mensagem.encode(), hashlib.sha256).hexdigest()
    codigo = assinatura[:8].upper()
    return f"{codigo[:4]}-{codigo[4:]}"


def normalizar_timestamp(valor):
    valor = valor.strip().replace("T", " ")
    if len(valor) == 16:
        valor += ":00"
    return valor


def gerar_qrcode(codigo, funcionario_id):
    QRCODES_DIR.mkdir(parents=True, exist_ok=True)
    caminho = QRCODES_DIR / f"{funcionario_id}.png"
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
    FOTOS_DIR.mkdir(parents=True, exist_ok=True)
    imagem.save(FOTOS_DIR / f"{funcionario_id}.jpg", "JPEG", quality=85)
    return f"fotos/{funcionario_id}.jpg"


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
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    imagem.save(UPLOADS_DIR / "logo.png", "PNG")
    return "uploads/logo.png"


def processar_documento_upload(arquivo, funcionario_id):
    if not arquivo or not arquivo.filename:
        raise ValueError("Nenhum arquivo selecionado.")
    extensao = arquivo.filename.rsplit(".", 1)[-1].lower() if "." in arquivo.filename else ""
    if extensao not in EXTENSOES_DOCUMENTO_PERMITIDAS:
        raise ValueError("Formato não suportado. Use PDF, JPG, PNG ou WEBP.")
    nome_original = arquivo.filename
    nome_seguro = secure_filename(nome_original) or f"documento.{extensao}"
    nome_no_disco = f"{funcionario_id}_{secrets.token_hex(8)}_{nome_seguro}"
    DOCUMENTOS_DIR.mkdir(parents=True, exist_ok=True)
    caminho_completo = DOCUMENTOS_DIR / nome_no_disco
    arquivo.save(caminho_completo)
    return nome_original, nome_no_disco, caminho_completo.stat().st_size


def proxima_matricula_sugerida(conn):
    linha = conn.execute(
        """SELECT MAX(CAST(matricula AS INTEGER)) AS maior FROM funcionarios
           WHERE matricula GLOB '[0-9]*' AND matricula NOT GLOB '*[^0-9]*'"""
    ).fetchone()
    return f"{(linha['maior'] or 0) + 1:04d}"


def montar_backup_zip_bytes():
    """Gera um .zip com uma cópia consistente do banco + fotos/qrcodes/logo."""
    with tempfile.TemporaryDirectory() as pasta_tmp:
        caminho_tmp_db = Path(pasta_tmp) / "ponto.db"
        origem = sqlite3.connect(db.DB_PATH)
        destino = sqlite3.connect(caminho_tmp_db)
        try:
            with destino:
                origem.backup(destino)
        finally:
            origem.close()
            destino.close()

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(caminho_tmp_db, arcname="ponto.db")
            for pasta, prefixo in (
                (FOTOS_DIR, "static/fotos"),
                (QRCODES_DIR, "static/qrcodes"),
                (UPLOADS_DIR, "static/uploads"),
                (DOCUMENTOS_DIR, "uploads_privados/documentos"),
            ):
                if pasta.exists():
                    for arquivo in pasta.rglob("*"):
                        if arquivo.is_file():
                            zf.write(arquivo, arcname=f"{prefixo}/{arquivo.relative_to(pasta)}")
        return buffer.getvalue()


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
            ("static/fotos/", FOTOS_DIR),
            ("static/qrcodes/", QRCODES_DIR),
            ("static/uploads/", UPLOADS_DIR),
            ("uploads_privados/documentos/", DOCUMENTOS_DIR),
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


def _datahora_backup_auto(caminho):
    """Extrai a data/hora do nome do arquivo — aceita o formato novo (auto-AAAAMMDD-HHMMSS.zip)
    e o formato antigo (auto-AAAA-MM-DD.zip, de antes da frequência configurável)."""
    nome = caminho.stem[len("auto-"):]
    for formato in ("%Y%m%d-%H%M%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(nome, formato)
        except ValueError:
            continue
    return None


def verificar_backup_automatico():
    """Roda a cada carregamento de página autenticada (não há agendador em segundo plano —
    se ninguém acessar o sistema, o backup só roda no próximo acesso após o intervalo vencer)."""
    conn = db.get_db()
    try:
        config = conn.execute(
            "SELECT backup_frequencia_horas, backup_pasta_destino FROM configuracoes WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    frequencia_horas = config["backup_frequencia_horas"] if config else 24
    pasta_destino = config["backup_pasta_destino"] if config else None

    BACKUPS_AUTO_DIR.mkdir(parents=True, exist_ok=True)
    marcas = [d for d in (_datahora_backup_auto(a) for a in BACKUPS_AUTO_DIR.glob("auto-*.zip")) if d]
    if marcas and datetime.now() - max(marcas) < timedelta(hours=frequencia_horas):
        return

    nome_arquivo = f"auto-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    caminho = BACKUPS_AUTO_DIR / nome_arquivo

    conn_auditoria = db.get_db()
    try:
        try:
            dados = montar_backup_zip_bytes()
            caminho.write_bytes(dados)
        except Exception as erro:
            caminho.unlink(missing_ok=True)
            registrar_auditoria(
                conn_auditoria, "backup_automatico_falhou", "sistema",
                detalhes=f"Falha ao gerar backup: {erro}"[:500],
            )
            return

        if pasta_destino:
            try:
                pasta = Path(pasta_destino)
                if not pasta.is_dir():
                    raise FileNotFoundError(f"pasta não encontrada: {pasta_destino}")
                shutil.copy2(caminho, pasta / nome_arquivo)
            except Exception as erro:
                registrar_auditoria(
                    conn_auditoria, "backup_automatico_falhou", "sistema",
                    detalhes=f"Backup local ok, mas falhou ao copiar para a pasta de destino: {erro}"[:500],
                )
    finally:
        conn_auditoria.close()

    arquivos = sorted(BACKUPS_AUTO_DIR.glob("auto-*.zip"), key=lambda a: _datahora_backup_auto(a) or datetime.min)
    for antigo in arquivos[:-BACKUPS_AUTO_MANTER]:
        antigo.unlink(missing_ok=True)


def calcular_dashboard(conn):
    hoje_date = date.today()
    hoje = hoje_date.isoformat()
    agora = datetime.now()

    ativos = conn.execute(
        f"""SELECT funcionarios.*, {relatorios_service.SQL_COLUNAS_TURNO_EFETIVO}
           FROM funcionarios
           {relatorios_service.SQL_JOIN_TURNO_EFETIVO}
           WHERE funcionarios.situacao = 'ativo'"""
    ).fetchall()

    registros_hoje = conn.execute(
        """SELECT registros.funcionario_id, registros.tipo, registros.timestamp,
                  funcionarios.nome AS funcionario_nome
           FROM registros
           JOIN funcionarios ON funcionarios.id = registros.funcionario_id
           WHERE date(registros.timestamp) = ? ORDER BY registros.funcionario_id, registros.timestamp""",
        (hoje,),
    ).fetchall()

    primeiro_por_func, ultimo_por_func = {}, {}
    for r in registros_hoje:
        fid = r["funcionario_id"]
        primeiro_por_func.setdefault(fid, r)
        ultimo_por_func[fid] = r

    dias_por_funcionario_hoje = {
        dia["funcionario_id"]: dia for dia in relatorios_service.agrupar_registros_por_dia(registros_hoje)
    }

    ausencias_hoje = {
        r["funcionario_id"]
        for r in conn.execute("SELECT funcionario_id FROM ausencias WHERE data = ?", (hoje,)).fetchall()
    }
    feriados_hoje = relatorios_service.obter_feriados_no_periodo(conn, hoje, hoje)

    presentes, ausentes, atrasados, faltas, em_intervalo, saidas_antecipadas = [], [], [], [], [], []

    for f in ativos:
        fid = f["id"]
        primeiro, ultimo = primeiro_por_func.get(fid), ultimo_por_func.get(fid)

        if ultimo is not None and ultimo["tipo"] == "entrada":
            presentes.append(f)

        # Sábado, feriado ou folga da escala não é "ausente" — a pessoa nem trabalharia hoje.
        trabalha_hoje = relatorios_service.dia_e_util_para_funcionario(f, hoje_date, feriados_hoje) and not (
            f["data_admissao"] and f["data_admissao"] > hoje
        )
        if primeiro is None and fid not in ausencias_hoje and trabalha_hoje:
            ausentes.append(f)

        tem_turno_efetivo = f["horario_entrada"] is not None
        dia_util_hoje = tem_turno_efetivo and relatorios_service.dia_e_util_para_funcionario(
            f, hoje_date, feriados_hoje
        )

        if dia_util_hoje:
            limite = (
                datetime.strptime(f["horario_entrada"], "%H:%M") + timedelta(minutes=f["tolerancia_minutos"])
            ).time()
            if primeiro is not None and primeiro["tipo"] == "entrada":
                chegada = datetime.strptime(primeiro["timestamp"], "%Y-%m-%d %H:%M:%S").time()
                if chegada > limite:
                    atrasados.append(f)
            elif primeiro is None and agora.time() > limite and fid not in ausencias_hoje:
                faltas.append(f)

            dia_hoje = dias_por_funcionario_hoje.get(fid)
            pares_hoje = dia_hoje["pares"] if dia_hoje else []
            _entrada_hoje, _saida_almoco_hoje, _retorno_hoje, saida_hoje = relatorios_service.preencher_colunas_historico(
                pares_hoje
            )
            lunch_configurado = bool(f["horario_saida_almoco"] and f["horario_retorno_almoco"])

            esta_em_intervalo = False
            if lunch_configurado:
                if ultimo is not None and ultimo["tipo"] == "saida":
                    inicio_almoco = datetime.strptime(f["horario_saida_almoco"], "%H:%M").time()
                    fim_almoco = datetime.strptime(f["horario_retorno_almoco"], "%H:%M").time()
                    if inicio_almoco <= agora.time() <= fim_almoco:
                        esta_em_intervalo = True
            elif ultimo is not None and ultimo["tipo"] == "saida":
                limite_saida_heuristica = datetime.strptime(f["horario_saida"], "%H:%M").time()
                if agora.time() < limite_saida_heuristica:
                    esta_em_intervalo = True

            if esta_em_intervalo:
                em_intervalo.append(f)
            elif saida_hoje is not None and f["tolerancia_saida_minutos"] is not None:
                limite_saida_antecipada = (
                    datetime.strptime(f["horario_saida"], "%H:%M")
                    - timedelta(minutes=f["tolerancia_saida_minutos"])
                ).time()
                saida_hora = datetime.strptime(saida_hoje["timestamp"], "%Y-%m-%d %H:%M:%S").time()
                if saida_hora < limite_saida_antecipada:
                    saidas_antecipadas.append(f)

    def _ativos_com_ausencia_hoje(*tipos):
        marcadores = ",".join("?" * len(tipos))
        return conn.execute(
            f"""SELECT funcionarios.* FROM ausencias
               JOIN funcionarios ON funcionarios.id = ausencias.funcionario_id
               WHERE ausencias.data = ? AND ausencias.tipo IN ({marcadores}) AND funcionarios.situacao = 'ativo'""",
            (hoje, *tipos),
        ).fetchall()

    ferias_hoje = _ativos_com_ausencia_hoje("ferias")
    # Licença maternidade/paternidade soma junto de "licença" — mesmo critério de "folga"/"outro",
    # que também não têm indicador próprio no dashboard.
    licencas_hoje = _ativos_com_ausencia_hoje("licenca", "licenca_maternidade_paternidade")
    atestados_hoje = _ativos_com_ausencia_hoje("atestado")
    horas_extras_hoje_seg = relatorios_service.calcular_horas_extras_hoje(conn)

    ultimo_ponto = conn.execute(
        """SELECT registros.*, funcionarios.nome, funcionarios.foto, funcionarios.cargo
           FROM registros JOIN funcionarios ON funcionarios.id = registros.funcionario_id
           WHERE date(registros.timestamp) = ?
           ORDER BY registros.timestamp DESC LIMIT 1""",
        (hoje,),
    ).fetchone()

    return {
        "total_ativos": len(ativos),
        "presentes": presentes,
        "ausentes": ausentes,
        "atrasados": atrasados,
        "faltas": faltas,
        "em_intervalo": em_intervalo,
        "saidas_antecipadas": saidas_antecipadas,
        "ferias_hoje": ferias_hoje,
        "licencas_hoje": licencas_hoje,
        "atestados_hoje": atestados_hoje,
        "horas_extras_hoje": relatorios_service.formatar_horas(horas_extras_hoje_seg),
        "registros_hoje": len(registros_hoje),
        "ultimo_ponto": ultimo_ponto,
    }


def calcular_presenca_ultimos_dias(conn, dias=7):
    total_ativos = conn.execute("SELECT COUNT(*) AS n FROM funcionarios WHERE situacao = 'ativo'").fetchone()["n"]
    resultado = []
    for i in range(dias - 1, -1, -1):
        dia = date.today() - timedelta(days=i)
        presentes = conn.execute(
            """SELECT COUNT(DISTINCT registros.funcionario_id) AS n
               FROM registros JOIN funcionarios ON funcionarios.id = registros.funcionario_id
               WHERE date(registros.timestamp) = ? AND funcionarios.situacao = 'ativo'""",
            (dia.isoformat(),),
        ).fetchone()["n"]
        resultado.append(
            {
                "data": dia,
                "presentes": presentes,
                "total": total_ativos,
                "percentual": round(presentes / total_ativos * 100) if total_ativos else 0,
            }
        )
    return resultado


def calcular_presenca_por_setor(conn, dias=7):
    data_inicio = date.today() - timedelta(days=dias - 1)
    data_fim = date.today()

    setores = conn.execute("SELECT id, nome FROM setores ORDER BY nome").fetchall()
    grupos = [(s["id"], s["nome"]) for s in setores] + [(None, "Sem setor")]

    resultado = []
    for setor_id, setor_nome in grupos:
        if setor_id is not None:
            funcionarios = conn.execute(
                "SELECT id FROM funcionarios WHERE situacao = 'ativo' AND setor_id = ?", (setor_id,)
            ).fetchall()
        else:
            funcionarios = conn.execute(
                "SELECT id FROM funcionarios WHERE situacao = 'ativo' AND setor_id IS NULL"
            ).fetchall()

        if not funcionarios:
            continue

        ids_funcionarios = [f["id"] for f in funcionarios]
        placeholders = ",".join("?" * len(ids_funcionarios))
        dias_com_presenca = conn.execute(
            f"""SELECT COUNT(DISTINCT date(timestamp)) AS n FROM registros
                WHERE funcionario_id IN ({placeholders})
                  AND date(timestamp) BETWEEN ? AND ?""",
            [*ids_funcionarios, data_inicio.isoformat(), data_fim.isoformat()],
        ).fetchone()["n"]

        resultado.append(
            {
                "setor_nome": setor_nome,
                "percentual": round(dias_com_presenca / dias * 100) if dias else 0,
            }
        )

    return resultado


@app.route("/")
def kiosk():
    conn = db.get_db()
    try:
        config = conn.execute("SELECT * FROM configuracoes WHERE id = 1").fetchone()
    finally:
        conn.close()
    return render_template("kiosk.html", config=config, totem_autorizado=totem_autorizado())


COOKIE_TOTEM = "totem_autorizado"


def _token_totem():
    # ponytail: token derivado da secret_key — revogar um totem perdido exige apagar .secret_key
    # (desloga todos e invalida todos os totens); tabela de totens se precisar revogar um só.
    return hmac.new(app.config["SECRET_KEY"].encode(), b"totem", hashlib.sha256).hexdigest()


def totem_autorizado():
    return hmac.compare_digest(request.cookies.get(COOKIE_TOTEM, ""), _token_totem())


@app.route("/admin/totem/autorizar", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_SOMENTE_ADMIN)
def autorizar_totem():
    conn = db.get_db()
    try:
        registrar_auditoria(conn, "autorizar_totem", "totem", detalhes=f"ip={request.remote_addr}")
    finally:
        conn.close()
    resposta = redirect(url_for("kiosk"))
    resposta.set_cookie(
        COOKIE_TOTEM, _token_totem(), max_age=10 * 365 * 24 * 3600, httponly=True, samesite="Lax"
    )
    return resposta


@app.route("/admin/arquivos/<pasta>/<nome>")
@login_obrigatorio
def arquivo_privado(pasta, nome):
    pastas = {"fotos": FOTOS_DIR, "qrcodes": QRCODES_DIR}
    if pasta not in pastas:
        abort(404)
    return send_from_directory(pastas[pasta], nome)


def _foto_como_data_uri(foto):
    """O totem não tem login, então recebe a foto embutida na resposta em vez de uma URL."""
    caminho = FOTOS_DIR / Path(foto).name
    if not caminho.is_file():
        return None
    return "data:image/jpeg;base64," + base64.b64encode(caminho.read_bytes()).decode()


@app.route("/api/bater-ponto", methods=["POST"])
@csrf.exempt
def api_bater_ponto():
    if not totem_autorizado():
        return jsonify({"ok": False, "erro": "Este aparelho não está autorizado como totem."}), 403
    dados = request.get_json(silent=True) or {}
    codigo = (dados.get("codigo") or "").strip()
    if not codigo:
        return jsonify({"ok": False, "erro": "Código vazio."}), 400

    conn = db.get_db()
    try:
        funcionario = conn.execute("SELECT * FROM funcionarios WHERE codigo = ?", (codigo,)).fetchone()
        if not funcionario:
            return jsonify({"ok": False, "erro": "Crachá não reconhecido."}), 404

        if funcionario["situacao"] != "ativo":
            mensagens = {
                "inativo": "Funcionário inativo. Procure a coordenação.",
                "ferias": "Funcionário em férias.",
                "afastado": "Funcionário afastado. Procure a coordenação.",
            }
            return (
                jsonify(
                    {
                        "ok": False,
                        "erro": mensagens.get(funcionario["situacao"], "Funcionário não está ativo."),
                        "motivo": funcionario["situacao"],
                    }
                ),
                403,
            )

        agora = datetime.now()
        inicio_do_dia = agora.strftime("%Y-%m-%d") + " 00:00:00"
        ultimo = conn.execute(
            """SELECT * FROM registros
               WHERE funcionario_id = ? AND timestamp >= ?
               ORDER BY timestamp DESC LIMIT 1""",
            (funcionario["id"], inicio_do_dia),
        ).fetchone()

        if ultimo:
            ultimo_ts = datetime.strptime(ultimo["timestamp"], "%Y-%m-%d %H:%M:%S")
            if (agora - ultimo_ts).total_seconds() < COOLDOWN_SEGUNDOS:
                return jsonify({"ok": False, "erro": "Ponto já registrado agora há pouco."}), 429

        tipo = "entrada" if (ultimo is None or ultimo["tipo"] == "saida") else "saida"
        timestamp_str = agora.strftime("%Y-%m-%d %H:%M:%S")

        conn.execute(
            "INSERT INTO registros (funcionario_id, tipo, timestamp, manual) VALUES (?, ?, ?, 0)",
            (funcionario["id"], tipo, timestamp_str),
        )
        conn.commit()

        return jsonify(
            {
                "ok": True,
                "nome": funcionario["nome"],
                "cargo": funcionario["cargo"],
                "foto": _foto_como_data_uri(funcionario["foto"]) if funcionario["foto"] else None,
                "tipo": tipo,
                "horario": agora.strftime("%H:%M:%S"),
            }
        )
    finally:
        conn.close()


@app.route("/admin")
@login_obrigatorio
def admin_index():
    return redirect(url_for("dashboard"))


@app.route("/admin/dashboard")
@login_obrigatorio
def dashboard():
    periodo_grafico = request.args.get("periodo_grafico", "7")
    periodo_grafico = 30 if periodo_grafico == "30" else 7

    conn = db.get_db()
    try:
        indicadores = calcular_dashboard(conn)
        presenca_semana = calcular_presenca_ultimos_dias(conn, dias=periodo_grafico)
        presenca_setor = calcular_presenca_por_setor(conn, dias=periodo_grafico)

        comparativo_mensal = relatorios_service.montar_comparativo_mensal(
            conn, quantidade_meses=QUANTIDADE_MESES_COMPARATIVO
        )
        for item in comparativo_mensal:
            item["rotulo"] = f"{NOMES_MESES[item['mes']][:3]}/{item['ano']}"

        ranking_atrasos = []
        if session.get("usuario_perfil") != "consulta":
            hoje = date.today()
            data_inicio_ranking = (hoje - timedelta(days=periodo_grafico - 1)).isoformat()
            ranking_atrasos = relatorios_service.montar_ranking_atrasos(
                conn, data_inicio_ranking, hoje.isoformat(), top_n=TOP_N_RANKING_ATRASOS
            )

        return render_template(
            "dashboard.html",
            indicadores=indicadores,
            presenca_semana=presenca_semana,
            presenca_setor=presenca_setor,
            periodo_grafico=periodo_grafico,
            comparativo_mensal=comparativo_mensal,
            ranking_atrasos=ranking_atrasos,
        )
    finally:
        conn.close()


MAX_TENTATIVAS_LOGIN = 5
BLOQUEIO_LOGIN_MINUTOS = 15


@app.route("/admin/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        senha = request.form.get("senha", "")

        conn = db.get_db()
        try:
            registro = conn.execute(
                "SELECT * FROM usuarios WHERE usuario = ? AND ativo = 1", (usuario,)
            ).fetchone()
            agora = datetime.now()

            if registro and registro["bloqueado_ate"]:
                bloqueado_ate = datetime.strptime(registro["bloqueado_ate"], "%Y-%m-%d %H:%M:%S")
                if agora < bloqueado_ate:
                    registrar_auditoria(conn, "login_bloqueado", "usuario", registro["id"])
                    minutos_restantes = int((bloqueado_ate - agora).total_seconds() // 60) + 1
                    flash(
                        f"Conta bloqueada por tentativas seguidas incorretas. "
                        f"Tente novamente em {minutos_restantes} minuto(s).",
                        "erro",
                    )
                    return render_template("login.html")

            if registro and check_password_hash(registro["senha_hash"], senha):
                if registro["tentativas_falhas"] or registro["bloqueado_ate"]:
                    conn.execute(
                        "UPDATE usuarios SET tentativas_falhas = 0, bloqueado_ate = NULL WHERE id = ?",
                        (registro["id"],),
                    )
                    conn.commit()
                session.clear()
                session.permanent = True
                if senha == db.ADMIN_PADRAO_SENHA:
                    session["senha_padrao"] = True
                session["usuario_id"] = registro["id"]
                session["usuario_login"] = registro["usuario"]
                session["usuario_nome"] = registro["nome"] or registro["usuario"]
                session["usuario_perfil"] = registro["perfil"]
                registrar_auditoria(conn, "login_sucesso", "usuario", registro["id"])
                proximo = request.args.get("proximo") or ""
                # Só caminhos internos: "//site" ou "/\site" o navegador trata como outro domínio.
                if not proximo.startswith("/") or proximo[1:2] in ("/", "\\"):
                    proximo = url_for("dashboard")
                return redirect(proximo)

            if registro:
                tentativas = (registro["tentativas_falhas"] or 0) + 1
                if tentativas >= MAX_TENTATIVAS_LOGIN:
                    bloqueado_ate_novo = (agora + timedelta(minutes=BLOQUEIO_LOGIN_MINUTOS)).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    conn.execute(
                        "UPDATE usuarios SET tentativas_falhas = ?, bloqueado_ate = ? WHERE id = ?",
                        (tentativas, bloqueado_ate_novo, registro["id"]),
                    )
                    conn.commit()
                    registrar_auditoria(
                        conn, "login_bloqueado", "usuario", registro["id"], detalhes=f"tentativas={tentativas}"
                    )
                    flash(
                        f"Conta bloqueada por {BLOQUEIO_LOGIN_MINUTOS} minutos após "
                        f"{MAX_TENTATIVAS_LOGIN} tentativas seguidas incorretas.",
                        "erro",
                    )
                    return render_template("login.html")
                conn.execute(
                    "UPDATE usuarios SET tentativas_falhas = ? WHERE id = ?",
                    (tentativas, registro["id"]),
                )
                conn.commit()

            registrar_auditoria(conn, "login_falha", "usuario", detalhes=f"usuario_tentado={usuario}")
        finally:
            conn.close()

        flash("Usuário ou senha inválidos.", "erro")

    return render_template("login.html")


@app.route("/admin/logout")
def logout():
    if session.get("usuario_id"):
        conn = db.get_db()
        try:
            registrar_auditoria(conn, "logout", "usuario", session.get("usuario_id"))
        finally:
            conn.close()
    session.clear()
    return redirect(url_for("login"))


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
            conn.execute(
                f"UPDATE funcionarios SET {set_clause} WHERE id=?",
                valores + [funcionario_id],
            )
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
        caminho = DOCUMENTOS_DIR / documento["caminho_arquivo"]
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
        caminho = DOCUMENTOS_DIR / documento["caminho_arquivo"]
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

    return render_template("cracha.html", funcionario=funcionario, config=config)


def _ler_dados_setor_form():
    return {
        "nome": request.form.get("nome", "").strip(),
        "icone": request.form.get("icone", "").strip() or None,
        "cor": request.form.get("cor", "").strip() or None,
        "responsavel": request.form.get("responsavel", "").strip() or None,
        "centro_custo": request.form.get("centro_custo", "").strip() or None,
    }


def _validar_dados_setor(dados):
    if not dados["nome"]:
        return "Informe o nome do setor."
    return None


@app.route("/admin/setores", methods=["GET", "POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def setores():
    conn = db.get_db()
    try:
        if request.method == "POST":
            dados = _ler_dados_setor_form()
            erro = _validar_dados_setor(dados)
            if erro:
                flash(erro, "erro")
            else:
                try:
                    cursor = conn.execute(
                        """INSERT INTO setores (nome, icone, cor, responsavel, centro_custo)
                           VALUES (?, ?, ?, ?, ?)""",
                        (dados["nome"], dados["icone"], dados["cor"], dados["responsavel"], dados["centro_custo"]),
                    )
                    conn.commit()
                    registrar_auditoria(conn, "criar", "setor", cursor.lastrowid, f"nome={dados['nome']}")
                    flash("Setor adicionado.", "sucesso")
                except sqlite3.IntegrityError:
                    flash("Já existe um setor com esse nome.", "erro")
            return redirect(url_for("setores"))

        lista = conn.execute(
            """SELECT setores.*,
                      (SELECT COUNT(*) FROM funcionarios WHERE funcionarios.setor_id = setores.id) AS total_funcionarios
               FROM setores ORDER BY nome"""
        ).fetchall()
        return render_template("setores.html", setores=lista, icones_disponiveis=ICONES_SETOR_DISPONIVEIS)
    finally:
        conn.close()


@app.route("/admin/setores/<int:setor_id>/editar", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def editar_setor(setor_id):
    dados = _ler_dados_setor_form()
    erro = _validar_dados_setor(dados)
    conn = db.get_db()
    try:
        if erro:
            flash(erro, "erro")
        else:
            try:
                conn.execute(
                    """UPDATE setores SET nome = ?, icone = ?, cor = ?, responsavel = ?, centro_custo = ?
                       WHERE id = ?""",
                    (dados["nome"], dados["icone"], dados["cor"], dados["responsavel"], dados["centro_custo"], setor_id),
                )
                conn.commit()
                registrar_auditoria(conn, "editar", "setor", setor_id, f"nome={dados['nome']}")
                flash("Setor atualizado.", "sucesso")
            except sqlite3.IntegrityError:
                flash("Já existe um setor com esse nome.", "erro")
    finally:
        conn.close()
    return redirect(url_for("setores"))


@app.route("/admin/setores/<int:setor_id>/excluir", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def excluir_setor(setor_id):
    conn = db.get_db()
    try:
        setor = conn.execute("SELECT nome FROM setores WHERE id = ?", (setor_id,)).fetchone()
        try:
            conn.execute("DELETE FROM setores WHERE id = ?", (setor_id,))
            conn.commit()
            registrar_auditoria(conn, "excluir", "setor", setor_id, f"nome={setor['nome']}" if setor else None)
            flash("Setor excluído.", "sucesso")
        except sqlite3.IntegrityError:
            flash("Não é possível excluir: existem funcionários nesse setor.", "erro")
    finally:
        conn.close()
    return redirect(url_for("setores"))


def _ler_dados_feriado_form():
    return {
        "data": request.form.get("data", "").strip(),
        "nome": request.form.get("nome", "").strip(),
    }


def _validar_dados_feriado(dados):
    if not dados["data"] or not dados["nome"]:
        return "Informe a data e o nome do feriado."
    try:
        datetime.strptime(dados["data"], "%Y-%m-%d")
    except ValueError:
        return "Data inválida."
    return None


@app.route("/admin/feriados", methods=["GET", "POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def feriados():
    conn = db.get_db()
    try:
        if request.method == "POST":
            dados = _ler_dados_feriado_form()
            erro = _validar_dados_feriado(dados)
            if erro:
                flash(erro, "erro")
            else:
                try:
                    cursor = conn.execute(
                        "INSERT INTO feriados (data, nome, criado_por, criado_em) VALUES (?, ?, ?, ?)",
                        (
                            dados["data"], dados["nome"], session.get("usuario_login"),
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        ),
                    )
                    conn.commit()
                    registrar_auditoria(
                        conn, "criar", "feriado", cursor.lastrowid, f"data={dados['data']}; nome={dados['nome']}"
                    )
                    flash("Feriado adicionado.", "sucesso")
                except sqlite3.IntegrityError:
                    flash("Já existe um feriado cadastrado nessa data.", "erro")
            return redirect(url_for("feriados"))

        lista = conn.execute("SELECT * FROM feriados ORDER BY data").fetchall()
        return render_template("feriados.html", feriados=lista)
    finally:
        conn.close()


@app.route("/admin/feriados/<int:feriado_id>/editar", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def editar_feriado(feriado_id):
    dados = _ler_dados_feriado_form()
    erro = _validar_dados_feriado(dados)
    conn = db.get_db()
    try:
        if erro:
            flash(erro, "erro")
        else:
            try:
                conn.execute(
                    "UPDATE feriados SET data = ?, nome = ? WHERE id = ?",
                    (dados["data"], dados["nome"], feriado_id),
                )
                conn.commit()
                registrar_auditoria(
                    conn, "editar", "feriado", feriado_id, f"data={dados['data']}; nome={dados['nome']}"
                )
                flash("Feriado atualizado.", "sucesso")
            except sqlite3.IntegrityError:
                flash("Já existe um feriado cadastrado nessa data.", "erro")
    finally:
        conn.close()
    return redirect(url_for("feriados"))


@app.route("/admin/feriados/<int:feriado_id>/excluir", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def excluir_feriado(feriado_id):
    conn = db.get_db()
    try:
        feriado = conn.execute("SELECT nome, data FROM feriados WHERE id = ?", (feriado_id,)).fetchone()
        conn.execute("DELETE FROM feriados WHERE id = ?", (feriado_id,))
        conn.commit()
        registrar_auditoria(
            conn, "excluir", "feriado", feriado_id,
            f"data={feriado['data']}; nome={feriado['nome']}" if feriado else None,
        )
        flash("Feriado excluído.", "sucesso")
    finally:
        conn.close()
    return redirect(url_for("feriados"))


def _ler_dados_turno_form():
    dias_semana_lista = request.form.getlist("dias_semana")
    return {
        "nome": request.form.get("nome", "").strip(),
        "horario_entrada": request.form.get("horario_entrada", "").strip(),
        "horario_saida": request.form.get("horario_saida", "").strip(),
        "tolerancia_minutos": request.form.get("tolerancia_minutos", "10").strip(),
        "tolerancia_saida_minutos": request.form.get("tolerancia_saida_minutos", "10").strip(),
        "dias_semana": ",".join(dias_semana_lista) if dias_semana_lista else None,
        "horario_saida_almoco": request.form.get("horario_saida_almoco", "").strip() or None,
        "horario_retorno_almoco": request.form.get("horario_retorno_almoco", "").strip() or None,
    }


def _validar_dados_turno(dados):
    if not dados["nome"] or not dados["horario_entrada"] or not dados["horario_saida"]:
        return "Preencha nome, horário de entrada e de saída."
    if dados["horario_saida"] <= dados["horario_entrada"]:
        # ponytail: turno que vira a meia-noite não é suportado (totem e cálculos agrupam por data).
        return "O horário de saída precisa ser depois da entrada (turno que passa da meia-noite não é suportado)."
    if not dados["dias_semana"]:
        return "Marque pelo menos um dia da semana."
    if bool(dados["horario_saida_almoco"]) != bool(dados["horario_retorno_almoco"]):
        return "Preencha os dois horários de almoço, ou deixe os dois em branco."
    if dados["horario_saida_almoco"] and dados["horario_retorno_almoco"]:
        if dados["horario_retorno_almoco"] <= dados["horario_saida_almoco"]:
            return "O horário de retorno do almoço precisa ser depois da saída para o almoço."
    return None


@app.route("/admin/turnos", methods=["GET", "POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def turnos():
    conn = db.get_db()
    try:
        if request.method == "POST":
            dados = _ler_dados_turno_form()
            erro = _validar_dados_turno(dados)
            if erro:
                flash(erro, "erro")
            else:
                cursor = conn.execute(
                    """INSERT INTO turnos
                       (nome, horario_entrada, horario_saida, tolerancia_minutos, tolerancia_saida_minutos,
                        dias_semana, horario_saida_almoco, horario_retorno_almoco)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        dados["nome"],
                        dados["horario_entrada"],
                        dados["horario_saida"],
                        int(dados["tolerancia_minutos"] or 10),
                        int(dados["tolerancia_saida_minutos"] or 10),
                        dados["dias_semana"],
                        dados["horario_saida_almoco"],
                        dados["horario_retorno_almoco"],
                    ),
                )
                conn.commit()
                registrar_auditoria(conn, "criar", "turno", cursor.lastrowid, f"nome={dados['nome']}")
                flash("Turno adicionado.", "sucesso")
            return redirect(url_for("turnos"))

        lista = conn.execute(
            """SELECT turnos.*,
                      (SELECT COUNT(*) FROM funcionarios WHERE funcionarios.turno_id = turnos.id) AS total_funcionarios
               FROM turnos ORDER BY nome"""
        ).fetchall()
        return render_template("turnos.html", turnos=lista)
    finally:
        conn.close()


@app.route("/admin/turnos/<int:turno_id>/editar", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def editar_turno(turno_id):
    dados = _ler_dados_turno_form()
    erro = _validar_dados_turno(dados)
    conn = db.get_db()
    try:
        if erro:
            flash(erro, "erro")
        else:
            conn.execute(
                """UPDATE turnos SET nome=?, horario_entrada=?, horario_saida=?, tolerancia_minutos=?,
                   tolerancia_saida_minutos=?, dias_semana=?, horario_saida_almoco=?, horario_retorno_almoco=?
                   WHERE id=?""",
                (
                    dados["nome"],
                    dados["horario_entrada"],
                    dados["horario_saida"],
                    int(dados["tolerancia_minutos"] or 10),
                    int(dados["tolerancia_saida_minutos"] or 10),
                    dados["dias_semana"],
                    dados["horario_saida_almoco"],
                    dados["horario_retorno_almoco"],
                    turno_id,
                ),
            )
            conn.commit()
            registrar_auditoria(conn, "editar", "turno", turno_id, f"nome={dados['nome']}")
            flash("Turno atualizado.", "sucesso")
    finally:
        conn.close()
    return redirect(url_for("turnos"))


@app.route("/admin/turnos/<int:turno_id>/excluir", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def excluir_turno(turno_id):
    conn = db.get_db()
    try:
        turno = conn.execute("SELECT nome FROM turnos WHERE id = ?", (turno_id,)).fetchone()
        try:
            conn.execute("DELETE FROM turnos WHERE id = ?", (turno_id,))
            conn.commit()
            registrar_auditoria(conn, "excluir", "turno", turno_id, f"nome={turno['nome']}" if turno else None)
            flash("Turno excluído.", "sucesso")
        except sqlite3.IntegrityError:
            flash("Não é possível excluir: existem funcionários ou escalas usando esse turno.", "erro")
    finally:
        conn.close()
    return redirect(url_for("turnos"))


def _ler_dados_escala_form():
    return {
        "nome": request.form.get("nome", "").strip(),
        "turno_id": request.form.get("turno_id", "").strip(),
        "ciclo_dias_trabalho": request.form.get("ciclo_dias_trabalho", "").strip(),
        "ciclo_dias_descanso": request.form.get("ciclo_dias_descanso", "").strip(),
    }


def _validar_dados_escala(dados):
    if not dados["nome"] or not dados["turno_id"]:
        return "Preencha o nome e o turno base da escala."
    if not dados["ciclo_dias_trabalho"].isdigit() or int(dados["ciclo_dias_trabalho"]) <= 0:
        return "Dias de trabalho do ciclo precisa ser um número maior que zero."
    if not dados["ciclo_dias_descanso"].isdigit() or int(dados["ciclo_dias_descanso"]) <= 0:
        return "Dias de descanso do ciclo precisa ser um número maior que zero."
    return None


@app.route("/admin/escalas", methods=["GET", "POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def escalas():
    conn = db.get_db()
    try:
        if request.method == "POST":
            dados = _ler_dados_escala_form()
            erro = _validar_dados_escala(dados)
            if erro:
                flash(erro, "erro")
            else:
                cursor = conn.execute(
                    """INSERT INTO escalas (nome, turno_id, ciclo_dias_trabalho, ciclo_dias_descanso)
                       VALUES (?, ?, ?, ?)""",
                    (dados["nome"], int(dados["turno_id"]), int(dados["ciclo_dias_trabalho"]), int(dados["ciclo_dias_descanso"])),
                )
                conn.commit()
                registrar_auditoria(conn, "criar", "escala", cursor.lastrowid, f"nome={dados['nome']}")
                flash("Escala adicionada.", "sucesso")
            return redirect(url_for("escalas"))

        lista = conn.execute(
            """SELECT escalas.*, turnos.nome AS turno_nome,
                      (SELECT COUNT(*) FROM funcionarios WHERE funcionarios.escala_id = escalas.id) AS total_funcionarios
               FROM escalas
               JOIN turnos ON turnos.id = escalas.turno_id
               ORDER BY escalas.nome"""
        ).fetchall()
        turnos_lista = conn.execute("SELECT id, nome FROM turnos ORDER BY nome").fetchall()
        return render_template("escalas.html", escalas=lista, turnos=turnos_lista)
    finally:
        conn.close()


@app.route("/admin/escalas/<int:escala_id>/editar", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def editar_escala(escala_id):
    dados = _ler_dados_escala_form()
    erro = _validar_dados_escala(dados)
    conn = db.get_db()
    try:
        if erro:
            flash(erro, "erro")
        else:
            conn.execute(
                """UPDATE escalas SET nome=?, turno_id=?, ciclo_dias_trabalho=?, ciclo_dias_descanso=?
                   WHERE id=?""",
                (
                    dados["nome"],
                    int(dados["turno_id"]),
                    int(dados["ciclo_dias_trabalho"]),
                    int(dados["ciclo_dias_descanso"]),
                    escala_id,
                ),
            )
            conn.commit()
            registrar_auditoria(conn, "editar", "escala", escala_id, f"nome={dados['nome']}")
            flash("Escala atualizada.", "sucesso")
    finally:
        conn.close()
    return redirect(url_for("escalas"))


@app.route("/admin/escalas/<int:escala_id>/excluir", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def excluir_escala(escala_id):
    conn = db.get_db()
    try:
        escala = conn.execute("SELECT nome FROM escalas WHERE id = ?", (escala_id,)).fetchone()
        try:
            conn.execute("DELETE FROM escalas WHERE id = ?", (escala_id,))
            conn.commit()
            registrar_auditoria(conn, "excluir", "escala", escala_id, f"nome={escala['nome']}" if escala else None)
            flash("Escala excluída.", "sucesso")
        except sqlite3.IntegrityError:
            flash("Não é possível excluir: existem funcionários nessa escala.", "erro")
    finally:
        conn.close()
    return redirect(url_for("escalas"))


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


def _ler_filtros_central(req):
    hoje = date.today()
    data_inicio = req.args.get("data_inicio") or hoje.replace(day=1).isoformat()
    data_fim = req.args.get("data_fim") or hoje.isoformat()
    agrupar_por = req.args.get("agrupar_por") or "funcionario"
    if agrupar_por not in ("funcionario", "setor", "cargo", "turno"):
        agrupar_por = "funcionario"
    visao = req.args.get("visao") or "frequencia"
    if visao not in NOMES_VISAO:
        visao = "frequencia"
    funcionario_id = req.args.get("funcionario_id", "").strip() or None
    return data_inicio, data_fim, agrupar_por, visao, funcionario_id


@app.route("/admin/relatorios")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)
def relatorios():
    conn = db.get_db()
    try:
        data_inicio, data_fim, agrupar_por, visao, funcionario_id = _ler_filtros_central(request)
        linhas = relatorios_service.montar_linhas_central(conn, data_inicio, data_fim, agrupar_por, visao, funcionario_id)
        funcionarios_lista = conn.execute(
            "SELECT id, nome FROM funcionarios WHERE situacao = 'ativo' ORDER BY nome"
        ).fetchall()
        atalhos = relatorios_service.calcular_atalhos_periodo()
        config = conn.execute("SELECT * FROM configuracoes WHERE id = 1").fetchone()

        return render_template(
            "central_relatorios.html",
            linhas=linhas,
            funcionarios=funcionarios_lista,
            atalhos=atalhos,
            data_inicio=data_inicio,
            data_fim=data_fim,
            agrupar_por=agrupar_por,
            visao=visao,
            funcionario_id=funcionario_id or "",
            nomes_visao=NOMES_VISAO,
            config=config,
            mes_folha_ponto_atual=date.today().strftime("%Y-%m"),
        )
    finally:
        conn.close()


def _dados_exportacao_central_relatorios(conn, req):
    data_inicio, data_fim, agrupar_por, visao, funcionario_id = _ler_filtros_central(req)
    linhas = relatorios_service.montar_linhas_central(conn, data_inicio, data_fim, agrupar_por, visao, funcionario_id)
    colunas = relatorios_service.colunas_para_visao(visao, agrupar_por)
    linhas_tabela = relatorios_service.linhas_para_exportacao(linhas, visao)
    cabecalho = exportadores.obter_dados_cabecalho(conn)
    titulo = "Central de Relatórios"
    subtitulo = f"{NOMES_VISAO[visao]} - {data_inicio} a {data_fim}"
    return cabecalho, titulo, subtitulo, colunas, linhas_tabela


def _resposta_exportacao(formato, cabecalho, titulo, subtitulo, colunas, linhas_tabela, nome_arquivo):
    if formato == "pdf":
        conteudo = exportadores.gerar_pdf_tabela(cabecalho, titulo, subtitulo, colunas, linhas_tabela)
        mimetype = "application/pdf"
    else:
        conteudo = exportadores.gerar_excel_tabela(cabecalho, titulo, subtitulo, colunas, linhas_tabela)
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return app.response_class(
        conteudo, mimetype=mimetype, headers={"Content-Disposition": f"attachment; filename={nome_arquivo}"}
    )


@app.route("/admin/relatorios/exportar.pdf")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)
def relatorios_exportar_pdf():
    conn = db.get_db()
    try:
        dados = _dados_exportacao_central_relatorios(conn, request)
    finally:
        conn.close()
    return _resposta_exportacao("pdf", *dados, "relatorio.pdf")


@app.route("/admin/relatorios/exportar.xlsx")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)
def relatorios_exportar_excel():
    conn = db.get_db()
    try:
        dados = _dados_exportacao_central_relatorios(conn, request)
    finally:
        conn.close()
    return _resposta_exportacao("xlsx", *dados, "relatorio.xlsx")


def _dados_exportacao_folha_ponto(conn, req):
    mes_str = req.args.get("mes") or date.today().strftime("%Y-%m")
    try:
        ano, mes = (int(parte) for parte in mes_str.split("-"))
    except ValueError:
        hoje = date.today()
        ano, mes = hoje.year, hoje.month
    funcionario_id = req.args.get("funcionario_id", "").strip() or None

    resultado = relatorios_service.montar_folha_ponto_mensal(conn, ano, mes, funcionario_id)
    cabecalho = exportadores.obter_dados_cabecalho(conn)
    titulo = "Folha de Ponto Mensal"
    subtitulo = f"{NOMES_MESES[mes]}/{ano}"
    return (
        cabecalho, titulo, subtitulo,
        resultado["ultimo_dia"], resultado["dias_semana_abrev"], resultado["linhas"],
    )


def _resposta_exportacao_folha_ponto(
    formato, cabecalho, titulo, subtitulo, ultimo_dia, dias_semana_abrev, linhas, nome_arquivo
):
    if formato == "pdf":
        conteudo = exportadores.gerar_pdf_folha_ponto(
            cabecalho, titulo, subtitulo, ultimo_dia, dias_semana_abrev, linhas
        )
        mimetype = "application/pdf"
    else:
        conteudo = exportadores.gerar_excel_folha_ponto(
            cabecalho, titulo, subtitulo, ultimo_dia, dias_semana_abrev, linhas
        )
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return app.response_class(
        conteudo, mimetype=mimetype, headers={"Content-Disposition": f"attachment; filename={nome_arquivo}"}
    )


@app.route("/admin/relatorios/dias/exportar.pdf")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)
def relatorios_dias_exportar_pdf():
    conn = db.get_db()
    try:
        dados = _dados_exportacao_folha_ponto(conn, request)
    finally:
        conn.close()
    return _resposta_exportacao_folha_ponto("pdf", *dados, "folha_de_ponto.pdf")


@app.route("/admin/relatorios/dias/exportar.xlsx")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)
def relatorios_dias_exportar_excel():
    conn = db.get_db()
    try:
        dados = _dados_exportacao_folha_ponto(conn, request)
    finally:
        conn.close()
    return _resposta_exportacao_folha_ponto("xlsx", *dados, "folha_de_ponto.xlsx")


@app.route("/admin/ajustes")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def ajustes_ponto():
    conn = db.get_db()
    try:
        funcionarios_lista = conn.execute("SELECT id, nome FROM funcionarios ORDER BY nome").fetchall()

        funcionario_id = request.args.get("funcionario_id", "").strip()
        hoje = date.today()
        data_inicio = request.args.get("data_inicio") or hoje.replace(day=1).isoformat()
        data_fim = request.args.get("data_fim") or hoje.isoformat()

        query = """
            SELECT registros.*, funcionarios.nome AS funcionario_nome
            FROM registros
            JOIN funcionarios ON funcionarios.id = registros.funcionario_id
            WHERE date(registros.timestamp) BETWEEN date(?) AND date(?)
        """
        params = [data_inicio, data_fim]
        if funcionario_id:
            query += " AND registros.funcionario_id = ?"
            params.append(funcionario_id)
        query += " ORDER BY funcionario_nome, registros.timestamp"

        registros = conn.execute(query, params).fetchall()
        dias = relatorios_service.agrupar_registros_por_dia(registros)

        query_ausencias = """
            SELECT ausencias.*, funcionarios.nome AS funcionario_nome
            FROM ausencias
            JOIN funcionarios ON funcionarios.id = ausencias.funcionario_id
            WHERE ausencias.data BETWEEN ? AND ?
        """
        params_ausencias = [data_inicio, data_fim]
        if funcionario_id:
            query_ausencias += " AND ausencias.funcionario_id = ?"
            params_ausencias.append(funcionario_id)
        query_ausencias += " ORDER BY ausencias.data DESC"
        ausencias = conn.execute(query_ausencias, params_ausencias).fetchall()

        documentos_por_ausencia = {}
        if ausencias:
            ids_ausencias = [a["id"] for a in ausencias]
            marcadores = ",".join("?" * len(ids_ausencias))
            for doc in conn.execute(
                f"SELECT * FROM documentos WHERE ausencia_id IN ({marcadores}) ORDER BY criado_em DESC",
                ids_ausencias,
            ).fetchall():
                documentos_por_ausencia.setdefault(doc["ausencia_id"], []).append(doc)

        return render_template(
            "ajustes_ponto.html",
            funcionarios=funcionarios_lista,
            dias=dias,
            ausencias=ausencias,
            documentos_por_ausencia=documentos_por_ausencia,
            funcionario_id=funcionario_id,
            data_inicio=data_inicio,
            data_fim=data_fim,
            nomes_tipo_ausencia=NOMES_TIPO_AUSENCIA,
        )
    finally:
        conn.close()


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
    except sqlite3.IntegrityError:
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

        # Apaga os documentos anexados antes (FK bloquearia o DELETE de ausencias senão).
        documentos_anexados = conn.execute(
            "SELECT * FROM documentos WHERE ausencia_id = ?", (ausencia_id,)
        ).fetchall()
        for documento in documentos_anexados:
            caminho = DOCUMENTOS_DIR / documento["caminho_arquivo"]
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


@app.route("/admin/relatorios/exportar.csv")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)
def exportar_csv():
    conn = db.get_db()
    try:
        funcionario_id = request.args.get("funcionario_id", "").strip()
        hoje = date.today()
        data_inicio = request.args.get("data_inicio") or hoje.replace(day=1).isoformat()
        data_fim = request.args.get("data_fim") or hoje.isoformat()

        query = """
            SELECT registros.timestamp, registros.tipo, registros.manual,
                   funcionarios.nome AS funcionario_nome, funcionarios.cargo
            FROM registros
            JOIN funcionarios ON funcionarios.id = registros.funcionario_id
            WHERE date(registros.timestamp) BETWEEN date(?) AND date(?)
        """
        params = [data_inicio, data_fim]
        if funcionario_id:
            query += " AND registros.funcionario_id = ?"
            params.append(funcionario_id)
        query += " ORDER BY funcionario_nome, registros.timestamp"

        registros = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";")
    escritor.writerow(["Funcionario", "Cargo", "Data", "Hora", "Tipo", "Lancado manualmente"])
    for r in registros:
        data_hora = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
        escritor.writerow(
            [
                r["funcionario_nome"],
                r["cargo"],
                data_hora.strftime("%d/%m/%Y"),
                data_hora.strftime("%H:%M:%S"),
                "Entrada" if r["tipo"] == "entrada" else "Saida",
                "Sim" if r["manual"] else "Nao",
            ]
        )

    conteudo = chr(0xFEFF) + saida.getvalue()
    return app.response_class(
        conteudo,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=relatorio_ponto.csv"},
    )


@app.route("/admin/registros/novo", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def novo_registro_manual():
    funcionario_id = request.form.get("funcionario_id", "").strip()
    tipo = request.form.get("tipo", "").strip()
    timestamp_valor = normalizar_timestamp(request.form.get("timestamp", ""))
    motivo = request.form.get("motivo", "").strip()

    if not funcionario_id or tipo not in ("entrada", "saida") or not timestamp_valor or not motivo:
        flash("Preencha todos os campos (incluindo o motivo) para adicionar um registro manual.", "erro")
        return redirect(request.referrer or url_for("ajustes_ponto"))

    conn = db.get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO registros (funcionario_id, tipo, timestamp, manual, criado_por, motivo) VALUES (?, ?, ?, 1, ?, ?)",
            (funcionario_id, tipo, timestamp_valor, session.get("usuario_login"), motivo),
        )
        conn.commit()
        registrar_auditoria(
            conn, "criar", "registro", cursor.lastrowid,
            f"funcionario_id={funcionario_id}; tipo={tipo}; timestamp={timestamp_valor}; motivo={motivo}",
        )
    finally:
        conn.close()
    flash("Registro manual adicionado.", "sucesso")
    return redirect(request.referrer or url_for("ajustes_ponto"))


@app.route("/admin/registros/<int:registro_id>/editar", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def editar_registro(registro_id):
    novo_timestamp = normalizar_timestamp(request.form.get("timestamp", ""))
    novo_tipo = request.form.get("tipo", "").strip()
    motivo = request.form.get("motivo", "").strip()

    if novo_tipo not in ("entrada", "saida") or not novo_timestamp or not motivo:
        flash("Preencha os dados (incluindo o motivo) para editar o registro.", "erro")
        return redirect(request.referrer or url_for("ajustes_ponto"))

    conn = db.get_db()
    try:
        conn.execute(
            """UPDATE registros SET timestamp = ?, tipo = ?, manual = 1, atualizado_por = ?, atualizado_em = ?,
               motivo = ? WHERE id = ?""",
            (
                novo_timestamp,
                novo_tipo,
                session.get("usuario_login"),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                motivo,
                registro_id,
            ),
        )
        conn.commit()
        registrar_auditoria(
            conn, "editar", "registro", registro_id, f"tipo={novo_tipo}; timestamp={novo_timestamp}; motivo={motivo}"
        )
    finally:
        conn.close()
    flash("Registro atualizado.", "sucesso")
    return redirect(request.referrer or url_for("ajustes_ponto"))


@app.route("/admin/registros/<int:registro_id>/excluir", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def excluir_registro(registro_id):
    conn = db.get_db()
    try:
        registro = conn.execute(
            """SELECT registros.*, funcionarios.nome AS funcionario_nome
               FROM registros JOIN funcionarios ON funcionarios.id = registros.funcionario_id
               WHERE registros.id = ?""",
            (registro_id,),
        ).fetchone()
        conn.execute("DELETE FROM registros WHERE id = ?", (registro_id,))
        conn.commit()
        if registro:
            registrar_auditoria(
                conn, "excluir", "registro", registro_id,
                f"funcionario={registro['funcionario_nome']}; tipo={registro['tipo']}; timestamp={registro['timestamp']}",
            )
    finally:
        conn.close()
    flash("Registro excluído.", "sucesso")
    return redirect(request.referrer or url_for("ajustes_ponto"))


@app.route("/admin/senha", methods=["GET", "POST"])
@login_obrigatorio
def trocar_senha():
    if request.method == "POST":
        senha_atual = request.form.get("senha_atual", "")
        nova_senha = request.form.get("nova_senha", "")
        confirmar_senha = request.form.get("confirmar_senha", "")

        conn = db.get_db()
        try:
            registro_usuario = conn.execute(
                "SELECT * FROM usuarios WHERE id = ?", (session["usuario_id"],)
            ).fetchone()

            if not registro_usuario or not check_password_hash(registro_usuario["senha_hash"], senha_atual):
                flash("Senha atual incorreta.", "erro")
            elif len(nova_senha) < 6:
                flash("A nova senha precisa ter pelo menos 6 caracteres.", "erro")
            elif nova_senha == db.ADMIN_PADRAO_SENHA:
                flash("Escolha uma senha diferente da senha padrão.", "erro")
            elif nova_senha != confirmar_senha:
                flash("A confirmação não corresponde à nova senha.", "erro")
            else:
                conn.execute(
                    "UPDATE usuarios SET senha_hash = ? WHERE id = ?",
                    (generate_password_hash(nova_senha), session["usuario_id"]),
                )
                conn.commit()
                registrar_auditoria(conn, "trocar_senha", "usuario", session["usuario_id"])
                session.pop("senha_padrao", None)
                flash("Senha alterada com sucesso.", "sucesso")
        finally:
            conn.close()

    return render_template("trocar_senha.html")


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

        condicoes = ["date(timestamp) BETWEEN date(?) AND date(?)"]
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
    BACKUPS_AUTO_DIR.mkdir(parents=True, exist_ok=True)
    arquivos = sorted(
        BACKUPS_AUTO_DIR.glob("auto-*.zip"),
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
    caminho = BACKUPS_AUTO_DIR / nome_arquivo
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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
