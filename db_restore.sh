#!/bin/bash
# Восстановление базы «Плана» из резервной копии.
#
#   ./db_restore.sh                       # из самой свежей копии
#   ./db_restore.sh backups/plandb_….gz   # из конкретной
#   ./db_restore.sh --list                # что вообще есть
#
# ВНИМАНИЕ: содержимое базы ЗАМЕЩАЕТСЯ. Всё, что введено после снятия копии,
# будет потеряно — поэтому спрашиваем подтверждение и сначала снимаем копию
# текущего состояния.
set -u

cd "$(dirname "$0")" || exit 1
DIR=${BACKUP_DIR:-$(pwd)/backups}
[ -f .env ] && . ./.env
DB=${PG_DB:-plandb}
USER=${PG_USER:-planuser}

CONT=""
for name in plan-db plan-pg; do
  if docker ps --format '{{.Names}}' | grep -qx "$name"; then CONT="$name"; break; fi
done

if [ "${1:-}" = '--list' ]; then
  ls -lht "$DIR"/plandb_*.sql.gz 2>/dev/null | head -20 || echo "копий нет"
  exit 0
fi

if [ -z "$CONT" ]; then
  echo "Контейнер базы не запущен. Сначала: docker compose up -d"
  exit 1
fi

src=${1:-$(ls -1t "$DIR"/plandb_*.sql.gz 2>/dev/null | head -1)}
if [ -z "$src" ] || [ ! -f "$src" ]; then
  echo "Копия не найдена. Что есть: ./db_restore.sh --list"
  exit 1
fi

if ! gunzip -t "$src" 2>/dev/null; then
  echo "Файл повреждён: $src"
  exit 1
fi

echo "Восстановить базу «$DB» из $(basename "$src")?"
echo "ТЕКУЩИЕ ДАННЫЕ БУДУТ ЗАМЕЩЕНЫ."
printf 'Введите «да» для продолжения: '
read -r answer
[ "$answer" = "да" ] || { echo "Отменено."; exit 1; }

# Копия «на всякий случай»: восстановление из ошибочного файла иначе не отменить.
echo "Сначала сохраняю текущее состояние…"
if ! ./db_backup.sh; then
  echo "Не удалось снять копию текущего состояния — восстановление отменено."
  exit 1
fi

echo "Восстанавливаю…"
if gunzip -c "$src" | docker exec -i "$CONT" psql -U "$USER" -d "$DB" -q >/dev/null; then
  echo "Готово. Проверьте приложение и, если нужно, перезапустите: docker compose restart web"
else
  echo "Восстановление завершилось с ошибкой. Данные могли остаться в промежуточном"
  echo "состоянии — свежая копия прежних данных лежит в $DIR"
  exit 1
fi
