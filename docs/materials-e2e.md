# Проверка материалов, очереди и пользовательского сценария

Текущий статус: offline Java/JSONB/frontend проверки выполнены; публикация всего сценария и
смысловая приёмка требуют общего runtime и отдельного browser/live evidence. Synthetic transport
не подтверждает смысловую корректность реального LLM. Paid live session проводит C0 после budget gate.

## Изоляция и воспроизведение проверок

Работать в отдельном worktree. Для ML обязательно задать `PYTHONPATH` на `ml-service/src`
именно проверяемого дерева, если используется venv из другого worktree. Не менять shared venv.

```sh
# Из ml-service; PYTHON — существующий Python с exact lock dependencies.
PYTHONPATH="$PWD/src" "$PYTHON" -m pytest -q

# Из корня, JDK21 и Maven3.9+.
mvn -B -ntp -f backend-api/pom.xml test

# Node suites запускаются из корня; python должен быть доступен в PATH.
node frontend/tests/test_frontend.cjs
node frontend/tests/test_integration.cjs
node frontend/tests/test_backend_bindings.cjs
node frontend/tests/test_material_catalog.cjs

# Из frontend.
python -m unittest discover -s tests -p 'test_*.py'
```

Для Java DB-тестов `BI_JDBC_TEST_URL` указывает на `/postgres` собственного локального
PostgreSQL17+; `BI_JDBC_TEST_USER` и `BI_JDBC_TEST_PASSWORD` — его тестовые credentials.
Harness создаёт случайные `bi_c3_test_*` databases, применяет Flyway и удаляет только созданные
им databases. Внешние/non-loopback URL и URL рабочей БД не принимаются. Без URL DB cases
явно SKIPPED/NOT_RUN, поэтому общий зелёный Maven без DB не закрывает G07.

Проверяемые инварианты:

- Полный SourceRef, claim IDs/arrays, conflicts, outcome/error и profile uncertainty сохраняются
  в Java DTO, JSONB и API; schema parity проверяется против выбранного ML commit.
- Legacy POST `/api/v1/events` остаётся immutable. Trusted publication сравнивает полный
  `CatalogToken(store_id,generation,revision)`; semantic event hash не заменяет token.
- Bank-only revision продвигает head. Старый token, другой store и изменение payload под тем же
  token отвергаются. Конкурентная legacy-вставка не перезаписывается catalog UPSERT.
- Event/profile/role/cutoff/request/corpus/hash сохраняются до очереди одной операцией.
  Worker не перечитывает текущие event/profile. Worker запускается только после commit.
- Одинаковые параллельные submissions с одним key возвращают один operation ID и делают один
  ML-вызов. Изменённый явный контекст с тем же key получает 409.
- QA использует сохранённый профиль и контекст parent. Ответ другой company/event/revision/cutoff
  не публикуется. Feedback/opened/hidden сохраняются после реконструкции сервиса.

## Разделение идентификаторов и исходов

`insightId` публичного Java API — UUID operation. `eventId`, `sourceId`, `mlInsightId` и `runId`
сохраняются как opaque strings. Source ID нельзя разбирать по двоеточию. Client Idempotency-Key
и ML `requestId` — разные идентификаторы, их нельзя считать равными.

UI читает ML outcome отдельно от состояния операции: full/limited/refused/none. Refused/no-result
не показывают проверяемый черновик как готовую аналитику. Источники сохраняют дату/точность,
excerpt, document/hash, источник часового пояса, горизонт оценки и attribution перевода.
PDF href должен вести через доступный браузеру absolute URL с `?revision=...#page=N`.

`asOf` — publication cutoff, а не воспроизведение сведений, известных рынку в прошлом.
Поправленная позднее публикация со старой датой не делает запрос полноценным point-in-time replay.
Для воспроизведения принятой операции используется её сохранённая immutable corpus revision.

## Каталог доступных материалов

`GET /api/v1/materials/events?limit=50&offset=0` использует существующий gateway и авторизацию
Java. Сервер получает все страницы текущего ML-каталога, закрепляя последующие запросы за
полным token первой страницы, и только затем сохраняет подборку одной транзакцией с CAS.
Неполный ответ, повтор события/курсора, смена token или устаревший CAS не публикуют частичную
подборку. Ограничения загрузки: 100 страниц по 100 материалов и 16 MiB сериализованных событий.
Превышение возвращает ошибку; каталог не обрезается молча. Получение каталога не запускает LLM.

Ответ содержит `catalogToken`, `items`, `limit`, `offset`, `total`. Для последующих страниц UI
передаёт `revision` первой страницы. Если текущая публикация изменилась, API возвращает
`409 CATALOG_CHANGED`. Пользователь обновляет список и выбирает материал заново.

Явный `POST /api/v1/analyses` отправляет выбранные `eventId`, `corpusRevision` и
`eventContentSha256` вместе с компанией, ролью и новым request ID. Backend сверяет этот контекст
и сохраняет его до очереди. Каталог и лента подготовленных инсайтов остаются отдельными ресурсами.
Историческое чтение ML-каталога не используется для продвижения Java head.

## Browser/release evidence

До отметки G08 PASS выполнить на общем disposable stack (реальный Java/PostgreSQL/gateway):

1. Новый материал каждого publisher → выбранные company/role → явно отправленный analysis;
   available material catalog и prepared insights показаны раздельно.
2. Зафиксировать operation/ML run/insight/request/corpus IDs; 202 polling не повторяет POST.
3. Открыть original article и PDF, проверить физическую страницу и revision после refresh.
4. Сопоставить claim ID/excerpt/hash в карточке и trace; проверить bank/AI разделение.
5. QA старого результата после refresh остаётся на старой revision либо явно сообщает об отсутствии
   контекста. Wrong tenant/company и transport errors не подменяются demo-данными.
6. Сохранить feedback/hidden/opened, перезапустить сервис, перезагрузить браузер и проверить сохранность.
7. Сохранить screenshots, href/navigation results и идентификаторы в release report вне Git, если
   они содержат реальные материалы или профили. Не переносить raw PDF/OCR/full articles/keys в Git.

URL unit assertion не заменяет browser navigation. Все не выполненные шаги остаются NOT_RUN,
а отказ внешнего сайта фиксируется с конкретным шагом и владельцем.
