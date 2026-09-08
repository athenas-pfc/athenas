import os
import sys
import json
import pandas as pd
from sqlalchemy import create_engine, text
import uuid
from tqdm import tqdm

# ==========================================
# 0. PRE-FLIGHT CHECK
# ==========================================
BASE_DIR = '/home/luan/Documentos/FESA/TCC/Bases de Dados'
pastas_esperadas = ['SP', 'MG', 'RJ']

print("Verificando estrutura de diretórios...")
if not os.path.exists(BASE_DIR):
    print(f"[ERRO CRÍTICO] O diretório base não foi encontrado:\n{BASE_DIR}")
    sys.exit(1)

for p in pastas_esperadas:
    caminho_p = os.path.join(BASE_DIR, p)
    if os.path.exists(caminho_p):
        print(f" -> Pasta {p} encontrada com sucesso.")

# ==========================================
# 1. AUTOCOMMIT E PREPARAÇÃO
# ==========================================
senha = 'athenas'
engine_setup = create_engine(
    f'mysql+pymysql://root:{senha}@localhost:3306/',
    isolation_level="AUTOCOMMIT"
)

print("\nPreparando o banco de dados (Modo Autocommit - Raw Flexível via JSON)...")
with engine_setup.connect() as conn:
    conn.execute(text("DROP DATABASE IF EXISTS mercado_db"))
    conn.execute(text("CREATE DATABASE mercado_db"))

engine = create_engine(f'mysql+pymysql://root:{senha}@localhost:3306/mercado_db')

ddls = [
    """
    CREATE TABLE imovel (
        id_imovel VARCHAR(100) PRIMARY KEY,
        municipio VARCHAR(100),
        area_construida DECIMAL(15,2),
        area_terreno DECIMAL(15,2),
        uso TEXT, 
        unidade_territorial VARCHAR(100)
    );
    """,
    """
    CREATE TABLE transacao (
        id_transacao INT AUTO_INCREMENT PRIMARY KEY,
        id_imovel VARCHAR(100),
        municipio VARCHAR(100),
        granularidade VARCHAR(50),
        valor_nominal DECIMAL(15,2),
        data_transacao VARCHAR(10),
        FOREIGN KEY (id_imovel) REFERENCES imovel(id_imovel)
    );
    """
]

with engine.connect() as conn:
    for ddl in ddls:
        conn.execute(text(ddl))


# ==========================================
# 2. FUNÇÕES DE HIGIENIZAÇÃO
# ==========================================
def limpar_numero(valor):
    if pd.isna(valor): return 0.0
    try:
        v = str(valor).strip()
        if '.' in v and ',' in v:
            v = v.replace('.', '')
        v = v.replace(',', '.')
        return float(v)
    except:
        return 0.0


def padronizar_data(serie, dayfirst=False):
    dt = pd.to_datetime(serie, errors='coerce', dayfirst=dayfirst)
    return dt.dt.strftime('%Y-%m-%d').where(dt.notnull(), None)


def empacotar_raw_json(df_completo, col_id):
    """Transforma todas as colunas originais em um único pacote JSON"""
    df_raw = df_completo.copy().fillna('Não informado')

    # BLINDAGEM: Força o nome de TODAS as colunas a virarem texto
    df_raw.columns = df_raw.columns.astype(str)

    df_dados = df_raw.drop(columns=[col_id]).astype(str)
    records = df_dados.to_dict(orient='records')

    df_json = pd.DataFrame({
        'id_imovel': df_raw[col_id],
        'atributos_raw': [json.dumps(r, ensure_ascii=False) for r in records]
    })
    return df_json

