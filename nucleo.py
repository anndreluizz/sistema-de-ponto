import io
import logging
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import zipfile
from datetime import date, datetime, timedelta
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, flash, has_request_context, redirect, request, session, url_for
from flask_wtf import CSRFProtect

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
        try:
            return SECRET_KEY_PATH.read_text().strip()
        except PermissionError:
            sys.exit(
                "ERRO: este usuario do Windows nao tem permissao para ler o arquivo .secret_key.\n"
                "O sistema precisa ser iniciado pelo mesmo usuario que o instalou (ou por um\n"
                "Administrador). Para liberar outro usuario, um Administrador pode rodar nesta pasta:\n"
                "    icacls .secret_key /reset"
            )
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
        ativos = relatorios_service.anexar_historico_jornada(conn, ativos)
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
    if not has_request_context():
        # Chamado pelo agendador de backup (thread em segundo plano, sem usuário nem navegador).
        conn.execute(
            """INSERT INTO auditoria (usuario, perfil, acao, entidade, entidade_id, detalhes, timestamp)
               VALUES ('sistema', 'sistema', ?, ?, ?, ?, ?)""",
            (acao, entidade, entidade_id, detalhes, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
        return
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


def hex_para_rgb(cor_hex):
    cor_hex = (cor_hex or "").lstrip("#")
    if len(cor_hex) != 6:
        return "30, 64, 175"
    try:
        return f"{int(cor_hex[0:2], 16)}, {int(cor_hex[2:4], 16)}, {int(cor_hex[4:6], 16)}"
    except ValueError:
        return "30, 64, 175"


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
    """Chamado a cada hora por _agendador_backup (thread em segundo plano)."""
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


COOKIE_TOTEM = "totem_autorizado"


FOTOS_BATIDA_DIR = BASE_DIR / "uploads_privados" / "batidas"
DIAS_GUARDAR_FOTOS_BATIDA = 90
TAMANHO_MAX_FOTO_BATIDA = 300_000  # bytes do JPEG; o totem manda ~15 KB


def _caminho_foto_batida(registro_id, timestamp):
    return FOTOS_BATIDA_DIR / timestamp[:7] / f"{registro_id}.jpg"


def apagar_fotos_batida_antigas():
    limite = time.time() - DIAS_GUARDAR_FOTOS_BATIDA * 86400
    if not FOTOS_BATIDA_DIR.is_dir():
        return
    for pasta_mes in FOTOS_BATIDA_DIR.iterdir():
        for foto in pasta_mes.glob("*.jpg"):
            if foto.stat().st_mtime < limite:
                foto.unlink(missing_ok=True)
        if pasta_mes.is_dir() and not any(pasta_mes.iterdir()):
            pasta_mes.rmdir()


@app.template_global()
def tem_foto_batida(registro):
    return _caminho_foto_batida(registro["id"], registro["timestamp"]).is_file()


MAX_TENTATIVAS_LOGIN = 5
BLOQUEIO_LOGIN_MINUTOS = 15


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


def eh_erro_mes_fechado(erro):
    return isinstance(erro, sqlite3.IntegrityError) and "mes_fechado" in str(erro)


@app.errorhandler(sqlite3.IntegrityError)
def erro_integridade(erro):
    """Os triggers de mês fechado (db._criar_travas_mes_fechado) chegam aqui de qualquer tela."""
    if not eh_erro_mes_fechado(erro):
        raise erro
    flash(
        "Esse mês já foi fechado (folha entregue) e não pode mais ser alterado. "
        "Um Administrador pode reabrir o mês na Central de Relatórios.",
        "erro",
    )
    return redirect(request.referrer or url_for("dashboard"))


@app.errorhandler(500)
def erro_interno(erro):
    return (
        "<h1>Ocorreu um erro inesperado</h1><p>O problema foi registrado no arquivo erros.log, "
        "na pasta do sistema. Volte à página anterior e tente de novo; se continuar, avise o "
        "administrador.</p>",
        500,
    )


def _agendador_backup():
    """Antes o backup só rodava quando alguém abria o painel logado — e o totem nunca dispara
    isso, então passavam semanas sem backup. Agora checa sozinho a cada hora."""
    while True:
        try:
            verificar_backup_automatico()
        except Exception:
            app.logger.exception("Falha no agendador de backup automático")
        try:
            apagar_fotos_batida_antigas()
        except Exception:
            app.logger.exception("Falha ao apagar fotos de batida antigas")
        time.sleep(3600)


# Fora dos testes (que importam este módulo com banco temporário): log de erros em arquivo
# e o agendador de backup — senão os testes gravariam backup do banco de teste na pasta real.
if "pytest" not in sys.modules:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(BASE_DIR / "erros.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"),
        ],
    )
    threading.Thread(target=_agendador_backup, daemon=True, name="backup-automatico").start()

