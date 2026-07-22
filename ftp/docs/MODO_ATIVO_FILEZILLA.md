# Usar Modo ATIVO no FileZilla (Workaround)

Se está tendo problema com PASV timeout, use **Modo ATIVO** temporariamente:

## Passos:

1. **Abra FileZilla**
2. **Edit → Settings → FTP**
3. Procure por **"Transfer Mode"** ou **"Connection"**
4. Mude de **"Passive"** para **"Active"**
5. Clique **OK**
6. **Desconecte e reconecte** no servidor

## Por que funciona:

- **PASV (Passivo):** Cliente conecta nas portas 30000-30009 do servidor
  - ❌ Pode ter problema com NAT/redirecionamento
  
- **ATIVO:** Servidor conecta de volta no cliente (porta aleatória)
  - ✅ Funciona mesmo com problemas de redirecionamento
  - ⚠️ Pode ter problema se seu firewall pessoal bloquear

## Se ATIVO também não funcionar:

Seu firewall pessoal pode estar bloqueando conexões de entrada. Neste caso:
- Tente em uma rede diferente (mobile hotspot)
- Ou procure a configuração de firewall pessoal

---

**Nós vamos resolver o PASV também!** Enquanto isso, você consegue trabalhar com ATIVO.
