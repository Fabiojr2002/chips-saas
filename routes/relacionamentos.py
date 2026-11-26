# -*- coding: utf-8 -*-
import re
from flask import Blueprint, jsonify, render_template, request
from sqlalchemy import text

from routes.chips import ensure_maturando_em_column, maturando_em_assignment
from utils.postgres_client import PostgresClient
from utils.sanitizer import sanitize_df

relacionamentos_bp = Blueprint("relacionamentos", __name__)
bq = PostgresClient()

STATUS_OPCOES = [
    "DISPARANDO",
    "MATURANDO",
    "BANIDO",
    "SEM SALDO",
    "ATIVO",
    "DISPONIVEL",
    "MATURADO",
    "DESCANSO_1",
    "DESCANSO_2",
    "PRONTO_PARA_MATURAR",
    "EM_USO",
    "RESTRINGIDO",
    "BLOQUEADO",
    "MANUTENCAO",
    "INATIVO",
]


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
        chips_ativos_df = sanitize_df(bq.run_df(f"""
            SELECT sk_chip, numero, operadora, tipo_whatsapp, status, sk_aparelho_atual, slot_whatsapp
            FROM {bq.table('dim_chip')}
            WHERE COALESCE(ativo, TRUE) = TRUE
        """))

        linked_by_aparelho = {}
        chips_livres = []
        if not chips_ativos_df.empty:
            for _, r in chips_ativos_df.iterrows():
                sk = to_int(r.get("sk_chip"))
                if not sk:
                    continue
                ap_atual = to_int(r.get("sk_aparelho_atual"))
                st = r.get("status")
                status_clean = str(st).strip() if not is_null(st) else "DISPONIVEL"
                if ap_atual is None:
                    chips_livres.append({
                        "sk_chip": sk,
                        "numero": r.get("numero"),
                        "operadora": r.get("operadora"),
                        "tipo_whatsapp": r.get("tipo_whatsapp") or "A DEFINIR",
                        "status": status_clean,
                    })
                else:
                    linked_by_aparelho.setdefault(ap_atual, []).append({
                        "sk_chip": sk,
                        "numero": r.get("numero"),
                        "operadora": r.get("operadora"),
                        "tipo_whatsapp": r.get("tipo_whatsapp"),
                        "status": status_clean,
                        "slot_whatsapp": to_int(r.get("slot_whatsapp")),
                    })

        chips_livres.sort(key=lambda c: str(c.get("numero") or ""))
        if df.empty:
            return render_template("relacionamentos.html", aparelhos=[], chips_livres=chips_livres, status_opcoes=STATUS_OPCOES)
        aparelhos = []
        for sk_aparelho, g in df.groupby("sk_aparelho"):
            sk_aparelho = to_int(sk_aparelho)
            if not sk_aparelho:
                continue
            cap_bus = to_int(g["cap_whats_business"].iloc[0]) if "cap_whats_business" in g.columns else 0
            cap_norm = to_int(g["cap_whats_normal"].iloc[0]) if "cap_whats_normal" in g.columns else 0
            cap_total = cap_bus + cap_norm
            slots = {i: None for i in range(1, cap_total + 1)}
            extra_slots = []

            for ch in linked_by_aparelho.get(sk_aparelho, []):
                slot = ch.get("slot_whatsapp")
                tipo = ch.get("tipo_whatsapp")
                tipo_norm = ("BUSINESS" if (slot is not None and slot <= cap_bus) else "NORMAL") if is_null(tipo) else str(tipo).strip().upper()
                if tipo_norm not in ("BUSINESS", "NORMAL"):
                    tipo_norm = "BUSINESS" if (slot is not None and slot <= cap_bus) else "NORMAL"
                chip_obj = {
                    "sk_chip": ch["sk_chip"],
                    "numero": ch["numero"],
                    "operadora": ch["operadora"],
                    "tipo_whatsapp": tipo_norm,
                    "status": ch["status"],
                }
                if slot in slots and slots[slot] is None:
                    slots[slot] = chip_obj
                else:
                    extra_slots.append({
                        "slot": slot or 0,
                        "chip": chip_obj,
                        "extra": True,
                        "motivo": f"Slot {slot} (fora da capacidade)" if (slot and slot > cap_total) else ("Slot duplicado" if slot in slots else "Sem slot definido"),
                    })

            lista_slots = [{"slot": s, "chip": slots[s], "extra": False, "motivo": None} for s in range(1, cap_total + 1)]
            lista_slots.extend(extra_slots)

            aparelhos.append({
                "sk_aparelho": sk_aparelho,
                "marca": g["marca"].iloc[0],
                "modelo": g["modelo"].iloc[0],
                "capacidade_total": cap_total,
                "cap_whats_business": cap_bus,
                "cap_whats_normal": cap_norm,
                "slots": lista_slots,
            })
        aparelhos.sort(key=sort_key_aparelho)
        return render_template("relacionamentos.html", aparelhos=aparelhos, chips_livres=chips_livres, status_opcoes=STATUS_OPCOES)
    except Exception as e:
        print("🚨 ERRO RELACIONAMENTOS:", e)
        return "Erro ao carregar relacionamentos", 500


