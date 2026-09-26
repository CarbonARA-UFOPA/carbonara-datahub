"""carbonara_parse — leitura e parse dos arquivos das estações, COMPARTILHADO.

A dashboard (preview e gráficos) e o serviço de ingestão (banco) leem os mesmos
arquivos com a MESMA configuração — o registro do "arquivo monitorado" feito na
dashboard (separador, cabeçalho, coluna/formato do timestamp, tipos das variáveis).
Este pacote é a única implementação desse parse: se o preview mostra um valor, é
exatamente esse valor que entra no banco. Não depende de Flask nem de banco.

Módulos:
- ``tailer``    leitura eficiente do fim do arquivo + divisão em colunas + conversores
- ``timeparse`` carimbos de tempo (texto, epoch), fuso e formato de exibição
- ``paths``     caminho com data/hora (strftime/curinga) e resolução sob a raiz de dados
- ``records``   leitura INCREMENTAL por offset e conversão das variáveis registradas
"""
from . import paths, records, tailer, timeparse  # noqa: F401
