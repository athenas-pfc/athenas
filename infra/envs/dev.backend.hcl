# O nome do bucket (athenas-tfstate-<ACCOUNT_ID>) e passado no init:
#   terraform init -backend-config=envs/dev.backend.hcl -backend-config="bucket=athenas-tfstate-<ACCOUNT_ID>"
key          = "dev/athenas.tfstate"
region       = "sa-east-1"
encrypt      = true
use_lockfile = true
