#!/bin/bash
# Резервная копия базы «Плана» (Postgres в контейнере plan-pg) с ротацией.
#
#   /root/plan/db_backup.sh            # обычный запуск (из cron)
#   /root/plan/db_backup.sh --check    # проверить последнюю копию и выйти
#
# Почему отдельный скрипт, а не строка в backup.sh: тот копирует «План.html» и
# работает, даже когда базы нет вовсе. Смешивать их значит однажды потерять
# копию файла из-за упавшего дампа.
#
# ВОССТАНОВЛЕНИЕ (данные будут ЗАМЕЩЕНЫ):
#   gunzip -c backups/plandb_ГГГГММДД_ЧЧММСС.sql.gz | \
#     docker exec -i plan-pg psql -U planuser -d plandb
set -u

cd "$(dirname "$0")" || exit 1

DIR=${BACKUP_DIR:-$(pwd)/backups}
KEEP=${KEEP:-48}                  # почасовые копии за двое суток
DAILY_KEEP=${DAILY_KEEP:-30}      # плюс по одной на день за месяц

# Имя контейнера базы зависит от способа установки: через docker-compose это
# `plan-db`, у ранней ручной установки — `plan-pg`. Ищем то, что есть, вместо
# того чтобы держать два почти одинаковых скрипта.
CONT=""
for name in plan-db plan-pg; do
  if docker ps --format '{{.Names}}' | grep -qx "$name"; then CONT="$name"; break; fi
done

# Пароль и имена берём из .env установки, если он есть.
[ -f .env ] && . ./.env
DB=${PG_DB:-plandb}
USER=${PG_USER:-planuser}

mkdir -p "$DIR" || exit 1
stamp=$(date +%Y%m%d_%H%M%S)
out="$DIR/plandb_$stamp.sql.gz"

# ── Проверка последней копии ────────────────────────────────────────────────
if [ "${1:-}" = '--check' ]; then
  last=$(ls -1t "$DIR"/plandb_*.sql.gz 2>/dev/null | head -1)
  [ -n "$last" ] || { echo "копий нет"; exit 1; }
  age=$(( ($(date +%s) - $(stat -c %Y "$last")) / 60 ))
  size=$(stat -c %s "$last")
  # Дамп пустой базы всё равно весит килобайты, поэтому маленький файл — это
  # сбой, а не «данных мало».
  if ! gunzip -t "$last" 2>/dev/null; then echo "ПОВРЕЖДЕНА: $last"; exit 1; fi
  echo "последняя копия: $(basename "$last"), $((size/1024)) КБ, ${age} мин назад"
  [ "$size" -gt 10240 ] || { echo "ПОДОЗРИТЕЛЬНО МАЛА"; exit 1; }
  exit 0
fi

# ── Дамп ────────────────────────────────────────────────────────────────────
if [ -z "$CONT" ]; then
  echo "$(date '+%F %T') контейнер базы не запущен — копия не снята" >&2
  exit 1
fi

# Пишем во временный файл и переименовываем только при успехе: оборванный дамп
# под правильным именем выглядел бы как рабочая копия ровно до попытки
# восстановления.
tmp="$out.part"
if ! docker exec "$CONT" pg_dump -U "$USER" -d "$DB" --clean --if-exists \
     | gzip -9 > "$tmp"; then
  echo "$(date '+%F %T') pg_dump не удался" >&2
  rm -f "$tmp"
  exit 1
fi
# `pg_dump | gzip` не возвращает ошибку первой команды, поэтому проверяем размер.
if [ ! -s "$tmp" ] || [ "$(stat -c %s "$tmp")" -lt 10240 ]; then
  echo "$(date '+%F %T') дамп подозрительно мал — не принят" >&2
  rm -f "$tmp"
  exit 1
fi
mv "$tmp" "$out"

# ── Ротация ─────────────────────────────────────────────────────────────────
# Сначала оставляем KEEP свежих, затем из более старых — по одной на день.
ls -1t "$DIR"/plandb_*.sql.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r f; do
  day=$(basename "$f" | sed -E 's/plandb_([0-9]{8})_.*/\1/')
  # Самая свежая копия этого дня остаётся, остальные уходят.
  newest=$(ls -1t "$DIR"/plandb_"$day"_*.sql.gz 2>/dev/null | head -1)
  [ "$f" = "$newest" ] || rm -f "$f"
done
# Дневные копии старше DAILY_KEEP дней убираем совсем.
find "$DIR" -name 'plandb_*.sql.gz' -mtime +"$DAILY_KEEP" -delete

echo "$(date '+%F %T') копия: $(basename "$out"), $(( $(stat -c %s "$out") / 1024 )) КБ"
