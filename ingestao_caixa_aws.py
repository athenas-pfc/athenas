"""
Lambda 1 - EXTRACAO (urllib3 + proxy rotativo)
Baixa a lista publica de imoveis da Caixa e grava o arquivo bruto,
sem nenhuma transformacao, na camada RAW do S3.

A origem fica atras do Radware Bot Manager, que responde com pagina de
desafio no lugar do CSV. A estrategia aqui e nao parecer automacao:
cabecalhos coerentes com o User-Agent declarado, sessao TLS nova a cada
tentativa e IP de saida diferente a cada rodada.
"""

import hashlib
import os
import random
import time
import urllib3
import boto3
from urllib.parse import urlparse, urljoin, unquote

from pipeline_log import etapa_monitorada, data_referencia

# Variaveis de ambiente
BUCKET = os.environ.get("BUCKET", "").strip()
BASE_URL = os.environ.get("BASE_URL", "https://venda-imoveis.caixa.gov.br/listaweb").strip()
PAGINA_ORIGEM = os.environ.get(
    "PAGINA_ORIGEM",
    "https://venda-imoveis.caixa.gov.br/sistema/download-lista.asp").strip()
PREFIXO_RAW = os.environ.get("PREFIXO_RAW", "raw/caixa").strip()
TENTATIVAS = int(os.environ.get("TENTATIVAS", "5"))
TIMEOUT = int(os.environ.get("TIMEOUT_HTTP", "60"))
LIMITE_REDIRECIONAMENTOS = int(os.environ.get("LIMITE_REDIRECIONAMENTOS", "5"))
PROXY_URL = os.environ.get("PROXY_URL", "").strip()

# Versao do Chrome anunciada. Precisa bater com o sec-ch-ua montado abaixo:
# UA de uma versao e client hints de outra e sinal de automacao.
VERSAO_CHROME = os.environ.get("VERSAO_CHROME", "126").strip()

USER_AGENT = (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              f"(KHTML, like Gecko) Chrome/{VERSAO_CHROME}.0.0.0 Safari/537.36")

SEC_CH_UA = (f'"Not/A)Brand";v="8", "Chromium";v="{VERSAO_CHROME}", '
             f'"Google Chrome";v="{VERSAO_CHROME}"')

REDIRECIONAMENTOS = (301, 302, 303, 307, 308)

MARCADORES_HTML = (b"<html", b"<head", b"<body", b"<!doctype",
                   b"<script", b"<meta", b"<title")

# Dominios do Radware Bot Manager: se a URL final cair aqui, foi desafio.
DOMINIOS_DESAFIO = ("perfdrive.com", "shieldsquare.com", "validate.perfdrive")


class ErroDownload(Exception):
    """Erro recuperavel de rede - a Step Function faz retry nesta excecao."""


class ErroProxy(ErroDownload):
    """Falha de autenticacao ou conexao com o proxy, nao com a origem."""


class ErroBloqueio(ErroDownload):
    """A origem respondeu, mas devolveu desafio anti-bot em vez do arquivo."""


def _abrir_sessao():
    """Cria um cliente novo, com sessao TLS e IP de saida proprios.

    Deliberadamente NAO e cacheado entre tentativas: reaproveitar a conexao
    mantem o mesmo IP, e um IP ja marcado pelo bot manager continua marcado.
    """
    if not PROXY_URL:
        print("[INFO] Sem proxy configurado: conexao direta.")
        return urllib3.PoolManager()

    partes = urlparse(PROXY_URL)
    if not partes.hostname:
        raise ValueError(f"PROXY_URL invalida: {PROXY_URL!r}")

    endereco = f"{partes.scheme or 'http'}://{partes.hostname}:{partes.port or 80}"

    cabecalhos_proxy = None
    if partes.username and partes.password:
        auth = f"{unquote(partes.username)}:{unquote(partes.password)}"
        cabecalhos_proxy = urllib3.make_headers(proxy_basic_auth=auth)

    return urllib3.ProxyManager(endereco, proxy_headers=cabecalhos_proxy)


