ambiente = "dev"
regiao   = "sa-east-1"

# TROCAR pelo bucket raw de DEV (pode ser o mesmo de PROD com outro prefixo).
bucket_raw  = "TROCAR-bucket-raw-dev"
prefixo_raw = "raw/caixa"

# ARN(s) da layer com o modulo pipeline_log.
lambda_layers = []

# DEV nao roda sozinho: dispare a Step Function manualmente para testar.
agendamento_ativo = false
