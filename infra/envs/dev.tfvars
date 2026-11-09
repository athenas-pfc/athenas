ambiente = "dev"
regiao   = "sa-east-1"

# Bucket raw de DEV; os CSVs ficam sob o prefixo dev/.
bucket_raw  = "tcc-fesa-2026-116678225423-sa-east-1-an"
prefixo_raw = "dev/raw/caixa"

# Tabela de log do pipeline_log (precisa do prefixo athenas-dev-*).
tabela_log = "athenas-dev-pipeline-logs"

# Mesmo valor da lambda antiga (FESA-Ingestion); o default do codigo e 5.
tentativas = 3

# Vazio de proposito: o pipeline_log vai dentro do zip da lambda
# (data.archive_file empacota src/ inteiro), sem layer.
lambda_layers = []

# DEV nao roda sozinho: dispare a Step Function manualmente para testar.
agendamento_ativo = false
