# -*- coding: utf-8 -*-
"""Справочник баз получает общие поля: created_at, updated_at, version.

Без `version` оптимистичная блокировка на этой сущности не работала бы, и правка
вслепую затирала бы чужую — а именно это свойство мы проверяем тестами на всех
остальных сущностях.

Миграция написана вручную: `makemigrations` не умеет добавлять поле с
`auto_now_add=True` без значения для уже существующих строк. Здесь берётся
`timezone.now` — таблица на момент миграции пуста, так что затронуть нечего.
"""
from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_contract_contract_article_items_contract_deadlines_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='deliverybase',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='deliverybase',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name='deliverybase',
            name='version',
            field=models.PositiveIntegerField(
                default=1, verbose_name='версия для оптимистичной блокировки'),
        ),
        migrations.AlterField(
            model_name='deliverybase',
            name='src_id',
            field=models.BigIntegerField(blank=True, db_index=True, null=True,
                                         verbose_name='id в исходном JSON'),
        ),
        migrations.AlterField(
            model_name='deliverybase',
            name='extra',
            field=models.JSONField(
                blank=True, default=dict,
                verbose_name='несмоделированные поля исходной записи'),
        ),
    ]
