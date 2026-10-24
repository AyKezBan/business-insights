# Data Import

Чтение сырых CSV, нормализация и проверка данных, загрузка исходных и подготовленных записей в PostgreSQL. Требуются Python 3.11+, PostgreSQL 17+ и заранее созданная база.

Пути и команды указаны от корня репозитория `business-insights`. Команды приведены для Bash.

## Загрузка

Исходные CSV находятся в `data-import/source-data/`: `df_Uid_data_hack.csv`, `df_Fid_Uid_link_hack.csv` и `df_Uid_payment_hack.csv`. Загрузчик читает их без изменений; промежуточные CSV не создаются.

```bash
python -m venv data-import/.venv
. data-import/.venv/bin/activate
python -m pip install -r data-import/requirements.txt
export DATABASE_URL='postgresql://user:password@localhost:5432/business_insights'
python data-import/load_postgres.py --data-dir data-import/source-data
```

В DSN укажите свои параметры подключения. Пользователю нужны права на схему `bi_data`; базу и пользователя загрузчик не создаёт.

Повтор тех же файлов возвращает `unchanged`. Для нового набора нужен `--replace-current`: он заменяет содержимое `bi_data` в одной транзакции. При ошибке изменения откатываются. История и автоматические миграции не реализованы. Замену набора выполняйте вне активной работы с БД: она блокирует затронутые таблицы.

`data-import/data_core.py` читает и проверяет CSV, нормализует записи и формирует отчёт качества. `data-import/load_postgres.py` загружает их через COPY по схеме `data-import/sql/schema.sql`. Хеши файлов и отчёт сохраняются в `bi_data.dataset_metadata`.

## Тесты

```bash
python -m unittest discover -s data-import -t data-import -v
```

Шесть unit-тестов работают без PostgreSQL. Для интеграционного теста создайте отдельную одноразовую БД: тест заменяет её данные в `bi_data`.

```bash
export BI_TEST_DATABASE_URL='postgresql://user:password@localhost:5432/bi_data_test'
python -m unittest discover -s data-import -t data-import -p test_postgres.py -v
```

Без `BI_TEST_DATABASE_URL` интеграционный тест пропускается; `DATABASE_URL` для тестов не используется. Тест сверяет raw и типизированные записи с CSV, повторный импорт, отклонение неявной замены и откат при ошибке COPY.

Подробности: [правила данных](/data-import/docs/DATA_RULES.md), [схема БД](/data-import/docs/SCHEMA.md), [результаты проверки](/data-import/docs/VALIDATION.md).
