locals {
  # Todo recurso leva o prefixo do ambiente. As roles de deploy do GitHub
  # (bootstrap/) so tem permissao sobre recursos com o prefixo do proprio
  # ambiente: e isso que impede o pipeline de DEV de mexer em PROD.
  prefixo = "athenas-${var.ambiente}"

  raiz_projeto = "${path.module}/.."
}

data "aws_caller_identity" "atual" {}

# ---------------------------------------------------------------------------
# DynamoDB: tabela de controle, populada com os JSONs de src/dynamo
# ---------------------------------------------------------------------------

resource "aws_dynamodb_table" "controle" {
  name         = "${local.prefixo}-parametros"
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
  for_each = fileset("${local.raiz_projeto}/src/dynamo", "*.json")

  table_name = aws_dynamodb_table.controle.name
  hash_key   = aws_dynamodb_table.controle.hash_key
  range_key  = aws_dynamodb_table.controle.range_key
  item       = file("${local.raiz_projeto}/src/dynamo/${each.value}")
}

# ---------------------------------------------------------------------------
# Lambda: ingestao_caixa.py
# ---------------------------------------------------------------------------

data "archive_file" "ingestao" {
  type        = "zip"
  source_dir  = "${local.raiz_projeto}/src"
  output_path = "${path.module}/build/ingestao.zip"
  excludes    = ["dynamo/**", "**/__pycache__/**", "ingestao_itbi.py"]
}

resource "aws_iam_role" "lambda_ingestao" {
  name = "${local.prefixo}-lambda-ingestao"
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
      Resource = "arn:aws:s3:::${var.bucket_raw}/${var.prefixo_raw}/*"
    }]
  })
}

resource "aws_lambda_function" "ingestao_caixa" {
  function_name    = "${local.prefixo}-ingestao-caixa"
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
      PREFIXO_RAW = var.prefixo_raw
      PROXY_URL   = var.proxy_url
    }
  }
}

# ---------------------------------------------------------------------------
# Step Function
# ---------------------------------------------------------------------------

resource "aws_iam_role" "sfn_ingestao" {
  name = "${local.prefixo}-sfn-ingestao"
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
  name     = "${local.prefixo}-ingestao"
  role_arn = aws_iam_role.sfn_ingestao.arn

  # Os nomes da tabela e da lambda entram por template, para cada ambiente
  # apontar para os proprios recursos.
  definition = templatefile("${local.raiz_projeto}/step_functions/ingestao.asl.json", {
    tabela_controle       = aws_dynamodb_table.controle.name
    lambda_ingestao_caixa = aws_lambda_function.ingestao_caixa.function_name
  })
}

# ---------------------------------------------------------------------------
# Agendamento diario (as bases mensais sao filtradas dentro da Step Function)
# ---------------------------------------------------------------------------

resource "aws_iam_role" "scheduler_ingestao" {
  name = "${local.prefixo}-scheduler-ingestao"
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
  name                         = "${local.prefixo}-ingestao-diaria"
  schedule_expression          = var.agendamento
  schedule_expression_timezone = "America/Sao_Paulo"
  state                        = var.agendamento_ativo ? "ENABLED" : "DISABLED"

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

output "lambda_ingestao_caixa" {
  value = aws_lambda_function.ingestao_caixa.function_name
}
