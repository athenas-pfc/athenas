# Executado UMA vez, localmente, com credenciais de administrador da conta.
# Cria o que o pipeline precisa antes de existir:
#   - bucket S3 do state do Terraform (um prefixo por ambiente, com lock nativo)
#   - provedor OIDC do GitHub (login sem access key guardada no GitHub)
#   - roles assumidas pelo GitHub Actions:
#       github-athenas-plan -> leitura, usada nos PRs (terraform plan)
#       github-athenas-dev  -> deploy, so no environment "dev"  e so em athenas-dev-*
#       github-athenas-prod -> deploy, so no environment "prod" e so em athenas-prod-*
#
# As roles de deploy NAO comecam com "athenas-<ambiente>-" de proposito:
# assim nenhuma delas consegue alterar a si mesma.

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.70"
    }
  }
}

provider "aws" {
  region = var.regiao

  default_tags {
    tags = {
      Projeto       = "athenas"
      GerenciadoPor = "terraform-bootstrap"
    }
  }
}

variable "regiao" {
  type    = string
  default = "sa-east-1"
}

variable "github_repo" {
  description = "Repositorio no formato dono/nome."
  type        = string
  default     = "athenas-pfc/athenas"
}

# A org athenas-pfc inclui os IDs numericos do dono e do repositorio no sub
# do token OIDC (dono@id/nome@id). Os IDs nao mudam se o repo for renomeado.
variable "github_sub_prefixo" {
  description = "Prefixo do claim sub do token OIDC do GitHub, sem o contexto final (:pull_request, :environment:...)."
  type        = string
  default     = "repo:athenas-pfc@353591849/athenas@1346632218"
}

variable "criar_provedor_oidc" {
  description = "false se a conta ja tiver o provedor OIDC token.actions.githubusercontent.com."
  type        = bool
  default     = true
}

data "aws_caller_identity" "atual" {}

locals {
  conta      = data.aws_caller_identity.atual.account_id
  ambientes  = toset(["dev", "prod"])
  oidc_url   = "token.actions.githubusercontent.com"
  bucket_tfs = "athenas-tfstate-${local.conta}"
  oidc_arn = (var.criar_provedor_oidc
    ? aws_iam_openid_connect_provider.github[0].arn
  : "arn:aws:iam::${local.conta}:oidc-provider/${local.oidc_url}")
}

# ---------------------------------------------------------------------------
# State remoto
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "tfstate" {
  bucket = local.bucket_tfs

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------------------------------------------------------
# OIDC do GitHub
# ---------------------------------------------------------------------------

resource "aws_iam_openid_connect_provider" "github" {
  count = var.criar_provedor_oidc ? 1 : 0

  url             = "https://${local.oidc_url}"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1", "1c58a3a8518e8759bf075b76b750d4f2df264fcd"]
}

# Trust policy: so o repositorio, e so no contexto indicado (sub do token).
data "aws_iam_policy_document" "confianca" {
  for_each = {
    plan = "${var.github_sub_prefixo}:pull_request"
    dev  = "${var.github_sub_prefixo}:environment:dev"
    prod = "${var.github_sub_prefixo}:environment:prod"
  }

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_url}:sub"
      values   = [each.value]
    }
  }
}

# ---------------------------------------------------------------------------
# Role de plan (PRs): somente leitura
# ---------------------------------------------------------------------------

resource "aws_iam_role" "plan" {
  name               = "github-athenas-plan"
  assume_role_policy = data.aws_iam_policy_document.confianca["plan"].json
}

resource "aws_iam_role_policy_attachment" "plan_leitura" {
  role       = aws_iam_role.plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# ---------------------------------------------------------------------------
# Roles de deploy: cada uma limitada aos recursos athenas-<ambiente>-*
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "deploy" {
  for_each = local.ambientes

  statement {
    sid       = "ListarState"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.tfstate.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${each.key}/*"]
    }
  }

  statement {
    sid       = "LerGravarState"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.tfstate.arn}/${each.key}/*"]
  }

  statement {
    sid       = "Lambda"
    actions   = ["lambda:*"]
    resources = ["arn:aws:lambda:${var.regiao}:${local.conta}:function:athenas-${each.key}-*"]
  }

  statement {
    sid       = "LerLayers"
    actions   = ["lambda:GetLayerVersion"]
    resources = ["*"]
  }

  statement {
    sid       = "DynamoDB"
    actions   = ["dynamodb:*"]
    resources = ["arn:aws:dynamodb:${var.regiao}:${local.conta}:table/athenas-${each.key}-*"]
  }

  statement {
    sid     = "StepFunctions"
    actions = ["states:*"]
    resources = [
      "arn:aws:states:${var.regiao}:${local.conta}:stateMachine:athenas-${each.key}-*",
      "arn:aws:states:${var.regiao}:${local.conta}:execution:athenas-${each.key}-*:*",
    ]
  }

  statement {
    sid       = "Scheduler"
    actions   = ["scheduler:*"]
    resources = ["arn:aws:scheduler:${var.regiao}:${local.conta}:schedule/*/athenas-${each.key}-*"]
  }

  statement {
    sid = "RolesDoAmbiente"
    actions = [
      "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:UpdateRole",
      "iam:UpdateAssumeRolePolicy", "iam:TagRole", "iam:UntagRole",
      "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole",
      "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy",
    ]
    resources = ["arn:aws:iam::${local.conta}:role/athenas-${each.key}-*"]
  }

  # So deixa anexar a policy gerenciada que o projeto usa (evita anexar AdministratorAccess).
  statement {
    sid       = "AnexarSomenteLogs"
    actions   = ["iam:AttachRolePolicy", "iam:DetachRolePolicy"]
    resources = ["arn:aws:iam::${local.conta}:role/athenas-${each.key}-*"]
    condition {
      test     = "ArnEquals"
      variable = "iam:PolicyARN"
      values   = ["arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"]
    }
  }

  statement {
    sid       = "PassarRolesDoAmbiente"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${local.conta}:role/athenas-${each.key}-*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com", "states.amazonaws.com", "scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  for_each = local.ambientes

  name                 = "github-athenas-${each.key}"
  assume_role_policy   = data.aws_iam_policy_document.confianca[each.key].json
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "deploy" {
  for_each = local.ambientes

  name   = "deploy-athenas-${each.key}"
  role   = aws_iam_role.deploy[each.key].id
  policy = data.aws_iam_policy_document.deploy[each.key].json
}

# ---------------------------------------------------------------------------
# Saidas: copiar AWS_ACCOUNT_ID para as Variables do repositorio no GitHub
# ---------------------------------------------------------------------------

output "AWS_ACCOUNT_ID" {
  value = local.conta
}

output "bucket_tfstate" {
  value = aws_s3_bucket.tfstate.bucket
}

output "roles" {
  value = merge(
    { plan = aws_iam_role.plan.arn },
    { for amb, r in aws_iam_role.deploy : amb => r.arn },
  )
}
