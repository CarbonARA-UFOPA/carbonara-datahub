"""Textos do Python que chegam à tela via JSON: séries/preview (series.py), frescor
dos arquivos (aba Estações) e erros do blueprint /api (api.py)."""

EN = {
    # series.build_series / series_from_api
    "o banco ainda não tem valores deste arquivo (a ingestão roda a cada ciclo; confira o chip 'no banco' na estação)":
        "the database has no values for this file yet (ingestion runs every cycle; check the 'in database' chip on the station)",
    "%(erro)s — lendo o arquivo diretamente": "%(erro)s — reading the file directly",
    "banco indisponível (%(erro)s) — lendo o arquivo diretamente":
        "database unavailable (%(erro)s) — reading the file directly",
    " e mais %(n)s": " and %(n)s more",
    "%(n)s variável(is) ainda sem valores no banco: %(lista)s":
        "%(n)s variable(s) still without values in the database: %(lista)s",
    "o banco devolveu só os últimos %(n)s instantes (limite da API de %(lim)s pontos no total das variáveis) — reduza 'Linhas do final'":
        "the database returned only the last %(n)s instants (API limit of %(lim)s points across all variables) — reduce 'Lines from the end'",
    # series.build_series_from_file
    "Aguardando o arquivo do período: %(nome)s ainda não chegou (nenhum arquivo casa com o padrão).":
        "Waiting for the period's file: %(nome)s has not arrived yet (no file matches the pattern).",
    "Arquivo não encontrado: %(caminho)s": "File not found: %(caminho)s",
    "Nenhuma linha de dados encontrada no final do arquivo.": "No data lines found at the end of the file.",
    "as %(n)s linhas lidas podem não cobrir o período de %(h)s h — aumente 'Linhas do final a plotar'":
        "the %(n)s lines read may not cover the %(h)s h period — increase 'Lines from the end to plot'",
    "coluna '%(col)s' não encontrada (variável '%(var)s')": "column '%(col)s' not found (variable '%(var)s')",
    "o arquivo do momento (%(atual)s) ainda não existe — lendo o mais recente que casa com o padrão (%(lido)s)":
        "the current file (%(atual)s) does not exist yet — reading the newest one matching the pattern (%(lido)s)",
    "%(n)s arquivos casam com o padrão do período — lendo só o mais recente (%(lido)s); se não é o esperado, o truncamento/curinga está frouxo demais":
        "%(n)s files match the period's pattern — reading only the newest (%(lido)s); if that is not the expected one, the truncation/wildcard is too loose",
    # series.column_display (preview de booleano)
    "sim": "yes",
    "não": "no",
    # series.file_freshness (coluna "Últ. dado" — notas e dica)
    "%(quando)s (há %(idade)s)": "%(quando)s (%(idade)s ago)",
    "sem leitura": "no reading",
    "nenhum arquivo monitorado": "no monitored file",
    "aguardando o arquivo do período": "waiting for the period's file",
    "arquivo não encontrado": "file not found",
    "sem linhas de dados": "no data lines",
    "carimbo não reconhecido": "timestamp not recognized",
    # series.build_preview
    "Arquivo do momento ainda não existe e nenhum arquivo casa com o padrão: %(padrao)s":
        "The current file does not exist yet and no file matches the pattern: %(padrao)s",
    "Cada linha virou UMA coluna só — o separador provavelmente está errado.":
        "Each line became a SINGLE column — the separator is probably wrong.",
    "Linhas com número variável de colunas (%(min)s–%(max)s) — confira separador e linhas de cabeçalho.":
        "Lines with a varying number of columns (%(min)s–%(max)s) — check the separator and header lines.",
    "Timestamp não casa com o formato: valor '%(valor)s' × formato '%(formato)s'.":
        "Timestamp does not match the format: value '%(valor)s' × format '%(formato)s'.",
    "Nenhuma linha de dados lida — arquivo vazio ou só cabeçalho.":
        "No data lines read — empty file or header only.",
    "Caminho com data/hora: %(padrao)s → %(arquivo)s": "Date/time path: %(padrao)s → %(arquivo)s",
    "(truncado: %(n)s caractere(s) do fim do nome)": "(truncated: %(n)s character(s) from the end of the name)",
    "(relógio deslocado %(h)sh)": "(clock shifted %(h)sh)",
    "(arquivo lido: %(nome)s)": "(file read: %(nome)s)",
    "(ainda não existe — lendo o mais recente que casa com o padrão: %(nome)s)":
        "(does not exist yet — reading the newest one matching the pattern: %(nome)s)",
    "— ATENÇÃO: %(n)s arquivos casam com o padrão; só o mais recente é lido. Truncamento/curinga frouxo demais?":
        "— WARNING: %(n)s files match the pattern; only the newest is read. Truncation/wildcard too loose?",
    # api.py
    "Estação '%(id)s' não encontrada.": "Station '%(id)s' not found.",
    "Arquivo '%(id)s' não encontrado.": "File '%(id)s' not found.",
    "Falha ao ler o arquivo: %(erro)s": "Failed to read the file: %(erro)s",
    "Falha ao listar a pasta: %(erro)s": "Failed to list the folder: %(erro)s",
    "Informe o caminho do arquivo para pré-visualizar.": "Enter the file path to preview.",
}
