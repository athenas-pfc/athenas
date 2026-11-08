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
