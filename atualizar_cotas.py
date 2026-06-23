#!/usr/bin/env python3
"""
Robô CVM — Aporta
Baixa o Informe Diário de Fundos da CVM, extrai a cota mais recente
dos CNPJs listados em cnpjs.json e grava o resultado em cotas.json.

Uso normal (GitHub Actions):  python atualizar_cotas.py
Uso em teste com arquivo local: python atualizar_cotas.py --arquivo caminho/arquivo.zip
"""

import csv
import io
import json
import re
import socket
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Força IPv4: os runners do GitHub Actions não têm rota IPv6, e o servidor
# da CVM anuncia endereço IPv6 — sem isto, dá "Network is unreachable".
# ---------------------------------------------------------------------------
_getaddrinfo_original = socket.getaddrinfo

def _getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
    return _getaddrinfo_original(host, port, socket.AF_INET, type, proto, flags)

socket.getaddrinfo = _getaddrinfo_ipv4

URL_BASE = "https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/inf_diario_fi_{ano_mes}.zip"
ARQ_CNPJS = "cnpjs.json"
ARQ_SAIDA = "cotas.json"

# Fuso de Brasília (UTC-3, sem horário de verão desde 2019)
TZ_BR = timezone(timedelta(hours=-3))


def so_digitos(cnpj: str) -> str:
    return re.sub(r"\D", "", cnpj or "")


def carregar_cnpjs() -> dict:
    """Lê cnpjs.json -> {cnpj_digitos: nome}"""
    with open(ARQ_CNPJS, encoding="utf-8") as f:
        bruto = json.load(f)
    alvos = {}
    for cnpj, nome in bruto.items():
        digitos = so_digitos(cnpj)
        if len(digitos) != 14:
            print(f"AVISO: CNPJ inválido ignorado: {cnpj}")
            continue
        alvos[digitos] = nome
    return alvos


def baixar_zip(ano_mes: str, tentativas: int = 4) -> bytes | None:
    """Baixa o zip do mês. Retorna None se o arquivo não existir (404).
    Em erros de rede, tenta de novo com espera progressiva (20s, 40s, 60s)."""
    url = URL_BASE.format(ano_mes=ano_mes)
    ultimo_erro = None
    for i in range(1, tentativas + 1):
        print(f"Baixando {url} (tentativa {i}/{tentativas}) ...")
        req = urllib.request.Request(url, headers={"User-Agent": "aporta-cotas/1.1"})
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"Arquivo de {ano_mes} ainda não disponível (404).")
                return None
            ultimo_erro = e
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            ultimo_erro = e
        if i < tentativas:
            espera = 20 * i
            print(f"Falhou ({ultimo_erro}). Aguardando {espera}s antes de tentar de novo...")
            time.sleep(espera)
    raise RuntimeError(f"Download de {url} falhou após {tentativas} tentativas: {ultimo_erro}")


def extrair_cotas(conteudo_zip: bytes, alvos: dict) -> dict:
    """
    Lê o(s) CSV(s) dentro do zip e devolve, por CNPJ alvo, o registro
    de data mais recente: {cnpj: {"cota": float, "data": "AAAA-MM-DD"}}
    """
    resultado = {}
    with zipfile.ZipFile(io.BytesIO(conteudo_zip)) as zf:
        for nome_interno in zf.namelist():
            if not nome_interno.lower().endswith(".csv"):
                continue
            with zf.open(nome_interno) as f_bin:
                # CVM usa ; como separador; encoding pode variar
                texto = io.TextIOWrapper(f_bin, encoding="utf-8", errors="replace")
                leitor = csv.DictReader(texto, delimiter=";")
                campos = leitor.fieldnames or []
                col_cnpj = next(
                    (c for c in ("CNPJ_FUNDO_CLASSE", "CNPJ_FUNDO") if c in campos), None
                )
                if not col_cnpj or "VL_QUOTA" not in campos or "DT_COMPTC" not in campos:
                    print(f"AVISO: colunas esperadas ausentes em {nome_interno}: {campos}")
                    continue
                for linha in leitor:
                    cnpj = so_digitos(linha.get(col_cnpj, ""))
                    if cnpj not in alvos:
                        continue
                    data = (linha.get("DT_COMPTC") or "").strip()
                    vl_quota = (linha.get("VL_QUOTA") or "").strip()
                    if not data or not vl_quota:
                        continue
                    try:
                        cota = float(vl_quota.replace(",", "."))
                    except ValueError:
                        continue
                    atual = resultado.get(cnpj)
                    if atual is None or data > atual["data"]:
                        resultado[cnpj] = {"cota": cota, "data": data}
    return resultado


