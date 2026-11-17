CREATE TABLE IF NOT EXISTS chips.dim_chip (
  sk_chip bigint PRIMARY KEY,
  id_chip text,
  numero text,
  operadora text,
  plano text,
  status text,
  ativo boolean DEFAULT true,
  maturando_em timestamptz,
  created_at timestamptz DEFAULT CURRENT_TIMESTAMP,
  updated_at timestamptz DEFAULT CURRENT_TIMESTAMP
);
