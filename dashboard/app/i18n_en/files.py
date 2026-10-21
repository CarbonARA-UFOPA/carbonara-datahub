"""Formulário de arquivo monitorado, Tratamento (índice e por arquivo) e Configurações."""

EN = {
    # ---------------------------------------------------------------- comuns
    "Tratamento": "Data Quality",
    "Configurações": "Settings",
    "Cancelar": "Cancel",
    "Salvar": "Save",
    "Voltar": "Back",
    "sim": "yes",
    "não": "no",
    "erro": "error",
    "Variáveis": "Variables",
    "Variável": "Variable",
    "Banco": "Database",
    "banco": "database",
    "no banco": "in the database",
    "ID no banco": "Database ID",
    "Tipo de dado": "Data type",
    "Estação": "Station",
    "Arquivo": "File",
    "Fuso": "Time zone",
    "vazio = UTC": "empty = UTC",
    "tipo": "type",
    "Não — cada registro": "No — every record",
    "Reamostrar antes de gravar": "Resample before storing",

    # ---------------------------------------------------------------- tratamento.html
    "Cada arquivo monitorado tem sua própria página de tratamento: se é ingerido no banco, o fuso dos carimbos, a reamostragem e, variável a variável, se entra no banco e as regras de qualidade (faixa física → <code>2</code>, salto → <code>3</code>, valor travado → <code>4</code>). Abra <em>Tratar</em> no arquivo desejado.":
        "Each monitored file has its own data quality page: whether it is ingested into the database, the time zone of its timestamps, resampling and, variable by variable, whether it goes into the database and its quality rules (physical range → <code>2</code>, spike → <code>3</code>, stuck value → <code>4</code>). Open <em>Configure QC</em> on the file you want.",
    "Estado da ingestão indisponível: %(erro)s": "Ingestion status unavailable: %(erro)s",
    'API não configurada (<a href="%(url)s">Configurações</a>) — o estado "no banco" não aparece.':
        'API not configured (<a href="%(url)s">Settings</a>) — the "in the database" status is not shown.',
    "Ver estação": "View station",
    "Nenhum arquivo monitorado — cadastre em <em>Estações → Editar</em>.":
        "No monitored files — register them under <em>Stations → Edit</em>.",
    "No banco?": "In database?",
    "Regras": "Rules",
    "Reamostragem": "Resampling",
    "Ingerido": "Ingested",
    "faixa %(faixa)s · salto %(salto)s · travado %(travado)s":
        "range %(faixa)s · spike %(salto)s · stuck %(travado)s",
    "cada registro": "every record",
    "média 1 min": "1-min mean",
    "média 30 min": "30-min mean",
    "%(n)s valores": "%(n)s values",
    "até %(quando)s": "up to %(quando)s",
    "⏳ sem leitura ainda": "⏳ not read yet",
    "Tratar": "Configure QC",

    # ---------------------------------------------------------------- tratamento_arquivo.html
    "← Todos os arquivos": "← All files",
    "Editar parse/colunas": "Edit parsing/columns",
    "Ingerir este arquivo no banco": "Ingest this file into the database",
    "%(n)s valores no banco": "%(n)s values in the database",
    "até %(quando)s UTC": "up to %(quando)s UTC",
    "%(n)s kB pendentes": "%(n)s kB pending",
    "erro: %(erro)s": "error: %(erro)s",
    "⏳ sem leitura ainda: %(motivo)s.": "⏳ not read yet: %(motivo)s.",
    "ex.: datalogger, fluxo, radiacao": "e.g. datalogger, fluxo, radiacao",
    "Agrupa os arquivos da estação; no banco: <code>%(esquema)s</code>.":
        "Groups the station's files; in the database: <code>%(esquema)s</code>.",
    "Nome da tabela no banco": "Table name in the database",
    "Vazio = <code>%(id)s</code>. Trocar o nome move a tabela (o histórico fica).":
        "Empty = <code>%(id)s</code>. Renaming moves the table (history is kept).",
    "Fuso dos carimbos do arquivo": "Time zone of the file's timestamps",
    "Epoch já é UTC e ignora este campo.": "Epoch is already UTC and ignores this field.",
    "Média por 1 min": "1-min mean",
    "Média por 30 min": "30-min mean",
    "%(n)s de %(total)s no banco": "%(n)s of %(total)s in the database",
    "As regras só marcam <code>quality</code>, nunca alteram o valor: <strong>Mín/Máx</strong> = faixa física (flag 2) · <strong>Salto máx.</strong> = maior variação aceitável entre duas leituras seguidas, na unidade da variável (flag 3) · <strong>Travado (N)</strong> = N leituras iguais seguidas marcam sensor travado (flag 4). Vazio = regra desligada.":
        "Rules only set <code>quality</code>, they never change the value: <strong>Min/Max</strong> = physical range (flag 2) · <strong>Max spike</strong> = largest acceptable change between two consecutive readings, in the variable's unit (flag 3) · <strong>Stuck (N)</strong> = N identical consecutive readings flag a stuck sensor (flag 4). Empty = rule off.",
    "filtrar variáveis": "filter variables",
    "filtrar variáveis (rótulo, ID, coluna)…": "filter variables (label, ID, column)…",
    "Marcar visíveis": "Check visible",
    "Desmarcar visíveis": "Uncheck visible",
    "Inverter visíveis": "Invert visible",
    "Mín": "Min",
    "Máx": "Max",
    "salto máximo entre leituras seguidas (flag 3)": "maximum spike between consecutive readings (flag 3)",
    "Salto máx.": "Max spike",
    "nº de leituras iguais seguidas que marca sensor travado (flag 4)":
        "number of identical consecutive readings that flags a stuck sensor (flag 4)",
    "Travado (N)": "Stuck (N)",
    "texto: entra no banco como coluna de texto (sem gráfico e sem regras de QC)":
        "text: stored in the database as a text column (no chart and no QC rules)",
    "Sem variáveis mapeadas — mapeie as colunas em <em>Editar parse/colunas</em>.":
        "No mapped variables — map the columns in <em>Edit parsing/columns</em>.",
    "Salvar tratamento": "Save data quality settings",
    "alterações não salvas": "unsaved changes",
    "Para reaplicar estas regras ao histórico já gravado: no banco da estação, abra a tabela deste arquivo → <em>Zona de perigo</em> → <em>Zerar e reingerir</em>.":
        "To reapply these rules to the history already stored: in the station's database, open this file's table → <em>Danger zone</em> → <em>Reset and re-ingest</em>.",
    "%(v)s de %(n)s visíveis · %(m)s no banco": "%(v)s of %(n)s visible · %(m)s in the database",

    # ---------------------------------------------------------------- settings.html
    "A dashboard é <strong>complementar ao rsync-hub</strong>: você só precisa apontar a <strong>raiz do hub</strong> aqui. As demais localizações — <code>incoming/&lt;id&gt;/</code>, o heartbeat <code>.carbonara-status</code> e o <code>log/connections.log</code> — têm locais padrão derivados dessa raiz. Se a raiz mudar (ex.: sair de <code>/srv/carbonara</code>), altere só aqui e tudo acompanha.":
        "The dashboard <strong>complements rsync-hub</strong>: you only need to point it to the <strong>hub root</strong> here. The other locations — <code>incoming/&lt;id&gt;/</code>, the <code>.carbonara-status</code> heartbeat and <code>log/connections.log</code> — have default paths derived from that root. If the root changes (e.g. moving away from <code>/srv/carbonara</code>), change it only here and everything follows.",
    "A dashboard está lendo a raiz errada.": "The dashboard is reading the wrong root.",
    "Ela lê de <code>%(base)s</code>, mas o rsync-hub usa <code>%(conf)s</code> (em <code>/etc/rsync-hub.conf</code>) — é o rastro de um <code>rsync-hub move-root</code> feito pelo terminal: o hub migra, mas o config da dashboard é outro arquivo e o script não tem como atualizá-lo. Enquanto estiver assim, as estações aparecem sem dados novos, porque a dashboard está olhando a árvore antiga, congelada.":
        "It reads from <code>%(base)s</code>, but rsync-hub uses <code>%(conf)s</code> (in <code>/etc/rsync-hub.conf</code>) — the trace of an <code>rsync-hub move-root</code> run from the terminal: the hub migrates, but the dashboard config is a separate file the script cannot update. Until this is fixed, stations show no new data, because the dashboard is looking at the old, frozen tree.",
    "Alinhar a dashboard com %(conf)s": "Align the dashboard with %(conf)s",
    "Troca o <code>data_root</code> e reaponta os caminhos absolutos dos cadastros (heartbeat, arquivos monitorados, log de conexões). Nada em disco é movido.":
        "Changes <code>data_root</code> and repoints the absolute paths in the registrations (heartbeat, monitored files, connection log). Nothing on disk is moved.",
    "Pasta raiz do hub (rsync-hub)": "Hub root folder (rsync-hub)",
    "ex.: /srv/carbonara": "e.g. /srv/carbonara",
    "Onde ficam <code>incoming/</code>, <code>archive/</code>, <code>log/</code> e <code>stations/</code>. Caminho absoluto (produção) ou relativo à pasta do <code>config.yaml</code> (dev usa <code>./data</code>).":
        "Where <code>incoming/</code>, <code>archive/</code>, <code>log/</code> and <code>stations/</code> live. Absolute path (production) or relative to the <code>config.yaml</code> folder (dev uses <code>./data</code>).",
    "Ajustes avançados (opcionais)": "Advanced settings (optional)",
    "Log de conexões (override)": "Connection log (override)",
    "vazio = &lt;raiz&gt;/log/connections.log (padrão)": "empty = &lt;root&gt;/log/connections.log (default)",
    "Só se o <code>connections.log</code> do rsync-hub não estiver no local padrão.":
        "Only if rsync-hub's <code>connections.log</code> is not in the default location.",
    "Fuso horário de exibição": "Display time zone",
    "ex.: America/Santarem (vazio = fuso do processo)": "e.g. America/Santarem (empty = process time zone)",
    'Afeta apenas a EXIBIÇÃO do "último sinal". O status (online/offline) é calculado em epoch, imune a fuso.':
        'Only affects how the "last signal" is DISPLAYED. The status (online/offline) is computed in epoch, immune to time zones.',
    "Fallback de offline (segundos)": "Offline fallback (seconds)",
    "Janela usada só quando a estação não tem heartbeat com <code>interval_min</code>. Espelha o <code>RSYNC_HUB_STALE</code> (padrão 3600 = 1 h).":
        "Window used only when the station has no heartbeat with <code>interval_min</code>. Mirrors <code>RSYNC_HUB_STALE</code> (default 3600 = 1 h).",
    "API de dados": "Data API",
    'Serviço <code>api</code> do Docker Compose (porta 8000). A dashboard usa a <em>URL interna</em> para ler as séries dos arquivos marcados com "Ingerir no banco" e para gerir as chaves; a <em>URL pública</em> é só o que se mostra aos usuários.':
        'The Docker Compose <code>api</code> service (port 8000). The dashboard uses the <em>internal URL</em> to read the series of files marked "Ingest into the database" and to manage keys; the <em>public URL</em> is only what is shown to users.',
    "URL interna da API": "Internal API URL",
    "ex.: http://127.0.0.1:8000": "e.g. http://127.0.0.1:8000",
    "Chave de serviço da dashboard": "Dashboard service key",
    "configurada (%(prefixo)s…) — vazio mantém, - remove": "configured (%(prefixo)s…) — empty keeps it, - removes it",
    "cole a chave (escopos read,admin)": "paste the key (scopes read,admin)",
    "Crie com <code>docker compose exec api python -m app.keys_cli create --owner dashboard --scopes read,admin</code>.":
        "Create it with <code>docker compose exec api python -m app.keys_cli create --owner dashboard --scopes read,admin</code>.",
    "URL pública (opcional)": "Public URL (optional)",
    "ex.: http://200.x.y.z:8000": "e.g. http://200.x.y.z:8000",
    "Salvar configuração master": "Save master settings",
    "Mover a raiz de dados (avançado)": "Move the data root (advanced)",
    "Migra a raiz <strong>inteira</strong> do hub — <code>incoming/</code>, <code>archive/</code>, <code>quarantine/</code>, <code>stations/</code> (chaves), log e configuração — para outra pasta, executando <code>rsync-hub move-root</code> no host. O comando copia preservando dono/permissões, muda o home da conta de serviço, regrava <code>/etc/rsync-hub.conf</code> e reinicia o rsyncd; a raiz <strong>antiga fica intacta</strong> como cópia de segurança (a remoção é manual, depois de conferir). Só a raiz completa é movível — <code>incoming/</code> sozinho quebraria os invariantes do hub (home do sshd, caminhos do rsyncd).":
        "Migrates the <strong>entire</strong> hub root — <code>incoming/</code>, <code>archive/</code>, <code>quarantine/</code>, <code>stations/</code> (keys), log and configuration — to another folder by running <code>rsync-hub move-root</code> on the host. The command copies preserving owner/permissions, changes the service account's home, rewrites <code>/etc/rsync-hub.conf</code> and restarts rsyncd; the <strong>old root is left intact</strong> as a backup copy (removal is manual, after checking). Only the whole root can be moved — <code>incoming/</code> alone would break the hub's invariants (sshd home, rsyncd paths).",
    "Migrar a raiz de dados AGORA? A cópia pode demorar; não feche esta página até terminar.":
        "Migrate the data root NOW? The copy may take a while; do not close this page until it finishes.",
    "<strong>O destino precisa cumprir três coisas</strong> — o hub recusa a migração (sem mover nada) quando falta alguma:":
        "<strong>The destination must meet three requirements</strong> — the hub refuses the migration (without moving anything) if any is missing:",
    "<strong>Sistema de arquivos Linux</strong> (ext4/xfs…). <em>Pendrive ou HD externo em exFAT/NTFS não serve</em>: eles não guardam dono e permissão, e o sshd passa a recusar o <code>authorized_keys</code> — <strong>todas</strong> as estações cairiam com <code>Permission denied (publickey)</code>. Confira com <code>df -Th &lt;destino&gt;</code>.":
        "<strong>A Linux filesystem</strong> (ext4/xfs…). <em>A USB stick or external drive in exFAT/NTFS will not work</em>: they do not keep owner and permissions, and sshd starts refusing <code>authorized_keys</code> — <strong>every</strong> station would fail with <code>Permission denied (publickey)</code>. Check with <code>df -Th &lt;destination&gt;</code>.",
    "<strong>Montagem fixa e permanente</strong> (via <code>/etc/fstab</code>), não <code>/media/&lt;usuário&gt;</code>: a raiz é o <em>home</em> da conta de serviço — com o disco desmontado, o sshd não acha as chaves. Pastas em <code>/media</code> ainda nascem <code>700</code> a cada montagem, fora do alcance da conta do hub.":
        "<strong>A fixed, permanent mount</strong> (via <code>/etc/fstab</code>), not <code>/media/&lt;user&gt;</code>: the root is the service account's <em>home</em> — with the disk unmounted, sshd cannot find the keys. Folders under <code>/media</code> are also created <code>700</code> on every mount, out of the hub account's reach.",
    "<strong>Pasta vazia</strong>, e de preferência uma <em>subpasta</em> do ponto de montagem (<code>/srv/dados/carbonara</code>, não <code>/srv/dados</code>) — um disco recém-formatado já vem com <code>lost+found</code>.":
        "<strong>An empty folder</strong>, preferably a <em>subfolder</em> of the mount point (<code>/srv/dados/carbonara</code>, not <code>/srv/dados</code>) — a freshly formatted disk already contains <code>lost+found</code>.",
    "Nova raiz (caminho absoluto)": "New root (absolute path)",
    "ex.: /dados/carbonara": "e.g. /dados/carbonara",
    "Raiz atual: <code>%(raiz)s</code>.": "Current root: <code>%(raiz)s</code>.",
    "Ideal antes de migrar: <code>docker compose stop ingestion</code>.":
        "Ideally, before migrating: <code>docker compose stop ingestion</code>.",
    "Executando <code>rsync-hub %(versao)s</code> (%(bin)s) — as mensagens de erro desta migração vêm dele, não da dashboard; atualize a cópia instalada para ter as verificações mais novas.":
        "Running <code>rsync-hub %(versao)s</code> (%(bin)s) — error messages from this migration come from it, not from the dashboard; update the installed copy to get the newest checks.",
    "Entendi que as <strong>estações não precisam de nenhuma mudança</strong> (o servidor decide o destino a cada conexão) e que, <em>somente se</em> este servidor rodar a pilha Docker do projeto (banco/ingestão/FTP), preciso ao final ajustar <code>CARBONARA_ROOT</code> no <code>.env</code> e rodar <code>docker compose up -d</code>. A raiz do dashboard é atualizada automaticamente.":
        "I understand that the <strong>stations need no change at all</strong> (the server decides the destination on each connection) and that, <em>only if</em> this server runs the project's Docker stack (database/ingestion/FTP), I must afterwards set <code>CARBONARA_ROOT</code> in <code>.env</code> and run <code>docker compose up -d</code>. The dashboard root is updated automatically.",
    "Migrar raiz de dados": "Migrate data root",
    "Para volumes muito grandes, prefira o terminal (a página não precisa ficar aberta): <code>sudo rsync-hub move-root /nova/raiz</code>.":
        "For very large volumes, prefer the terminal (the page does not need to stay open): <code>sudo rsync-hub move-root /new/root</code>.",
    'Indisponível: o gerenciamento está em modo somente leitura (veja a página <a href="%(url)s">Coleta</a>). Habilite rodando no servidor: <code>sudo dashboard/setup-hub-access.sh &lt;usuario-da-dashboard&gt;</code>.':
        'Unavailable: management is in read-only mode (see the <a href="%(url)s">Collection</a> page). Enable it by running on the server: <code>sudo dashboard/setup-hub-access.sh &lt;dashboard-user&gt;</code>.',
    "Estações detectadas no rsync-hub": "Stations detected in rsync-hub",
    "Importar as que faltam": "Import the missing ones",
    "Nenhuma estação detectada sob <code>%(raiz)s</code>. Procuramos em <code>incoming/</code>, <code>stations/</code>, <code>.ssh/authorized_keys</code> e <code>rsyncd.secrets</code> dessa raiz. <br>Dica: a <strong>raiz do hub</strong> é a pasta que <strong>contém</strong> <code>incoming/</code> — ex.: se seus dados estão em <code>/srv/carbonara/incoming/&lt;estação&gt;</code>, a raiz é <code>/srv/carbonara</code> (não <code>/srv/carbonara/incoming</code>).":
        "No station detected under <code>%(raiz)s</code>. We looked in <code>incoming/</code>, <code>stations/</code>, <code>.ssh/authorized_keys</code> and <code>rsyncd.secrets</code> of that root. <br>Tip: the <strong>hub root</strong> is the folder that <strong>contains</strong> <code>incoming/</code> — e.g. if your data is in <code>/srv/carbonara/incoming/&lt;station&gt;</code>, the root is <code>/srv/carbonara</code> (not <code>/srv/carbonara/incoming</code>).",
    "Fontes": "Sources",
    "Na dashboard?": "In the dashboard?",
    "✓ sim": "✓ yes",
    "— ainda não": "— not yet",

    # ---------------------------------------------------------------- file_form.html
    "Editar arquivo — %(id)s": "Edit file — %(id)s",
    "Novo arquivo em monitoramento": "New monitored file",
    "estação %(id)s": "station %(id)s",
    "O dashboard lê as <strong>últimas linhas</strong> do arquivo, separa em colunas, acha o horário e plota <strong>um gráfico por variável</strong>. Confira com <em>Pré-visualizar parse</em> antes de salvar.":
        "The dashboard reads the <strong>last lines</strong> of the file, splits them into columns, finds the timestamp and plots <strong>one chart per variable</strong>. Check with <em>Preview parsing</em> before saving.",
    "Caminho do arquivo": "File path",
    "ex.: incoming/%(id)s/%(id)s.dat (relativo à raiz) ou /caminho/absoluto":
        "e.g. incoming/%(id)s/%(id)s.dat (relative to the root) or /absolute/path",
    "<strong>O nome do arquivo muda com a data/hora</strong> — o caminho vira um padrão resolvido a cada leitura: <code>%Y</code> ano · <code>%m</code> mês · <code>%d</code> dia · <code>%H</code> hora · <code>*</code> = parte livre. Sem o arquivo do momento, vale o mais recente que casa.":
        "<strong>The file name changes with date/time</strong> — the path becomes a pattern resolved on every read: <code>%Y</code> year · <code>%m</code> month · <code>%d</code> day · <code>%H</code> hour · <code>*</code> = free part. If the current file does not exist, the newest match is used.",
    "Ajustes do caminho dinâmico — truncar nome / deslocar horas (opcional)":
        "Dynamic path options — truncate name / shift hours (optional)",
    "Truncar o fim do nome": "Truncate the end of the name",
    "Ignora os N últimos caracteres do nome e busca com <code>*</code> no lugar; o padrão gravado fica intacto. Vários casando → vale o mais recente.":
        "Ignores the last N characters of the name and searches with <code>*</code> in their place; the stored pattern stays intact. Several matches → the newest wins.",
    "Deslocar horas (±)": "Shift hours (±)",
    "Soma ±N h ao relógio antes de expandir; a virada é automática (23h <code>+2</code> → 01h do dia seguinte). Alvo inexistente → o mais recente que casa.":
        "Adds ±N h to the clock before expanding; rollover is automatic (23:00 <code>+2</code> → 01:00 the next day). Missing target → the newest match.",
    "Formato do arquivo": "File format",
    "TOA5 — Campbell/LoggerNet (padrão)": "TOA5 — Campbell/LoggerNet (default)",
    "CSV com cabeçalho (1 linha)": "CSV with header (1 line)",
    "TSV — separado por tabulação": "TSV — tab-separated",
    "Separado por espaço + Unix epoch (.sfo)": "Space-separated + Unix epoch (.sfo)",
    "Separado por espaço, data em texto": "Space-separated, text date",
    "CSV sem cabeçalho": "CSV without header",
    "Personalizado": "Custom",
    "Linhas do final a plotar": "Trailing lines to plot",
    "Apelido (opcional)": "Alias (optional)",
    "ex.: Torre Norte — nome de exibição nos gráficos": "e.g. North Tower — display name on the charts",
    "ex.: datalogger, fluxo, radiacao, perfil": "e.g. datalogger, fluxo, radiacao, perfil",
    "O nível entre a estação e o arquivo: que equipamento/conjunto gera este arquivo. No banco vira <code>%(id)s__&lt;tipo&gt;</code>, com uma tabela por arquivo. Vazio = <code>datalogger</code> para TOA5, <code>dados</code> para o resto.":
        "The level between the station and the file: which instrument/system produces this file. In the database it becomes <code>%(id)s__&lt;type&gt;</code>, with one table per file. Empty = <code>datalogger</code> for TOA5, <code>dados</code> for everything else.",
    "Nome da tabela no banco (opcional)": "Table name in the database (optional)",
    "vazio = identificador do registro": "empty = registration identifier",
    "Como a tabela deste arquivo aparece no banco da estação (ex.: <code>sondagens_rs41</code>). Útil quando o registro nasceu de um arquivo concreto e herdou a data no nome. Só letras, números e <code>_</code>.":
        "How this file's table appears in the station's database (e.g. <code>sondagens_rs41</code>). Useful when the registration was created from a concrete file and inherited the date in its name. Letters, digits and <code>_</code> only.",
    "Banco de dados": "Database",
    "Só entra no banco o que for marcado aqui — e só as variáveis da tabela abaixo (as de tipo <em>Texto</em> ficam de fora). O arquivo em disco nunca é movido nem alterado; a ingestão lê só o que cresceu desde a última leitura.":
        "Only what is checked here goes into the database — and only the variables in the table below (those of type <em>Text</em> are left out). The file on disk is never moved or changed; ingestion only reads what has grown since the last read.",
    "<strong>Ingerir este arquivo no banco de dados</strong> — as variáveis selecionadas passam a ser gravadas em <code>measurements</code> e servidas pela API. Os limites Mín/Máx físicos viram a regra de qualidade (fora deles, o valor entra com <code>quality=2</code>).":
        "<strong>Ingest this file into the database</strong> — the selected variables are stored in <code>measurements</code> and served by the API. The physical Min/Max limits become the quality rule (outside them, the value is stored with <code>quality=2</code>).",
    "Fuso horário dos carimbos do arquivo": "Time zone of the file's timestamps",
    "Cada equipamento grava no seu fuso (UTC ou local); o banco guarda sempre UTC. Carimbo em <em>Unix epoch</em> já é UTC e ignora este campo.":
        "Each instrument writes in its own time zone (UTC or local); the database always stores UTC. A <em>Unix epoch</em> timestamp is already UTC and ignores this field.",
    "Média por 1 minuto": "1-minute mean",
    "Média por 30 minutos": "30-minute mean",
    'Para arquivos de alta frequência (10–20 Hz, 1 s): grava a média de cada intervalo em vez de cada leitura. Arquivos de 30 min ficam em "cada registro".':
        'For high-frequency files (10–20 Hz, 1 s): stores the mean of each interval instead of every reading. 30-min files stay on "every record".',
    "Ajuste fino do parse e do período": "Fine-tune parsing and period",
    "Separador de colunas": "Column separator",
    "<code>\\t</code> = tabulação · <code>\\s</code> = espaço (aceita espaços repetidos).":
        "<code>\\t</code> = tab · <code>\\s</code> = space (repeated spaces accepted).",
    "Caractere de aspas": "Quote character",
    "Separador decimal": "Decimal separator",
    "Linhas de cabeçalho a pular": "Header lines to skip",
    "Linha com os nomes das colunas": "Line with the column names",
    "vazio = sem nomes": "empty = no names",
    "Período a plotar (horas)": "Period to plot (hours)",
    "vazio = tudo que foi lido": "empty = everything that was read",
    "Coluna do timestamp": "Timestamp column",
    "Índice (0 = primeira) ou nome — qualquer coluna serve.": "Index (0 = first) or name — any column works.",
    "Tipo do carimbo": "Timestamp type",
    "Data/hora em texto": "Date/time as text",
    "Unix epoch (segundos desde 1970)": "Unix epoch (seconds since 1970)",
    "Epoch aceita fração — <code>1787542188.433029</code> tem precisão de microssegundo — e também valores em ms/µs/ns.":
        "Epoch accepts fractions — <code>1787542188.433029</code> has microsecond precision — and also values in ms/µs/ns.",
    "Formato do timestamp": "Timestamp format",
    "Remover dos valores (regex)": "Strip from values (regex)",
    "ex.: [A-Za-z]": "e.g. [A-Za-z]",
    "Limpa cada valor; não mexe no timestamp.": "Cleans each value; does not touch the timestamp.",
    '<strong>Ignorar aspas</strong> — quebra também os campos entre aspas pelas vírgulas de dentro (ex.: <code>"m -125,1,-1247"</code>). Depois da coluna quebrada os nomes do cabeçalho deixam de alinhar: mapeie por índice.':
        '<strong>Ignore quotes</strong> — also splits quoted fields on the commas inside them (e.g. <code>"m -125,1,-1247"</code>). After the split column, header names no longer line up: map by index.',
    "Variáveis a plotar": "Variables to plot",
    "Uma linha = uma variável = um gráfico. Use <em>⇊ Todas as colunas do arquivo</em> para trazer as colunas detectadas de uma vez.":
        "One row = one variable = one chart. Use <em>⇊ All columns of the file</em> to bring in all detected columns at once.",
    "Índice": "Index",
    "Rótulo": "Label",
    "Tipo": "Type",
    "Unidade": "Unit",
    "Mín físico": "Physical min",
    "Máx físico": "Physical max",
    "Índice da coluna (0 = primeira, -1 = última) ou o nome dela": "Column index (0 = first, -1 = last) or its name",
    "Identificador da série no banco (gerado do rótulo se vazio; não mude depois de ingerir)":
        "Series identifier in the database (generated from the label if empty; do not change it after ingesting)",
    "Número": "Number",
    "Inteiro": "Integer",
    "Booleano": "Boolean",
    "Data/hora (epoch)": "Date/time (epoch)",
    "Data/hora (texto)": "Date/time (text)",
    "Texto": "Text",
    "+ Variável": "+ Variable",
    "⇊ Todas as colunas do arquivo": "⇊ All columns of the file",
    "O que significa cada coluna": "What each column means",
    "<em>Índice</em> — posição da coluna no arquivo (<code>0</code> = primeira), <strong>editável</strong>: digite direto o índice que quiser. Vale <code>-1</code> para a última coluna (e <code>-2</code> para a penúltima), ou o <em>nome</em> da coluna quando o arquivo tem linha de cabeçalho. Linha nova já vem com o próximo índice livre. Não pode repetir índice nem usar a coluna do timestamp — o servidor recusa e diz qual é.":
        "<em>Index</em> — column position in the file (<code>0</code> = first), <strong>editable</strong>: type the index you want directly. <code>-1</code> works for the last column (and <code>-2</code> for the second to last), or the column <em>name</em> when the file has a header line. A new row comes with the next free index. Indexes cannot repeat nor use the timestamp column — the server refuses and says which one.",
    "<em>Rótulo</em> — título do gráfico. <em>Unidade</em> — só exibição na legenda.":
        "<em>Label</em> — chart title. <em>Unit</em> — shown in the legend only.",
    "<em>ID no banco</em> — chave da série em <code>measurements</code> (<code>[a-z0-9_]</code>). Vazio = gerado do rótulo (<code>Rad1_A(1)</code> → <code>rad1_a_1</code>). Único dentro do arquivo e entre os arquivos ingeridos da mesma estação. Trocar o ID depois de ingerir inicia OUTRA série.":
        "<em>Database ID</em> — series key in <code>measurements</code> (<code>[a-z0-9_]</code>). Empty = generated from the label (<code>Rad1_A(1)</code> → <code>rad1_a_1</code>). Unique within the file and across the station's ingested files. Changing the ID after ingesting starts ANOTHER series.",
    "<em>Tipo</em> — como o valor da coluna é lido: <strong>Número</strong> (padrão) · <strong>Inteiro</strong> (sem casas decimais) · <strong>Booleano</strong> (0/1, também aceita <code>sim/não</code>, <code>true/false</code>, <code>on/off</code>) · <strong>Data/hora (epoch)</strong> — para as OUTRAS colunas de carimbo do arquivo: a linha mostra quando aquele bloco avançou e os valores aparecem como hora, não como número · <strong>Data/hora (texto)</strong> — idem, para carimbos escritos por extenso (<code>2026-08-24</code>, <code>24/08/2026 00:30</code>): o formato é descoberto entre os usuais · <strong>Texto</strong> — não vira gráfico; a tela mostra o último valor lido. <br>Os tipos valem também na <em>pré-visualização</em>: cada coluna mapeada mostra o valor convertido embaixo do valor cru.":
        "<em>Type</em> — how the column value is read: <strong>Number</strong> (default) · <strong>Integer</strong> (no decimals) · <strong>Boolean</strong> (0/1, also accepts <code>sim/não</code>, <code>true/false</code>, <code>on/off</code>) · <strong>Date/time (epoch)</strong> — for the file's OTHER timestamp columns: the line shows when that block advanced and the values appear as a time, not a number · <strong>Date/time (text)</strong> — same, for timestamps written out (<code>2026-08-24</code>, <code>24/08/2026 00:30</code>): the format is detected among the usual ones · <strong>Text</strong> — not charted; the page shows the last value read. <br>Types also apply in the <em>preview</em>: each mapped column shows the converted value under the raw value.",
    '<em>Mín / Máx físicos</em> — limites plausíveis: viram linhas tracejadas no gráfico e os trechos fora deles ficam <strong style="color:#d64545">vermelhos</strong>.':
        '<em>Physical Min / Max</em> — plausible limits: they become dashed lines on the chart and the segments outside them turn <strong style="color:#d64545">red</strong>.',
    "Valores não numéricos (<code>NAN</code>, <code>±7999</code>) viram lacunas.":
        "Non-numeric values (<code>NAN</code>, <code>±7999</code>) become gaps.",
    "Pré-visualizar parse": "Preview parsing",
    # JS do file_form
    "separador %(s)s": "separator %(s)s",
    "aspas %(q)s": "quotes %(q)s",
    "nenhuma": "none",
    "pula %(n)s linha(s)": "skips %(n)s line(s)",
    "nomes na linha %(n)s": "names on line %(n)s",
    "sem nomes de coluna": "no column names",
    "timestamp na coluna %(c)s": "timestamp in column %(c)s",
    "Agora isto aponta para:": "Right now this points to:",
    "relógio deslocado %(h)sh": "clock shifted %(h)sh",
    "(curinga: o mais recente que casar)": "(wildcard: the newest match)",
}