# ==========================================
# 3. ADAPTADORES MUNICIPAIS
# ==========================================
def adaptador_bh(caminho):
    df = pd.read_csv(caminho, sep=';', encoding='latin1')
    df.columns = df.columns.str.replace('ï»¿', '').str.strip()

    col_area_const = 'Area Construida Adquirida' if 'Area Construida Adquirida' in df.columns else df.columns[4]
    col_area_terr = 'Area Terreno Total' if 'Area Terreno Total' in df.columns else df.columns[3]
    col_uso = 'Descricao Tipo Ocupacao Unidade' if 'Descricao Tipo Ocupacao Unidade' in df.columns else 'Uso'
    col_bairro = 'Bairro' if 'Bairro' in df.columns else 'Bairro'
    col_valor = 'Valor Declarado' if 'Valor Declarado' in df.columns else df.columns[10]
    col_data = 'Data Quitacao Transacao' if 'Data Quitacao Transacao' in df.columns else df.columns[-1]

    df['id_imovel'] = [f"BH_{uuid.uuid4().hex[:8]}" for _ in range(len(df))]

    df_imovel = pd.DataFrame({
        'id_imovel': df['id_imovel'],
        'municipio': 'Belo Horizonte',
        'area_construida': df.get(col_area_const, pd.Series([0] * len(df))).apply(limpar_numero),
        'area_terreno': df.get(col_area_terr, pd.Series([0] * len(df))).apply(limpar_numero),
        'uso': df.get(col_uso, pd.Series(['Não informado'] * len(df))).fillna('Não informado'),
        'unidade_territorial': df.get(col_bairro, pd.Series(['Não informado'] * len(df))).fillna('Não informado')
    })

    df_transacao = pd.DataFrame({
        'id_imovel': df['id_imovel'],
        'municipio': 'Belo Horizonte',
        'granularidade': 'Individual',
        'valor_nominal': df.get(col_valor, pd.Series([0] * len(df))).apply(limpar_numero),
        'data_transacao': padronizar_data(df.get(col_data, pd.Series([None] * len(df))), dayfirst=True)
    })

    df_raw = empacotar_raw_json(df, 'id_imovel')
    return df_imovel, df_transacao, df_raw


def adaptador_sp(caminho):
    xls = pd.ExcelFile(caminho)
    abas_validas = [aba for aba in xls.sheet_names if
                    aba.upper() not in ['LEGENDA', 'EXPLICAÇÕES', 'TABELA DE USOS', 'TABELA DE PADRÕES', 'USOS',
                                        'PADRÕES']]

    df_list = []
    for aba in abas_validas:
        df_list.append(pd.read_excel(caminho, sheet_name=aba))
    df = pd.concat(df_list, ignore_index=True)

    col_sql = 'N° do Cadastro (SQL)' if 'N° do Cadastro (SQL)' in df.columns else df.columns[0]
    col_area_const = 'Área Construída (m2)' if 'Área Construída (m2)' in df.columns else 'Area Construida'
    col_area_terr = 'Área do Terreno (m2)' if 'Área do Terreno (m2)' in df.columns else 'Area Terreno'
    col_uso = 'Descrição do uso (IPTU)' if 'Descrição do uso (IPTU)' in df.columns else 'Uso'
    col_bairro = 'Bairro' if 'Bairro' in df.columns else 'Bairro'
    col_valor = 'Valor de Transação (declarado pelo contribuinte)' if 'Valor de Transação (declarado pelo contribuinte)' in df.columns else \
    df.columns[8]
    col_data = 'Data de Transação' if 'Data de Transação' in df.columns else 'Data'

    df['id_imovel'] = df[col_sql].astype(str).str.strip()

    df_imovel = pd.DataFrame({
        'id_imovel': df['id_imovel'],
        'municipio': 'São Paulo',
        'area_construida': df.get(col_area_const, pd.Series([0] * len(df))).apply(limpar_numero),
        'area_terreno': df.get(col_area_terr, pd.Series([0] * len(df))).apply(limpar_numero),
        'uso': df.get(col_uso, pd.Series(['Não informado'] * len(df))).fillna('Não informado'),
        'unidade_territorial': df.get(col_bairro, pd.Series(['Não informado'] * len(df))).fillna('Não informado')
    }).drop_duplicates(subset=['id_imovel'])

    df_transacao = pd.DataFrame({
        'id_imovel': df['id_imovel'],
        'municipio': 'São Paulo',
        'granularidade': 'Individual',
        'valor_nominal': df.get(col_valor, pd.Series([0] * len(df))).apply(limpar_numero),
        'data_transacao': padronizar_data(df.get(col_data, pd.Series([None] * len(df))), dayfirst=True)
    })

    df_raw = empacotar_raw_json(df, 'id_imovel')
    return df_imovel, df_transacao, df_raw


