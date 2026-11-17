from flask import Blueprint, jsonify, render_template, request

from utils.postgres_client import PostgresClient
from utils.sanitizer import sanitize_df

recargas_bp = Blueprint("recargas", __name__)
bq = PostgresClient()


@recargas_bp.route("/recargas")
def recargas_page():
    return render_template("recargas.html")


@recargas_bp.route("/api/chips/listar")
def listar_chips():
    df = sanitize_df(bq.get_view("vw_chips_painel"))
    cols = [c for c in ["sk_chip", "numero", "operadora"] if c in df.columns]
    return jsonify([] if not cols else df[cols].drop_duplicates().to_dict(orient="records"))


@recargas_bp.route("/api/recargas/listar")
def listar_recargas():
    df = sanitize_df(bq.run_df(f"""
        SELECT sk_chip, NULL::bigint AS id_recarga, numero, operadora,
               ultima_recarga_valor AS valor, ultima_recarga_data::text AS data,
               NULL::text AS obs
        FROM {bq.table('vw_chips_painel')}
        WHERE ultima_recarga_data IS NOT NULL
        ORDER BY ultima_recarga_data DESC
    """))
    return jsonify(df.to_dict(orient="records"))


@recargas_bp.route("/api/recargas/salvar", methods=["POST"])
def salvar_recarga():
    payload = request.json or {}
    sk_chip = payload.get("id_chip") or payload.get("sk_chip")
    valor = payload.get("valor")
    obs = payload.get("obs") or "Recarga via painel"
    if not sk_chip or valor in [None, ""]:
        return jsonify({"success": False, "error": "Chip e valor são obrigatórios"}), 400
    sk_chip, valor = int(sk_chip), float(valor)
    with bq.engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text(f"""
            UPDATE {bq.table('dim_chip')}
            SET ultima_recarga_valor=:valor, ultima_recarga_data=CURRENT_DATE,
                total_gasto=COALESCE(total_gasto,0)+:valor, updated_at=CURRENT_TIMESTAMP
            WHERE sk_chip=:sk_chip
        """), {"valor": valor, "sk_chip": sk_chip})
        conn.execute(text(f"""
            INSERT INTO {bq.table('f_chip_evento')}
            (sk_evento, sk_chip, data_evento, tipo_evento, origem, observacao, created_at, categoria)
            VALUES ((SELECT COALESCE(MAX(sk_evento),0)+1 FROM {bq.table('f_chip_evento')}), :sk_chip,
                    CURRENT_TIMESTAMP, 'RECARGA', 'Painel', :obs, CURRENT_TIMESTAMP, 'RECARGA')
        """), {"sk_chip": sk_chip, "obs": obs})
    return jsonify({"success": True})
