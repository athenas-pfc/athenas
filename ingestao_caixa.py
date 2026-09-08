import os
import re
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime
from playwright.sync_api import sync_playwright

# ==========================================
# 1. PREPARAÇÃO DO BANCO DE DADOS
# ==========================================
senha = 'athenas'
engine_setup = create_engine(
    f'mysql+pymysql://root:{senha}@localhost:3306/',
    isolation_level="AUTOCOMMIT"
)

with engine_setup.connect() as conn:
    conn.execute(text("CREATE DATABASE IF NOT EXISTS leiloes_db"))

engine = create_engine(f'mysql+pymysql://root:{senha}@localhost:3306/leiloes_db')

ddls = [
    """
    CREATE TABLE IF NOT EXISTS lote (
        id_lote VARCHAR(50) PRIMARY KEY,
        estado VARCHAR(2),
        municipio VARCHAR(100),
        endereco_bruto TEXT,
        tipologia VARCHAR(100),
        area_total DECIMAL(15,2)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS oferta_historica (
        id_oferta INT AUTO_INCREMENT PRIMARY KEY,
        id_lote VARCHAR(50),
        data_extracao DATE,
        modalidade VARCHAR(100),
        valor_avaliacao DECIMAL(15,2),
        preco_minimo DECIMAL(15,2),
        UNIQUE KEY uk_lote_data (id_lote, data_extracao),
        FOREIGN KEY (id_lote) REFERENCES lote(id_lote)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS oferta_atual (
        id_oferta INT AUTO_INCREMENT PRIMARY KEY,
        id_lote VARCHAR(50),
        data_extracao DATE,
        modalidade VARCHAR(100),
        valor_avaliacao DECIMAL(15,2),
        preco_minimo DECIMAL(15,2),
        FOREIGN KEY (id_lote) REFERENCES lote(id_lote)
    );
    """
]

print("Preparando Estrutura do Banco de Leilões (leiloes_db)...")
with engine.begin() as conn:
    for ddl in ddls:
        conn.execute(text(ddl))
    conn.execute(text("TRUNCATE TABLE oferta_atual"))


# ==========================================
# 2. FUNÇÕES DE HIGIENIZAÇÃO E PARSERS
# ==========================================
def limpar_moeda(valor):
    if pd.isna(valor):
        return 0.0
    v = str(valor).replace('R$', '').strip()
    if '.' in v and ',' in v:
        v = v.replace('.', '').replace(',', '.')
    else:
        v = v.replace(',', '.')
    try:
        return float(v)
    except Exception:
        return 0.0


def normalizar_metragem(valor):
    if pd.isna(valor):
        return 0.0
    v_str = str(valor).strip()
    if '.' in v_str and ',' in v_str:
        v_str = v_str.replace('.', '').replace(',', '.')
    else:
        v_str = v_str.replace(',', '.')
    try:
        return float(v_str)
    except Exception:
        return 0.0


def extrair_metragem_descricao(texto, tipologia=''):
    """
    Extrai metragem de acordo com a tipologia do imóvel:
    - Apartamento: Privativa é soberana (elimina captura de área de condomínio).
    - Casa: Prioriza privativa/construída e total.
    - Terreno: Captura área de terreno/gleba.
    """
    if pd.isna(texto):
        return 0.0
    t = str(texto).lower()

    def capturar(padrao):
        m = re.search(padrao, t, flags=re.IGNORECASE)
        if m:
            return normalizar_metragem(m.group(1))
        return 0.0

    a_priv = capturar(r'([\d\.\,]+)\s*(?:de\s+)?(?:[áa]rea\s+privativa|[áa]rea\s+constru[íi]da)') or \
             capturar(r'(?:[áa]rea\s+privativa|[áa]rea\s+constru[íi]da)\s*(?:=|de|:)?\s*([\d\.\,]+)')

    a_tot  = capturar(r'([\d\.\,]+)\s*(?:de\s+)?[áa]rea\s+total') or \
             capturar(r'[áa]rea\s+total\s*(?:=|de|:)?\s*([\d\.\,]+)')

    a_terr = capturar(r'([\d\.\,]+)\s*(?:de\s+)?[áa]rea\s+(?:do\s+)?terreno') or \
             capturar(r'[áa]rea\s+(?:do\s+)?terreno\s*(?:=|de|:)?\s*([\d\.\,]+)')

    a_m2   = capturar(r'([\d\.\,]+)\s*(?:m2|m²)')

    tipo = str(tipologia).lower()

    # 1. Regra para Apartamentos (área privativa é estritamente prioritária)
    if 'apartamento' in tipo or 'apto' in tipo:
        if 15.0 <= a_priv <= 600.0:
            return a_priv
        if 15.0 <= a_tot <= 600.0:
            return a_tot
        if 15.0 <= a_m2 <= 600.0:
            return a_m2
        return a_priv if a_priv > 0 else 0.0

    # 2. Regra para Casas
    if 'casa' in tipo or 'sobrado' in tipo:
        if 20.0 <= a_priv <= 1500.0:
            return a_priv
        if 20.0 <= a_tot <= 1500.0:
            return a_tot
        if 20.0 <= a_terr <= 5000.0:
            return a_terr
        return a_priv if a_priv > 0 else (a_tot if a_tot > 0 else a_m2)

    # 3. Regra para Terrenos / Glebas
    if 'terreno' in tipo or 'gleba' in tipo or 'lote' in tipo:
        if a_terr > 0.0:
            return a_terr
        if a_tot > 0.0:
            return a_tot
        return a_m2

    # 4. Fallback Geral
    if a_priv > 0.0: return a_priv
    if a_tot > 0.0:  return a_tot
    if a_terr > 0.0: return a_terr
    return a_m2


