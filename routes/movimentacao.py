# -*- coding: utf-8 -*-
from flask import Blueprint, jsonify, render_template, request

from utils.postgres_client import PostgresClient
from utils.sanitizer import sanitize_df

mov_bp = Blueprint("movimentacao", __name__)
bq = PostgresClient()


@mov_bp.route("/movimentacao")
def movimentacao_home():
    try:
        chips_df = sanitize_df(bq.get_view("vw_chips_painel"))
        return render_template("movimentacao.html", chips=chips_df.to_dict(orient="records"))
    except Exception as e:
        print("🚨 Erro ao carregar página de movimentação:", e)
        return "Erro ao carregar movimentação", 500


@mov_bp.route("/movimentacao/buscar")
def buscar_chip():
    try:
        termo = request.args.get("q", "").strip().lower()
        if len(termo) < 2:
            return jsonify([])
        df = sanitize_df(bq.get_view("vw_chips_painel"))
        filtrado = df[df["numero"].astype(str).str.contains(termo, case=False, na=False)]
        return jsonify(filtrado[["sk_chip", "numero", "operadora"]].drop_duplicates().to_dict(orient="records"))
    except Exception as e:
        print("🚨 Erro no autocomplete:", e)
        return jsonify([]), 500


@mov_bp.route("/movimentacao/historico/<int:sk_chip>")
def historico_chip(sk_chip):
    try:
        df = sanitize_df(bq.run_df(f"""
            SELECT sk_chip, categoria, tipo, valor_antigo, valor_novo, origem, observacao, data_evento, data_fmt
            FROM {bq.table('vw_chip_timeline')}
            WHERE sk_chip = :sk_chip AND origem = :origem
            ORDER BY data_evento DESC
        """, {"sk_chip": sk_chip, "origem": "Painel"}))
        return jsonify(df.to_dict(orient="records"))
    except Exception as e:
        print("🚨 Erro ao buscar histórico:", e)
        return jsonify([]), 500
