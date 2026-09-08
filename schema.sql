-- ========================================================
-- BANCO DE LEILÕES (Base Volátil e Snapshots)
-- ========================================================
CREATE DATABASE IF NOT EXISTS leiloes_db;
USE leiloes_db;

-- Cadastro físico do lote anunciado
CREATE TABLE lote (
    id_lote VARCHAR(50) PRIMARY KEY,
    estado VARCHAR(2),
    municipio VARCHAR(100),
    endereco_bruto TEXT,
    tipologia VARCHAR(100),
    area_total DECIMAL(15,2)
);

-- Arquivamento intocável das ofertas passadas (Snapshots)
CREATE TABLE oferta_historica (
    id_oferta INT AUTO_INCREMENT PRIMARY KEY,
    id_lote VARCHAR(50),
    data_extracao DATE,
    modalidade VARCHAR(100),
    valor_avaliacao DECIMAL(15,2),
    preco_minimo DECIMAL(15,2),
    UNIQUE KEY uk_lote_data (id_lote, data_extracao), -- Garante a Idempotência
    FOREIGN KEY (id_lote) REFERENCES lote(id_lote)
);

-- Vitrine de acesso rápido com apenas a carga do dia (Truncada a cada execução)
CREATE TABLE oferta_atual (
    id_oferta INT AUTO_INCREMENT PRIMARY KEY,
    id_lote VARCHAR(50),
    data_extracao DATE,
    modalidade VARCHAR(100),
    valor_avaliacao DECIMAL(15,2),
    preco_minimo DECIMAL(15,2),
    FOREIGN KEY (id_lote) REFERENCES lote(id_lote)
);


-- ========================================================
-- BANCO DE MERCADO (Base Imutável de Transações)
-- ========================================================
CREATE DATABASE IF NOT EXISTS mercado_db;
USE mercado_db;

-- Núcleo canônico com as características físicas homogeneizadas
CREATE TABLE imovel (
    id_imovel VARCHAR(100) PRIMARY KEY,
    municipio VARCHAR(100),
    area_construida DECIMAL(15,2),
    area_terreno DECIMAL(15,2),
    uso TEXT,
    unidade_territorial VARCHAR(100)
);

-- Fato de transações com recolhimento de ITBI
CREATE TABLE transacao (
    id_transacao INT AUTO_INCREMENT PRIMARY KEY,
    id_imovel VARCHAR(100),
    municipio VARCHAR(100),
    granularidade VARCHAR(50), -- Indica se é 'Individual' ou 'Agregado'
    valor_nominal DECIMAL(15,2),
    data_transacao VARCHAR(10),
    FOREIGN KEY (id_imovel) REFERENCES imovel(id_imovel)
);

-- Camada Raw (Isolamento de sujeira via JSON)
-- Nota: O Pandas cria essas tabelas implicitamente, mas este é o DDL real sob o capô
CREATE TABLE raw_bh (
    id_imovel VARCHAR(100),
    atributos_raw JSON
);

CREATE TABLE raw_sp (
    id_imovel VARCHAR(100),
    atributos_raw JSON
);

CREATE TABLE raw_rj (
    id_imovel VARCHAR(100),
    atributos_raw JSON
);