def _cabecalhos(destino: str, jarra: dict) -> dict:
    """Conjunto de cabecalhos na ordem e na composicao que o Chrome usa.

    Client hints e Sec-Fetch-* nao sao decoracao: o Chrome real sempre os
    envia, e a ausencia deles e um dos sinais mais baratos de automacao.
    """
    cabecalhos = {
        "Connection": "keep-alive",
        "sec-ch-ua": SEC_CH_UA,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": USER_AGENT,
    }

    if destino == "pagina":
        cabecalhos.update({
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                       "image/avif,image/webp,image/apng,*/*;q=0.8"),
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-Fetch-Dest": "document",
        })
    else:
        cabecalhos.update({
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                       "image/avif,image/webp,image/apng,*/*;q=0.8"),
            "Referer": PAGINA_ORIGEM,
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-Fetch-Dest": "document",
        })

    # Sem brotli/zstd de proposito: o urllib3 do runtime nao descomprime os
    # dois, e anunciar o que nao se sabe ler quebra o download.
    cabecalhos["Accept-Encoding"] = "gzip, deflate"
    cabecalhos["Accept-Language"] = "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"

    if jarra:
        cabecalhos["Cookie"] = "; ".join(f"{n}={v}" for n, v in jarra.items())
    return cabecalhos


def _coletar_cookies(resposta, jarra: dict) -> None:
    """Acumula os Set-Cookie da resposta na jarra da sessao."""
    try:
        brutos = resposta.headers.getlist("Set-Cookie")
    except AttributeError:
        unico = resposta.headers.get("Set-Cookie")
        brutos = [unico] if unico else []

    for bruto in brutos:
        par = bruto.split(";")[0].strip()
        if "=" in par:
            nome, _, valor = par.partition("=")
            jarra[nome.strip()] = valor.strip()


def _validar_csv(conteudo: bytes, tipo: str, url_final: str) -> None:
    """Confirma que o corpo e mesmo o CSV, e nao uma pagina de desafio."""
    amostra = conteudo[:4096].lower()
    inicio = conteudo[:400].decode("latin1", errors="replace").replace("\n", " | ")

    if any(dominio in url_final.lower() for dominio in DOMINIOS_DESAFIO):
        raise ErroBloqueio(f"desafio anti-bot (Radware) | URL final: {url_final}")

    if "text/html" in (tipo or "").lower():
        raise ErroBloqueio(f"Content-Type text/html | inicio: {inicio}")

    for marcador in MARCADORES_HTML:
        if marcador in amostra:
            raise ErroBloqueio(
                f"corpo contem marcador HTML {marcador.decode()} | inicio: {inicio}")

    if conteudo[:2048].count(b";") < 5:
        raise ErroBloqueio(f"corpo sem delimitadores de CSV | inicio: {inicio}")


def _requisitar(cliente, url: str, destino: str, jarra: dict, rotulo: str):
    """GET seguindo redirecionamentos na mao, registrando cada salto."""
    atual = url

    for salto in range(LIMITE_REDIRECIONAMENTOS + 1):
        resposta = cliente.request(
            "GET", atual,
            headers=_cabecalhos(destino, jarra),
            timeout=TIMEOUT, retries=False, redirect=False,
        )
        _coletar_cookies(resposta, jarra)

        if resposta.status not in REDIRECIONAMENTOS:
            return resposta, atual

        local = resposta.headers.get("Location", "")
        if not local:
            raise ErroDownload(f"{rotulo}: HTTP {resposta.status} sem Location")

        proximo = urljoin(atual, local)
        if any(d in proximo.lower() for d in DOMINIOS_DESAFIO):
            raise ErroBloqueio(
                f"{rotulo}: redirecionado ao desafio anti-bot no salto {salto + 1}")

        print(f"[INFO] {rotulo}: salto {salto + 1} | HTTP {resposta.status} -> {proximo}")
        atual = proximo

    raise ErroDownload(
        f"{rotulo}: excedeu {LIMITE_REDIRECIONAMENTOS} redirecionamentos "
        f"(ultimo destino: {atual})")


