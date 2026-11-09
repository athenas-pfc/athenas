# Esteira CI/CD — DEV e PROD na mesma conta AWS

## Fluxo de branches

```text
feature/* ──PR──▶ develop ──push──▶ Deploy DEV  (automático)
                     │
                     └──PR──▶ main ──manual──▶ Deploy PROD (workflow_dispatch)

hotfix/* ──PR──▶ main  (depois: PR main ──▶ develop para sincronizar)
```

O merge em `main` **não** sobe produção. Para fazer o deploy de PROD: aba
*Actions* → workflow *Deploy* → *Run workflow* → branch `main`. Motivo: o plano
gratuito do GitHub não oferece *Required reviewers* em repositório privado, então
um deploy automático em `main` não teria nenhuma aprovação.

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
| Deploy | PROD só sobe por disparo manual a partir de `main`; push em `main` não faz deploy | gatilho `on:` em `deploy.yml` |
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
| Agendamento | `athenas-dev-ingestao-diaria` (desligado) | `athenas-prod-ingestao-diaria` (ligado, 6h) |

Os valores de cada ambiente ficam em `infra/envs/dev.tfvars` e `infra/envs/prod.tfvars`.

## Convivência com o pipeline FESA

O pipeline antigo (FESA) continua rodando na mesma conta e grava no mesmo bucket
(`tcc-fesa-2026-116678225423-sa-east-1-an`). Nada do FESA é gerenciado por este
repositório.

| | athenas-prod | FESA |
|---|---|---|
| Agendamento | `athenas-prod-ingestao-diaria`, 6h | `trigg-tcc` → `FESA_Ingestion_Caixa`, 9h |
| Prefixo no bucket | `prod/raw/caixa/` | `raw/caixa/` |

- Os horários são em `America/Sao_Paulo` e não se sobrepõem.
- Os prefixos são separados porque os dois usam a mesma estrutura
  (`uf=XX/dt=AAAA-MM-DD/Lista_imoveis_XX.csv`) e o bucket não tem versionamento:
  com o mesmo prefixo, um sobrescreveria o arquivo do outro todo dia, sem volta.
  A policy da lambda de PROD só permite gravar sob `prod/raw/caixa/`, então o
  histórico do FESA fica fora de alcance.
- A convivência está definida: os dois agendamentos ficam ligados, cada um no
  seu horário e gravando no seu prefixo (`agendamento_ativo = true` em
  `prod.tfvars`).

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