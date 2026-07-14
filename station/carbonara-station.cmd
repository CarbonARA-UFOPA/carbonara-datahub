@echo off
rem carbonara-station.cmd — atalho para o cliente CARBONARA no Windows.
rem
rem Executa o carbonara-station.ps1 (que deve estar NA MESMA PASTA) contornando a
rem politica de execucao do PowerShell (ExecutionPolicy), que por padrao bloqueia
rem scripts .ps1. Assim nao e preciso rodar Set-ExecutionPolicy nem Unblock-File.
rem
rem Uso: carbonara-station [setup|test|send|schedule|unschedule|status|help]
rem      (sem argumentos, ou com duplo clique, abre o menu interativo)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0carbonara-station.ps1" %*
