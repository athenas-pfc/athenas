terraform {
  # use_lockfile (trava do state direto no S3, sem DynamoDB) exige >= 1.10.
  required_version = ">= 1.10"

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

  # Configuracao parcial: bucket/key vem de envs/<ambiente>.backend.hcl no init.
  backend "s3" {}
}

provider "aws" {
  region = var.regiao

  default_tags {
    tags = {
      Projeto       = "athenas"
      Ambiente      = var.ambiente
      GerenciadoPor = "terraform"
    }
  }
}
