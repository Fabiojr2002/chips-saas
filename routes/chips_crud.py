# -*- coding: utf-8 -*-
import re
from datetime import datetime

from flask import Blueprint, jsonify, request
from sqlalchemy import text

from routes.chips import (
    bq,
    chip_by_clean_number,
    clean_text,
    insert_event,
    only_digits,
    record_aparelho_movimento,
    resolve_and_validate_slot,
    to_float,
    to_int,
)

chips_crud_bp = Blueprint("chips_crud", __name__)

_SAFE_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

EDITABLE_FIELDS = {
    "id_chip",
    "numero",
    "operadora",
    "plano",
    "status",
    "dt_inicio",
    "ultima_recarga_valor",
    "ultima_recarga_data",
    "total_gasto",
    "sk_aparelho_atual",
    "ativo",
    "operador",
    "observacao",
    "tipo_whatsapp",
    "slot_whatsapp",
    "qt_disparos",
    "qt_banimentos",
    "dt_banimentos",
    "data_status",
    "maturando_em",
    "created_at",
}

INTEGER_FIELDS = {"sk_aparelho_atual", "slot_whatsapp", "qt_disparos", "qt_banimentos"}
FLOAT_FIELDS = {"ultima_recarga_valor", "total_gasto"}
BOOLEAN_FIELDS = {"ativo"}


