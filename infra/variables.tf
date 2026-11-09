variable "ambiente" {
  description = "Ambiente do deploy. Vira prefixo de todos os recursos (athenas-<ambiente>-*)."
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.ambiente)
    error_message = "ambiente deve ser \"dev\" ou \"prod\"."
  }
}

variable "regiao" {
  type    = string
  default = "sa-east-1"
}

variable "bucket_raw" {
  description = "Bucket onde a ingestao grava os CSVs brutos (camada raw)."
  type        = string
}

variable "prefixo_raw" {
  description = "Prefixo dos CSVs brutos dentro do bucket_raw."
  type        = string
  default     = "raw/caixa"
}

variable "proxy_url" {
  description = "Proxy de saida da ingestao Caixa (http://usuario:senha@host:porta). Vazio = conexao direta."
  type        = string
  default     = ""
  sensitive   = true
}

variable "tabela_log" {
  description = "Tabela DynamoDB de log operacional da pipeline (TABELA_LOG do pipeline_log). Deve seguir o prefixo athenas-<ambiente>-*."
  type        = string
}

variable "ttl_dias" {
  description = "Dias ate o DynamoDB expirar os eventos de log (TTL_DIAS do pipeline_log)."
  type        = number
  default     = 90
}

variable "base_url" {
  description = "URL base dos CSVs da Caixa; o arquivo baixado e <base_url>/Lista_imoveis_<UF>.csv."
  type        = string
  default     = "https://venda-imoveis.caixa.gov.br/listaweb"
}

variable "pagina_origem" {
  description = "Pagina visitada antes do download (aquecimento) e enviada como Referer."
  type        = string
  default     = "https://venda-imoveis.caixa.gov.br/sistema/download-lista.asp"
}

variable "tentativas" {
  description = "Tentativas de download dentro da lambda, antes do retry da Step Function."
  type        = number
  default     = 5
}

variable "timeout_http" {
  description = "Timeout de cada requisicao HTTP da ingestao, em segundos."
  type        = number
  default     = 60
}

variable "limite_redirecionamentos" {
  description = "Maximo de redirecionamentos seguidos em cada requisicao."
  type        = number
  default     = 5
}

variable "versao_chrome" {
  description = "Versao do Chrome anunciada no User-Agent e no sec-ch-ua."
  type        = string
  default     = "126"
}

variable "lambda_layers" {
  description = "ARNs de layers da lambda (ex.: layer com o modulo pipeline_log)."
  type        = list(string)
  default     = []
}

variable "agendamento" {
  description = "Horario da execucao diaria, no fuso America/Sao_Paulo."
  type        = string
  default     = "cron(0 6 * * ? *)"
}

variable "agendamento_ativo" {
  description = "Liga o agendamento diario. Em DEV fica desligado para nao rodar a captura da Caixa duas vezes por dia."
  type        = bool
  default     = true
}