def extrair_data_arquivo(nome_arquivo):
    match_8 = re.search(r'_(\d{8})\.csv', nome_arquivo)
    if match_8:
        try:
            return datetime.strptime(match_8.group(1), '%d%m%Y').strftime('%Y-%m-%d')
        except Exception:
            pass

    match_6 = re.search(r'_(\d{6})\.csv', nome_arquivo)
    if match_6:
        try:
            return datetime.strptime(match_6.group(1), '%d%m%y').strftime('%Y-%m-%d')
        except Exception:
            pass

    return datetime.today().strftime('%Y-%m-%d')


# ==========================================
# 3. EXTRAÇÃO VIA PLAYWRIGHT
# ==========================================
def baixar_arquivos_caixa_playwright(base_dir):
    print("\n[DOWNLOAD] Verificando novos arquivos no portal da Caixa Econômica...")
    try:
        with sync_playwright() as p:
            # 1. MUDANÇA: Abrir o navegador fisicamente na tela. WAFs bloqueiam navegadores fantasmas.
            browser = p.chromium.launch(headless=False)

            # 2. MUDANÇA: Injetar User-Agent fingindo ser um usuário humano comum no Chrome
            context = browser.new_context(
                accept_downloads=True,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            url_portal = "https://venda-imoveis.caixa.gov.br/sistema/download-lista.asp"

            estados = ['SP', 'MG', 'RJ']
            for estado in estados:
                caminho_pasta = os.path.join(base_dir, estado)
                os.makedirs(caminho_pasta, exist_ok=True)

                data_hoje = datetime.today().strftime('%d%m%Y')
                nome_arquivo = f"Lista_imoveis_{estado}_{data_hoje}.csv"
                caminho_completo = os.path.join(caminho_pasta, nome_arquivo)

                if os.path.exists(caminho_completo):
                    print(f"  -> [OK] {estado}: Arquivo de hoje ({nome_arquivo}) já existe localmente.")
                    continue

                print(f"  -> Solicitando lote diário para o estado {estado}...")
                page.goto(url_portal, timeout=60000)

                # 3. MUDANÇA: Esperar explicitamente o formulário renderizar na tela antes de clicar
                try:
                    page.wait_for_selector("#cmb_estado", state="visible", timeout=30000)
                except Exception:
                    print(f"  -> [ERRO] O portal da Caixa bloqueou a tela ou está fora do ar para {estado}.")
                    continue

                page.locator("#cmb_estado").select_option(estado)

                with page.expect_download(timeout=90000) as download_info:
                    page.get_by_role("button", name=" Próximo").click()

                download = download_info.value
                download.save_as(caminho_completo)
                print(f"     [SUCESSO] Planilha recebida e arquivada: {nome_arquivo}")

            # Pequena pausa antes de encerrar o contexto para garantir segurança do I/O
            page.wait_for_timeout(2000)
            browser.close()

    except Exception as e:
        print(f"  -> [AVISO CRÍTICO] Falha durante a rotina Playwright: {e}")


# ==========================================
# 4. PROCESSAMENTO E CARGA (UPSERT)
# ==========================================
def processar_arquivo_caixa(caminho_arquivo, estado, is_latest):
    data_extracao = extrair_data_arquivo(os.path.basename(caminho_arquivo))
    print(f"\n--- Processando {os.path.basename(caminho_arquivo)} (Data: {data_extracao}) ---")

    header_idx = 1
    try:
        with open(caminho_arquivo, 'r', encoding='latin1') as f:
            for i, linha in enumerate(f):
                if 'Cidade' in linha or 'UF' in linha or 'Bairro' in linha:
                    header_idx = i
                    break
    except Exception as e:
        print(f"     [ERRO] Falha ao inspecionar o arquivo: {e}")
        return

    try:
        df = pd.read_csv(caminho_arquivo, sep=';', encoding='latin1', skiprows=header_idx, on_bad_lines='skip')
    except Exception as e:
        print(f"     [ERRO] Leitura via Pandas falhou: {e}")
        return

    df = df.dropna(how='all')

    df.columns = (df.columns.astype(str).str.strip().str.lower()
                  .str.replace(r'[^a-z0-9]', '_', regex=True)
                  .str.replace(r'_+', '_', regex=True)
                  .str.strip('_'))

    col_id = next((c for c in df.columns if 'im_vel' in c or 'imovel' in c or 'n_do' in c), df.columns[0])
    col_cidade = next((c for c in df.columns if 'cidade' in c), None)
    col_endereco = next((c for c in df.columns if 'endere' in c), None)
    col_preco = next((c for c in df.columns if 'pre_o' in c or 'preco' in c or 'venda' in c), None)
    col_avaliacao = next((c for c in df.columns if 'avalia' in c), None)
    col_modalidade = next((c for c in df.columns if 'modalidade' in c), None)
    col_descricao = next((c for c in df.columns if 'descri' in c), None)

    df = df[df[col_id].notna()]
    df[col_id] = df[col_id].astype(str).str.strip()
    df = df[df[col_id] != 'nan']

    if df.empty:
        print("     [AVISO] Nenhuma oferta válida encontrada na planilha.")
        return

    # Extração de tipologia e cálculo de área sensível à tipologia
    if col_descricao:
        df['tipologia_ext'] = (df[col_descricao].fillna('Não informada')
                               .astype(str).str.split(',').str[0].str.strip())
        df['tipologia_ext'] = df['tipologia_ext'].replace({'': 'Não informada', 'nan': 'Não informada'})
        df['area_ext'] = [
            extrair_metragem_descricao(desc, tipo)
            for desc, tipo in zip(df[col_descricao], df['tipologia_ext'])
        ]
    else:
        df['tipologia_ext'] = 'Não informada'
        df['area_ext'] = 0.0

    df_lote = pd.DataFrame({
        'id_lote': df[col_id],
        'estado': estado,
        'municipio': df[col_cidade] if col_cidade else '',
        'endereco_bruto': df[col_endereco] if col_endereco else '',
        'tipologia': df['tipologia_ext'],
        'area_total': df['area_ext'].fillna(0.0)
    }).drop_duplicates(subset=['id_lote'])

    df_oferta = pd.DataFrame({
        'id_lote': df[col_id],
        'data_extracao': data_extracao,
        'modalidade': df[col_modalidade] if col_modalidade else 'Leilão',
        'valor_avaliacao': df[col_avaliacao].apply(limpar_moeda) if col_avaliacao else 0.0,
        'preco_minimo': df[col_preco].apply(limpar_moeda) if col_preco else 0.0
    })

    # Carga idempotente no Lote Mestre com UPSERT
    df_lote.to_sql('stg_lote', con=engine, if_exists='replace', index=False)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO lote (id_lote, estado, municipio, endereco_bruto, tipologia, area_total)
            SELECT id_lote, estado, municipio, endereco_bruto, tipologia, area_total FROM stg_lote
            ON DUPLICATE KEY UPDATE
                estado = VALUES(estado),
                municipio = VALUES(municipio),
                endereco_bruto = VALUES(endereco_bruto),
                tipologia = VALUES(tipologia),
                area_total = VALUES(area_total);
        """))

    # Carga no Histórico de Ofertas
    df_oferta.to_sql('stg_oferta', con=engine, if_exists='replace', index=False)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO oferta_historica (id_lote, data_extracao, modalidade, valor_avaliacao, preco_minimo)
            SELECT id_lote, data_extracao, modalidade, valor_avaliacao, preco_minimo FROM stg_oferta
            ON DUPLICATE KEY UPDATE
                modalidade = VALUES(modalidade),
                valor_avaliacao = VALUES(valor_avaliacao),
                preco_minimo = VALUES(preco_minimo);
        """))
        conn.execute(text("DROP TABLE IF EXISTS stg_lote, stg_oferta"))

    print(f"     [OK] Histórico consolidado! {len(df_oferta)} ofertas registradas.")

    # Atualização da vitrine corrente
    if is_latest:
        df_oferta.to_sql('oferta_atual', con=engine, if_exists='append', index=False)
        print(f"     -> [VITRINE ATUALIZADA] {len(df_oferta)} ofertas ativas enviadas para 'oferta_atual'.")


