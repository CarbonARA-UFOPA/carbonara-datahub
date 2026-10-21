"""Coleta (/hub): hub.html, hub_station.html, hub_files.html, _dir_picker.html,
hub_views.py (flash) e hub.py (mensagens de erro devolvidas à UI)."""

EN = {
    # --- hub.html ---------------------------------------------------------------
    "Coleta (rsync-hub)": "Collection (rsync-hub)",
    "Coleta — gerenciamento do rsync-hub": "Collection — rsync-hub management",
    "Esta tela administra a <strong>coleta</strong> (o rsync-hub) pela dashboard: autorizar e revogar estações, ver/baixar credenciais e navegar pelos arquivos de cada estação (botão <em>arquivos</em> na lista, ou <em>gerenciar</em> para recebidos/arquivados/quarentena). As operações de escrita executam o próprio <code>rsync-hub</code> no host — a lógica de chaves continua num lugar só.":
        "This page manages <strong>collection</strong> (the rsync-hub) from the dashboard: authorize and revoke stations, view/download credentials and browse each station's files (the <em>files</em> button in the list, or <em>manage</em> for received/archived/quarantine). Write operations run <code>rsync-hub</code> itself on the host — the key logic stays in one place.",
    "Gerenciamento em modo somente leitura.": "Management in read-only mode.",
    "O executável <code>rsync-hub</code> não foi encontrado neste host — prepare-o com <code>sudo ./collection/rsync-hub install</code>.":
        "The <code>rsync-hub</code> executable was not found on this host — set it up with <code>sudo ./collection/rsync-hub install</code>.",
    "A dashboard não tem permissão para executar <code>%(bin)s</code> como root sem senha.":
        "The dashboard is not allowed to run <code>%(bin)s</code> as root without a password.",
    "Resposta do <code>sudo</code>:": "<code>sudo</code> response:",
    "A dashboard roda como <strong>%(user)s</strong> — é ESTE o usuário que precisa estar na regra (se o script foi rodado com outro nome, ou a partir de um shell já root, a regra não vale aqui). Rode no servidor:":
        "The dashboard runs as <strong>%(user)s</strong> — THIS is the user that must be in the rule (if the script was run with another name, or from an already-root shell, the rule does not apply here). Run on the server:",
    "Confira depois com <code>sudo -l -U %(user)s</code>; se a regra aparece lá e mesmo assim não vale, veja se o <code>/etc/sudoers</code> tem a linha <code>#includedir /etc/sudoers.d</code>.":
        "Then check with <code>sudo -l -U %(user)s</code>; if the rule shows up there and still does not apply, make sure <code>/etc/sudoers</code> has the line <code>#includedir /etc/sudoers.d</code>.",
    "(ou libere manualmente no <code>visudo</code>: <code>%(hint)s</code>)":
        "(or allow it manually in <code>visudo</code>: <code>%(hint)s</code>)",
    "Enquanto isso, listagem, credenciais e downloads continuam funcionando.":
        "Meanwhile, listing, credentials and downloads keep working.",
    "A dashboard está lendo <code>%(base)s</code>, mas o rsync-hub usa <code>%(conf)s</code>. Alinhe em <a href=\"%(url)s\">Configurações</a>.":
        "The dashboard is reading <code>%(base)s</code>, but rsync-hub uses <code>%(conf)s</code>. Align them in <a href=\"%(url)s\">Settings</a>.",
    "Estações autorizadas no hub": "Stations authorized on the hub",
    "Nenhuma estação detectada sob <code>%(base)s</code>. Autorize a primeira no formulário abaixo.":
        "No station detected under <code>%(base)s</code>. Authorize the first one with the form below.",
    "Estação": "Station",
    "Autenticação": "Authentication",
    "Recepção": "Reception",
    "Credenciais no hub": "Credentials on the hub",
    "Na dashboard?": "In the dashboard?",
    "chave SSH": "SSH key",
    "usuário+senha": "username+password",
    "nenhuma (só dados)": "none (data only)",
    "pausada": "paused",
    "pausa global": "global pause",
    "ativa": "active",
    "▶ retomar": "▶ resume",
    "⏸ pausar": "⏸ pause",
    "Pausar a recepção de %(id)s? Ela continua autorizada e reenvia os arquivos quando você retomar.":
        "Pause reception for %(id)s? It stays authorized and resends its files when you resume.",
    "✓ sim": "✓ yes",
    "— ainda não": "— not yet",
    "arquivos": "files",
    "chave + INSTRUCOES.txt + cliente, num .zip": "key + INSTRUCOES.txt + client, in one .zip",
    "gerenciar": "manage",
    "Autorizar nova estação": "Authorize a new station",
    "indisponível: veja o aviso acima": "unavailable: see the notice above",
    "Identificador da estação": "Station identifier",
    "ex.: flona01": "e.g. flona01",
    "Minúsculas, dígitos e <code>_</code> (mesma regra do rsync-hub).":
        "Lowercase letters, digits and <code>_</code> (same rule as rsync-hub).",
    "Modo de autenticação": "Authentication mode",
    "Chave SSH (recomendado — criptografado, porta 22)": "SSH key (recommended — encrypted, port 22)",
    "Usuário + senha (rsync daemon, porta 873 — dados SEM criptografia)":
        "Username + password (rsync daemon, port 873 — data NOT encrypted)",
    "Chave pública da estação (opcional)": "Station public key (optional)",
    "vazio = GERAR o par aqui no hub (fica em stations/<id>/ com o INSTRUCOES.txt) · ou cole a linha 'ssh-ed25519 AAAA…'":
        "empty = GENERATE the pair here on the hub (stored in stations/<id>/ with INSTRUCOES.txt) · or paste the 'ssh-ed25519 AAAA…' line",
    "Senha (opcional)": "Password (optional)",
    "vazio = gerar uma senha aleatória": "empty = generate a random password",
    "Sem <code>:</code> nem espaços. A senha final fica no <code>INSTRUCOES.txt</code> da estação. Este modo exige a porta 873/tcp aberta até a estação e o tráfego de dados NÃO é criptografado.":
        "No <code>:</code> or spaces. The final password goes into the station's <code>INSTRUCOES.txt</code>. This mode requires port 873/tcp open all the way to the station, and data traffic is NOT encrypted.",
    "Autorizar estação": "Authorize station",
    "Recepção de dados": "Data reception",
    "PAUSADA (todas as estações)": "PAUSED (all stations)",
    "Ativa": "Active",
    "Pausar suspende a <strong>chegada</strong> de arquivos ao servidor sem revogar credenciais e sem apagar nada: as estações continuam autorizadas, tentam enviar, recebem uma recusa e <strong>reenviam tudo quando a recepção voltar</strong>. Vale para os dois modos de autenticação (chave SSH e usuário+senha). Útil em manutenção do servidor (disco cheio, migração da raiz de dados, banco parado).":
        "Pausing suspends the <strong>arrival</strong> of files on the server without revoking credentials or deleting anything: stations stay authorized, try to send, get a refusal and <strong>resend everything when reception resumes</strong>. It applies to both authentication modes (SSH key and username+password). Useful for server maintenance (full disk, data root migration, database down).",
    "A regra do <code>sudo</code> deste servidor é anterior à pausa e não libera <code>pause</code>/<code>resume</code>. Atualize-a rodando no servidor:":
        "This server's <code>sudo</code> rule predates the pause feature and does not allow <code>pause</code>/<code>resume</code>. Update it by running on the server:",
    "&lt;usuario-que-roda-a-dashboard&gt;": "&lt;user-running-the-dashboard&gt;",
    "Pausada": "Paused",
    "em <strong>%(quando)s</strong>": "on <strong>%(quando)s</strong>",
    "por <strong>%(quem)s</strong>": "by <strong>%(quem)s</strong>",
    "motivo:": "reason:",
    "gerenciamento em modo somente leitura": "management in read-only mode",
    "▶ Retomar a recepção de todas as estações": "▶ Resume reception for all stations",
    "Pausar a recepção de TODAS as estações? Elas continuam autorizadas e reenviam os arquivos quando você retomar.":
        "Pause reception for ALL stations? They stay authorized and resend their files when you resume.",
    "Motivo (opcional — aparece para todo mundo na dashboard)":
        "Reason (optional — shown to everyone on the dashboard)",
    "ex.: manutenção do servidor": "e.g. server maintenance",
    "⏸ Pausar a recepção de todas as estações": "⏸ Pause reception for all stations",
    "Pausas individuais em vigor (independentes da global):":
        "Individual pauses in effect (independent of the global one):",
    "— retome cada uma na linha da estação abaixo.": "— resume each one on its station row below.",
    "Base do hub:": "Hub base:",
    "executável:": "executable:",

    # --- hub_station.html ---------------------------------------------------------
    "Coleta": "Collection",
    "Estação <code>%(id)s</code> — coleta": "Station <code>%(id)s</code> — collection",
    "← Voltar ao gerenciamento": "← Back to management",
    "Situação no hub": "Status on the hub",
    "Autenticação por chave SSH": "SSH key authentication",
    "✓ autorizada": "✓ authorized",
    "não": "no",
    "Autenticação por usuário+senha": "Username+password authentication",
    "(rsync daemon, porta 873)": "(rsync daemon, port 873)",
    "PAUSADA": "PAUSED",
    "(pausa global do servidor)": "(server-wide pause)",
    "Fontes detectadas": "Detected sources",
    "Status ao vivo": "Live status",
    "último sinal:": "last signal:",
    "✓ sim — abrir monitoramento": "✓ yes — open monitoring",
    "ainda não — importe em <a href=\"%(url)s\">Configurações</a>":
        "not yet — import it in <a href=\"%(url)s\">Settings</a>",
    "Arquivos recebidos": "Received files",
    "Arquivados": "Archived",
    "Quarentena": "Quarantine",
    "Credenciais": "Credentials",
    "⬇ Baixar kit da estação (.zip)": "⬇ Download station kit (.zip)",
    "O kit reúne num pacote só tudo o que o operador leva para a máquina da estação: a chave, o <code>INSTRUCOES.txt</code> (passo a passo de ativação) e o cliente de coleta (Linux e Windows), com um LEIA-ME.":
        "The kit bundles in a single package everything the operator takes to the station machine: the key, <code>INSTRUCOES.txt</code> (step-by-step activation, in Portuguese) and the collection client (Linux and Windows), with a LEIA-ME (readme).",
    "Sem credenciais guardadas no hub para esta estação (a chave foi gerada na própria estação, ou o modo é usuário+senha antigo).":
        "No credentials stored on the hub for this station (the key was generated on the station itself, or it uses the old username+password mode).",
    "Arquivo": "File",
    "Tamanho": "Size",
    "Modificado": "Modified",
    "baixar": "download",
    "A chave <em>privada</em> (<code>%(id)s_key</code>) é a que o operador leva para a máquina da estação; a <code>.pub</code> permanece aqui (já autorizada).":
        "The <em>private</em> key (<code>%(id)s_key</code>) is the one the operator takes to the station machine; the <code>.pub</code> stays here (already authorized).",
    "Chave pública": "Public key",
    "Linha no authorized_keys (comando forçado)": "authorized_keys line (forced command)",
    "(ativação da estação — pode conter a senha)": "(station activation — may contain the password)",
    "Esta estação está parada pela <strong>pausa global</strong> do servidor":
        "This station is stopped by the server's <strong>global pause</strong>",
    "desde %(quando)s": "since %(quando)s",
    "Retome-a no card <em>Recepção de dados</em> da <a href=\"%(url)s\">tela de Coleta</a>.":
        "Resume it in the <em>Data reception</em> card on the <a href=\"%(url)s\">Collection page</a>.",
    "Recepção pausada só desta estação": "Reception paused for this station only",
    "(por %(quem)s)": "(by %(quem)s)",
    "Ela continua autorizada: os arquivos ficam guardados na estação e sobem no primeiro ciclo depois de retomada.":
        "It stays authorized: files are kept on the station and uploaded in the first cycle after resuming.",
    "▶ Retomar a recepção de %(id)s": "▶ Resume reception for %(id)s",
    "Pausar suspende só a <strong>chegada</strong> de novos arquivos desta estação — sem revogar a chave/senha e sem apagar nada. Ela tenta enviar, recebe uma recusa e reenvia tudo quando a recepção for retomada.":
        "Pausing only suspends the <strong>arrival</strong> of new files from this station — without revoking the key/password and without deleting anything. It tries to send, gets a refusal and resends everything once reception resumes.",
    "Motivo (opcional)": "Reason (optional)",
    "ex.: troca do disco da estação": "e.g. replacing the station's disk",
    "⏸ Pausar a recepção de %(id)s": "⏸ Pause reception for %(id)s",
    "Revogar acesso": "Revoke access",
    "Revoga chave <em>e</em> senha desta estação no hub. Os dados em <code>incoming/%(id)s/</code> e as credenciais em <code>stations/%(id)s/</code> são preservados (comportamento do rsync-hub).":
        "Revokes this station's key <em>and</em> password on the hub. The data in <code>incoming/%(id)s/</code> and the credentials in <code>stations/%(id)s/</code> are preserved (rsync-hub behavior).",
    "Revogar o acesso da estação %(id)s? Ela deixará de conseguir enviar dados (os dados no servidor são preservados).":
        "Revoke access for station %(id)s? It will no longer be able to send data (data on the server is preserved).",
    "indisponível: gerenciamento em modo somente leitura": "unavailable: management in read-only mode",
    "Revogar acesso de %(id)s": "Revoke access for %(id)s",
    "⚠ Excluir estação definitivamente": "⚠ Permanently delete station",
    "<strong>Ação IRREVERSÍVEL — não há lixeira.</strong> Além de revogar o acesso, isto <strong>APAGA do servidor</strong> tudo desta estação: <code>incoming/%(id)s/</code> (dados recebidos), <code>archive/%(id)s/</code> (dados já processados/arquivados), <code>quarantine/%(id)s/</code> e <code>stations/%(id)s/</code> (chaves e instruções — a estação não conseguirá se reconectar nem com a chave antiga). Se ela estiver cadastrada no monitoramento do dashboard, o cadastro também é removido.":
        "<strong>IRREVERSIBLE action — there is no trash.</strong> Besides revoking access, this <strong>DELETES from the server</strong> everything of this station: <code>incoming/%(id)s/</code> (received data), <code>archive/%(id)s/</code> (already processed/archived data), <code>quarantine/%(id)s/</code> and <code>stations/%(id)s/</code> (keys and instructions — the station will not be able to reconnect, not even with the old key). If it is registered in the dashboard's monitoring, that registration is removed too.",
    "Para manter os dados e só cortar o envio, use <em>Revogar acesso</em> acima. Considere <a href=\"%(url)s\">baixar os arquivos</a> antes.":
        "To keep the data and only cut off sending, use <em>Revoke access</em> above. Consider <a href=\"%(url)s\">downloading the files</a> first.",
    "ÚLTIMA CONFIRMAÇÃO: excluir DEFINITIVAMENTE a estação %(id)s e apagar todos os seus dados do servidor? Não há como desfazer.":
        "FINAL CONFIRMATION: PERMANENTLY delete station %(id)s and erase all its data from the server? This cannot be undone.",
    "Para liberar o botão, digite o id da estação (<code>%(id)s</code>):":
        "To enable the button, type the station id (<code>%(id)s</code>):",
    "Excluir %(id)s e apagar os dados do servidor": "Delete %(id)s and erase its data from the server",
    "A digitação é reconferida no servidor — sem o nome exato, nada é apagado. Se o hub tiver dados no banco (pilha de ingestão), as linhas já inseridas no TimescaleDB não são afetadas.":
        "The typed name is re-checked on the server — without the exact name, nothing is deleted. If the hub has data in the database (ingestion stack), rows already inserted into TimescaleDB are not affected.",

    # --- hub_files.html ---------------------------------------------------------
    "Arquivos": "Files",
    "← Voltar à estação %(id)s": "← Back to station %(id)s",
    "← Voltar ao FTP": "← Back to FTP",
    "Esta pasta ainda não existe no hub (nada foi recebido/movido para cá).":
        "This folder does not exist on the hub yet (nothing was received/moved here).",
    "Pasta vazia.": "Empty folder.",
    "Nome": "Name",
    "↩ pasta acima": "↩ parent folder",
    "(oculto — a ingestão ignora)": "(hidden — ignored by ingestion)",
    "Listagem truncada em %(n)s entradas — esta pasta tem mais itens; navegue pelas subpastas.":
        "Listing truncated at %(n)s entries — this folder has more items; browse the subfolders.",
    "Navegação <strong>somente leitura</strong>: a dashboard nunca apaga nem move arquivos do hub — quem move <code>incoming/ → archive/ | quarantine/</code> é a ingestão.":
        "<strong>Read-only</strong> browsing: the dashboard never deletes or moves hub files — moving <code>incoming/ → archive/ | quarantine/</code> is the ingestion's job.",
    # Rótulos das áreas (hub.AREAS, traduzidos no template com _(label))
    "Recebidos (incoming)": "Received (incoming)",
    "Arquivados (archive)": "Archived (archive)",
    "Credenciais (stations)": "Credentials (stations)",
    "FTP (publicado)": "FTP (published)",

    # --- _dir_picker.html (JS) --------------------------------------------------
    "carregando…": "loading…",
    "(pasta ainda não existe)": "(folder does not exist yet)",
    "sem permissão para listar esta pasta": "no permission to list this folder",
    "(sem subpastas)": "(no subfolders)",
    "%(n)s arquivo(s) aqui · ": "%(n)s file(s) here · ",
    "valor: ": "value: ",
    "(raiz)": "(root)",
    "falha ao listar": "listing failed",
    "escolha uma SUBPASTA (a raiz não é aceita aqui)": "choose a SUBFOLDER (the root is not accepted here)",

    # --- hub_views.py (flash) ---------------------------------------------------
    "Recepção pausada.": "Reception paused.",
    "Falha ao pausar a recepção.": "Failed to pause reception.",
    "Recepção retomada.": "Reception resumed.",
    "Falha ao retomar a recepção.": "Failed to resume reception.",
    "Não consegui ler a BASE em /etc/rsync-hub.conf — ajuste a raiz à mão no campo acima.":
        "Could not read BASE from /etc/rsync-hub.conf — set the root manually in the field above.",
    "A raiz da dashboard já é %(base)s — nada a alinhar.": "The dashboard root is already %(base)s — nothing to align.",
    "Raiz da dashboard alinhada com o rsync-hub: %(base)s.": "Dashboard root aligned with rsync-hub: %(base)s.",
    "%(n)s caminho(s) absoluto(s) de cadastros foram reapontados.":
        "%(n)s absolute registration path(s) were repointed.",
    "Estação autorizada.": "Station authorized.",
    "Falha ao autorizar.": "Authorization failed.",
    "Kit de ativação pronto: use o botão \"Baixar kit da estação (.zip)\" abaixo — chave, INSTRUCOES.txt e o cliente num pacote só.":
        "Activation kit ready: use the \"Download station kit (.zip)\" button below — key, INSTRUCOES.txt and the client in a single package.",
    "Acesso revogado.": "Access revoked.",
    "Falha ao revogar.": "Revocation failed.",
    "Exclusão NÃO executada: o nome digitado ('%(typed)s') não confere com '%(id)s'. Nada foi apagado.":
        "Deletion NOT performed: the typed name ('%(typed)s') does not match '%(id)s'. Nothing was deleted.",
    "'%(id)s' também foi removida do cadastro de monitoramento do dashboard.":
        "'%(id)s' was also removed from the dashboard's monitoring registrations.",
    "Estação excluída.": "Station deleted.",
    "Falha na exclusão.": "Deletion failed.",
    "Migração não executada: confirme que entendeu as pendências manuais (caixa de confirmação).":
        "Migration not performed: confirm that you understand the manual follow-ups (checkbox).",
    "O caminho informado terminava em incoming/ — usei '%(root)s' (a raiz do hub é a que CONTÉM incoming/).":
        "The given path ended in incoming/ — used '%(root)s' instead (the hub root is the one that CONTAINS incoming/).",
    "%(n)s caminho(s) absoluto(s) de cadastros do dashboard (watchdog/arquivos monitorados/log) foram reapontados para a nova raiz.":
        "%(n)s absolute dashboard registration path(s) (watchdog/monitored files/log) were repointed to the new root.",
    "Raiz migrada.": "Root migrated.",
    "Falha na migração.": "Migration failed.",
    "Pasta não encontrada (ou fora da área permitida).": "Folder not found (or outside the allowed area).",

    # --- hub.py (erros devolvidos à UI) -----------------------------------------
    "rsync-hub não encontrado neste host.": "rsync-hub not found on this host.",
    "o comando 'sudo' não existe (ou não está no PATH deste processo).":
        "the 'sudo' command does not exist (or is not in this process's PATH).",
    "o 'sudo' não respondeu em 10s (pediu senha e ficou esperando?).":
        "'sudo' did not answer within 10 s (did it ask for a password and hang?).",
    "falha ao executar o sudo: %(erro)s": "failed to run sudo: %(erro)s",
    "sudo recusou (rc=%(rc)s) sem mensagem.": "sudo refused (rc=%(rc)s) without a message.",
    "rsync-hub não encontrado neste host (procurei o override 'rsync_hub_bin' do config, /usr/local/bin/rsync-hub e o PATH). Prepare o host com: sudo ./collection/rsync-hub install":
        "rsync-hub not found on this host (looked at the config's 'rsync_hub_bin' override, /usr/local/bin/rsync-hub and the PATH). Set up the host with: sudo ./collection/rsync-hub install",
    "sudo não encontrado — rode a dashboard como root ou instale o sudo.":
        "sudo not found — run the dashboard as root or install sudo.",
    "tempo esgotado executando: %(cmd)s": "timed out running: %(cmd)s",
    "sudo pediu senha (a dashboard só executa modo não interativo).\nRode no servidor: sudo dashboard/setup-hub-access.sh <usuario-da-dashboard>\n(ou libere no visudo a linha: %(hint)s)":
        "sudo asked for a password (the dashboard only runs non-interactively).\nRun on the server: sudo dashboard/setup-hub-access.sh <dashboard-user>\n(or allow this line in visudo: %(hint)s)",
    "falha (rc=%(rc)s) executando: %(cmd)s": "failure (rc=%(rc)s) running: %(cmd)s",
    "todas as estações": "all stations",
    "a estação '%(id)s'": "station '%(id)s'",
    "id de estação inválido para pausar.": "invalid station id to pause.",
    "id de estação inválido para retomar.": "invalid station id to resume.",
    "id inválido (minúsculas, dígitos e '_', começando por letra/dígito).":
        "invalid id (lowercase letters, digits and '_', starting with a letter/digit).",
    "isso não parece uma chave pública SSH ('ssh-ed25519 AAAA…').":
        "this does not look like an SSH public key ('ssh-ed25519 AAAA…').",
    "a senha não pode conter ':' nem espaço.": "the password cannot contain ':' or spaces.",
    "informe um caminho ABSOLUTO (começando com /) para a nova raiz.":
        "enter an ABSOLUTE path (starting with /) for the new root.",
    "não dá para usar '/' como raiz.": "'/' cannot be used as the root.",
    "o novo caminho não pode conter espaços, aspas, ':' ou '\\'.":
        "the new path cannot contain spaces, quotes, ':' or '\\'.",
    "id inválido.": "invalid id.",
    "nada para empacotar: a estação não tem credenciais em %(dir)s e os clientes de collection/station/ não foram encontrados junto ao dashboard.":
        "nothing to package: the station has no credentials in %(dir)s and the collection/station/ clients were not found next to the dashboard.",
}
