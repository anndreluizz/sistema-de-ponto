import secrets
import sqlite3
from pathlib import Path

from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "ponto.db"

ADMIN_PADRAO_USUARIO = "admin"
ADMIN_PADRAO_SENHA = "admin123"

SITUACOES_VALIDAS = ("ativo", "inativo", "ferias", "afastado")
TIPOS_AUSENCIA_VALIDOS = (
    "atestado", "licenca", "ferias", "folga",
    "licenca_maternidade_paternidade", "falecimento", "abono_legal", "outro",
)
PERFIS_VALIDOS = ("administrador", "rh", "direcao", "consulta")


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _colunas_existentes(conn, tabela):
    return {linha["name"] for linha in conn.execute(f"PRAGMA table_info({tabela})").fetchall()}


def _migrar_funcionarios(conn):
    colunas = _colunas_existentes(conn, "funcionarios")

    if "situacao" not in colunas:
        conn.execute(
            """ALTER TABLE funcionarios ADD COLUMN situacao TEXT NOT NULL DEFAULT 'ativo'
               CHECK (situacao IN ('ativo', 'inativo', 'ferias', 'afastado'))"""
        )
        if "ativo" in colunas:
            conn.execute(
                "UPDATE funcionarios SET situacao = CASE WHEN ativo = 1 THEN 'ativo' ELSE 'inativo' END"
            )
        conn.commit()

    novas_colunas_simples = {
        "foto": "TEXT",
        "cpf": "TEXT",
        "matricula": "TEXT",
        "telefone": "TEXT",
        "email": "TEXT",
        "data_admissao": "TEXT",
        "observacoes": "TEXT",
    }
    colunas = _colunas_existentes(conn, "funcionarios")
    for nome, tipo in novas_colunas_simples.items():
        if nome not in colunas:
            conn.execute(f"ALTER TABLE funcionarios ADD COLUMN {nome} {tipo}")
    conn.commit()

    colunas = _colunas_existentes(conn, "funcionarios")
    if "setor_id" not in colunas:
        conn.execute("ALTER TABLE funcionarios ADD COLUMN setor_id INTEGER REFERENCES setores(id)")
    if "turno_id" not in colunas:
        conn.execute("ALTER TABLE funcionarios ADD COLUMN turno_id INTEGER REFERENCES turnos(id)")
    conn.commit()

    colunas = _colunas_existentes(conn, "funcionarios")
    if "escala_id" not in colunas:
        conn.execute("ALTER TABLE funcionarios ADD COLUMN escala_id INTEGER REFERENCES escalas(id)")
    if "escala_data_referencia" not in colunas:
        conn.execute("ALTER TABLE funcionarios ADD COLUMN escala_data_referencia TEXT")
    if "banco_horas_inicio" not in colunas:
        conn.execute("ALTER TABLE funcionarios ADD COLUMN banco_horas_inicio TEXT")
    conn.commit()

    colunas = _colunas_existentes(conn, "funcionarios")
    if "rg" not in colunas:
        conn.execute("ALTER TABLE funcionarios ADD COLUMN rg TEXT")
    if "salario" not in colunas:
        conn.execute("ALTER TABLE funcionarios ADD COLUMN salario TEXT")
    conn.commit()

    conn.execute(
        "UPDATE funcionarios SET matricula = printf('%04d', id) WHERE matricula IS NULL OR matricula = ''"
    )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_funcionarios_matricula ON funcionarios(matricula)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_funcionarios_setor ON funcionarios(setor_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_funcionarios_turno ON funcionarios(turno_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_funcionarios_escala ON funcionarios(escala_id)")
    conn.commit()

    colunas = _colunas_existentes(conn, "funcionarios")
    if "ativo" in colunas:
        conn.execute("ALTER TABLE funcionarios DROP COLUMN ativo")
        conn.commit()


def _migrar_funcionarios_vinculo(conn):
    colunas = _colunas_existentes(conn, "funcionarios")
    if "vinculo" not in colunas:
        conn.execute(
            """ALTER TABLE funcionarios ADD COLUMN vinculo TEXT
               CHECK (vinculo IS NULL OR vinculo IN ('efetivo', 'contratado'))"""
        )
        conn.commit()


def _migrar_funcionarios_categoria(conn):
    colunas = _colunas_existentes(conn, "funcionarios")
    if "categoria" not in colunas:
        conn.execute(
            """ALTER TABLE funcionarios ADD COLUMN categoria TEXT
               CHECK (categoria IS NULL OR categoria IN ('gestao', 'apoio_pedagogico', 'professor', 'outro'))"""
        )
        conn.commit()


def _migrar_feriados(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS feriados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT NOT NULL UNIQUE,
            nome TEXT NOT NULL,
            criado_por TEXT,
            criado_em TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_feriados_data ON feriados(data);
        """
    )
    conn.commit()


def _migrar_registros(conn):
    colunas = _colunas_existentes(conn, "registros")
    novas_colunas_simples = {
        "criado_por": "TEXT",
        "atualizado_por": "TEXT",
        "atualizado_em": "TEXT",
        "motivo": "TEXT",
    }
    for nome, tipo in novas_colunas_simples.items():
        if nome not in colunas:
            conn.execute(f"ALTER TABLE registros ADD COLUMN {nome} {tipo}")
    conn.commit()


def _migrar_configuracoes(conn):
    colunas = _colunas_existentes(conn, "configuracoes")
    if "cor_primaria" not in colunas:
        conn.execute("ALTER TABLE configuracoes ADD COLUMN cor_primaria TEXT NOT NULL DEFAULT '#1E40AF'")
        conn.commit()
    if "backup_frequencia_horas" not in colunas:
        conn.execute("ALTER TABLE configuracoes ADD COLUMN backup_frequencia_horas INTEGER NOT NULL DEFAULT 24")
    if "backup_pasta_destino" not in colunas:
        conn.execute("ALTER TABLE configuracoes ADD COLUMN backup_pasta_destino TEXT")
    if "backup_senha" not in colunas:
        conn.execute("ALTER TABLE configuracoes ADD COLUMN backup_senha TEXT")
    conn.commit()


def _migrar_auditoria(conn):
    colunas = _colunas_existentes(conn, "auditoria")
    if "navegador" not in colunas:
        conn.execute("ALTER TABLE auditoria ADD COLUMN navegador TEXT")
    if "sistema_operacional" not in colunas:
        conn.execute("ALTER TABLE auditoria ADD COLUMN sistema_operacional TEXT")
    conn.commit()


def _migrar_usuarios(conn):
    linha_legado = conn.execute("SELECT * FROM admin LIMIT 1").fetchone()
    if not linha_legado:
        return
    ja_migrado = conn.execute(
        "SELECT 1 FROM usuarios WHERE usuario = ?", (linha_legado["usuario"],)
    ).fetchone()
    if ja_migrado:
        return
    try:
        conn.execute(
            """INSERT INTO usuarios (nome, usuario, senha_hash, perfil, ativo)
               VALUES (?, ?, ?, 'administrador', 1)""",
            (linha_legado["usuario"], linha_legado["usuario"], linha_legado["senha_hash"]),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass


def _migrar_setores(conn):
    colunas = _colunas_existentes(conn, "setores")
    novas_colunas_simples = {
        "icone": "TEXT",
        "cor": "TEXT",
        "responsavel": "TEXT",
        "centro_custo": "TEXT",
    }
    for nome, tipo in novas_colunas_simples.items():
        if nome not in colunas:
            conn.execute(f"ALTER TABLE setores ADD COLUMN {nome} {tipo}")
    conn.commit()


def _migrar_turnos(conn):
    colunas = _colunas_existentes(conn, "turnos")
    if "dias_semana" not in colunas:
        conn.execute("ALTER TABLE turnos ADD COLUMN dias_semana TEXT")
        conn.execute("UPDATE turnos SET dias_semana = '0,1,2,3,4' WHERE dias_semana IS NULL")
        conn.commit()
    novas_colunas_simples = {
        "horario_saida_almoco": "TEXT",
        "horario_retorno_almoco": "TEXT",
    }
    for nome, tipo in novas_colunas_simples.items():
        if nome not in colunas:
            conn.execute(f"ALTER TABLE turnos ADD COLUMN {nome} {tipo}")
    if "tolerancia_saida_minutos" not in colunas:
        conn.execute("ALTER TABLE turnos ADD COLUMN tolerancia_saida_minutos INTEGER NOT NULL DEFAULT 10")
    conn.commit()


def _migrar_usuarios_seguranca(conn):
    colunas = _colunas_existentes(conn, "usuarios")
    if "tentativas_falhas" not in colunas:
        conn.execute("ALTER TABLE usuarios ADD COLUMN tentativas_falhas INTEGER NOT NULL DEFAULT 0")
    if "bloqueado_ate" not in colunas:
        conn.execute("ALTER TABLE usuarios ADD COLUMN bloqueado_ate TEXT")
    conn.commit()


def _migrar_ausencias_tipos(conn):
    """Reconstrói `ausencias` pra ampliar o CHECK de tipo (SQLite não altera CHECK direto).
    Precisa rodar ANTES de `_migrar_documentos` — nesse ponto `documentos` ainda não existe,
    então a reconstrução não tem tabela filha (FK) pra corromper."""
    linha = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ausencias'"
    ).fetchone()
    if not linha or "licenca_maternidade_paternidade" in linha["sql"]:
        return  # já migrado, ou instalação nova (já nasce com os 8 tipos)

    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        conn.execute(
            """CREATE TABLE ausencias_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                funcionario_id INTEGER NOT NULL REFERENCES funcionarios(id),
                data TEXT NOT NULL,
                tipo TEXT NOT NULL CHECK (tipo IN (
                    'atestado', 'licenca', 'ferias', 'folga',
                    'licenca_maternidade_paternidade', 'falecimento', 'abono_legal', 'outro'
                )),
                observacao TEXT,
                criado_por TEXT,
                criado_em TEXT NOT NULL
            )"""
        )
        conn.execute(
            """INSERT INTO ausencias_new (id, funcionario_id, data, tipo, observacao, criado_por, criado_em)
               SELECT id, funcionario_id, data, tipo, observacao, criado_por, criado_em FROM ausencias"""
        )
        conn.execute("DROP TABLE ausencias")
        conn.execute("ALTER TABLE ausencias_new RENAME TO ausencias")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_ausencias_funcionario_data ON ausencias(funcionario_id, data)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ausencias_data ON ausencias(data)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _migrar_documentos(conn):
    """Só é chamada depois de `_migrar_ausencias_tipos` — ver docstring lá."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS documentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            funcionario_id INTEGER NOT NULL REFERENCES funcionarios(id),
            ausencia_id INTEGER REFERENCES ausencias(id),
            tipo TEXT,
            nome_arquivo TEXT NOT NULL,
            caminho_arquivo TEXT NOT NULL,
            tamanho_bytes INTEGER,
            criado_por TEXT,
            criado_em TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_documentos_funcionario ON documentos(funcionario_id);
        CREATE INDEX IF NOT EXISTS idx_documentos_ausencia ON documentos(ausencia_id);
        """
    )
    conn.commit()


def _seed_configuracoes(conn):
    conn.execute(
        "INSERT OR IGNORE INTO configuracoes (id, nome_instituicao, logo_path, rodape_cracha) "
        "VALUES (1, '', NULL, '')"
    )
    conn.commit()


def init_db():
    conn = get_db()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS funcionarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                cargo TEXT NOT NULL DEFAULT '',
                codigo TEXT NOT NULL UNIQUE,
                situacao TEXT NOT NULL DEFAULT 'ativo'
                    CHECK (situacao IN ('ativo', 'inativo', 'ferias', 'afastado')),
                foto TEXT,
                cpf TEXT,
                matricula TEXT,
                setor_id INTEGER REFERENCES setores(id),
                turno_id INTEGER REFERENCES turnos(id),
                telefone TEXT,
                email TEXT,
                data_admissao TEXT,
                observacoes TEXT
            );

            CREATE TABLE IF NOT EXISTS registros (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                funcionario_id INTEGER NOT NULL REFERENCES funcionarios(id),
                tipo TEXT NOT NULL CHECK (tipo IN ('entrada', 'saida')),
                timestamp TEXT NOT NULL,
                manual INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_registros_funcionario
                ON registros(funcionario_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_registros_timestamp ON registros(timestamp);

            CREATE TABLE IF NOT EXISTS admin (
                usuario TEXT PRIMARY KEY,
                senha_hash TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS setores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS turnos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                horario_entrada TEXT NOT NULL,
                horario_saida TEXT NOT NULL,
                tolerancia_minutos INTEGER NOT NULL DEFAULT 10
            );

            CREATE TABLE IF NOT EXISTS escalas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                turno_id INTEGER NOT NULL REFERENCES turnos(id),
                ciclo_dias_trabalho INTEGER NOT NULL,
                ciclo_dias_descanso INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS configuracoes (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                nome_instituicao TEXT NOT NULL DEFAULT '',
                logo_path TEXT,
                rodape_cracha TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS ausencias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                funcionario_id INTEGER NOT NULL REFERENCES funcionarios(id),
                data TEXT NOT NULL,
                tipo TEXT NOT NULL CHECK (tipo IN (
                    'atestado', 'licenca', 'ferias', 'folga',
                    'licenca_maternidade_paternidade', 'falecimento', 'abono_legal', 'outro'
                )),
                observacao TEXT,
                criado_por TEXT,
                criado_em TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_ausencias_funcionario_data ON ausencias(funcionario_id, data);
            CREATE INDEX IF NOT EXISTS idx_ausencias_data ON ausencias(data);

            CREATE TABLE IF NOT EXISTS compensacoes_banco_horas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                funcionario_id INTEGER NOT NULL REFERENCES funcionarios(id),
                data TEXT NOT NULL,
                segundos INTEGER NOT NULL,
                motivo TEXT NOT NULL,
                criado_por TEXT,
                criado_em TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_compensacoes_funcionario ON compensacoes_banco_horas(funcionario_id, data);

            -- Meses fechados (folha entregue): os triggers de _criar_travas_mes_fechado
            -- recusam qualquer alteração de ponto/justificativa/compensação/feriado neles.
            CREATE TABLE IF NOT EXISTS meses_fechados (
                mes TEXT PRIMARY KEY,
                fechado_por TEXT,
                fechado_em TEXT NOT NULL
            );

            -- "Foto" da jornada (turno/escala efetivos) que valia até `vigente_ate`, gravada
            -- quando ela muda — assim trocar o turno não recalcula meses passados.
            -- `dados` é JSON com as colunas de relatorios_service.COLUNAS_JORNADA.
            CREATE TABLE IF NOT EXISTS jornada_historico (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                funcionario_id INTEGER NOT NULL REFERENCES funcionarios(id),
                vigente_ate TEXT NOT NULL,
                dados TEXT NOT NULL,
                criado_em TEXT NOT NULL,
                UNIQUE (funcionario_id, vigente_ate)
            );

            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL DEFAULT '',
                usuario TEXT NOT NULL UNIQUE,
                senha_hash TEXT NOT NULL,
                perfil TEXT NOT NULL DEFAULT 'consulta'
                    CHECK (perfil IN ('administrador', 'rh', 'direcao', 'consulta')),
                ativo INTEGER NOT NULL DEFAULT 1,
                criado_em TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS auditoria (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario TEXT NOT NULL,
                perfil TEXT NOT NULL,
                acao TEXT NOT NULL,
                entidade TEXT NOT NULL,
                entidade_id INTEGER,
                detalhes TEXT,
                ip TEXT,
                timestamp TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_auditoria_timestamp ON auditoria(timestamp);
            CREATE INDEX IF NOT EXISTS idx_auditoria_usuario ON auditoria(usuario);
            """
        )
        conn.commit()

        _migrar_setores(conn)
        _migrar_turnos(conn)
        _migrar_funcionarios(conn)
        _migrar_funcionarios_vinculo(conn)
        _migrar_funcionarios_categoria(conn)
        _migrar_registros(conn)
        _migrar_configuracoes(conn)
        _migrar_ausencias_tipos(conn)
        _migrar_documentos(conn)
        _migrar_auditoria(conn)
        _migrar_feriados(conn)
        _seed_configuracoes(conn)

        ja_existe_admin = conn.execute("SELECT 1 FROM admin LIMIT 1").fetchone()
        if not ja_existe_admin:
            conn.execute(
                "INSERT INTO admin (usuario, senha_hash) VALUES (?, ?)",
                (ADMIN_PADRAO_USUARIO, generate_password_hash(ADMIN_PADRAO_SENHA)),
            )
            conn.commit()

        _migrar_usuarios(conn)
        _migrar_usuarios_seguranca(conn)
        _criar_travas_mes_fechado(conn)  # por último: _migrar_ausencias_tipos recria a tabela
    finally:
        conn.close()


TABELAS_TRAVADAS_POR_MES = {
    "registros": "timestamp",
    "ausencias": "data",
    "compensacoes_banco_horas": "data",
    "feriados": "data",
}


def _criar_travas_mes_fechado(conn):
    """Triggers que recusam INSERT/UPDATE/DELETE em mês fechado — vale para qualquer tela,
    inclusive as que ainda não existem. O erro chega no Python como sqlite3.IntegrityError
    com a mensagem 'mes_fechado'."""
    for tabela, coluna in TABELAS_TRAVADAS_POR_MES.items():
        fechado = "EXISTS (SELECT 1 FROM meses_fechados WHERE mes = substr({}.%s, 1, 7))" % coluna
        condicoes = {
            "insert": fechado.format("NEW"),
            "update": f"{fechado.format('OLD')} OR {fechado.format('NEW')}",
            "delete": fechado.format("OLD"),
        }
        for operacao, condicao in condicoes.items():
            conn.execute(
                f"""CREATE TRIGGER IF NOT EXISTS trava_mes_{tabela}_{operacao}
                    BEFORE {operacao.upper()} ON {tabela} WHEN {condicao}
                    BEGIN SELECT RAISE(ABORT, 'mes_fechado'); END"""
            )
    conn.commit()


def gerar_codigo():
    return secrets.token_hex(8)
