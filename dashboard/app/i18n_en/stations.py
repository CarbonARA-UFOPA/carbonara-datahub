"""Estações (lista, detalhe, cadastro), mapa, comparar, guia e login."""

EN = {
    # stations.html
    "Estações": "Stations",
    "outras interfaces": "other interfaces",
    "<strong>%(n)s</strong> estação(ões) configurada(s) no rsync-hub ainda não estão na dashboard.":
        "<strong>%(n)s</strong> station(s) configured in rsync-hub are not in the dashboard yet.",
    "Importar do rsync-hub": "Import from rsync-hub",
    "Nenhuma estação cadastrada ainda.": "No stations registered yet.",
    'Autorize a primeira na aba <a href="%(url)s">Coleta</a> — depois ela aparece aqui (ou use "Importar do rsync-hub", quando já houver estações no hub).':
        'Authorize the first one in the <a href="%(url)s">Collection</a> tab — it will then show up here (or use "Import from rsync-hub" when the hub already has stations).',
    "Status": "Status",
    "Estação": "Station",
    "Último sinal": "Last signal",
    "Velocidade": "Speed",
    "Consumo": "Volume",
    "Ping": "Ping",
    "Janela": "Window",
    "Arqv. mon.": "Mon. files",
    "Último carimbo de tempo lido de cada arquivo monitorado. Amarelo: parado há mais de 1 dia. Vermelho: mais de 7 dias.":
        "Latest timestamp read from each monitored file. Yellow: stalled for more than 1 day. Red: more than 7 days.",
    "Últ. dado": "Latest data",
    "Pausada": "Paused",
    "Recebendo dados": "Receiving data",
    "Online": "Online",
    "Offline": "Offline",
    "Sem sinal": "No signal",
    "recepção pausada só desta estação": "reception paused for this station only",
    "velocidade da última transmissão medida; abaixo, a média de %(n)s sessão(ões)":
        "speed of the last measured transfer; below, the average over %(n)s session(s)",
    "méd": "avg",
    "volume da última transmissão medida; abaixo, o acumulado dos últimos 30 dias":
        "volume of the last measured transfer; below, the total over the last 30 days",
    "ping da última transmissão medida; abaixo, a média de %(n)s sessão(ões)":
        "ping of the last measured transfer; below, the average over %(n)s session(s)",
    "fallback %(n)s min": "fallback %(n)s min",
    "sem leitura": "no reading",
    "há %(t)s": "%(t)s ago",
    "+%(n)s atrasado(s)": "+%(n)s late",
    "+%(n)s sem leitura": "+%(n)s with no reading",
    "Ver": "View",
    "Editar": "Edit",
    "Remover esta estação do dashboard? (dados em disco não são apagados)":
        "Remove this station from the dashboard? (data on disk is not deleted)",
    "Remover": "Remove",

    # station_detail.html
    "Editar estação": "Edit station",
    "+ Adicionar arquivo": "+ Add file",
    "Recepção pausada": "Reception paused",
    "Recebendo dados agora": "Receiving data now",
    "Sem sinal (heartbeat/logs não encontrados)": "No signal (heartbeat/logs not found)",
    "Pausar a recepção de dados de %(id)s? Ela continua autorizada; os arquivos ficam na estação e sobem quando você retomar.":
        "Pause data reception from %(id)s? It stays authorized; files are kept at the station and are uploaded when you resume.",
    "a pausa é GLOBAL (todas as estações) — retome na aba Coleta":
        "the pause is GLOBAL (all stations) — resume it in the Collection tab",
    "▶ Retomar recepção": "▶ Resume reception",
    "motivo (opcional)": "reason (optional)",
    "⏸ Pausar recepção": "⏸ Pause reception",
    "Nenhum dado novo é aceito desta estação": "No new data is accepted from this station",
    "(pausa global do servidor)": "(server-wide pause)",
    "desde %(quando)s": "since %(quando)s",
    "motivo:": "reason:",
    "Os arquivos ficam guardados na estação e sobem quando a recepção for retomada.":
        "Files are kept at the station and are uploaded once reception resumes.",
    "Intervalo declarado": "Declared interval",
    "— (sem heartbeat)": "— (no heartbeat)",
    "Janela de offline": "Offline window",
    "Watchdog (heartbeat)": "Watchdog (heartbeat)",
    "— não encontrado": "— not found",
    "Nenhum arquivo em monitoramento.": "No files being monitored.",
    "Adicione o primeiro": "Add the first one",
    "tipo de dado": "data type",
    "id do cadastro: %(id)s": "registration id: %(id)s",
    "Remover este arquivo do monitoramento?": "Remove this file from monitoring?",
    "Gráficos": "Charts",
    "últimas <strong>%(n)s</strong> linhas": "last <strong>%(n)s</strong> lines",
    "separador": "separator",
    "%(n)s variável(is)": "%(n)s variable(s)",
    "Este arquivo é ingerido no banco de dados": "This file is ingested into the database",
    "fuso %(tz)s": "timezone %(tz)s",
    "média por %(r)s": "averaged per %(r)s",
    "no banco": "in database",
    "%(n)s valor(es)": "%(n)s value(s)",
    "último %(t)s UTC": "latest %(t)s UTC",
    "%(n)s kB pendentes": "%(n)s kB pending",
    "erro:": "error:",
    "(ainda sem leitura)": "(not read yet)",

    # station_form.html
    "Nova estação": "New station",
    "Identificador (station_id)": "Identifier (station_id)",
    "ex.: flona01": "e.g. flona01",
    "Igual ao usado no rsync-hub (nome da pasta em <code>incoming/</code>). Minúsculas, números, <code>-</code> e <code>_</code>.":
        "Same as used in rsync-hub (folder name under <code>incoming/</code>). Lowercase letters, digits, <code>-</code> and <code>_</code>.",
    "Nome de exibição": "Display name",
    "ex.: FLONA Tapajós 01": "e.g. FLONA Tapajós 01",
    "Latitude": "Latitude",
    "Longitude": "Longitude",
    "ex.: -3.578041 (graus decimais)": "e.g. -3.578041 (decimal degrees)",
    "ex.: -55.074330 (graus decimais)": "e.g. -55.074330 (decimal degrees)",
    "Coordenadas geográficas (WGS84, graus decimais; sul e oeste são negativos). Necessárias para a estação aparecer na página <strong>Mapa</strong> — opcionais no restante do dashboard.":
        "Geographic coordinates (WGS84, decimal degrees; south and west are negative). Required for the station to appear on the <strong>Map</strong> page — optional everywhere else in the dashboard.",
    "O status <strong>Online/Offline</strong> não se configura aqui: vem do sinal de vida que a própria estação envia (<code>.carbonara-status</code> em <code>incoming/%(id)s/</code>), com o intervalo de reciclagem declarado por ela. A janela de offline acompanha esse intervalo — <strong>2× o intervalo + 2 min</strong>, a mesma regra do <code>rsync-hub monitor</code> e do watchdog do cliente. Para mudá-la, mude o tempo de reciclagem <em>na estação</em>.":
        "The <strong>Online/Offline</strong> status is not configured here: it comes from the heartbeat the station itself sends (<code>.carbonara-status</code> in <code>incoming/%(id)s/</code>), together with the send interval it declares. The offline window follows that interval — <strong>2× the interval + 2 min</strong>, the same rule used by <code>rsync-hub monitor</code> and by the client watchdog. To change it, change the send interval <em>at the station</em>.",
    "Salvar": "Save",
    "Cancelar": "Cancel",
    "Arquivos desta estação no servidor": "This station's files on the server",
    "Arquivados": "Archived",
    "Quarentena": "Quarantine",
    "Gerenciar na Coleta": "Manage in Collection",
    "Navegue em <code>incoming/%(id)s/</code> — o diretório do <strong>Arquivo de watchdog</strong> cadastrado acima — e <strong>marque os arquivos que quer monitorar</strong>. A navegação é <strong>somente leitura</strong> (listar e baixar): a dashboard nunca apaga nem move arquivos do hub. A seleção <strong>sobrevive à troca de pastas</strong>; ao final clique em <em>Adicionar selecionados</em>. Depois use <em>Editar</em> em cada arquivo para mapear as colunas e variáveis.":
        "Browse <code>incoming/%(id)s/</code> — the directory of the <strong>watchdog file</strong> registered above — and <strong>tick the files you want to monitor</strong>. Browsing is <strong>read-only</strong> (list and download): the dashboard never deletes or moves hub files. The selection <strong>survives switching folders</strong>; when done, click <em>Add selected</em>. Then use <em>Edit</em> on each file to map its columns and variables.",
    "Filtrar nesta pasta": "Filter this folder",
    "texto ou extensão — ex.: .dat, meteo": "text or extension — e.g. .dat, meteo",
    "Marcar/desmarcar visíveis": "Toggle visible",
    "Limpar seleção": "Clear selection",
    "Adicionar selecionados": "Add selected",
    "Arquivos monitorados desta estação": "Monitored files of this station",
    "Nenhum arquivo ainda. É no cadastro do <strong>arquivo</strong> que se configura o separador de colunas, o cabeçalho, o timestamp e as variáveis a plotar.":
        "No files yet. The column separator, header, timestamp and variables to plot are configured in each <strong>file</strong> registration.",
    "Arquivo · caminho": "File · path",
    "Apelido": "Alias",
    "Linhas do final": "Tail lines",
    "Variáveis": "Variables",
    "coluna": "column",
    "var.": "var.",
    "nenhuma": "none",
    "Remover o arquivo %(id)s do monitoramento? O arquivo em disco não é apagado — só deixa de ser exibido no dashboard.":
        "Remove file %(id)s from monitoring? The file on disk is not deleted — it just stops being shown in the dashboard.",

    # map.html
    "Mapa": "Map",
    "Salvar imagem": "Save image",
    "Ver página da estação": "Open station page",
    "Fechar": "Close",
    "✕ Fechar": "✕ Close",

    # compare.html
    "Comparar": "Compare",
    "Comparar variáveis entre estações": "Compare variables across stations",
    "Alterar seleção": "Change selection",
    "Selecionar variáveis": "Select variables",
    "%(n)s variável(is) selecionada(s) — marque de quantas estações quiser; a combinação fica na URL (dá para favoritar).":
        "%(n)s variable(s) selected — pick from as many stations as you like; the combination is kept in the URL (you can bookmark it).",
    "%(n)s variáveis": "%(n)s variables",
    "sem arquivos configurados": "no files configured",
    "Visualizar": "Show",
    "Comparação": "Comparison",
    "%(n)s série(s) · legenda: estação · arquivo · variável — clique numa legenda para ocultar/mostrar a série":
        "%(n)s series · legend: station · file · variable — click a legend entry to hide/show the series",
    "Valores comparados": "Compared values",
    "— um instante por linha (mais recente primeiro); “—” = a série não tem ponto naquele instante":
        "— one instant per row (most recent first); “—” = the series has no point at that instant",
    "Nenhuma variável selecionada ainda.": "No variables selected yet.",
    "Nenhuma estação configurada.": "No stations configured.",
    "Cadastre a primeira": "Register the first one",

    # guia.html
    "Guia de acesso aos dados": "Data access guide",
    "Guia para pesquisadores — como acessar os dados": "Guide for researchers — how to access the data",
    'Defina a <strong>URL pública</strong> da API em <a href="%(url)s">Configurações</a> para este guia mostrar o endereço que os pesquisadores usam (hoje aparece o endereço interno).':
        'Set the API <strong>public URL</strong> in <a href="%(url)s">Settings</a> so this guide shows the address researchers use (it currently shows the internal address).',
    'Os dados das estações chegam a este servidor, passam pelo controle de qualidade e ficam num banco de séries temporais (PostgreSQL/TimescaleDB). O acesso é pela <strong>API de dados</strong> (<code>%(api)s</code>, documentação interativa em <a href="%(api)s/docs" target="_blank" rel="noopener"><code>%(api)s/docs</code></a>) com uma <strong>chave</strong> pessoal — peça a um administrador da plataforma. Cada consulta leva a chave no cabeçalho <code>X-API-Key</code>.':
        'Station data arrives at this server, goes through quality control and is stored in a time-series database (PostgreSQL/TimescaleDB). Access is through the <strong>data API</strong> (<code>%(api)s</code>, interactive docs at <a href="%(api)s/docs" target="_blank" rel="noopener"><code>%(api)s/docs</code></a>) with a personal <strong>key</strong> — ask a platform administrator for one. Every request carries the key in the <code>X-API-Key</code> header.',
    "1. O que existe": "1. What is available",
    "Estações e variáveis são abertas (não precisam de chave):": "Stations and variables are open (no key required):",
    "estação": "station",
    "arquivos e variáveis registrados": "registered files and variables",
    "Nome": "Name",
    "Arquivos no banco": "Files in database",
    "Último dado (UTC)": "Latest data (UTC)",
    "2. Séries temporais": "2. Time series",
    "raw = cada registro; 1h/1d = média, mín, máx e nº de amostras APROVADAS":
        "raw = every record; 1h/1d = mean, min, max and count of PASSED samples",
    "(raw) good = só valores aprovados; all = tudo, com o flag de qualidade":
        "(raw) good = passed values only; all = everything, with the quality flag",
    "fuso dos carimbos na resposta (padrão UTC)": "timezone of the timestamps in the response (default UTC)",
    "csv = formato longo (time, station_id, variable_id, value, quality)":
        "csv = long format (time, station_id, variable_id, value, quality)",
    "atalhos: now, now-12h, now-7d, now-4w": "shortcuts: now, now-12h, now-7d, now-4w",
    "último valor de cada variável": "latest value of each variable",
    "Limites: respostas cruas param em 20 mil pontos (100 mil com chave <code>download</code>); agregados em 100 mil intervalos. Para períodos longos prefira <code>agg=1h</code> ou <code>1d</code>.":
        "Limits: raw responses stop at 20 thousand points (100 thousand with a <code>download</code> key); aggregates at 100 thousand intervals. For long periods prefer <code>agg=1h</code> or <code>1d</code>.",
    "3. Qualidade dos dados (<code>quality</code>)": "3. Data quality (<code>quality</code>)",
    "Flag": "Flag",
    "Significado": "Meaning",
    "Como usar": "How to use",
    "aprovado": "passed",
    "use": "use it",
    "valor não numérico no arquivo (NAN, texto)": "non-numeric value in the file (NAN, text)",
    "o valor vem nulo": "the value is null",
    "fora da faixa física definida para a variável": "outside the physical range defined for the variable",
    "descarte ou investigue": "discard or investigate",
    "salto maior que o aceitável entre duas leituras seguidas": "jump larger than acceptable between two consecutive readings",
    "provável ruído": "probably noise",
    "valor travado: N leituras iguais seguidas (sensor parado)": "stuck value: N identical consecutive readings (sensor halted)",
    "descarte": "discard",
    "Nenhum valor é alterado ou removido pelo controle de qualidade: o dado cru fica no banco com o flag. Os agregados (<code>1h</code>/<code>1d</code>) consideram só <code>quality = 0</code> e informam quantos valores ficaram de fora (<code>n_flagged</code>).":
        "Quality control never changes or removes a value: the raw data stays in the database with its flag. Aggregates (<code>1h</code>/<code>1d</code>) only consider <code>quality = 0</code> and report how many values were left out (<code>n_flagged</code>).",
    "4. Exemplos": "4. Examples",
    "Linha de comando": "Command line",
    "5. Boas práticas": "5. Good practices",
    "Guarde a chave como variável de ambiente, nunca em código versionado; ela pode ser revogada e trocada a qualquer momento.":
        "Keep the key in an environment variable, never in version-controlled code; it can be revoked and replaced at any time.",
    "Os carimbos estão em <strong>UTC</strong>; use <code>tz=</code> só para exibição. O fuso original de cada arquivo está registrado na dashboard.":
        "Timestamps are in <strong>UTC</strong>; use <code>tz=</code> for display only. Each file's original timezone is recorded in the dashboard.",
    "Cite a variável pelo <code>variable_id</code> (estável); o rótulo humano e a unidade vêm em <code>/variables</code>.":
        "Refer to a variable by its <code>variable_id</code> (stable); the human label and unit come from <code>/variables</code>.",
    "Para bases grandes, baixe uma vez por período (<code>format=csv</code>) e trabalhe localmente; a API tem limite de requisições por minuto.":
        "For large datasets, download once per period (<code>format=csv</code>) and work locally; the API has a per-minute request limit.",
    "Dúvidas sobre sensores, alturas e calibrações: fale com a equipe da estação — a dashboard mostra, por arquivo, o que está sendo ingerido e com quais regras.":
        "Questions about sensors, heights and calibrations: talk to the station team — the dashboard shows, per file, what is being ingested and with which rules.",

    # login.html
    "Entrar": "Log in",
    "Login de administração": "Administrator login",
    "A visualização é aberta — o login é necessário apenas para <strong>editar</strong> (cadastrar/remover estações e arquivos).":
        "Viewing is open — login is only needed to <strong>edit</strong> (add/remove stations and files).",
    "Usuário": "Username",
    "Senha": "Password",
    "Voltar": "Back",
}
