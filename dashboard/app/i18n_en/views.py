"""Mensagens do lado Python: views.py (flash, motivos de pendência), validações do
config_store, dbconn e api_client. Também os rótulos de constantes de módulo de
views.py (_SQL_EXEMPLOS, _COLUMN_TYPES, _MAINT_ACTIONS), que os templates traduzem
com `_()` no momento de exibir."""

EN = {
    # --- Login / sessão ---------------------------------------------------------
    "Login de administração necessário.": "Administrator login required.",
    "Faça login para editar.": "Log in to edit.",
    "Login de administração efetuado.": "Logged in as administrator.",
    "Usuário ou senha incorretos.": "Incorrect username or password.",
    "Sessão de administração encerrada.": "Administrator session ended.",

    # --- Estações / configuração master ----------------------------------------
    "falha ao ler os arquivos monitorados": "failed to read the monitored files",
    "%(n)s caminho(s) absoluto(s) de cadastros (watchdog, arquivos monitorados, log de conexões) foram reapontados para a nova raiz.":
        "%(n)s absolute path(s) in registrations (watchdog, monitored files, connection log) were repointed to the new root.",
    "A raiz apontava para uma pasta incoming/; ajustei para '%(raiz)s' (a raiz do hub é a que CONTÉM incoming/).":
        "The root pointed to an incoming/ folder; adjusted it to '%(raiz)s' (the hub root is the one that CONTAINS incoming/).",
    "Configuração master salva. Pastas, heartbeat e log passam a derivar da nova raiz.":
        "Master settings saved. Folders, heartbeat and log are now derived from the new root.",
    "Falha ao detectar estações do rsync-hub: %(erro)s": "Failed to detect rsync-hub stations: %(erro)s",
    "%(n)s estação(ões) importada(s) do rsync-hub. Pastas e heartbeat usam os locais padrão sob a raiz do hub — abra cada uma para adicionar arquivos de monitoramento.":
        "%(n)s station(s) imported from rsync-hub. Folders and heartbeat use the default locations under the hub root — open each one to add monitored files.",
    "Nenhuma estação nova detectada no rsync-hub.": "No new station detected in rsync-hub.",
    "Estação '%(id)s' cadastrada.": "Station '%(id)s' registered.",
    "Estação atualizada.": "Station updated.",
    "Estação '%(id)s' removida da dashboard. Os arquivos em disco e o que já estava no banco continuam lá (séries órfãs: Banco → Diagnóstico para remover, se for o caso).":
        "Station '%(id)s' removed from the dashboard. Files on disk and what was already in the database are kept (orphan series: Database → Diagnostics to remove them, if needed).",

    # --- Arquivos monitorados --------------------------------------------------
    "Arquivo '%(id)s' adicionado ao monitoramento.": "File '%(id)s' added to monitoring.",
    "%(n)s arquivo(s) adicionado(s) ao monitoramento. Abra 'Editar' em cada um para mapear as colunas e variáveis antes de aparecerem gráficos.":
        "%(n)s file(s) added to monitoring. Open 'Edit' on each one to map columns and variables before charts appear.",
    "Nenhum arquivo novo foi selecionado.": "No new file was selected.",
    "Configuração do arquivo atualizada.": "File settings updated.",
    "Arquivo '%(id)s' removido do monitoramento. O arquivo em disco e o que já estava no banco continuam lá (para apagar do banco: Banco → tabela → Zona de perigo, ou Diagnóstico → séries órfãs).":
        "File '%(id)s' removed from monitoring. The file on disk and what was already in the database are kept (to delete from the database: Database → table → Danger zone, or Diagnostics → orphan series).",

    # --- Chaves da API ---------------------------------------------------------
    "Validade e limite devem ser números.": "Validity and limit must be numbers.",
    "Não foi possível criar a chave: %(erro)s": "Could not create the key: %(erro)s",
    "A API não devolveu a chave.": "The API did not return the key.",
    "Chave criada para '%(dono)s'. Copie-a AGORA: ela não será mostrada de novo.":
        "Key created for '%(dono)s'. Copy it NOW: it will not be shown again.",
    "Chave #%(id)s revogada.": "Key #%(id)s revoked.",
    "Não foi possível revogar: %(erro)s": "Could not revoke: %(erro)s",

    # --- Tratamento ------------------------------------------------------------
    "estado indisponível: a API não respondeu (Configurações → API de dados)":
        "status unavailable: the API did not respond (Settings → Data API)",
    "a ingestão ainda não leu este registro — ela relê o config a cada ciclo (30 s); se persistir, o serviço ingestion não está enxergando o config.yaml atual":
        "the ingestion has not read this registration yet — it rereads the config every cycle (30 s); if this persists, the ingestion service is not seeing the current config.yaml",
    "nenhum arquivo casou com o caminho/padrão registrado (ou ainda está sendo escrito) — confira o caminho em Editar parse/colunas e a verificação 'Registros ingeridos sem nenhum arquivo encontrado' em Banco → Diagnóstico":
        "no file matched the registered path/pattern (or it is still being written) — check the path in Edit parse/columns and the check 'Ingested registrations with no file found' in Database → Diagnostics",
    "Tratamento de %(id)s salvo — no banco.": "Processing of %(id)s saved — in the database.",
    "Tratamento de %(id)s salvo — fora do banco.": "Processing of %(id)s saved — kept out of the database.",

    # --- Banco -----------------------------------------------------------------
    "Banco '%(nome)s' criado.": "Database '%(nome)s' created.",
    "Defina pelo menos uma coluna.": "Define at least one column.",
    "Tabela '%(nome)s' criada.": "Table '%(nome)s' created.",
    "Linha inserida.": "Row inserted.",
    "Linha apagada.": "Row deleted.",
    "Tabela '%(nome)s' excluída.": "Table '%(nome)s' dropped.",
    "Confira host, porta (número), banco e usuário (minúsculas, números e '_').":
        "Check host, port (a number), database and user (lowercase letters, digits and '_').",
    "Conexão OK": "Connection OK",
    "Falha na conexão": "Connection failed",
    "Não consegui gravar o .env: %(erro)s": "Could not write the .env: %(erro)s",
    "Conexão gravada em %(arq)s (modo %(modo)s, %(host)s:%(porta)s/%(banco)s). Clique em 'Aplicar aos serviços' para a API e a ingestão passarem a usar.":
        "Connection saved to %(arq)s (mode %(modo)s, %(host)s:%(porta)s/%(banco)s). Click 'Apply to services' so the API and the ingestion start using it.",
    "Banco local parado.": "Local database stopped.",
    "Falha ao parar o banco local.": "Failed to stop the local database.",
    "Serviços atualizados.": "Services updated.",
    "Falha ao aplicar.": "Failed to apply.",
    "Ação desconhecida.": "Unknown action.",
    "Para ações que apagam dados, digite APAGAR no campo de confirmação.":
        "For actions that delete data, type APAGAR in the confirmation field.",
    "Exportação falhou: %(erro)s": "Export failed: %(erro)s",
    # zona de perigo (format_map com {chaves} — manter as chaves)
    "Tabela zerada: {deleted} valor(es) removido(s); a ingestão relê o arquivo do início.":
        "Table cleared: {deleted} value(s) removed; the ingestion rereads the file from the start.",
    "Variável '{vid}' apagada: {deleted} valor(es). {note}.": "Variable '{vid}' deleted: {deleted} value(s). {note}.",
    "Tabela {truncated} esvaziada.": "Table {truncated} emptied.",
    "Coluna {dropped_column} apagada.": "Column {dropped_column} dropped.",
    "Tabela %(tabela)s apagada: %(n)s valor(es) removido(s), view removida e ingestão do arquivo desligada (o arquivo em disco e o monitoramento dos gráficos continuam).":
        "Table %(tabela)s deleted: %(n)s value(s) removed, view dropped and ingestion of the file turned off (the file on disk and chart monitoring are kept).",
    # _SQL_EXEMPLOS (títulos)
    "Últimos valores de uma estação": "Latest values of a station",
    "Tabela larga da estação (view gerada)": "Station wide table (generated view)",
    "Contagem por estação e variável": "Count per station and variable",
    "Média diária (agregado contínuo)": "Daily mean (continuous aggregate)",
    "Valores com flag de qualidade": "Values with a quality flag",
    "Ledger da ingestão": "Ingestion ledger",
    # _COLUMN_TYPES (rótulos)
    "Texto": "Text",
    "Número (decimal)": "Number (decimal)",
    "Inteiro": "Integer",
    "Sim/Não": "Yes/No",
    "Data e hora (com fuso)": "Date and time (with time zone)",
    "Data": "Date",
    "JSON": "JSON",
    # _MAINT_ACTIONS (rótulo, descrição)
    "Atualizar estatísticas (ANALYZE)": "Update statistics (ANALYZE)",
    "rápido; melhora os planos de consulta": "fast; improves query plans",
    "VACUUM ANALYZE": "VACUUM ANALYZE",
    "recupera espaço de linhas apagadas; pode levar minutos": "reclaims space from deleted rows; may take minutes",
    "Comprimir chunks antigos": "Compress old chunks",
    "aplica agora a compressão dos dados com mais de 30 dias": "compresses data older than 30 days now",
    "Recalcular agregados (30 dias)": "Recompute aggregates (30 days)",
    "refaz as médias horárias/diárias do período": "rebuilds the hourly/daily means for the period",
    "Remover carimbos implausíveis": "Remove implausible timestamps",
    "APAGA linhas antes de 1990 ou no futuro": "DELETES rows before 1990 or in the future",
    "Remover séries órfãs": "Remove orphan series",
    "APAGA do banco as séries de arquivos/variáveis que não estão mais registrados para ingestão (veja a lista na verificação 'Séries órfãs' antes)":
        "DELETES from the database the series of files/variables no longer registered for ingestion (check the list in the 'Orphan series' check first)",

    # --- config_store: validações ----------------------------------------------
    "Informe a pasta raiz do hub (ex.: /srv/carbonara).": "Enter the hub root folder (e.g. /srv/carbonara).",
    "Fuso horário não suportado neste Python (zoneinfo ausente).": "Time zones are not supported by this Python (zoneinfo missing).",
    "Fuso horário inválido: '%(tz)s' (ex.: America/Santarem).": "Invalid time zone: '%(tz)s' (e.g. America/Santarem).",
    "Fuso horário inválido: '%(tz)s' (ex.: UTC, America/Santarem).": "Invalid time zone: '%(tz)s' (e.g. UTC, America/Santarem).",
    "Fallback de offline: use segundos entre 60 e 86400.": "Offline fallback: use seconds between 60 and 86400.",
    "URL da API inválida: comece com http:// ou https:// (ex.: http://127.0.0.1:8000).":
        "Invalid API URL: start with http:// or https:// (e.g. http://127.0.0.1:8000).",
    "Identificador inválido: use apenas letras minúsculas, números, '-' e '_'.":
        "Invalid identifier: use only lowercase letters, digits, '-' and '_'.",
    "Já existe uma estação com o id '%(id)s'.": "A station with id '%(id)s' already exists.",
    "%(campo)s: valor inválido ('%(valor)s').": "%(campo)s: invalid value ('%(valor)s').",
    "%(campo)s: valor inválido ('%(valor)s') — use um inteiro ≥ 2.": "%(campo)s: invalid value ('%(valor)s') — use an integer ≥ 2.",
    "%(campo)s: fora do intervalo permitido (%(lo)s a %(hi)s).": "%(campo)s: outside the allowed range (%(lo)s to %(hi)s).",
    "Latitude": "Latitude",
    "Longitude": "Longitude",
    "Coordenadas: informe latitude E longitude (ou deixe ambas vazias).":
        "Coordinates: enter latitude AND longitude (or leave both empty).",
    "Informe o caminho do arquivo monitorado.": "Enter the path of the monitored file.",
    "Truncar do fim do nome: valor inválido ('%(valor)s') — use um número de caracteres ≥ 1, ou deixe vazio.":
        "Truncate end of name: invalid value ('%(valor)s') — use a number of characters ≥ 1, or leave empty.",
    "O truncamento do fim do nome só vale com \"O nome do arquivo muda com a data/hora\" marcado — é ali que o padrão é resolvido a cada leitura.":
        "Truncating the end of the name only applies with \"The file name changes with the date/time\" checked — that is where the pattern is resolved on every read.",
    "Deslocar horas: valor inválido ('%(valor)s') — use um inteiro entre -168 e +168 (ex.: +2 ou -3), ou deixe vazio.":
        "Shift hours: invalid value ('%(valor)s') — use an integer between -168 and +168 (e.g. +2 or -3), or leave empty.",
    "O deslocamento de horas só vale com \"O nome do arquivo muda com a data/hora\" marcado — ele desloca o relógio usado para expandir o padrão.":
        "The hour shift only applies with \"The file name changes with the date/time\" checked — it shifts the clock used to expand the pattern.",
    "Deslocar horas exige campos de tempo no caminho (%Y, %m, %d, %H…) — num padrão só com curinga (*) não há relógio a deslocar.":
        "Shifting hours requires time fields in the path (%Y, %m, %d, %H…) — a wildcard-only (*) pattern has no clock to shift.",
    "Truncar %(n)s caractere(s) consome o nome inteiro ('%(nome)s', %(tam)s caracteres) — sobraria só '*', que casa qualquer arquivo da pasta. Diminua o valor (ou use um padrão com * escrito, se é isso mesmo que você quer).":
        "Truncating %(n)s character(s) consumes the whole name ('%(nome)s', %(tam)s characters) — only '*' would remain, which matches any file in the folder. Lower the value (or write a pattern with *, if that is really what you want).",
    "Caminho com data/hora marcado, mas o caminho não tem campo de tempo (%Y, %m, %d, %H…) nem curinga (*). Ex.: incoming/est/dados_%Y%m%d.dat ou incoming/est/%Y-%m-%d__*.sfo":
        "Date/time path checked, but the path has no time field (%Y, %m, %d, %H…) nor wildcard (*). E.g. incoming/est/dados_%Y%m%d.dat or incoming/est/%Y-%m-%d__*.sfo",
    "O caminho tem campos de tempo (%Y, %m, %d, %H…) ou curinga (*) — marque \"O nome do arquivo muda com a data/hora\" para que sejam resolvidos, ou remova-os.":
        "The path has time fields (%Y, %m, %d, %H…) or a wildcard (*) — check \"The file name changes with the date/time\" so they are resolved, or remove them.",
    "Linhas do final": "Lines from the end",
    "Período a plotar (horas)": "Period to plot (hours)",
    "Linhas de cabeçalho a pular": "Header lines to skip",
    "Linha com nomes das colunas": "Line with column names",
    "Separador de colunas deve ter exatamente 1 caractere (recebi '%(sep)s').":
        "Column separator must be exactly 1 character (got '%(sep)s').",
    "Separador decimal não pode ser igual ao separador de colunas.":
        "Decimal separator cannot be the same as the column separator.",
    "Expressão para remover dos valores é inválida: %(erro)s": "The expression to strip from values is invalid: %(erro)s",
    "Nome de tabela '%(tabela)s' já usado pelo arquivo '%(arq)s' desta estação.":
        "Table name '%(tabela)s' is already used by file '%(arq)s' of this station.",
    "Nome de tabela '%(tabela)s' já usado pelo arquivo '%(arq)s'.":
        "Table name '%(tabela)s' is already used by file '%(arq)s'.",
    "Reamostragem inválida ('%(valor)s') — use uma de: %(opcoes)s.": "Invalid resampling ('%(valor)s') — use one of: %(opcoes)s.",
    "Reamostragem inválida ('%(valor)s').": "Invalid resampling ('%(valor)s').",
    "A coluna %(col)s é a do timestamp — remova-a das variáveis.": "Column %(col)s is the timestamp column — remove it from the variables.",
    "Índice de coluna repetido: %(col)s — cada variável deve ter um índice único.":
        "Repeated column index: %(col)s — each variable must have a unique index.",
    "Tipo de coluna inválido ('%(valor)s') — use um de: %(opcoes)s.": "Invalid column type ('%(valor)s') — use one of: %(opcoes)s.",
    "Limite mínimo de '%(var)s'": "Minimum limit of '%(var)s'",
    "Limite máximo de '%(var)s'": "Maximum limit of '%(var)s'",
    "'%(var)s': limite mínimo (%(min)s) maior que o máximo (%(max)s).": "'%(var)s': minimum limit (%(min)s) greater than the maximum (%(max)s).",
    "ID no banco inválido para '%(var)s' ('%(valor)s'): use letras minúsculas, números e '_' (começando por letra, até 64).":
        "Invalid database ID for '%(var)s' ('%(valor)s'): use lowercase letters, digits and '_' (starting with a letter, up to 64).",
    "ID no banco repetido: '%(id)s' ('%(var)s') — cada variável do arquivo precisa de um ID único.":
        "Repeated database ID: '%(id)s' ('%(var)s') — each variable of the file needs a unique ID.",
    "ID no banco repetido: '%(id)s' ('%(var)s').": "Repeated database ID: '%(id)s' ('%(var)s').",
    "Defina pelo menos uma variável (coluna + rótulo).": "Define at least one variable (column + label).",
    "ID(s) no banco já usados pelo arquivo '%(arq)s' desta estação: %(ids)s — dois arquivos ingeridos não podem gravar a mesma variável (mude o ID aqui ou desmarque a ingestão).":
        "Database ID(s) already used by file '%(arq)s' of this station: %(ids)s — two ingested files cannot write the same variable (change the ID here or uncheck ingestion).",
    "Mín de '%(var)s'": "Min of '%(var)s'",
    "Máx de '%(var)s'": "Max of '%(var)s'",
    "'%(var)s': mínimo (%(min)s) maior que o máximo (%(max)s).": "'%(var)s': minimum (%(min)s) greater than the maximum (%(max)s).",
    "Salto máximo de '%(var)s'": "Maximum jump of '%(var)s'",
    "Salto máximo de '%(var)s' deve ser > 0.": "Maximum jump of '%(var)s' must be > 0.",
    "Leituras iguais de '%(var)s'": "Identical readings of '%(var)s'",
    "ID(s) já usados pelo arquivo '%(arq)s' desta estação: %(ids)s — mude o ID ou tire a variável do banco num deles.":
        "ID(s) already used by file '%(arq)s' of this station: %(ids)s — change the ID or keep the variable out of the database in one of them.",

    # --- dbconn / api_client ---------------------------------------------------
    "docker não encontrado no PATH da dashboard": "docker not found in the dashboard's PATH",
    "docker compose indisponível: %(erro)s": "docker compose unavailable: %(erro)s",
    "docker compose falhou": "docker compose failed",
    "API não configurada (Configurações → API de dados).": "API not configured (Settings → Data API).",
    "API respondeu %(codigo)s: %(detalhe)s": "API responded %(codigo)s: %(detalhe)s",
    "API inacessível em %(url)s: %(motivo)s": "API unreachable at %(url)s: %(motivo)s",
    "falha ao falar com a API: %(erro)s": "failed to talk to the API: %(erro)s",
}