@relacionamentos_bp.route("/relacionamentos/vincular", methods=["POST"])
def relacionamentos_vincular():
    try:
        data = request.get_json(force=True) or {}
        sk_chip, sk_aparelho, slot = to_int(data.get("sk_chip")), to_int(data.get("sk_aparelho")), to_int(data.get("slot"))
        tipo_informado = str(data.get("tipo") or data.get("tipo_whatsapp") or "").strip().upper()
        if not sk_chip or not sk_aparelho or not slot:
            return jsonify({"ok": False, "error": "Dados inválidos"}), 400
        if tipo_informado not in ("BUSINESS", "NORMAL"):
            cap_row = bq.run_df(f"""
                SELECT cap_whats_business
                FROM {bq.table('vw_relacionamentos_whatsapp')}
                WHERE sk_aparelho = :sk_aparelho
                LIMIT 1
            """, {"sk_aparelho": sk_aparelho})
            cap_bus = to_int(cap_row["cap_whats_business"].iloc[0]) if (not cap_row.empty and "cap_whats_business" in cap_row.columns) else 0
            tipo = "BUSINESS" if (cap_bus and slot <= cap_bus) else "NORMAL"
        else:
            tipo = tipo_informado
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


@relacionamentos_bp.route("/relacionamentos/atualizar-status", methods=["POST"])
def relacionamentos_atualizar_status():
    try:
        data = request.get_json(force=True) or {}
        sk_chip = to_int(data.get("sk_chip"))
        novo_status = str(data.get("status") or "").strip()
        if not sk_chip or not novo_status:
            return jsonify({"ok": False, "error": "sk_chip e status são obrigatórios"}), 400

        has_maturando = ensure_maturando_em_column()
        with bq.engine.begin() as conn:
            atual = conn.execute(
                text(f"SELECT sk_chip, status FROM {bq.table('dim_chip')} WHERE sk_chip = :sk_chip LIMIT 1"),
                {"sk_chip": sk_chip},
            ).mappings().first()
            if not atual:
                return jsonify({"ok": False, "error": "Chip não encontrado"}), 404

            status_anterior = atual.get("status")
            extra_sql = f", {maturando_em_assignment(novo_status, status_anterior)}" if has_maturando else ""
            data_status_sql = ", data_status = CURRENT_DATE" if novo_status != status_anterior else ""

            conn.execute(
                text(f"""
                    UPDATE {bq.table('dim_chip')}
                    SET status = :status{data_status_sql}{extra_sql}, updated_at = CURRENT_TIMESTAMP
                    WHERE sk_chip = :sk_chip
                """),
                {"status": novo_status, "sk_chip": sk_chip},
            )
            insert_event(
                conn,
                sk_chip,
                "ALTERACAO_STATUS",
                f"Status alterado de {status_anterior or 'N/A'} para {novo_status} (Relacionamentos)",
            )

        return jsonify({"ok": True, "sk_chip": sk_chip, "status": novo_status})
    except Exception as e:
        print("🚨 ERRO ATUALIZAR STATUS:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@relacionamentos_bp.route("/relacionamentos/atualizar-tipo-whatsapp", methods=["POST"])
def relacionamentos_atualizar_tipo_whatsapp():
    try:
        data = request.get_json(force=True) or {}
        sk_chip = to_int(data.get("sk_chip"))
        tipo_informado = str(data.get("tipo_whatsapp") or data.get("tipo") or "").strip().upper()
        if not sk_chip:
            return jsonify({"ok": False, "error": "sk_chip inválido"}), 400
        if tipo_informado and tipo_informado not in ("BUSINESS", "NORMAL"):
            return jsonify({"ok": False, "error": "tipo_whatsapp deve ser BUSINESS ou NORMAL"}), 400

        with bq.engine.begin() as conn:
            atual = conn.execute(
                text(f"SELECT sk_chip, tipo_whatsapp FROM {bq.table('dim_chip')} WHERE sk_chip = :sk_chip LIMIT 1"),
                {"sk_chip": sk_chip},
            ).mappings().first()
            if not atual:
                return jsonify({"ok": False, "error": "Chip não encontrado"}), 404

            tipo_anterior = str(atual.get("tipo_whatsapp") or "").strip().upper()
            novo_tipo = tipo_informado or ("NORMAL" if tipo_anterior == "BUSINESS" else "BUSINESS")

            conn.execute(
                text(f"""
                    UPDATE {bq.table('dim_chip')}
                    SET tipo_whatsapp = :tipo, updated_at = CURRENT_TIMESTAMP
                    WHERE sk_chip = :sk_chip
                """),
                {"tipo": novo_tipo, "sk_chip": sk_chip},
            )
            insert_event(
                conn,
                sk_chip,
                "ALTERACAO_TIPO_WHATSAPP",
                f"Tipo WhatsApp alterado de {tipo_anterior or 'N/A'} para {novo_tipo} (Relacionamentos)",
            )

        return jsonify({"ok": True, "sk_chip": sk_chip, "tipo_whatsapp": novo_tipo})
    except Exception as e:
        print("🚨 ERRO ATUALIZAR TIPO WHATSAPP:", e)
        return jsonify({"ok": False, "error": str(e)}), 500
