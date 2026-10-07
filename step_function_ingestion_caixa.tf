terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.70"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4"
    }
  }
}

provider "aws" {
  region = var.regiao
}

# ---------------------------------------------------------------------------
# Variaveis
# ---------------------------------------------------------------------------

variable "regiao" {
  type    = string
  default = "sa-east-1"
}

variable "bucket_raw" {
  description = "Bucket onde a ingestao grava os CSVs brutos (camada raw)."
  type        = string
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

data "aws_caller_identity" "atual" {}

# ---------------------------------------------------------------------------
# DynamoDB: tabela de controle, populada com os JSONs de src/dynamo
# ---------------------------------------------------------------------------

# Os nomes da tabela e da lambda estao fixos em step_functions/ingestao.asl.json,
# para a definicao poder ser colada direto no console. Mude nos dois lugares.
resource "aws_dynamodb_table" "controle" {
  name         = "tcc-parametros"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "estado"
  range_key    = "base"

  attribute {
    name = "estado"
    type = "S"
  }

  attribute {
    name = "base"
    type = "S"
  }
}

resource "aws_dynamodb_table_item" "controle" {
  for_each = fileset("${path.module}/src/dynamo", "*.json")

  table_name = aws_dynamodb_table.controle.name
  hash_key   = aws_dynamodb_table.controle.hash_key
  range_key  = aws_dynamodb_table.controle.range_key
  item       = file("${path.module}/src/dynamo/${each.value}")
}

# ---------------------------------------------------------------------------
# Lambda: ingestao_caixa.py
# ---------------------------------------------------------------------------

data "archive_file" "ingestao" {
  type        = "zip"
  source_dir  = "${path.module}/src"
  output_path = "${path.module}/build/ingestao.zip"
  excludes    = ["dynamo/**", "**/__pycache__/**", "ingestao_itbi.py"]
}

resource "aws_iam_role" "lambda_ingestao" {
  name = "athenas-lambda-ingestao"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda_ingestao.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_s3" {
  name = "gravar-raw-caixa"
  role = aws_iam_role.lambda_ingestao.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "s3:PutObject"
      Resource = "arn:aws:s3:::${var.bucket_raw}/raw/caixa/*"
    }]
  })
}

resource "aws_lambda_function" "ingestao_caixa" {
  function_name    = "FESA-Ingestion"
  role             = aws_iam_role.lambda_ingestao.arn
  runtime          = "python3.12"
  handler          = "ingestao_caixa.lambda_handler"
  filename         = data.archive_file.ingestao.output_path
  source_code_hash = data.archive_file.ingestao.output_base64sha256
  layers           = var.lambda_layers

  # baixar() faz ate 5 tentativas com backoff e timeouts HTTP de 60s.
  timeout     = 900
  memory_size = 512

  environment {
    variables = {
      BUCKET      = var.bucket_raw
      PREFIXO_RAW = "raw/caixa"
      PROXY_URL   = var.proxy_url
    }
  }
}

# ---------------------------------------------------------------------------
# Step Function
# ---------------------------------------------------------------------------

resource "aws_iam_role" "sfn_ingestao" {
  name = "athenas-sfn-ingestao"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.atual.account_id }
      }
    }]
  })
}

resource "aws_iam_role_policy" "sfn_ingestao" {
  name = "orquestrar-ingestao"
  role = aws_iam_role.sfn_ingestao.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "dynamodb:Scan"
        Resource = aws_dynamodb_table.controle.arn
      },
      {
        Effect = "Allow"
        Action = "lambda:InvokeFunction"
        Resource = [
          aws_lambda_function.ingestao_caixa.arn,
          "${aws_lambda_function.ingestao_caixa.arn}:*",
        ]
      },
    ]
  })
}

resource "aws_sfn_state_machine" "ingestao" {
  name     = "athenas-ingestao"
  role_arn = aws_iam_role.sfn_ingestao.arn
  definition = file("${path.module}/step_functions/ingestao.asl.json")

  depends_on = [aws_dynamodb_table.controle, aws_lambda_function.ingestao_caixa]
}

# ---------------------------------------------------------------------------
# Agendamento diario (as bases mensais sao filtradas dentro da Step Function)
# ---------------------------------------------------------------------------

resource "aws_iam_role" "scheduler_ingestao" {
  name = "athenas-scheduler-ingestao"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.atual.account_id }
      }
    }]
  })
}

resource "aws_iam_role_policy" "scheduler_ingestao" {
  name = "iniciar-ingestao"
  role = aws_iam_role.scheduler_ingestao.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "states:StartExecution"
      Resource = aws_sfn_state_machine.ingestao.arn
    }]
  })
}

resource "aws_scheduler_schedule" "ingestao_diaria" {
  name                         = "athenas-ingestao-diaria"
  schedule_expression          = var.agendamento
  schedule_expression_timezone = "America/Sao_Paulo"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.ingestao.arn
    role_arn = aws_iam_role.scheduler_ingestao.arn
    input    = jsonencode({})
  }
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.ingestao.arn
}
