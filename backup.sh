#!/bin/bash
# Резервная копия План.html с ротацией (хранить последние 80 копий).
cd /root/plan || exit 1
mkdir -p backups
[ -f "План.html" ] || exit 0
cp -a "План.html" "backups/План_$(date +%Y%m%d_%H%M%S).html"
# Ротация: оставляем 80 самых свежих
ls -1t backups/План_*.html 2>/dev/null | tail -n +81 | xargs -r rm -f
