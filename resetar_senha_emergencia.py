"""Script de emergência para redefinir a senha de um usuário direto no banco.

Use isso só se ficou sem nenhum Administrador com acesso ao sistema.
Funciona com o servidor rodando ou parado.
"""
import getpass

import db
from werkzeug.security import generate_password_hash


def main():
    conn = db.get_db()
    try:
        usuarios = conn.execute(
            "SELECT id, nome, usuario, perfil, ativo FROM usuarios ORDER BY id"
        ).fetchall()

        if not usuarios:
            print("Nenhum usuário encontrado no banco.")
            return

        print("\nUsuários cadastrados:")
        for u in usuarios:
            situacao = "ativo" if u["ativo"] else "INATIVO"
            print(f"  [{u['id']}] {u['nome']} (login: {u['usuario']}, perfil: {u['perfil']}, {situacao})")

        escolha = input("\nDigite o ID do usuário para redefinir a senha: ").strip()
        if not escolha.isdigit():
            print("ID inválido.")
            return

        usuario_id = int(escolha)
        if not any(u["id"] == usuario_id for u in usuarios):
            print("ID não encontrado.")
            return

        nova_senha = getpass.getpass("Nova senha (mínimo 6 caracteres): ")
        if len(nova_senha) < 6:
            print("A senha precisa ter pelo menos 6 caracteres.")
            return

        confirmacao = getpass.getpass("Confirme a nova senha: ")
        if nova_senha != confirmacao:
            print("As senhas não conferem.")
            return

        conn.execute(
            """UPDATE usuarios
               SET senha_hash = ?, ativo = 1, tentativas_falhas = 0, bloqueado_ate = NULL
               WHERE id = ?""",
            (generate_password_hash(nova_senha), usuario_id),
        )
        # Bloqueios de login por aparelho (tabela criada pelo sistema; pode não existir em banco antigo)
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'bloqueios_login'").fetchone():
            conn.execute(
                "DELETE FROM bloqueios_login WHERE usuario = (SELECT usuario FROM usuarios WHERE id = ?)",
                (usuario_id,),
            )
        conn.commit()
        print("\nSenha redefinida com sucesso. O usuário também foi marcado como ativo e desbloqueado.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
