#!/usr/bin/env bash
# Поднять чистый стенд для живой проверки серверного режима:
# перезалить фикстуру в базу и перезапустить сервер разработки.
#
#   tests/live_reset.sh [фикстура] [порт]
set -u
FIX=${1:-../tests/fixtures/real_export_20260801.json}
PORT=${2:-8011}
cd "$(dirname "$0")/../server" || exit 1

pkill -f "runserver 127.0.0.1:$PORT" >/dev/null 2>&1
sleep 1
../server_venv/bin/python manage.py import_json "$FIX" >/dev/null 2>&1 || {
  echo "импорт не удался"; exit 1; }
setsid ../server_venv/bin/python manage.py runserver "127.0.0.1:$PORT" --noreload \
  > /tmp/plan_dev_server.log 2>&1 < /dev/null &
sleep 5
code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/me")
echo "стенд готов: порт $PORT, /api/me → $code (403 = не вошли, это нормально)"
