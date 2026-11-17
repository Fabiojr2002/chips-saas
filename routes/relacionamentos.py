# -*- coding: utf-8 -*-
import re
from flask import Blueprint, jsonify, render_template, request
from sqlalchemy import text

from utils.postgres_client import PostgresClient
from utils.sanitizer import sanitize_df

relacionamentos_bp = Blueprint("relacionamentos", __name__)
bq = PostgresClient()


def to_int(v):
    try:
        return None if v is None or str(v).strip().lower() in ["", "none", "null", "nan"] else int(float(v))
    except Exception:
        return None


def is_null(v):
    return v is None or str(v).strip().lower() in ["", "none", "null", "nan"]


def sort_key_aparelho(a):
    texto = f"{a.get('modelo') or ''} {a.get('marca') or ''}"
    m = re.search(r'\((\d+)\)', texto)
    if m:
        return (0, int(m.group(1)), texto.lower())
    m2 = re.search(r'\b(\d+)\b', texto)
    if m2:
        return (0, int(m2.group(1)), texto.lower())
    return (1, 999999, texto.lower())


def insert_event(conn, sk_chip, tipo, obs):
    conn.execute(text(f"""
        INSERT INTO {bq.table('f_chip_evento')}
        (sk_evento, sk_chip, data_evento, tipo_evento, origem, observacao, created_at, categoria)
        VALUES ((SELECT COALESCE(MAX(sk_evento),0)+1 FROM {bq.table('f_chip_evento')}), :sk_chip,
                CURRENT_TIMESTAMP, :tipo, 'Painel', :obs, CURRENT_TIMESTAMP, 'APARELHO')
    """), {"sk_chip": sk_chip, "tipo": tipo, "obs": obs})


@relacionamentos_bp.route("/relacionamentos")
def relacionamentos_home():
    try:
        df = sanitize_df(bq.run_df(f"SELECT * FROM {bq.table('vw_relacionamentos_whatsapp')}"))
        chips_livres_df = bq.run_df(f"""
            SELECT sk_chip, numero, operadora, tipo_whatsapp
            FROM {bq.table('dim_chip')}
            WHERE ativo = TRUE AND sk_aparelho_atual IS NULL
            ORDER BY numero
        """)
        chips_livres = [{"sk_chip": to_int(r["sk_chip"]), "numero": r["numero"], "operadora": r["operadora"], "tipo_whatsapp": r.get("tipo_whatsapp") or "A DEFINIR"} for _, r in chips_livres_df.iterrows()] if not chips_livres_df.empty else []
        if df.empty:
            return render_template("relacionamentos.html", aparelhos=[], chips_livres=chips_livres)
        aparelhos = []
        for sk_aparelho, g in df.groupby("sk_aparelho"):
            sk_aparelho = to_int(sk_aparelho)
            if not sk_aparelho:
                continue
            cap_bus = to_int(g["cap_whats_business"].iloc[0]) if "cap_whats_business" in g.columns else 0
            cap_norm = to_int(g["cap_whats_normal"].iloc[0]) if "cap_whats_normal" in g.columns else 0
            slots = {i: None for i in range(1, cap_bus + cap_norm + 1)}
            for _, r in g.iterrows():
                sk_chip, slot = to_int(r.get("sk_chip")), to_int(r.get("slot_whatsapp"))
                if sk_chip and slot in slots:
                    tipo = r.get("tipo_whatsapp")
                    slots[slot] = {"sk_chip": sk_chip, "numero": r.get("numero"), "operadora": r.get("operadora"), "tipo_whatsapp": ("BUSINESS" if slot <= cap_bus else "NORMAL") if is_null(tipo) else tipo}
            aparelhos.append({"sk_aparelho": sk_aparelho, "marca": g["marca"].iloc[0], "modelo": g["modelo"].iloc[0], "capacidade_total": cap_bus + cap_norm, "cap_whats_business": cap_bus, "cap_whats_normal": cap_norm, "slots": [{"slot": s, "chip": slots[s]} for s in range(1, cap_bus + cap_norm + 1)]})
        aparelhos.sort(key=sort_key_aparelho)
        return render_template("relacionamentos.html", aparelhos=aparelhos, chips_livres=chips_livres)
    except Exception as e:
        print("🚨 ERRO RELACIONAMENTOS:", e)
        return "Erro ao carregar relacionamentos", 500