def baixar(url: str) -> bytes:
    ultimo_erro = None

    for tentativa in range(1, TENTATIVAS + 1):
        jarra = {}
        cliente = _abrir_sessao()
        try:
            # 1. AQUECIMENTO: o navegador real passa pela pagina antes do arquivo.
            print(f"[INFO] Tentativa {tentativa}/{TENTATIVAS} | aquecendo em {PAGINA_ORIGEM}")
            resp_aquecimento, _ = _requisitar(
                cliente, PAGINA_ORIGEM, "pagina", jarra, "aquecimento")
            print(f"[INFO] Aquecimento HTTP {resp_aquecimento.status} "
                  f"| cookies: {sorted(jarra) or 'nenhum'}")

            # Pausa curta: humano nao clica no link no mesmo milissegundo.
            time.sleep(random.uniform(1.5, 4.0))

            # 2. DOWNLOAD DO CSV
            print(f"[INFO] Baixando arquivo: {url}")
            resp_arquivo, url_final = _requisitar(
                cliente, url, "arquivo", jarra, "download")

            conteudo = resp_arquivo.data
            status = resp_arquivo.status
            tipo = resp_arquivo.headers.get("Content-Type", "")
            print(f"[INFO] HTTP {status} | {len(conteudo)} bytes | Tipo: {tipo} "
                  f"| URL final: {url_final}")

            if status == 407:
                raise ErroProxy("proxy recusou a autenticacao (407) na resposta")
            if status != 200:
                detalhe = conteudo[:200].decode("latin1", errors="replace").replace("\n", " ")
                raise ErroDownload(f"HTTP {status} | corpo: {detalhe}")
            if not conteudo or len(conteudo) < 1024:
                raise ErroDownload(f"conteudo vazio ou truncado ({len(conteudo)} bytes)")

            _validar_csv(conteudo, tipo, url_final)
            return conteudo

        except urllib3.exceptions.ProxyError as erro:
            ultimo_erro = ErroProxy(f"falha no tunel do proxy: {erro}")
            print(f"[ERRO] tentativa {tentativa} nao passou do proxy: {erro}")
        except ErroBloqueio as erro:
            ultimo_erro = erro
            print(f"[WARN] tentativa {tentativa} bloqueada pelo anti-bot: {erro}")
        except urllib3.exceptions.HTTPError as erro:
            ultimo_erro = erro
            print(f"[WARN] tentativa {tentativa} falhou na camada HTTP: {erro}")
        except ErroDownload as erro:
            ultimo_erro = erro
            print(f"[WARN] tentativa {tentativa} falhou no arquivo: {erro}")
        except Exception as erro:
            ultimo_erro = erro
            print(f"[WARN] tentativa {tentativa} falhou inesperadamente: {erro}")
        finally:
            # Encerra o pool: a proxima tentativa abre sessao e IP novos.
            cliente.clear()

        if tentativa < TENTATIVAS:
            espera = min(60, 5 * tentativa) + random.uniform(0, 5)
            print(f"[INFO] aguardando {espera:.1f}s antes da proxima tentativa")
            time.sleep(espera)

    if isinstance(ultimo_erro, ErroProxy):
        raise ErroProxy(
            f"falha ao baixar {url}: o proxy nao autenticou. Detalhe: {ultimo_erro}")
    if isinstance(ultimo_erro, ErroBloqueio):
        raise ErroBloqueio(
            f"falha ao baixar {url} apos {TENTATIVAS} tentativas: a origem devolveu "
            f"desafio anti-bot em todas. Detalhe: {ultimo_erro}")

    raise ErroDownload(f"falha ao baixar {url} apos {TENTATIVAS} tentativas: {ultimo_erro}")


@etapa_monitorada("EXTRACAO")
def lambda_handler(event, context):
    if not BUCKET:
        raise ValueError("variavel de ambiente BUCKET nao configurada na funcao Lambda")
    if "uf" not in event:
        raise ValueError(f"payload sem a chave 'uf'. Recebido: {sorted(event)}")

    uf = event["uf"].upper()
    dt = event.get("data_referencia") or data_referencia()

    nome_arquivo = f"Lista_imoveis_{uf}.csv"
    url = f"{BASE_URL}/{nome_arquivo}"

    conteudo = baixar(url)
    digest = hashlib.sha256(conteudo).hexdigest()

    chave = f"{PREFIXO_RAW}/uf={uf}/dt={dt}/{nome_arquivo}"
    boto3.client("s3").put_object(
        Bucket=BUCKET,
        Key=chave,
        Body=conteudo,
        ContentType="text/csv; charset=ISO-8859-1",
        Metadata={"origem": url, "sha256": digest, "uf": uf, "dt": dt},
    )

    return {
        "uf": uf,
        "data_referencia": dt,
        "bucket": BUCKET,
        "chave_raw": chave,
        "uri_raw": f"s3://{BUCKET}/{chave}",
        "bytes": len(conteudo),
        "hash_sha256": digest,
        "mensagem": f"{len(conteudo)} bytes gravados em s3://{BUCKET}/{chave}",
    }