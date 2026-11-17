# -*- coding: utf-8 -*-
import os
from flask import Flask, request

# Blueprints
from routes.aparelhos import aparelhos_bp
from routes.chips import chips_bp
from routes.chips_crud import chips_crud_bp
from routes.recargas import recargas_bp
from routes.relacionamentos import relacionamentos_bp
from routes.movimentacao import mov_bp
from routes.dashboard import bp_dashboard


# ================================
# CONFIG
# ================================
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


PORT = _env_int("PORT", 8000)


def create_app() -> Flask:
    app = Flask(__name__)

    # Segurança mínima (evita erro em session/flash)
    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-me")

    # ================================
    # ROTAS DE SAÚDE
    # ================================
    @app.get("/health")
    def health():
        from utils.postgres_client import PostgresClient
        PostgresClient().run_df("SELECT 1")
        return {"ok": True, "db": "connected"}, 200

    # ================================
    # BLUEPRINTS
    # ================================
    app.register_blueprint(bp_dashboard)
    app.register_blueprint(aparelhos_bp)
    app.register_blueprint(chips_bp)
    app.register_blueprint(chips_crud_bp)
    app.register_blueprint(recargas_bp)
    app.register_blueprint(relacionamentos_bp)
    app.register_blueprint(mov_bp)

    # Carrega o aprimoramento de CRUD somente na tela de chips.
    # A injeção evita duplicar o template grande e mantém compatibilidade
    # com a interface atual.
    @app.after_request
    def inject_chips_crud_script(response):
        if request.path == "/chips" and response.content_type.startswith("text/html"):
            html = response.get_data(as_text=True)
            script = '<script src="/static/js/chips_crud.js"></script>'
            if script not in html:
                html = html.replace("</body>", f"{script}</body>")
                response.set_data(html)
                response.headers["Content-Length"] = len(response.get_data())
        return response

    return app


app = create_app()


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "0").strip() == "1"
    app.run(host="0.0.0.0", port=PORT, debug=debug)
