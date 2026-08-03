#!/usr/bin/env bash
# Установка и обновление «План V3». Нужен только docker — без docker compose.
#
#   bash setup.sh            установить или обновить
#   bash setup.sh --stop     остановить
#   bash setup.sh --start    запустить снова
#   bash setup.sh --status   что сейчас работает
#   bash setup.sh --logs     логи приложения
#
# Скрипт идемпотентен: повторный запуск пересобирает образ и пересоздаёт
# приложение, НЕ трогая том с данными. Поэтому обновление — это тот же setup.sh,
# а не отдельная инструкция, которая однажды разойдётся с этой.
#
# Compose не используется намеренно: он есть не на каждом сервере (на Ubuntu
# пакет docker.io идёт без плагина), а голый docker есть везде, где есть docker.
set -euo pipefail

cd "$(dirname "$0")"

IMAGE=plan-app
NET=plan-net
VOL=plan-pgdata
DB=plan-db
WEB=plan-web
PG_IMAGE=postgres:16-alpine

# ── Управляющие команды ─────────────────────────────────────────────────────
case "${1:-}" in
  --stop)
    docker stop "$WEB" "$DB" >/dev/null 2>&1 || true
    echo "Остановлено. Данные на месте — запустить снова: bash setup.sh --start"
    exit 0 ;;
  --start)
    docker start "$DB" >/dev/null && sleep 3 && docker start "$WEB" >/dev/null
    echo "Запущено."
    exit 0 ;;
  --status)
    docker ps -a --filter "name=$DB" --filter "name=$WEB" \
      --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
    exit 0 ;;
  --logs)
    docker logs -f --tail 100 "$WEB"
    exit 0 ;;
  '') : ;;
  *)
    echo "Неизвестный ключ: $1. Смотрите начало файла."
    exit 1 ;;
esac

# ── Проверки окружения ──────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker не найден. Ссылки на установку — в README, раздел «Что нужно установить»."
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker установлен, но не запущен (или нет прав)."
  echo "Запустите службу Docker; в Linux может понадобиться sudo."
  exit 1
fi

# ── Настройки ───────────────────────────────────────────────────────────────
# Секреты генерируются здесь и остаются на машине: .env в репозиторий не идёт.
# Одинаковый ключ на всех установках означал бы, что сессию можно подделать,
# зная исходники.
if [ -f .env ]; then
  echo "Файл .env уже есть — оставляю как есть."
else
  echo "Создаю .env с новыми паролями…"
  gen() { LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c "$1"; }
  cat > .env <<EOF
# Настройки установки. НЕ добавлять в git: здесь пароли.
DJANGO_SECRET_KEY=$(gen 50)
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=*

PG_DB=plandb
PG_USER=planuser
PG_PASSWORD=$(gen 24)

# Порт, на котором откроется приложение.
PLAN_PORT=8000
TZ=Asia/Tashkent

# Сеть Docker. Оставьте пустым — установка создаст свою (plan-net). Укажите имя
# существующей сети, если на сервере кончились свободные подсети.
PLAN_NET=
EOF
  chmod 600 .env
fi

set -a; . ./.env; set +a
PLAN_PORT="${PLAN_PORT:-8000}"

# Занятый порт — самая частая осечка при установке. Docker в этом случае выдаёт
# простыню про endpoint и networking, из которой не видно ни порта, ни того, что
# делать. Проверяем заранее и говорим по-человечески.
if [ -z "$(docker ps -q --filter "name=$WEB")" ] \
   && (command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -qE "[:.]${PLAN_PORT}[[:space:]]"); then
  echo "Порт ${PLAN_PORT} уже занят другой программой."
  echo
  echo "Откройте .env, поставьте другой PLAN_PORT (например, $((PLAN_PORT + 1)))"
  echo "и запустите установку снова:  bash setup.sh"
  exit 1
fi

# ── Сеть и том ──────────────────────────────────────────────────────────────
#
# Своя сеть нужна, чтобы приложение находило базу по имени контейнера. Но на
# сервере, где Docker живёт давно, встроенный пул адресов бывает исчерпан:
# «all predefined address pools have been fully subnetted». Тогда Docker не может
# подобрать подсеть сам — её надо назвать явно. Перебираем кандидатов и отдаём
# проверку на пересечение самому Docker: он знает про занятые диапазоны точно,
# а разбирать их в скрипте значит написать вторую, менее надёжную проверку.
#
# Если в .env задан PLAN_NET, берём готовую сеть и ничего не создаём.
NET="${PLAN_NET:-$NET}"

