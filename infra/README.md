# Общий локальный стек

Root `compose.yaml` объединяет актуальный `develop` (e105b7c) и frontend из PR #6 (7c59dd0). PR #2–5 уже merged на 26.10.2026. Frontend добавлен локальным merge в ветку DevOps; shared branches/PR не менялись. В финализации D03/D04 дополнительно объединена `feature/ml-finalize` (416f24f). Исторический smoke DevOps PR #7 использовал прежний ML baseline; полный объединённый выпуск проверяется отдельно по финальному manifest. Ранее существовавший `compose.ml-materials.yml` оставлен для самостоятельного ML-сценария.

## Запуск и данные

Docker Compose 2.24.4+, доступный Docker daemon; Python 3 для wrapper init/smoke. Сборки загружают базовые образы, Maven и PyPI-зависимости. Из корня:

```sh
./stack init
# Отредактируйте .env: DATA_DIR=/абсолютный/путь/к/csv
./stack config
./stack up
```

`init` не перезаписывает существующий `.env`, создаёт случайные PostgreSQL password и backend bearer, права 0600. Шаблон [.env.example](../.env.example) не содержит секретов. Токен должен быть длиной ≥20 из букв/цифр/`_`/`-`; wrapper генерирует совместимое значение. Не публикуйте вывод обычного `docker compose config`: он содержит раскрытые настройки; `./stack config` выполняет только валидацию.

CSV-каталог должен содержать `df_Uid_data_hack.csv`, `df_Fid_Uid_link_hack.csv`, `df_Uid_payment_hack.csv`. Bind mount read-only, отсутствующий каталог не создаётся автоматически. `BACKEND_DEMO_PERSON_ID` должен существовать в связях ФЛ–ЮЛ; по умолчанию F_1. Исходные CSV/PDF не копируются в образы. Контексты сборки ограничены `.dockerignore` и явными COPY.

