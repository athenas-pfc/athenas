# Plataforma de Análise de Imóveis em Leilão 

Plataforma desenvolvida em **Python** para a coleta, consolidação e análise geoespacial de imóveis levados a leilão nos municípios de **São Paulo, Belo Horizonte e Rio de Janeiro**, acompanhada de um motor de métricas financeiras de retorno e visualização via dashboard interativo.

---

## 🏗️ Propósito e Tecnologias

### Propósito
O projeto resolve a opacidade do mercado de leilões imobiliários substituindo o "desconto anunciado" por um **desconto de referência** calculado empiricamente com base em transações reais de mercado (ITBI). O sistema entrega projeções de viabilidade e rentabilidade (TIR anualizada e *payback*) sob premissas declaradas[cite: 3].

### Tecnologias Utilizadas
* **Linguagem:** Python 3.x
* **Manipulação e Análise de Dados:** Pandas, NumPy
* **Automação & Web Scraping:** Playwright (Chromium Headless)
* **Banco de Dados & ORM:** MySQL, SQLAlchemy, PyMySQL
* **Interface Gráfica e Visualização:** Dash (Plotly)
* **Engenharia de Pipelines:** Requests, Openpyxl, TQDM

---

## 📂 Estrutura do Repositório

```text
├── docs
│   ├── diagrama_bd_leilao.jpeg         # Diagrama banco de dados dos leilões 
│   └── diagrama_bd_mercado.jpeg        # Diagrama banco de dados dos registro de ITBI
│    
├── Bases de Dados/
│   ├── SP/                             # Planilhas de ITBI da cidade de São Paulo
│   ├── MG/                             # Planilhas de ITBI da cidade de Belo Horizonte
│   └── RJ/                             # Planilhas de ITBI da cidade do Rio de Janeiro
│   └── Leiloes/                        # Snapshots diários organizados por estado
│       ├── SP/                         # Planilhas de Leilões do estado de São Paulo
│       ├── MG/                         # Planilhas de Leilões do estado de Minas Gerais
│       └── RJ/                         # Planilhas de Leilões do estado do Rio de Janeiro
│
├── ingestao_caixa.py                   # Pipeline ETL automatizado via Playwright para a Base Volátil (Leilões Caixa)
├── ingestao_mercado.py                 # Pipeline ETL para a Base Imutável (ITBI / Mercado)
├── analise_retorno.py                  # Motor Financeiro (Cálculo de TIR e Payback)
├── schema.sql                          # Estrutura SQL do banco de dados
└── README.md                           # Documentação inicial do repositório
