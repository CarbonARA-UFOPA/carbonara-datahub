# Servidor FTP/FTPS

O serviço `ftp` no `docker-compose.yml` é o **ponto único de concentração**: cada estação
envia seus arquivos para cá, dentro de `landing/<station_id>/`.

Usamos a imagem [`stilliard/pure-ftpd`](https://github.com/stilliard/docker-pure-ftpd).

## TLS (FTPS) — obrigatório em produção

A flag `--tls=1` exige conexão criptografada. É preciso fornecer um certificado em
`server/ftp/tls/pure-ftpd.pem` (chave + certificado concatenados no mesmo arquivo).

Gerar um certificado autoassinado para testes/campo:

```bash
mkdir -p server/ftp/tls
openssl req -x509 -nodes -newkey rsa:2048 -sha256 -days 3650 \
  -keyout server/ftp/tls/pure-ftpd.pem \
  -out    server/ftp/tls/pure-ftpd.pem \
  -subj "/CN=carbonara-ftp"
chmod 600 server/ftp/tls/pure-ftpd.pem
```

> Para produção, prefira um certificado emitido por uma CA. O `tls/` está no `.gitignore`.

## Um usuário por estação (recomendado)

O compose cria um usuário base (`FTP_USER_NAME`). Para isolar estações, crie usuários
adicionais dentro do container, cada um com home em sua subpasta de `landing/`:

```bash
docker exec -it carbonara-ftp \
  pure-pw useradd estacao_flona01 -f /etc/pure-ftpd/passwd/pureftpd.passwd \
  -m -u ftpuser -d /home/ftpusers/carbonara/flona01
docker exec -it carbonara-ftp pure-pw mkdb
```

Assim a estação `flona01` só enxerga `landing/flona01/`. Ajuste o `config.yaml` do
agente de coleta com essas credenciais.

## Trocar de transporte

Se no futuro o FTP for substituído por SFTP ou rsync, **apenas este serviço e a config
de transporte do agente mudam** — a ingestão e o banco continuam idênticos, pois só
dependem do diretório `landing/`.