def adaptador_rj(caminho):
    df = pd.read_csv(caminho, sep=',', encoding='utf-8')
    df['id_imovel'] = 'RJ_AGG_' + df['objectid'].astype(str)

    df_imovel = pd.DataFrame({
        'id_imovel': df['id_imovel'],
        'municipio': 'Rio de Janeiro',
        'area_construida': df.get('média_área_construída', pd.Series([0] * len(df))).apply(limpar_numero),
        'area_terreno': 0.0,
        'uso': df.get('uso', pd.Series(['Não informado'] * len(df))).fillna('Não informado'),
        'unidade_territorial': df.get('bairro', pd.Series(['Não informado'] * len(df))).fillna('Não informado')
    }).drop_duplicates(subset=['id_imovel'])

    ano = df.get('ano_transação', pd.Series([1900] * len(df))).astype(str).str.replace(r'\.0', '', regex=True)
    mes = df.get('mês_transação', pd.Series([1] * len(df))).astype(str).str.replace(r'\.0', '', regex=True).str.zfill(2)
    data_str = ano + '-' + mes + '-01'

    df_transacao = pd.DataFrame({
        'id_imovel': df['id_imovel'],
        'municipio': 'Rio de Janeiro',
        'granularidade': 'Agregado',
        'valor_nominal': df.get('média_valor_transação', pd.Series([0] * len(df))).apply(limpar_numero),
        'data_transacao': padronizar_data(data_str)
    })

    df_raw = empacotar_raw_json(df, 'id_imovel')
    return df_imovel, df_transacao, df_raw


# ==========================================
# 4. ORQUESTRAÇÃO VISUAL
# ==========================================
def orquestrar():
    configuracoes = {
        'BH': (os.path.join(BASE_DIR, 'MG'), 'ITBI_BH', '.csv', adaptador_bh),
        'SP': (os.path.join(BASE_DIR, 'SP'), 'ITBI_SP', '.xlsx', adaptador_sp),
        'RJ': (os.path.join(BASE_DIR, 'RJ'), 'ITBI_RJ', '.csv', adaptador_rj)
    }

    total_transacoes = 0

    for cidade, (caminho_pasta, prefixo, extensao, funcao_adaptador) in configuracoes.items():
        if os.path.exists(caminho_pasta):
            arquivos = sorted([f for f in os.listdir(caminho_pasta) if f.startswith(prefixo) and f.endswith(extensao)])
            if not arquivos:
                continue

            print(f"\n{'=' * 50}")
            print(f"[{cidade}] Iniciando Ingestão Canônica + Raw JSON de {len(arquivos)} arquivo(s)...")

            barra_progresso = tqdm(
                arquivos,
                desc="Progresso",
                bar_format="{n_fmt}/{total_fmt} {bar} tempo esperado: {remaining}",
                colour="blue"
            )

            nome_tabela_raw = f"raw_{cidade.lower()}"

            for arq in barra_progresso:
                caminho_completo = os.path.join(caminho_pasta, arq)
                tqdm.write(f"  -> Processando: {arq}")

                try:
                    df_imovel, df_transacao, df_raw = funcao_adaptador(caminho_completo)

                    df_imovel.to_sql('stg_imovel', con=engine, if_exists='replace', index=False, chunksize=15000)

                    # Salva os dados RAW encapsulados em JSON (100% imune a mudanças de esquema)
                    df_raw.to_sql(nome_tabela_raw, con=engine, if_exists='append', index=False, chunksize=15000)

                    with engine.begin() as conn:
                        conn.execute(text("""
                            INSERT IGNORE INTO imovel (id_imovel, municipio, area_construida, area_terreno, uso, unidade_territorial)
                            SELECT id_imovel, municipio, area_construida, area_terreno, uso, unidade_territorial FROM stg_imovel
                        """))
                        conn.execute(text("DROP TABLE IF EXISTS stg_imovel"))

                    df_transacao.to_sql('transacao', con=engine, if_exists='append', index=False, chunksize=15000)

                    tqdm.write(
                        f"     [OK] Transações inseridas: {len(df_transacao)} | Base encapsulada enviada para '{nome_tabela_raw}'")
                    total_transacoes += len(df_transacao)
                except Exception as e:
                    tqdm.write(f"     [ERRO] Falha no arquivo {arq}. Detalhe: {str(e)[:150]}")

    print(f"\n{'=' * 50}")
    print(f"CARGA FINALIZADA! Total de transações processadas no banco: {total_transacoes}")


if __name__ == '__main__':
    orquestrar()
