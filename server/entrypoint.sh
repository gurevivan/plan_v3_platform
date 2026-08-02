#!/bin/sh
# Подготовка контейнера приложения перед запуском.
#
# Миграции выполняются здесь, а не руками после установки: забытая миграция
# означает пятисотые ошибки на ровном месте, причём у всех сразу.
set -e

echo "Жду базу ${PG_HOST:-db}:${PG_PORT:-5432}…"
until python -c "
import os, socket, sys
s = socket.socket()
s.settimeout(2)
try:
    s.connect((os.environ.get('PG_HOST', 'db'), int(os.environ.get('PG_PORT', 5432))))
except OSError:
    sys.exit(1)
" 2>/dev/null; do
  sleep 1
done

python manage.py migrate --noinput
python manage.py collectstatic --noinput >/dev/null 2>&1 || true

# `ensure_roles` здесь НЕ вызывается намеренно: после установки в базе не должно
# быть ни одной строки, включая группы ролей. Роль заводится сама в тот момент,
# когда администратор впервые её кому-то выдаёт (core/api/roles.py, group_for).
# Команда остаётся — она нужна, только если роли хотят видеть в админке Django
# заранее.

exec "$@"