def ano_mes_de(data) -> str:
    return f"{data.year:04d}{data.month:02d}"


def mes_anterior(data):
    return (data.replace(day=1) - timedelta(days=1))


def carregar_saida_anterior() -> dict:
    try:
        with open(ARQ_SAIDA, encoding="utf-8") as f:
            return json.load(f).get("fundos", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def main():
    alvos = carregar_cnpjs()
    if not alvos:
        print("ERRO: nenhum CNPJ válido em cnpjs.json")
        sys.exit(1)
    print(f"{len(alvos)} CNPJs alvo carregados.")

    cotas = {}

    # Modo teste: --arquivo caminho.zip
    if "--arquivo" in sys.argv:
        caminho = sys.argv[sys.argv.index("--arquivo") + 1]
        print(f"[MODO TESTE] Lendo arquivo local: {caminho}")
        with open(caminho, "rb") as f:
            cotas = extrair_cotas(f.read(), alvos)
    else:
        hoje = datetime.now(TZ_BR)
        # 1) mês corrente (com fallback p/ mês anterior se ainda não publicado)
        dados = baixar_zip(ano_mes_de(hoje))
        if dados is None:
            dados = baixar_zip(ano_mes_de(mes_anterior(hoje)))
            if dados is None:
                print("ERRO: nenhum arquivo disponível na CVM.")
                sys.exit(1)
            cotas = extrair_cotas(dados, alvos)
        else:
            cotas = extrair_cotas(dados, alvos)
            # 2) CNPJs sem registro no mês corrente (ex.: início de mês):
            #    completar com o mês anterior
            faltantes = {c: n for c, n in alvos.items() if c not in cotas}
            if faltantes:
                print(f"{len(faltantes)} CNPJ(s) sem cota no mês corrente; buscando mês anterior...")
                dados_ant = baixar_zip(ano_mes_de(mes_anterior(hoje)))
                if dados_ant:
                    cotas.update(extrair_cotas(dados_ant, faltantes))

    # 3) Última rede de segurança: manter valor da execução anterior
    anteriores = carregar_saida_anterior()
    fundos = {}
    for cnpj, nome in alvos.items():
        if cnpj in cotas:
            fundos[cnpj] = {
                "nome": nome,
                "cota": cotas[cnpj]["cota"],
                "data": cotas[cnpj]["data"],
            }
        elif cnpj in anteriores:
            fundos[cnpj] = anteriores[cnpj]
            print(f"AVISO: {nome} ({cnpj}) sem cota nova; mantido valor anterior de {anteriores[cnpj].get('data')}.")
        else:
            print(f"AVISO: {nome} ({cnpj}) NÃO encontrado nos informes. Confira o CNPJ.")

    saida = {
        "atualizado_em": datetime.now(TZ_BR).strftime("%Y-%m-%d %H:%M:%S %z"),
        "fundos": fundos,
    }
    with open(ARQ_SAIDA, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=2)

    print(f"\nOK — {len(fundos)}/{len(alvos)} fundos gravados em {ARQ_SAIDA}:")
    for cnpj, info in fundos.items():
        print(f"  {info['nome']}: cota {info['cota']} em {info['data']}")


if __name__ == "__main__":
    main()
