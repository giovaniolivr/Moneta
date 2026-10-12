# Inicia o Moneta para uso real (perfil "personal"): migra, prepara os estáticos e serve em
# 127.0.0.1:8000 com o waitress. O Tailscale (`tailscale serve`) publica isso na sua rede privada.
# Agende no "Agendador de Tarefas" do Windows para iniciar ao fazer logon.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$env:DJANGO_SETTINGS_MODULE = "config.settings.personal"

uv run python manage.py migrate --noinput
uv run python manage.py collectstatic --noinput --verbosity 0
uv run waitress-serve --listen=127.0.0.1:8000 --threads=4 config.wsgi:application
