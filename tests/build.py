# -*- coding: utf-8 -*-
"""Собирает Node-харнесс из План.html (рецепт CLAUDE.md §6).

Что делает:
  1. извлекает единственный <script> из План.html;
  2. вырезает IIFE `populateModules()` — она ссылается на неопределённые функции
     (`renderReferenceTab` и пр.); в браузере это тихая ошибка в самом конце,
     в Node роняет загрузку;
  3. добавляет заглушку `migrateManualFrv` и экспорт в globalThis.__T.

Использование:  python3 build.py [путь_к_html]   (по умолчанию ../План.html)
Результат:      _build/harness.js
"""
import re
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
src_path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / 'План.html'

html = src_path.read_text(encoding='utf-8')
scripts = re.findall(r'<script>(.*?)</script>', html, re.S)
if not scripts:
    sys.exit(f'не найден <script> в {src_path}')
js = scripts[0]

lines = js.split('\n')
try:
    start = next(i for i, l in enumerate(lines) if l.startswith('(function populateModules()'))
    end = next(i for i, l in enumerate(lines[start:], start) if l.startswith('})();'))
    cut = end - start + 1
    del lines[start:end + 1]
except StopIteration:
    cut = 0  # IIFE не найдена — возможно, её убрали; это не ошибка
js = '\n'.join(lines)

PRELUDE = 'function migrateManualFrv(){}\n'
EXPORT = """
globalThis.__T = {
  get S() { return S; },
  set S(v) { S = v; },
  orderStagePlannedQty, orderCoveredBefore, validatePlanOrderLimit,
  recalcAllMicroPlans, recalcPlanRelease, releaseOrderEq, tcForMicroRow,
  // Объявлены через const, поэтому сами в контекст не попадают.
  get SRV() { return typeof SRV !== 'undefined' ? SRV : null; },
  get SRV_ENTITIES() { return typeof SRV_ENTITIES !== 'undefined' ? SRV_ENTITIES : null; },
  get SRV_TAB_KINDS() { return typeof SRV_TAB_KINDS !== 'undefined' ? SRV_TAB_KINDS : null; },
  get SRV_LOAD_ORDER() { return typeof SRV_LOAD_ORDER !== 'undefined' ? SRV_LOAD_ORDER : null; },
};
if (typeof SRV !== 'undefined') globalThis.SRV = SRV;
if (typeof SRV_ENTITIES !== 'undefined') globalThis.SRV_ENTITIES = SRV_ENTITIES;
if (typeof SRV_TAB_KINDS !== 'undefined') globalThis.SRV_TAB_KINDS = SRV_TAB_KINDS;
if (typeof SRV_LOAD_ORDER !== 'undefined') globalThis.SRV_LOAD_ORDER = SRV_LOAD_ORDER;
if (typeof SRV_TC_STAGES !== 'undefined') globalThis.SRV_TC_STAGES = SRV_TC_STAGES;
if (typeof SRV_TAB_SECTION !== 'undefined') globalThis.SRV_TAB_SECTION = SRV_TAB_SECTION;
"""

out_dir = ROOT / '_build'
out_dir.mkdir(exist_ok=True)
out = out_dir / 'harness.js'
out.write_text(PRELUDE + js + EXPORT, encoding='utf-8')
print(f'харнесс собран из {src_path.name}: {out} ({len(js)} байт скрипта, вырезано строк IIFE: {cut})')