# ==========================================
# 5. ORQUESTRADOR GERAL
# ==========================================
def orquestrar_ingestao():
    base_dir = '/home/luan/Documentos/FESA/TCC/Bases de Dados/Leiloes'

    baixar_arquivos_caixa_playwright(base_dir)

    diretorios = {
        'SP': os.path.join(base_dir, 'SP'),
        'MG': os.path.join(base_dir, 'MG'),
        'RJ': os.path.join(base_dir, 'RJ')
    }

    print("\n[PROCESSAMENTO] Iniciando sincronização no MySQL...")
    for estado, caminho_pasta in diretorios.items():
        if not os.path.exists(caminho_pasta):
            continue

        arquivos = [arq for arq in os.listdir(caminho_pasta) if arq.startswith('Lista_imoveis') and arq.endswith('.csv')]
        if not arquivos:
            continue

        arquivos.sort(key=lambda x: extrair_data_arquivo(x))
        arquivo_mais_recente = arquivos[-1]

        for arq in arquivos:
            caminho_completo = os.path.join(caminho_pasta, arq)
            is_latest = (arq == arquivo_mais_recente)
            processar_arquivo_caixa(caminho_completo, estado, is_latest)


if __name__ == '__main__':
    orquestrar_ingestao()
    print("\nOrquestração concluída com sucesso! Lotes e vitrine sincronizados.")