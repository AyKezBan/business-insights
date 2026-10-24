# Data Import

Нормализация трёх CSV и атомарная загрузка в PostgreSQL. Сборка ML-профиля относится к Java-бэкенду.

## Запуск

Нужны Python 3.11+, PostgreSQL 17+, созданная база и права на схему `business_insights`. Поместите в `data-import/source-data/` файлы UTF-8 с разделителем `;`:

- `df_Uid_data_hack.csv` — компании;
- `df_Fid_Uid_link_hack.csv` — связи физлиц и компаний;
- `df_Uid_payment_hack.csv` — платежи.

PowerShell, из корня репозитория:

```powershell
python -m venv data-import/.venv
data-import/.venv/Scripts/python.exe -m pip install -r data-import/requirements.txt
$env:DATABASE_URL = 'postgresql://user:password@localhost:5432/business_insights'
data-import/.venv/Scripts/python.exe data-import/load_postgres.py --data-dir data-import/source-data
```

Повтор того же набора возвращает `unchanged`. Для замены набора или перехода с версии `0.2.0` на `0.3.0` добавьте `--replace-current`: все данные схемы заменяются в одной транзакции. Другие версии отвергаются. Ошибка откатывает изменения; история наборов не хранится.

## Данные в БД

Загрузчик выполняет [создание схемы](sql/01_schemas.sql), [таблиц](sql/02_tables.sql) и [индексов](sql/03_indexes.sql).

[Все поля и Mermaid-диаграмма БД](docs/SCHEMA.md).

| Таблица | Содержимое |
| --- | --- |
| `dataset_metadata` | Хеши файлов, отчёт качества, версия и период данных |
| `company_ref` | ID компаний и доступность записи: available / ambiguous / external |
| `company_profile` | Нормализованные атрибуты компании и признаки качества |
| `person_ref` | ID физических лиц |
| `person_company_link` | Связи ФЛ–ЮЛ и география обслуживания |
| `payment_snapshot` | Все платежи, пригодность для расчётов и причины исключения |

Исходные CSV не меняются и не копируются в БД. Сохраняйте их: SHA-256 и номера записей позволяют восстановить происхождение данных. Правила нормализации — в [DATA_RULES.md](docs/DATA_RULES.md).

Тесты находятся в `tests/`. Интеграционный тест заменяет данные схемы и требует отдельную одноразовую БД через `BI_TEST_DATABASE_URL`.
