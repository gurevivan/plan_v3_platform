#!/usr/bin/env bash
# Тестовый стенд «Плана»: страница и API с одного адреса.
#
#   server/stand.sh start|stop|status [порт]
#
# Отладочный режим ВЫКЛЮЧЕН и ключ подписи настоящий (server/.env.local, вне git):
# стенд доступен снаружи, а при DEBUG=1 любая ошибка показывала бы кусок данных
# и настройки прямо в браузере.
#
# Это dev-сервер Django: один процесс, без HTTPS. Для проверки руками — годится,
# для боевой работы — нужен nginx + gunicorn (ТЗ §12).
set -u
cd "$(dirname "$0")" || exit 1

PORT=${2:-8012}
LOG=/tmp/plan_stand.log
PY=../server_venv/bin/python

case "${1:-start}" in
  stop)
    pkill -f "runserver 0.0.0.0:$PORT" && echo "стенд на порту $PORT остановлен" \
      || echo "стенд на порту $PORT не запущен"
    ;;
  status)
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/me")
    echo "порт $PORT → /api/me отвечает $code (403 = сервер жив, вы не вошли)"
    ;;
  start)
    pkill -f "runserver 0.0.0.0:$PORT" >/dev/null 2>&1
    sleep 1
    set -a; . ./.env.local; set +a
    export DJANGO_DEBUG=0
    export DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS:-*}"
    # --insecure: dev-сервер при DEBUG=0 не отдаёт статику, а без неё
    # админка и страница входа DRF остаются без стилей.
    setsid $PY manage.py runserver "0.0.0.0:$PORT" --noreload --insecure \
      > "$LOG" 2>&1 < /dev/null &
    sleep 5
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/")
    echo "стенд запущен на порту $PORT (страница отвечает $code), журнал: $LOG"
    ;;
  *)
    echo "использование: $0 start|stop|status [порт]"; exit 1;;
esac
