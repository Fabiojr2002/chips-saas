CREATE TABLE IF NOT EXISTS chips.f_chip_aparelho (
  sk_fato bigint PRIMARY KEY,
  sk_chip bigint,
  sk_aparelho bigint,
  slot_whatsapp bigint,
  tipo_whatsapp text,
  tipo_movimento text,
  data_movimento timestamptz DEFAULT CURRENT_TIMESTAMP,
  origem text,
  observacao text,
  created_at timestamptz DEFAULT CURRENT_TIMESTAMP
);
