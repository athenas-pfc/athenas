ambiente = "prod"
regiao   = "sa-east-1"

# TROCAR pelo bucket raw de PROD.
bucket_raw  = "TROCAR-bucket-raw-prod"
prefixo_raw = "raw/caixa"

# ARN(s) da layer com o modulo pipeline_log.
lambda_layers = []

agendamento       = "cron(0 6 * * ? *)"
agendamento_ativo = true