@relacionamentos_bp.route("/relacionamentos/vincular", methods=["POST"])
def relacionamentos_vincular():
    try:
        data = request.get_json(force=True) or {}
        sk_chip, sk_aparelho, slot = to_int(data.get("sk_chip")), to_int(data.get("sk_aparelho")), to_int(data.get("slot"))
        tipo = data.get("tipo") or data.get("tipo_whatsapp") or "NORMAL"
        if not sk_chip or not sk_aparelho or not slot:
            return jsonify({"ok": False, "error": "Dados inválidos"}), 400
        with bq.engine.begin() as conn:
            ocupado = conn.execute(text(f"""
                SELECT sk_chip FROM {bq.table('dim_chip')}
                WHERE sk_aparelho_atual=:sk_aparelho AND slot_whatsapp=:slot AND ativo=TRUE AND sk_chip<>:sk_chip
            """), {"sk_aparelho": sk_aparelho, "slot": slot, "sk_chip": sk_chip}).first()
            if ocupado:
                return jsonify({"ok": False, "error": "Slot já ocupado"}), 409
            conn.execute(text(f"""UPDATE {bq.table('dim_chip')} SET sk_aparelho_atual=:sk_aparelho, slot_whatsapp=:slot, tipo_whatsapp=:tipo, updated_at=CURRENT_TIMESTAMP WHERE sk_chip=:sk_chip"""), {"sk_aparelho": sk_aparelho, "slot": slot, "tipo": tipo, "sk_chip": sk_chip})
            conn.execute(text(f"""INSERT INTO {bq.table('f_chip_aparelho')} (sk_fato, sk_chip, sk_aparelho, slot_whatsapp, tipo_whatsapp, tipo_movimento, data_movimento, origem, observacao, created_at) VALUES ((SELECT COALESCE(MAX(sk_fato),0)+1 FROM {bq.table('f_chip_aparelho')}), :sk_chip, :sk_aparelho, :slot, :tipo, 'VINCULO', CURRENT_TIMESTAMP, 'Painel', 'Vínculo WhatsApp', CURRENT_TIMESTAMP)"""), {"sk_chip": sk_chip, "sk_aparelho": sk_aparelho, "slot": slot, "tipo": tipo})
            insert_event(conn, sk_chip, "VINCULO_APARELHO", f"Vinculado ao aparelho {sk_aparelho}, slot {slot}")
        return jsonify({"ok": True})
    except Exception as e:
        print("🚨 ERRO VINCULAR:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@relacionamentos_bp.route("/relacionamentos/desvincular", methods=["POST"])
def relacionamentos_desvincular():
    try:
        sk_chip = to_int((request.get_json(force=True) or {}).get("sk_chip"))
        if not sk_chip:
            return jsonify({"ok": False, "error": "sk_chip inválido"}), 400
        with bq.engine.begin() as conn:
            conn.execute(text(f"""UPDATE {bq.table('dim_chip')} SET sk_aparelho_atual=NULL, slot_whatsapp=NULL, tipo_whatsapp=NULL, updated_at=CURRENT_TIMESTAMP WHERE sk_chip=:sk_chip"""), {"sk_chip": sk_chip})
            conn.execute(text(f"""INSERT INTO {bq.table('f_chip_aparelho')} (sk_fato, sk_chip, tipo_movimento, data_movimento, origem, observacao, created_at) VALUES ((SELECT COALESCE(MAX(sk_fato),0)+1 FROM {bq.table('f_chip_aparelho')}), :sk_chip, 'DESVINCULO', CURRENT_TIMESTAMP, 'Painel', 'Desvinculação de aparelho', CURRENT_TIMESTAMP)"""), {"sk_chip": sk_chip})
            insert_event(conn, sk_chip, "DESVINCULO_APARELHO", "Desvinculação de aparelho")
        return jsonify({"ok": True})
    except Exception as e:
        print("🚨 ERRO DESVINCULAR:", e)
        return jsonify({"ok": False, "error": str(e)}), 500