if ! docker network inspect "$NET" >/dev/null 2>&1; then
  if [ -n "${PLAN_NET:-}" ]; then
    echo "Сеть «$NET» из .env не найдена. Уберите PLAN_NET или создайте сеть сами."
    exit 1
  fi
  created=""
  if docker network create "$NET" >/dev/null 2>&1; then
    created=1
  else
    for sub in 172.30.0.0/24 172.31.0.0/24 10.77.0.0/24 10.78.0.0/24 \
               192.168.240.0/24 192.168.241.0/24; do
      if docker network create --subnet "$sub" "$NET" >/dev/null 2>&1; then
        echo "Сеть создана с подсетью $sub."
        created=1
        break
      fi
    done
  fi
  if [ -z "$created" ]; then
    cat <<'EOF'
Не удалось создать сеть Docker: свободных подсетей не осталось
(«all predefined address pools have been fully subnetted»).

Что можно сделать — любое из трёх:

  1. Убрать неиспользуемые сети:      docker network prune
  2. Посмотреть, что занято:          docker network ls
  3. Использовать существующую сеть — допишите в .env строку
     PLAN_NET=имя_сети   и запустите установку снова.
EOF
    exit 1
  fi
fi

docker volume inspect "$VOL" >/dev/null 2>&1 || docker volume create "$VOL" >/dev/null

# ── База ────────────────────────────────────────────────────────────────────
# Порт наружу не выставляем: к базе ходит только приложение.
if docker ps -a --format '{{.Names}}' | grep -qx "$DB"; then
  docker start "$DB" >/dev/null 2>&1 || true
else
  echo "Поднимаю базу…"
  docker run -d --name "$DB" --network "$NET" --restart unless-stopped \
    -e POSTGRES_DB="$PG_DB" -e POSTGRES_USER="$PG_USER" \
    -e POSTGRES_PASSWORD="$PG_PASSWORD" \
    -v "$VOL":/var/lib/postgresql/data \
    --memory 512m \
    "$PG_IMAGE" >/dev/null
fi

echo -n "Жду базу"
for _ in $(seq 1 60); do
  if docker exec "$DB" pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; then
    echo " — готова."; break
  fi
  echo -n "."; sleep 1
done

# ── Приложение ──────────────────────────────────────────────────────────────
echo
echo "Собираю образ (в первый раз это несколько минут)…"
docker build -f server/Dockerfile -t "$IMAGE" . >/dev/null

# Пересоздаём: образ мог измениться. Данные это не трогает — они в томе базы.
docker rm -f "$WEB" >/dev/null 2>&1 || true
docker run -d --name "$WEB" --network "$NET" --restart unless-stopped \
  -e DJANGO_SECRET_KEY="$DJANGO_SECRET_KEY" \
  -e DJANGO_DEBUG="${DJANGO_DEBUG:-0}" \
  -e DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS:-*}" \
  -e PG_DB="$PG_DB" -e PG_USER="$PG_USER" -e PG_PASSWORD="$PG_PASSWORD" \
  -e PG_HOST="$DB" -e PG_PORT=5432 -e TZ="${TZ:-Asia/Tashkent}" \
  -p "${PLAN_PORT}:8000" \
  "$IMAGE" >/dev/null

# Контейнер сам ждёт базу и применяет миграции (server/entrypoint.sh).
echo -n "Жду приложение"
code=""
for _ in $(seq 1 60); do
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PLAN_PORT}/api/me" || true)"
  # 403 = сервер отвечает, просто мы не вошли. Это и есть признак готовности.
  case "$code" in
    200|401|403) echo " — готово."; break ;;
  esac
  echo -n "."; sleep 2
done

case "${code:-}" in
  200|401|403) : ;;
  *)
    echo
    echo "Приложение не ответило. Что случилось:"
    echo "  bash setup.sh --logs"
    exit 1 ;;
esac

cat <<EOF

════════════════════════════════════════════════════════════════
 Готово. База пустая — данных в ней ещё нет.

 Создайте администратора:

   docker exec -it $WEB python manage.py createsuperuser

 Откройте приложение:

   http://localhost:${PLAN_PORT}

 Включите «Серверный режим» на любой вкладке и войдите под
 созданной учётной записью.
════════════════════════════════════════════════════════════════
EOF
