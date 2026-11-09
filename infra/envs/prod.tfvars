ambiente = "prod"
regiao   = "sa-east-1"

# Bucket de destino dos dados, compartilhado com o pipeline FESA. O FESA grava
# em raw/caixa/ e o bucket nao tem versionamento: PROD usa prefixo proprio, e a
# policy da lambda so permite gravar sob ele, entao o historico do FESA fica
# fora de alcance.
bucket_raw  = "tcc-fesa-2026-116678225423-sa-east-1-an"
prefixo_raw = "prod/raw/caixa"

# Tabela de log do pipeline_log (precisa do prefixo athenas-prod-*).
tabela_log = "athenas-prod-pipeline-logs"

# Mesmo valor da lambda antiga (FESA-Ingestion); o default do codigo e 5.
tentativas = 3

# Vazio de proposito: o pipeline_log vai dentro do zip da lambda
# (data.archive_file empacota src/ inteiro), sem layer.
lambda_layers = []

# Convive com o FESA: athenas-prod as 6h e trigg-tcc as 9h (America/Sao_Paulo),
# com prefixos separados no bucket (prod/raw/caixa e raw/caixa).
agendamento       = "cron(0 6 * * ? *)"
agendamento_ativo = true
