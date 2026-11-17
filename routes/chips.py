# -*- coding: utf-8 -*-
import re
import time
import unicodedata
import uuid
from datetime import datetime, timezone

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import text

from utils.postgres_client import PostgresClient
from utils.sanitizer import sanitize_df

chips_bp = Blueprint("chips", __name__)
bq = PostgresClient()


def only_digits(value): return re.sub(r"\D+", "", str(value or ""))
def clean_text(value, default=None):
    if value is None: return default
    value = str(value).strip()
    return value if value else default

def to_int(value, default=None):
    try: return default if value in (None, "") else int(float(value))
    except Exception: return default

def to_float(value, default=None):
    try: return default if value in (None, "") else float(str(value).replace(",", "."))
    except Exception: return default

def normalize_status(value):
    normalized = unicodedata.normalize("NFD", str(value or ""))
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", normalized.lower().replace("_", " ")).strip()

def is_status_maturando(value): return normalize_status(value) in {"maturando", "em maturacao", "maturacao"}

def maturando_em_assignment(new_status, previous_status=None):
    if is_status_maturando(new_status):
        return "maturando_em=CURRENT_TIMESTAMP" if not is_status_maturando(previous_status) else "maturando_em=COALESCE(maturando_em, CURRENT_TIMESTAMP)"
    return "maturando_em=NULL"

def maturacao_concluida(value):
    if not value: return False
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() >= 604800
    except Exception: return False

