"""static/dashboard.js — gráficos, Comparar, Mapa, formulário de arquivo e navegador
de arquivos da estação (via `t()` do /i18n.js)."""

EN = {
    # Gráficos
    "máx": "max",
    "mín": "min",
    "sim": "yes",
    "não": "no",
    "sem rota padrão": "no default route",
    "medindo…": "measuring…",
    "(o arquivo do momento ainda não chegou)": "(the current file has not arrived yet)",
    "%(n)s linhas lidas": "%(n)s lines read",
    "(decimado p/ o gráfico)": "(decimated for the chart)",
    "atualizado %(quando)s": "updated %(quando)s",
    "Falha ao buscar os dados: %(erro)s": "Failed to fetch the data: %(erro)s",
    "falha ao buscar os dados: %(erro)s": "failed to fetch the data: %(erro)s",
    # Comparar
    "sem resposta": "no response",
    "variável não encontrada na série": "variable not found in the series",
    "recarregue a dashboard no servidor (API sem eixo de tempo comum)":
        "restart the dashboard on the server (API without a common time axis)",
    "nenhum timestamp reconhecido": "no recognized timestamp",
    "%(n)s série(s) · %(m)s instantes no eixo comum · passo de %(passo)s":
        "%(n)s series · %(m)s instants on the common axis · step of %(passo)s",
    "Data/hora": "Date/time",
    "— mostrando os %(n)s instantes mais recentes de %(total)s (mais recente primeiro); “—” = a série não tem ponto naquele instante":
        "— showing the %(n)s most recent instants of %(total)s (newest first); “—” = the series has no point at that instant",
    "— um instante por linha (mais recente primeiro); “—” = a série não tem ponto naquele instante":
        "— one instant per row (newest first); “—” = the series has no point at that instant",
    # Estados / Mapa
    "Recebendo dados": "Receiving data",
    "Online": "Online",
    "Offline": "Offline",
    "Sem sinal": "No signal",
    "Recepção pausada": "Reception paused",
    "Relevo": "Terrain",
    "Satélite": "Satellite",
    "Padrão (OpenStreetMap)": "Default (OpenStreetMap)",
    "carregando últimos valores…": "loading latest values…",
    "última leitura:": "last reading:",
    "sem dados disponíveis": "no data available",
    "Nenhum arquivo em monitoramento nesta estação.": "No monitored files at this station.",
    "%(n)s variável(is)": "%(n)s variable(s)",
    "último sinal:": "last signal:",
    "imagem gerada em %(quando)s": "image generated on %(quando)s",
    # Formulário de arquivo
    "Índice da coluna (0 = primeira, -1 = última) ou o nome dela":
        "Column index (0 = first, -1 = last) or its name",
    "Identificador da série no banco (gerado do rótulo se vazio)":
        "Series identifier in the database (generated from the label if empty)",
    "Número": "Number",
    "Inteiro": "Integer",
    "Booleano": "Boolean",
    "Data/hora (epoch)": "Date/time (epoch)",
    "Data/hora (texto)": "Date/time (text)",
    "Texto": "Text",
    "Lendo as colunas do arquivo…": "Reading the file's columns…",
    "Não foi possível determinar as colunas do arquivo.": "Could not determine the file's columns.",
    "coluna %(n)s": "column %(n)s",
    "%(n)s colunas listadas (a do timestamp ficou de fora). Ajuste rótulos, unidades e limites e clique em Salvar.":
        "%(n)s columns listed (the timestamp column was left out). Adjust labels, units and limits and click Save.",
    "Falha ao listar as colunas: %(erro)s": "Failed to list the columns: %(erro)s",
    "Lendo o final do arquivo…": "Reading the end of the file…",
    "Parse OK.": "Parse OK.",
    "carimbo de tempo": "timestamp",
    "Colunas destacadas = mapeadas no formulário (rótulo abaixo do número); azul = carimbo de tempo. As demais ainda não têm rótulo.":
        "Highlighted columns = mapped in the form (label below the number); blue = timestamp. The others have no label yet.",
    "Falha no preview: %(erro)s": "Preview failed: %(erro)s",
    # Navegador de arquivos da estação
    '%(n)s arquivo(s) selecionado(s) — de qualquer pasta; clique em "Adicionar selecionados".':
        '%(n)s file(s) selected — from any folder; click "Add selected".',
    "Nenhum arquivo selecionado ainda.": "No file selected yet.",
    '%(shown)s de %(total)s item(ns) exibido(s) nesta pasta — filtro "%(filtro)s". O filtro só esconde linhas; a seleção continua valendo.':
        '%(shown)s of %(total)s item(s) shown in this folder — filter "%(filtro)s". The filter only hides rows; the selection still applies.',
    "Carregando…": "Loading…",
    "Falha ao listar a pasta: %(erro)s": "Failed to list the folder: %(erro)s",
    "Esta pasta ainda não existe no servidor (nada foi recebido da estação até agora).":
        "This folder does not exist on the server yet (nothing has been received from the station so far).",
    "Pasta vazia.": "Empty folder.",
    "Nome": "Name",
    "Tipo": "Type",
    "Tamanho": "Size",
    "Modificado": "Modified",
    "Apelido (monitoramento)": "Alias (monitoring)",
    "pasta acima": "parent folder",
    "(oculto — a ingestão ignora)": "(hidden — ignored by ingestion)",
    "pasta": "folder",
    "(já monitorado)": "(already monitored)",
    "baixar": "download",
    "Listagem truncada em %(n)s entradas — esta pasta tem mais itens; use as subpastas ou o filtro.":
        "Listing truncated at %(n)s entries — this folder has more items; use the subfolders or the filter.",
    "Marque ao menos um arquivo antes de adicionar.": "Select at least one file before adding.",
}