def _table_columns(table_name):
    df = bq.run_df(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = :schema AND table_name = :table
        """,
        {"schema": bq.schema, "table": table_name},
    )
    return {str(row["column_name"]) for _, row in df.iterrows()}


def _bool(value):
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return None
    return str(value).strip().lower() in {"1", "true", "sim", "yes", "on"}


def _normalize_value(field, value):
    if value == "":
        value = None
    if field in INTEGER_FIELDS:
        return to_int(value)
    if field in FLOAT_FIELDS:
        return to_float(value)
    if field in BOOLEAN_FIELDS:
        return _bool(value)
    return clean_text(value)


def _validate_device_slot(sk_chip, sk_aparelho, slot):
    if not sk_aparelho:
        return
    if not slot:
        raise ValueError("Informe o slot WhatsApp ao vincular um aparelho.")
    duplicate = bq.run_df(
        f"""
        SELECT sk_chip
        FROM {bq.table('dim_chip')}
        WHERE sk_aparelho_atual = :aparelho
          AND slot_whatsapp = :slot
          AND sk_chip <> :sk_chip
          AND COALESCE(ativo, TRUE) = TRUE
        LIMIT 1
        """,
        {"aparelho": sk_aparelho, "slot": slot, "sk_chip": sk_chip},
    )
    if not duplicate.empty:
        raise ValueError("Este slot já está ocupado no aparelho selecionado.")


@chips_crud_bp.post("/chips/update-completo-json")
def update_chip_completo():
    payload = request.get_json(silent=True) or {}
    sk_chip = to_int(payload.get("sk_chip"))
    if not sk_chip:
        return jsonify({"error": "sk_chip obrigatório"}), 400

    try:
        current_df = bq.run_df(
            f"SELECT * FROM {bq.table('dim_chip')} WHERE sk_chip = :sk_chip LIMIT 1",
            {"sk_chip": sk_chip},
        )
        if current_df.empty:
            return jsonify({"error": "Chip não encontrado"}), 404
        current = current_df.iloc[0].to_dict()

        numero = only_digits(payload.get("numero"))
        if not numero:
            return jsonify({"error": "Número obrigatório"}), 400
        if chip_by_clean_number(numero, exclude_sk=sk_chip):
            return jsonify({"error": "Já existe outro chip ativo com este número."}), 409

        columns = _table_columns("dim_chip")
        fields = [field for field in EDITABLE_FIELDS if field in columns and field in payload]
        if "numero" not in fields:
            fields.append("numero")

        values = {field: _normalize_value(field, payload.get(field)) for field in fields}
        values["numero"] = numero
        values["sk_chip"] = sk_chip

        status_new = values.get("status", current.get("status"))
        status_old = current.get("status")
        if "data_status" in columns and status_new != status_old and not payload.get("data_status"):
            values["data_status"] = datetime.utcnow().date().isoformat()
            if "data_status" not in fields:
                fields.append("data_status")

        if "maturando_em" in columns and "maturando_em" not in payload:
            old_norm = str(status_old or "").lower().replace("_", " ")
            new_norm = str(status_new or "").lower().replace("_", " ")
            maturation = {"maturando", "em maturação", "em maturacao", "maturacao"}
            if new_norm in maturation and old_norm not in maturation:
                values["maturando_em"] = datetime.utcnow().isoformat()
                fields.append("maturando_em")
            elif new_norm not in maturation:
                values["maturando_em"] = None
                fields.append("maturando_em")

        aparelho_antigo = to_int(current.get("sk_aparelho_atual"))
        slot_antigo = to_int(current.get("slot_whatsapp"))
        aparelho = to_int(values.get("sk_aparelho_atual")) if "sk_aparelho_atual" in values else aparelho_antigo
        slot = to_int(values.get("slot_whatsapp")) if "slot_whatsapp" in values else slot_antigo

        if aparelho:
            slot_resolvido, default_tipo = resolve_and_validate_slot(sk_chip, aparelho, slot)
            values["sk_aparelho_atual"] = aparelho
            values["slot_whatsapp"] = slot_resolvido
            if "sk_aparelho_atual" not in fields and "sk_aparelho_atual" in columns:
                fields.append("sk_aparelho_atual")
            if "slot_whatsapp" not in fields and "slot_whatsapp" in columns:
                fields.append("slot_whatsapp")
            if "tipo_whatsapp" in columns and not values.get("tipo_whatsapp"):
                values["tipo_whatsapp"] = clean_text(current.get("tipo_whatsapp")) or default_tipo
                if "tipo_whatsapp" not in fields:
                    fields.append("tipo_whatsapp")
        elif "sk_aparelho_atual" in payload:
            # Ao remover o aparelho, limpa slot e tipo para manter 100% sincronizado com Relacionamentos
            values["sk_aparelho_atual"] = None
            if "slot_whatsapp" in columns:
                values["slot_whatsapp"] = None
                if "slot_whatsapp" not in fields:
                    fields.append("slot_whatsapp")
            if "tipo_whatsapp" in columns:
                values["tipo_whatsapp"] = None
                if "tipo_whatsapp" not in fields:
                    fields.append("tipo_whatsapp")

        fields = list(dict.fromkeys(fields))
        assignments = [f"{field} = :{field}" for field in fields]
        if "updated_at" in columns:
            assignments.append("updated_at = CURRENT_TIMESTAMP")

        bq.run(
            f"UPDATE {bq.table('dim_chip')} SET {', '.join(assignments)} WHERE sk_chip = :sk_chip",
            values,
        )

        # Sincroniza histórico de vínculo/desvínculo em f_chip_aparelho e f_chip_evento
        slot_novo = to_int(values.get("slot_whatsapp")) if "slot_whatsapp" in values else slot_antigo
        tipo_novo = values.get("tipo_whatsapp") if "tipo_whatsapp" in values else current.get("tipo_whatsapp")
        if aparelho and (aparelho != aparelho_antigo or slot_novo != slot_antigo):
            record_aparelho_movimento(
                sk_chip,
                aparelho,
                slot_novo,
                tipo_novo,
                "VINCULO",
                "Vínculo WhatsApp (Gestão de Chips)",
            )
            insert_event(sk_chip, "VINCULO_APARELHO", f"Vinculado ao aparelho {aparelho}, slot {slot_novo} (Gestão de Chips)")
        elif not aparelho and aparelho_antigo:
            record_aparelho_movimento(
                sk_chip,
                None,
                None,
                None,
                "DESVINCULO",
                "Desvinculação de aparelho (Gestão de Chips)",
            )
            insert_event(sk_chip, "DESVINCULO_APARELHO", "Desvinculado de aparelho (Gestão de Chips)")

        changed = [field for field in fields if str(current.get(field)) != str(values.get(field))]
        insert_event(
            sk_chip,
            "EDICAO_COMPLETA",
            "Chip atualizado pelo painel. Campos alterados: " + (", ".join(changed) if changed else "nenhum"),
        )
        return jsonify({"success": True, "changed_fields": changed})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        print("[Chips CRUD] Erro ao atualizar:", exc)
        return jsonify({"error": str(exc)}), 500


@chips_crud_bp.post("/chips/delete-json")
def delete_chip():
    payload = request.get_json(silent=True) or {}
    sk_chip = to_int(payload.get("sk_chip"))
    confirmation = str(payload.get("confirmation") or "").strip().upper()
    if not sk_chip:
        return jsonify({"error": "sk_chip obrigatório"}), 400
    if confirmation != "EXCLUIR":
        return jsonify({"error": "Confirmação inválida. Digite EXCLUIR."}), 400

    try:
        with bq.engine.begin() as conn:
            chip = conn.execute(
                text(f"SELECT numero FROM {bq.table('dim_chip')} WHERE sk_chip = :sk_chip FOR UPDATE"),
                {"sk_chip": sk_chip},
            ).mappings().first()
            if not chip:
                return jsonify({"error": "Chip não encontrado"}), 404

            references = conn.execute(
                text(
                    """
                    SELECT
                        child.relname AS child_table,
                        child_col.attname AS child_column
                    FROM pg_constraint fk
                    JOIN pg_class child ON child.oid = fk.conrelid
                    JOIN pg_namespace child_ns ON child_ns.oid = child.relnamespace
                    JOIN pg_class parent ON parent.oid = fk.confrelid
                    JOIN pg_namespace parent_ns ON parent_ns.oid = parent.relnamespace
                    JOIN unnest(fk.conkey) WITH ORDINALITY child_key(attnum, ord) ON TRUE
                    JOIN unnest(fk.confkey) WITH ORDINALITY parent_key(attnum, ord) ON parent_key.ord = child_key.ord
                    JOIN pg_attribute child_col ON child_col.attrelid = child.oid AND child_col.attnum = child_key.attnum
                    JOIN pg_attribute parent_col ON parent_col.attrelid = parent.oid AND parent_col.attnum = parent_key.attnum
                    WHERE fk.contype = 'f'
                      AND parent_ns.nspname = :schema
                      AND parent.relname = 'dim_chip'
                      AND parent_col.attname = 'sk_chip'
                      AND child_ns.nspname = :schema
                    """
                ),
                {"schema": bq.schema},
            ).mappings().all()

            deleted_dependencies = []
            for reference in references:
                table_name = reference["child_table"]
                column_name = reference["child_column"]
                if not _SAFE_IDENTIFIER.match(table_name) or not _SAFE_IDENTIFIER.match(column_name):
                    raise ValueError("Foi encontrada uma referência de banco inválida.")
                result = conn.execute(
                    text(f'DELETE FROM "{bq.schema}"."{table_name}" WHERE "{column_name}" = :sk_chip'),
                    {"sk_chip": sk_chip},
                )
                if result.rowcount:
                    deleted_dependencies.append({"table": table_name, "rows": result.rowcount})

            result = conn.execute(
                text(f"DELETE FROM {bq.table('dim_chip')} WHERE sk_chip = :sk_chip"),
                {"sk_chip": sk_chip},
            )
            if result.rowcount != 1:
                raise RuntimeError("O chip não foi excluído.")

        return jsonify(
            {
                "success": True,
                "deleted_sk_chip": sk_chip,
                "numero": chip["numero"],
                "deleted_dependencies": deleted_dependencies,
            }
        )
    except Exception as exc:
        print("[Chips CRUD] Erro ao excluir:", exc)
        return jsonify({"error": f"Não foi possível excluir o chip: {exc}"}), 500
