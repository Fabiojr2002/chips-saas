# Painel de Chips

Sistema Flask para controle de chips, aparelhos, recargas, vínculos WhatsApp, movimentação e indicadores operacionais.

## Infraestrutura

- Aplicação: Flask executado via Gunicorn.
- Deploy: EasyPanel na VPS.
- Domínio: `chips.gruporeferencia.tech`.
- Banco: PostgreSQL/Supabase self-hosted.
- Schema principal: `chips`.

## Variáveis de ambiente

```env
DATABASE_URL=postgresql://app_paineis:AppPaineis%402026@supabase_supabase-db-1:5432/postgres
DB_SCHEMA=chips
FLASK_SECRET_KEY=GrupoReferenciaChips@2026
PORT=8000
```

## Deploy no EasyPanel

1. Configure as variáveis de ambiente acima no serviço da aplicação.
2. Garanta que o container esteja na mesma rede interna do Supabase/PostgreSQL.
3. Use a imagem gerada pelo `Dockerfile` deste repositório.
4. O comando de produção é:

```bash
gunicorn -b 0.0.0.0:${PORT:-8000} app:app
```

## Banco de dados

O app usa somente `DATABASE_URL` e `DB_SCHEMA` para acessar PostgreSQL. As principais views consumidas são:

- `chips.vw_chips_painel`
- `chips.vw_chips_painel_dashboard`
- `chips.vw_aparelhos`
- `chips.vw_chip_timeline`
- `chips.vw_relacionamentos_whatsapp`

## Healthcheck

```http
GET /health
```

Resposta esperada quando o banco estiver acessível:

```json
{"ok": true, "db": "connected"}
```
