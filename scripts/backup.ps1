# Backup diário do banco real (perfil "personal"), mantendo as 30 cópias mais recentes.
# Agende no "Agendador de Tarefas" do Windows (ex.: todo dia às 22h).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$env:DJANGO_SETTINGS_MODULE = "config.settings.personal"

uv run python manage.py backup --manter 30