Порядок запуска: здоровый PostgreSQL → успешный импорт → backend с миграциями Flyway; ML должен отвечать на health до старта backend; frontend открывается после успешного авторизованного чтения компаний из БД. Зависимости используют [условия готовности Compose](https://docs.docker.com/compose/how-tos/startup-order/).

## Адреса и доступ

| Компонент | Внутри сети | Снаружи |
|---|---|---|
| PostgreSQL 17 | postgres:5432 | Не опубликован |
| Java API | backend:8080 | `/api/v1/*` через frontend |
| ML | ml:8000 | Только `/v1/materials/documents/*` через frontend |
| nginx + UI | frontend:80 | http://127.0.0.1:8088 |

`WEB_PORT` меняет локальный порт. Nginx автоматически добавляет демонстрационный bearer; браузер не получает секрет. Это **локальное однопользовательское демо**, любой локальный пользователь имеет доступ через gateway. Это не реализация банковской авторизации. Не публикуйте порт на 0.0.0.0 и не используйте этот режим как production. Запросы из постороннего Origin отклоняются, отдельный CORS не нужен. Proxy повторно разрешает имена сервисов после пересоздания контейнеров.

`/health` Java проверяет только процесс; Compose проверяет реальное чтение БД. `/health` ML сообщает количество материалов, но не проверяет доступность провайдера, ключ или качество модели. Пустой ключ не включает fake: попытка генерации завершится ограниченным результатом/ошибкой. Проверьте фактический статус операции. Default `LLM_BASE_URL` взят из существующего ML env example; доступность провайдера этим изменением не подтверждается.

## События и анализ

UI читает сохранённую ленту и не запускает анализ автоматически. После первого импорта она пуста. Загрузите подготовленное внешнее событие согласно [OpenAPI](../backend-api/src/main/resources/static/openapi.yaml), затем запросите анализ (это расходует токены, если настроен ключ):

```sh
curl --fail-with-body http://127.0.0.1:8088/api/v1/companies
curl --fail-with-body -H 'Content-Type: application/json' \
  --data-binary @/путь/к/event.json http://127.0.0.1:8088/api/v1/events
curl --fail-with-body -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: analysis-unique-request-1' \
  --data '{"companyId":"U_1","role":"cfo","eventId":"your-event-id","userProvided":{}}' \
  http://127.0.0.1:8088/api/v1/analyses
```

Ответ 202 содержит `Location` для опроса операции. Завершённые SUCCEEDED/LIMITED результаты отображаются для выбранной компании и роли. Клиентский адаптер реализует семь bindings текущего frontend и сохраняет ссылки, типы утверждений и ограничения; прямого frontend→ML вызова нет. Учебный `frontend/server.py` остаётся самостоятельным demo, но общий стек его не запускает.

## Повторный запуск, импорт и остановка

```sh
./stack up                   # существующие volumes сохраняются
./stack import               # тот же dataset → unchanged
./stack down                 # остановить, оставить данные
./stack logs import-data
```

Изменившийся набор importer отклоняет транзакционно; автоматического `--replace-current` нет. Для другого набора используйте новый `COMPOSE_PROJECT_NAME` и другой `WEB_PORT` либо отдельно согласованную процедуру замены. Named volumes `${project}_postgres-data` и `${project}_ml-traces` принадлежат Compose project. `down --volumes` необратимо удалит их — это не штатная остановка. Смена PostgreSQL password в `.env` не меняет пароль в уже созданной БД: сохраняйте первоначальное значение или меняйте пароль средствами БД.

## Банковский корпус и новости

Необязательный overlay `infra/compose.materials.yaml` включает `MATERIALS_DIR`. Укажите существующий `MATERIALS_STORE` с проверенным `current.json`; повреждённый/отсутствующий snapshot остановит ML, без подмены fixtures. Корпус смонтирован read-only в serving-контейнер.

```sh
# В .env: MATERIALS_STORE, PDF_DIR, MATERIALS_REVIEW_DIR — абсолютные пути.
# Для нового корпуса создайте только свой каталог назначения и review-каталог.
./stack materials bank --store /app/materials --pdf-dir /inputs \
  --manifest /config/bank-documents.json --review-file /review/bank-review.json
./stack materials news --store /app/materials --source cbr --limit 10
./stack materials check --store /app/materials
./stack materials-up
```

Jobs используют существующую CLI без изменений D03/D04. PDF и review — read-only, job пишет только выбранный store, работает под UID/GID вызывающего пользователя. На Linux каталоги должны быть доступны этому пользователю и ML uid1000 для чтения. Bank без достаточного review может завершиться кодом 2 и оставить предыдущую версию — не обходите проверку. Подробности: [material-ingestion.md](../ml-service/docs/material-ingestion.md).

После новой публикации snapshot выполните `docker compose -f compose.yaml -f infra/compose.materials.yaml restart ml`: ML закрепляет ревизию при старте. Для дальнейших `up` этого окружения используйте `materials-up`, чтобы не снять подключение корпуса. `MATERIALS_PUBLIC_BASE_URL` по умолчанию `http://127.0.0.1:${WEB_PORT}`; Java принимает только абсолютные http(s) ссылки. Пустое значение заменяется этим default. Произвольные ML generation/trace endpoints наружу не проксируются.

## Регулярное обновление материалов (необязательно)

После bootstrap корпуса, готовности `sync` из T05 и runtime/catalog T07/T09 явно запустите `./stack materials-watch`. Команда собирает и запускает только `materials-poller` из профиля `jobs`; `materials-up` сохраняет прежний ручной режим. Poller выполняет существующую команду `python -m insight_agent.materials.cli sync --sources cbr_press,fns_news,eia_today --limit-per-source 5 --store /app/materials` сразу, затем через 900 секунд после завершения предыдущей попытки. Он не вызывает LLM и не отправляет события в backend сам.

Коды 1/2 и неизвестные ошибки увеличивают паузу до 1800/3600 секунд, код 0 сбрасывает её; частичный результат 3 остаётся виден и даёт обычную паузу 900 секунд. Только код 0 сбрасывает накопленный backoff. `MATERIALS_POLL_INTERVAL_SECONDS` меняет обычную паузу (положительная, не более 3600), `MATERIALS_POLL_TIMEOUT_SECONDS` — предел одной попытки (по умолчанию 600 секунд). После timeout дочерняя CLI получает terminate и до 10 секунд на остановку, затем kill. SIGTERM/SIGINT прерывают ожидание и останавливают только собственную CLI; новая попытка после stop не начинается.

Poller пишет только в `MATERIALS_STORE`, под UID/GID пользователя; ключи LLM, credentials БД и admin token ему не передаются, дополнительные порты не открываются. Постоянный `.poller.lock` не допускает двух poller над одним store на файловой системе с POSIX flock. Общий `.writer.lock` CLI по-прежнему защищает публикации вместе с ручными jobs. Файл lock не удаляйте во время работы. `poller-status.json` атомарно хранит статус последней попытки, exit code, длительность и следующую паузу; подробные source reports принадлежат CLI. Её metadata output и статус poller доступны через `./stack logs materials-poller`; `./stack status` показывает его состояние. `./stack down` останавливает также poller и сохраняет volumes/store; `down -v` удаляет named volumes как прежде.

Проверки supervisor выполняются без сети и LLM. На исходном DevOps baseline команда `sync` ещё отсутствует: запуск watcher требует интеграции T05; offline тесты poller не являются проверкой реальных publisher/runtime/Java/browser gates. Автоматический restart supervisor отключён: ошибка конфигурации или занятый `.poller.lock` требует исправления и явного повторного запуска.

## Проверки и ограничения

```sh
./stack smoke
```

Smoke собирает образы, создаёт случайный `bi-smoke-*` project и синтетические CSV, запускает настоящий PostgreSQL/импорт/Flyway/Java/ML pipeline/nginx. Только LLM-клиент явно scripted, материалы синтетические; production image этих тестов не содержит. Проверяет HTTP-путь, async/idempotency, вопросы, источники, состояния, feedback, traces, повторный импорт, отказ замены изменённых CSV и сохранность результатов после рестарта. Все тестовые volumes удаляются в finally, отчёт остаётся в `.local/smoke-*/report.json`. Рабочий `.env` и реальные CSV не читаются.

Ограничения существующих сервисов:

- В текущем develop бюджет ML и контекст вопросов находятся в памяти. Рестарт ML теряет контекст Q&A и счётчик дневного бюджета; traces сохраняются. Не масштабируйте сервис и не считайте это надёжным общим лимитом расходов.
- Backend имеет одного владельца demo и одну локальную очередь. Незавершённые операции после рестарта становятся FAILED; автоматический повтор может повторно расходовать токены и не выполняется.
- Backend принимает события API, но автоматического моста публикации material-news → backend events нет. Подготовка корпуса сама не наполняет ленту.
- Backend сохраняет feedback, но read DTO не возвращает rating/comment; UI восстанавливает hidden после перезагрузки, feedback пока виден только в текущей сессии.
- Этот стек проверяет совместимость интегрированного develop. Новые ML/frontend контракты требуют отдельной повторной проверки.

Фактические результаты: [VERIFICATION.md](VERIFICATION.md).
