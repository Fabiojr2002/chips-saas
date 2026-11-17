import os

import pandas as pd
from sqlalchemy import create_engine, text


class PostgresClient:
    def __init__(self):
        self.database_url = os.getenv("DATABASE_URL")
        if not self.database_url:
            raise RuntimeError("Variável DATABASE_URL não configurada.")
        self.schema = os.getenv("DB_SCHEMA", "chips")
        self.project = None
        self.dataset = self.schema
        self.engine = create_engine(self.database_url, pool_pre_ping=True)

    def table(self, name):
        safe = str(name).replace('"', '').replace(';', '')
        return f"{self.schema}.{safe}"

    def run(self, sql: str, params=None):
        print("\n[Postgres] EXECUTANDO SQL:\n", sql, "\n" + "=" * 80)
        with self.engine.begin() as conn:
            return conn.execute(text(sql), params or {})

    def run_df(self, sql: str, params=None):
        print("\n[Postgres] EXECUTANDO SQL DF:\n", sql, "\n" + "=" * 80)
        with self.engine.connect() as conn:
            df = pd.read_sql_query(text(sql), conn, params=params or {})
        return df.astype(object).where(pd.notnull(df), None)

    def get_view(self, view_name: str):
        safe = view_name.replace('"', '').replace(';', '')
        return self.run_df(f"SELECT * FROM {self.schema}.{safe}")

    def call_sp(self, sp_name: str, params: str = ""):
        raise RuntimeError("Stored Procedures removidas. Use operações PostgreSQL transacionais.")

    def next_id(self, table, column):
        df = self.run_df(f"SELECT COALESCE(MAX({column}), 0) + 1 AS next_id FROM {self.table(table)}")
        return int(df.iloc[0]["next_id"])

    def insert_event(self, sk_chip, tipo_evento, observacao, origem="Painel", categoria=None, campo=None, valor_antigo=None, valor_novo=None):
        sql = f"""
            INSERT INTO {self.table('f_chip_evento')}
            (sk_evento, sk_chip, data_evento, tipo_evento, campo, valor_antigo, valor_novo, origem, observacao, created_at, categoria)
            VALUES
            ((SELECT COALESCE(MAX(e.sk_evento), 0) + 1 FROM {self.table('f_chip_evento')} e), :sk_chip, CURRENT_TIMESTAMP,
             :tipo_evento, :campo, :valor_antigo, :valor_novo, :origem, :observacao, CURRENT_TIMESTAMP, :categoria)
        """
        self.run(sql, {"sk_chip": int(sk_chip), "tipo_evento": tipo_evento, "campo": campo, "valor_antigo": valor_antigo, "valor_novo": valor_novo, "origem": origem, "observacao": observacao, "categoria": categoria})

    def upsert_aparelho(self, payload):
        sk_aparelho = payload.get("sk_aparelho")
        if sk_aparelho:
            self.run(f"""
                UPDATE {self.table('dim_aparelho')}
                SET id_aparelho=:id_aparelho, modelo=:modelo, marca=:marca, imei=:imei, status=:status, updated_at=CURRENT_TIMESTAMP
                WHERE sk_aparelho=:sk_aparelho
            """, {**payload, "sk_aparelho": int(sk_aparelho)})
            return sk_aparelho
        novo_id = self.next_id("dim_aparelho", "sk_aparelho")
        self.run(f"""
            INSERT INTO {self.table('dim_aparelho')}
            (sk_aparelho, id_aparelho, modelo, marca, imei, status, ativo, created_at, updated_at)
            VALUES (:sk_aparelho, :id_aparelho, :modelo, :marca, :imei, :status, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, {**payload, "sk_aparelho": novo_id})
        return novo_id
