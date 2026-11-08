# Esteira CI/CD — DEV e PROD na mesma conta AWS

## Fluxo de branches

```text
feature/* ──PR──▶ develop ──push──▶ Deploy DEV  (automático)
                     │
                     └──PR──▶ main ──push──▶ Deploy PROD (aguarda aprovação)

hotfix/* ──PR──▶ main  (depois: PR main ──▶ develop para sincronizar)
```

| Branch      | Papel                         | Quem escreve nela                      |
|-------------|-------------------------------|----------------------------------------|
| `feature/*` | desenvolvimento individual    | push direto                            |
| `develop`   | integração = ambiente **DEV** | só via PR (`feature/*`, `fix/*`, `hotfix/*`) |
| `main`      | produção = ambiente **PROD**  | só via PR (`develop` ou `hotfix/*`)    |

## Travas

| Camada | Trava | Onde está |
|--------|-------|-----------|
| Git | PR obrigatório + CI verde em `develop` e `main` | Branch protection (GitHub) |
| Git | PR para `main` só a partir de `develop`/`hotfix/*` | job *Trava de fluxo de branches* em `ci.yml` |
| Deploy | PROD exige aprovação manual de revisor | Environment `prod` (GitHub) |
| AWS | A role de DEV só pode ser assumida pelo environment `dev`; a de PROD só pelo `prod` | trust policy OIDC em `bootstrap/main.tf` |
| AWS | A role de DEV só altera `athenas-dev-*`; a de PROD só `athenas-prod-*` | policy das roles em `bootstrap/main.tf` |
| AWS | State separado por ambiente (`dev/…`, `prod/…`) com lock | `infra/envs/*.backend.hcl` |
| AWS | PRs só leem (plan), nunca aplicam | role `github-athenas-plan` (ReadOnlyAccess) |

## Recursos por ambiente

| Recurso | DEV | PROD |
|---------|-----|------|
| DynamoDB | `athenas-dev-parametros` | `athenas-prod-parametros` |
| Lambda | `athenas-dev-ingestao-caixa` | `athenas-prod-ingestao-caixa` |
| Step Function | `athenas-dev-ingestao` | `athenas-prod-ingestao` |
| Agendamento | `athenas-dev-ingestao-diaria` (desligado) | `athenas-prod-ingestao-diaria` (ligado) |

Os valores de cada ambiente ficam em `infra/envs/dev.tfvars` e `infra/envs/prod.tfvars`.

## Rodar o Terraform localmente

```bash
cd infra
terraform init -reconfigure \
  -backend-config=envs/dev.backend.hcl \
  -backend-config="bucket=athenas-tfstate-<ACCOUNT_ID>"
terraform plan -var-file=envs/dev.tfvars
```

Para testar DEV, dispare a Step Function manualmente (o agendamento de DEV fica desligado):

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:sa-east-1:<ACCOUNT_ID>:stateMachine:athenas-dev-ingestao \
  --input '{"forcar_mensal": false}'
```
