#!/usr/bin/env bash
set -e
VPS="deployer@74.208.177.68"
REMOTE_DIR="/var/www/maewalliscorp.org/test/api/mct7atest"

echo "Subiendo backend a $VPS..."
scp backend/main.py backend/dashboard.html "$VPS:$REMOTE_DIR/"

echo "Reiniciando servicio mct7atest..."
ssh -t "$VPS" "sudo systemctl restart mct7atest && sudo systemctl status mct7atest --no-pager -n 5"
echo "Despliegue completado."
