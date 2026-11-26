# routes/aparelhos.py
# -*- coding: utf-8 -*-

from flask import Blueprint, render_template, request, redirect
from utils.postgres_client import PostgresClient
from utils.sanitizer import sanitize_df

aparelhos_bp = Blueprint("aparelhos", __name__)
bq = PostgresClient()


# =======================================================
# LISTAGEM
# =======================================================
@aparelhos_bp.route("/aparelhos")
def aparelhos_list():
    try:
        df = bq.get_view("vw_aparelhos")
        df = sanitize_df(df)

        # 🔐 BLINDAGEM ABSOLUTA CONTRA Undefined
        colunas_padrao = {
            "id_aparelho": "",
            "modelo": "",
            "marca": "",
            "imei": "",
            "status": "DESCONHECIDO"
        }

        for col, default in colunas_padrao.items():
            if col not in df.columns:
                df[col] = default

        df = df.fillna(colunas_padrao)

        aparelhos = df.to_dict(orient="records")

        chips_df = sanitize_df(bq.run_df(f"""
            SELECT sk_chip, numero, status, sk_aparelho_atual, slot_whatsapp, tipo_whatsapp
            FROM {bq.table('dim_chip')}
            WHERE COALESCE(ativo, TRUE) = TRUE AND sk_aparelho_atual IS NOT NULL
            ORDER BY slot_whatsapp NULLS LAST, numero
        """))
        chips_por_aparelho = {}
        if not chips_df.empty:
            for _, r in chips_df.iterrows():
                try:
                    sk_ap = int(float(r.get("sk_aparelho_atual")))
                except Exception:
                    continue
                chips_por_aparelho.setdefault(sk_ap, []).append({
                    "sk_chip": int(float(r.get("sk_chip"))) if r.get("sk_chip") is not None else None,
                    "numero": r.get("numero") or "",
                    "status": r.get("status") or "",
                    "slot_whatsapp": int(float(r.get("slot_whatsapp"))) if r.get("slot_whatsapp") not in (None, "") else None,
                    "tipo_whatsapp": r.get("tipo_whatsapp") or "",
                })

        for ap in aparelhos:
            try:
                sk_ap = int(float(ap.get("sk_aparelho")))
            except Exception:
                sk_ap = None
            vinculados = chips_por_aparelho.get(sk_ap, []) if sk_ap is not None else []
            ap["chips_vinculados"] = vinculados
            ap["qtd_chips_vinculados"] = len(vinculados)

        return render_template(
            "aparelhos.html",
            aparelhos=aparelhos
        )

    except Exception as e:
        print("🚨 Erro ao carregar aparelhos:", e)
        return "Erro ao carregar aparelhos", 500


# =======================================================
# UPSERT (INSERT + UPDATE)
# =======================================================
@aparelhos_bp.route("/aparelhos/add", methods=["POST"])
def aparelhos_add():
    try:
        payload = {
            "id_aparelho": request.form.get("id_aparelho"),
            "modelo": request.form.get("modelo"),
            "marca": request.form.get("marca"),
            "imei": request.form.get("imei") or None,
            "status": request.form.get("status") or "ATIVO",
        }

        bq.upsert_aparelho(payload)

        return redirect("/aparelhos")

    except Exception as e:
        print("🚨 Erro ao salvar aparelho:", e)
        return "Erro ao salvar aparelho", 500
