import pytest

import db as db_module
import demo_seed


def test_demo_seed_cria_banco_ficticio(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "demo.db")
    demo_seed.main()
    conn = db_module.get_db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM funcionarios").fetchone()[0] == len(demo_seed.FUNCIONARIOS)
        assert conn.execute("SELECT COUNT(*) FROM registros").fetchone()[0] > 0
    finally:
        conn.close()


def test_demo_seed_recusa_banco_existente(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "demo.db")
    db_module.init_db()
    with pytest.raises(SystemExit):
        demo_seed.main()
