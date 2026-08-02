#!/bin/bash
# Теневая сверка: берёт новые выгрузки, присланные боту, и проверяет на них
# серверный расчёт. Отчёт уходит в Telegram. Запускается по cron.
#
# Ручной запуск:            /root/plan/shadow_check.sh
# Конкретный файл:          /root/plan/shadow_check.sh --file /путь/к/export.json
# Без отправки в Telegram:  /root/plan/shadow_check.sh --no-telegram
set -u

cd /root/plan/server || exit 1
export PYTHONDONTWRITEBYTECODE=1

# База поднимается контейнером; если сервер перезагружался, контейнер мог не стартовать.
if ! docker ps --filter name=plan-pg --format '{{.Names}}' | grep -q plan-pg; then
  docker start plan-pg >/dev/null 2>&1
  sleep 5
fi

exec /root/plan/server_venv/bin/python manage.py shadow_check "$@"
