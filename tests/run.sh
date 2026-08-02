#!/bin/bash
# Прогон всех проверок расчётного ядра «План V3».
# Использование:  ./run.sh [путь_к_html]    (по умолчанию ../План.html)
# Проверить старую версию:  ./run.sh ../backups/План_20260731_190001.html
cd "$(dirname "$0")" || exit 1

HTML="${1:-../План.html}"

echo "═══ Сборка харнесса ═══"
python3 build.py "$HTML" || exit 1

echo
echo "═══ Синтаксис ═══"
node --check _build/harness.js && echo "OK"

fail=0
for t in test_*.js; do
  echo
  echo "═══ $t ═══"
  node "$t" || fail=1
done

echo
if [ $fail -eq 0 ]; then
  echo "ИТОГ: все тесты зелёные"
else
  echo "ИТОГ: есть падения"
fi
exit $fail
