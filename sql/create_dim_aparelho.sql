CREATE TABLE IF NOT EXISTS chips.dim_aparelho (
  sk_aparelho bigint PRIMARY KEY,
  id_aparelho text,
  modelo text,
  marca text,
  imei text,
  status text,
  ativo boolean DEFAULT true,
  created_at timestamptz DEFAULT CURRENT_TIMESTAMP,
  updated_at timestamptz DEFAULT CURRENT_TIMESTAMP
);
