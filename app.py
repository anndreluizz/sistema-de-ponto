"""Ponto de entrada do sistema de ponto (python app.py, ou waitress app:app).

O app e o que é comum a tudo ficam em nucleo.py; as telas, em rotas/ (uma área por arquivo).
"""

from nucleo import app
import rotas  # noqa: F401  (registra as rotas no app)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