def get_table_columns(table_name):
    try:
        df = bq.run_df("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = :schema AND table_name = :table_name
        """, {"schema": bq.schema, "table_name": table_name})
        return {str(row["column_name"]) for _, row in df.iterrows()}
    except Exception as exc:
        print(f"[Chips] Erro ao ler schema de {table_name}: {exc}"); return set()

def ensure_maturando_em_column():
    if "maturando_em" in get_table_columns("dim_chip"): return True
    try:
        bq.run(f"ALTER TABLE {bq.table('dim_chip')} ADD COLUMN IF NOT EXISTS maturando_em TIMESTAMPTZ")
        return True
    except Exception as exc:
        print(f"[Chips] Aviso: maturando_em não foi criado automaticamente: {exc}"); return False

def fetch_one(sql, params=None):
    df = bq.run_df(sql, params=params)
    return None if df.empty else sanitize_df(df).iloc[0].to_dict()

def chip_by_clean_number(numero_limpo, exclude_sk=None):
    params = {"numero_limpo": numero_limpo}
    extra = ""
    if exclude_sk:
        extra = " AND sk_chip <> :exclude_sk"; params["exclude_sk"] = int(exclude_sk)
    return fetch_one(f"""
        SELECT sk_chip, id_chip, numero, status FROM {bq.table('dim_chip')}
        WHERE regexp_replace(COALESCE(numero,''), '[^0-9]', '', 'g') = :numero_limpo AND COALESCE(ativo, TRUE) = TRUE {extra}
        LIMIT 1
    """, params)

def insert_event(sk_chip, tipo_evento, observacao, origem="Painel", categoria=None, campo=None, valor_antigo=None, valor_novo=None):
    try: bq.insert_event(sk_chip, tipo_evento, observacao, origem, categoria, campo, valor_antigo, valor_novo)
    except Exception as exc: print(f"⚠️ Evento não registrado ({tipo_evento}) para sk_chip={sk_chip}: {exc}")

def _vincular_chip(sk_chip, sk_aparelho, slot, tipo_whatsapp=None):
    if not slot: raise ValueError("Slot WhatsApp é obrigatório para vincular aparelho.")
    dup = fetch_one(f"""SELECT sk_chip FROM {bq.table('dim_chip')} WHERE sk_aparelho_atual=:ap AND slot_whatsapp=:slot AND sk_chip<>:sk AND COALESCE(ativo,TRUE)=TRUE LIMIT 1""", {"ap": sk_aparelho, "slot": slot, "sk": sk_chip})
    if dup: raise ValueError("Este slot já está ocupado no aparelho selecionado.")
    bq.run(f"""UPDATE {bq.table('dim_chip')} SET sk_aparelho_atual=:ap, slot_whatsapp=:slot, tipo_whatsapp=:tipo, updated_at=CURRENT_TIMESTAMP WHERE sk_chip=:sk""", {"ap": sk_aparelho, "slot": slot, "tipo": tipo_whatsapp, "sk": sk_chip})
    insert_event(sk_chip, "VINCULO_APARELHO", f"Vinculado ao aparelho {sk_aparelho}, slot {slot}")


@chips_bp.route("/chips")
def chips_list():
    started = time.perf_counter()
    try:
        page = max(to_int(request.args.get("page"), 1), 1); per_page = min(max(to_int(request.args.get("per_page"), 50), 10), 100); offset = (page - 1) * per_page
        filters = {k: clean_text(request.args.get(k)) for k in ["q","status","operadora","plano","operador","responsavel","aparelho","tipo_whatsapp","ativo","quick","created_from","created_to","updated_from","updated_to"]}
        ensure_maturando_em_column(); where, params = ["1=1"], {}
        q = filters.get("q")
        if q:
            params["q"] = f"%{q.lower()}%"; parts = ["LOWER(COALESCE(numero,'')) LIKE :q", "LOWER(COALESCE(id_chip,'')) LIKE :q", "LOWER(COALESCE(operador,'')) LIKE :q"]
            digits = only_digits(q)
            if digits: params["q_digits"] = f"%{digits}%"; parts.append("regexp_replace(COALESCE(numero,''), '[^0-9]', '', 'g') LIKE :q_digits")
            where.append(f"({' OR '.join(parts)})")
        for name, col in {"status":"status","operadora":"operadora","plano":"plano","operador":"operador","responsavel":"operador","tipo_whatsapp":"tipo_whatsapp"}.items():
            if filters.get(name): where.append(f"LOWER(COALESCE({col},'')) = :{name}"); params[name] = filters[name].lower()
        if filters.get("aparelho"): where.append("sk_aparelho_atual::text = :aparelho"); params["aparelho"] = filters["aparelho"]
        if filters.get("ativo") in ["true", "false"]: where.append("COALESCE(ativo, TRUE) = :ativo"); params["ativo"] = filters["ativo"] == "true"
        for key, col, op in [("created_from","created_at",">="),("created_to","created_at","<="),("updated_from","updated_at",">="),("updated_to","updated_at","<=")]:
            if filters.get(key): where.append(f"{col}::date {op} :{key}"); params[key] = filters[key]
        quick = filters.get("quick")
        if quick == "sem_aparelho": where.append("sk_aparelho_atual IS NULL")
        if quick == "banidos": where.append("UPPER(COALESCE(status,'')) = 'BANIDO'")
        if quick == "recarga": where.append("(ultima_recarga_data IS NULL OR ultima_recarga_data < CURRENT_DATE - INTERVAL '30 days')")
        if quick == "maturando": where.append("LOWER(status) IN ('maturando','em maturacao','maturacao')")
        if quick == "maturacao_concluida": where.append("LOWER(status) IN ('maturando','em maturacao','maturacao') AND maturando_em IS NOT NULL AND EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - maturando_em)) >= 604800")
        params.update({"limit": per_page, "offset": offset})
        chips_df = sanitize_df(bq.run_df(f"""SELECT *, COUNT(*) OVER() AS total_count FROM {bq.table('vw_chips_painel')} WHERE {' AND '.join(where)} ORDER BY COALESCE(updated_at, created_at, TIMESTAMP '1970-01-01') DESC, sk_chip DESC LIMIT :limit OFFSET :offset""", params))
        total = int(chips_df["total_count"].iloc[0]) if not chips_df.empty and "total_count" in chips_df else 0
        chips_records = chips_df.drop(columns=["total_count"], errors="ignore").to_dict("records")
        aparelhos = sanitize_df(bq.run_df(f"SELECT sk_aparelho, marca, modelo FROM {bq.table('dim_aparelho')} ORDER BY marca, modelo LIMIT 500")).to_dict("records")
        stats = {"total": total, "ativos": sum(1 for c in chips_records if c.get("ativo") is True or str(c.get("status") or "").upper()=="ATIVO"), "inativos": sum(1 for c in chips_records if c.get("ativo") is False or str(c.get("status") or "").upper()=="INATIVO"), "banidos": sum(1 for c in chips_records if str(c.get("status") or "").upper()=="BANIDO"), "disponiveis": sum(1 for c in chips_records if str(c.get("status") or "").upper() in ["DISPONIVEL","DISPONÍVEL"]), "em_uso": sum(1 for c in chips_records if str(c.get("status") or "").upper() in ["EM_USO","EM USO","ATIVO"]), "com_problema": sum(1 for c in chips_records if str(c.get("status") or "").upper() in ["BANIDO","BLOQUEADO","RESTRINGIDO","MANUTENCAO"]), "sem_aparelho": sum(1 for c in chips_records if not c.get("sk_aparelho_atual")), "vinculados": sum(1 for c in chips_records if c.get("sk_aparelho_atual")), "precisam_recarga": sum(1 for c in chips_records if not c.get("ultima_recarga_data")), "maturando": sum(1 for c in chips_records if is_status_maturando(c.get("status"))), "maturacao_concluida": sum(1 for c in chips_records if is_status_maturando(c.get("status")) and maturacao_concluida(c.get("maturando_em"))), "total_gasto": sum(float(c.get("total_gasto") or 0) for c in chips_records)}
        print(f"[Chips] Registros retornados={len(chips_records)} total={total} tempo_ms={round((time.perf_counter()-started)*1000,1)}")
        return render_template("chips.html", chips=chips_records, aparelhos=aparelhos, page=page, per_page=per_page, total=total, filters=filters, stats=stats, loading=False)
    except Exception as e:
        print(f"[Chips] Erro ao carregar chips: {e}")
        return render_template("chips.html", chips=[], aparelhos=[], page=1, per_page=50, total=0, filters={}, stats={}, error="Erro ao carregar chips.", loading=False), 200

@chips_bp.route("/chips/add", methods=["POST"])
def chips_add():
    data = request.form.to_dict(); numero_limpo = only_digits(data.get("numero")); operadora = clean_text(data.get("operadora"))
    if not numero_limpo or not operadora: flash("Número e operadora são obrigatórios.", "error"); return redirect(url_for("chips.chips_list"))
    try:
        if chip_by_clean_number(numero_limpo): flash("Chip já cadastrado para este número.", "error"); return redirect(url_for("chips.chips_list"))
        sk_chip = bq.next_id("dim_chip", "sk_chip"); status = clean_text(data.get("status"), "DISPONIVEL")
        bq.run(f"""INSERT INTO {bq.table('dim_chip')} (sk_chip,id_chip,numero,operadora,plano,status,observacao,operador,tipo_whatsapp,slot_whatsapp,qt_disparos,qt_banimentos,dt_banimentos,ativo,total_gasto,maturando_em,created_at,updated_at) VALUES (:sk_chip,:id_chip,:numero,:operadora,:plano,:status,:observacao,:operador,:tipo_whatsapp,:slot_whatsapp,:qt_disparos,:qt_banimentos,:dt_banimentos,TRUE,0,CASE WHEN LOWER(:status) IN ('maturando','em maturacao','maturacao') THEN CURRENT_TIMESTAMP ELSE NULL END,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""", {"sk_chip": sk_chip, "id_chip": clean_text(data.get("id_chip"), f"CHIP-{uuid.uuid4().hex[:10].upper()}"), "numero": numero_limpo, "operadora": operadora, "plano": clean_text(data.get("plano"), ""), "status": status, "observacao": clean_text(data.get("observacao"), ""), "operador": clean_text(data.get("operador")), "tipo_whatsapp": clean_text(data.get("tipo_whatsapp")), "slot_whatsapp": to_int(data.get("slot_whatsapp")), "qt_disparos": to_int(data.get("qt_disparos"),0), "qt_banimentos": to_int(data.get("qt_banimentos"),0), "dt_banimentos": clean_text(data.get("dt_banimentos"))})
        if to_int(data.get("sk_aparelho_atual")): _vincular_chip(sk_chip, to_int(data.get("sk_aparelho_atual")), to_int(data.get("slot_whatsapp")), clean_text(data.get("tipo_whatsapp")))
        insert_event(sk_chip, "CADASTRO", "Cadastro realizado pelo painel"); flash(f"Chip cadastrado com sucesso (SK {sk_chip}).", "success")
    except Exception as e: print(f"🚨 Erro ao cadastrar chip: {e}"); flash(f"Erro ao cadastrar chip: {e}", "error")
    return redirect(url_for("chips.chips_list"))

@chips_bp.route("/chips/sk/<int:sk_chip>")
def chips_get_by_sk(sk_chip):
    row = fetch_one(f"SELECT * FROM {bq.table('vw_chips_painel')} WHERE sk_chip=:sk LIMIT 1", {"sk": sk_chip})
    return (jsonify(row), 200) if row else (jsonify({"error":"Chip não encontrado"}),404)

@chips_bp.route("/chips/update-json", methods=["POST"])
def chips_update_json():
    try:
        p = request.json or {}; sk = to_int(p.get("sk_chip"))
        if not sk: return jsonify({"error":"sk_chip ausente"}),400
        atual = fetch_one(f"SELECT * FROM {bq.table('dim_chip')} WHERE sk_chip=:sk LIMIT 1", {"sk": sk})
        if not atual: return jsonify({"error":"Chip não encontrado"}),404
        numero_limpo = only_digits(p.get("numero"));
        if not numero_limpo: return jsonify({"error":"Número obrigatório"}),400
        if chip_by_clean_number(numero_limpo, exclude_sk=sk): return jsonify({"error":"Já existe chip ativo com este número."}),409
        status_novo = clean_text(p.get("status"), atual.get("status") or "DISPONIVEL")
        data_status_sql = "data_status=CURRENT_DATE," if status_novo != atual.get("status") else "data_status=:data_status,"
        bq.run(f"""UPDATE {bq.table('dim_chip')} SET numero=:numero, operadora=:operadora, plano=:plano, status=:status, operador=:operador, observacao=:observacao, tipo_whatsapp=:tipo_whatsapp, slot_whatsapp=:slot_whatsapp, qt_disparos=:qt_disparos, qt_banimentos=:qt_banimentos, dt_banimentos=:dt_banimentos, {data_status_sql} {maturando_em_assignment(status_novo, atual.get('status'))}, updated_at=CURRENT_TIMESTAMP WHERE sk_chip=:sk""", {"numero": numero_limpo, "operadora": clean_text(p.get("operadora")), "plano": clean_text(p.get("plano")), "status": status_novo, "operador": clean_text(p.get("operador")), "observacao": clean_text(p.get("observacao")), "tipo_whatsapp": clean_text(p.get("tipo_whatsapp")), "slot_whatsapp": to_int(p.get("slot_whatsapp")), "qt_disparos": to_int(p.get("qt_disparos"),0), "qt_banimentos": to_int(p.get("qt_banimentos"),0), "dt_banimentos": clean_text(p.get("dt_banimentos")), "data_status": clean_text(p.get("data_status") or p.get("dt_inicio")), "sk": sk})
        if "sk_aparelho_atual" in p:
            ap = to_int(p.get("sk_aparelho_atual"))
            if ap: _vincular_chip(sk, ap, to_int(p.get("slot_whatsapp")), clean_text(p.get("tipo_whatsapp")))
            else: bq.run(f"UPDATE {bq.table('dim_chip')} SET sk_aparelho_atual=NULL, slot_whatsapp=NULL, tipo_whatsapp=NULL, updated_at=CURRENT_TIMESTAMP WHERE sk_chip=:sk", {"sk": sk}); insert_event(sk, "DESVINCULO_APARELHO", "Desvinculado pelo painel")
        insert_event(sk, "EDICAO", "Chip editado pelo painel"); return jsonify({"success": True})
    except Exception as e: print("🚨 Erro ao editar chip:", e); return jsonify({"error": str(e)}),500

@chips_bp.route("/chips/recarga", methods=["POST"])
def chips_recarga():
    try:
        p = request.json or {}; sk = to_int(p.get("sk_chip")); valor = to_float(p.get("valor"))
        if not sk or valor is None: return jsonify({"error":"sk_chip e valor obrigatórios"}),400
        bq.run(f"""UPDATE {bq.table('dim_chip')} SET ultima_recarga_valor=:valor, ultima_recarga_data=CURRENT_DATE, total_gasto=COALESCE(total_gasto,0)+:valor, updated_at=CURRENT_TIMESTAMP WHERE sk_chip=:sk""", {"valor": valor, "sk": sk})
        insert_event(sk, "RECARGA", f"Recarga de R$ {valor:.2f}", categoria="RECARGA"); return jsonify({"success": True})
    except Exception as e: print("🚨 Erro ao recarregar chip:", e); return jsonify({"error": str(e)}),500

@chips_bp.route("/chips/banir", methods=["POST"])
def chips_banir():
    try:
        sk = to_int((request.json or {}).get("sk_chip"))
        if not sk: return jsonify({"error":"sk_chip obrigatório"}),400
        bq.run(f"UPDATE {bq.table('dim_chip')} SET status='BANIDO', maturando_em=NULL, qt_banimentos=COALESCE(qt_banimentos,0)+1, dt_banimentos=CURRENT_DATE, data_status=CURRENT_DATE, updated_at=CURRENT_TIMESTAMP WHERE sk_chip=:sk", {"sk": sk})
        insert_event(sk, "BANIMENTO", "Banimento registrado pelo painel"); return jsonify({"success": True})
    except Exception as e: print("🚨 Erro ao banir chip:", e); return jsonify({"error":str(e)}),500

@chips_bp.route("/chips/timeline/<int:sk_chip>")
def chips_timeline(sk_chip):
    try:
        df = bq.run_df(f"SELECT sk_chip, tipo_evento, origem, observacao, data_evento FROM {bq.table('vw_chip_timeline')} WHERE sk_chip=:sk ORDER BY data_evento DESC LIMIT 100", {"sk": sk_chip})
        return jsonify(sanitize_df(df).to_dict("records"))
    except Exception as e: print("🚨 Erro timeline:", e); return jsonify([]),500

@chips_bp.route("/admin/diagnostico")
def diagnostico():
    queries = {"dim_chip existe": f"SELECT 1 FROM {bq.table('dim_chip')} LIMIT 1", "vw_chips_painel existe": f"SELECT 1 FROM {bq.table('vw_chips_painel')} LIMIT 1", "chips sem sk_chip": f"SELECT COUNT(*) qtd FROM {bq.table('dim_chip')} WHERE sk_chip IS NULL", "números duplicados ativos": f"SELECT COUNT(*) qtd FROM (SELECT regexp_replace(numero, '[^0-9]', '', 'g') n FROM {bq.table('dim_chip')} WHERE COALESCE(ativo,TRUE) GROUP BY n HAVING COUNT(*)>1) d", "vínculo inválido": f"SELECT COUNT(*) qtd FROM {bq.table('dim_chip')} c LEFT JOIN {bq.table('dim_aparelho')} a ON c.sk_aparelho_atual=a.sk_aparelho WHERE c.sk_aparelho_atual IS NOT NULL AND a.sk_aparelho IS NULL", "slots duplicados": f"SELECT COUNT(*) qtd FROM (SELECT sk_aparelho_atual, slot_whatsapp FROM {bq.table('dim_chip')} WHERE sk_aparelho_atual IS NOT NULL AND slot_whatsapp IS NOT NULL GROUP BY 1,2 HAVING COUNT(*)>1) d"}
    checks = []
    for nome, sql in queries.items():
        try: checks.append({"check": nome, "ok": True, "resultado": fetch_one(sql)})
        except Exception as e: checks.append({"check": nome, "ok": False, "erro": str(e)})
    return jsonify(checks)
