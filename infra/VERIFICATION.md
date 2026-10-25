# Проверка общего стека — 26.10.2026

Исходная интеграция: `origin/develop=e105b7c`; frontend PR #6 `7c59dd0`, локальный merge `61632ae`. Разработка выполнена в отдельном worktree/ветке `feature/full-stack-compose`. Исходный checkout `business-insights` остался detached на `cb28d5f`; его untracked `docs/superpowers` не изменены. D03/D04 orchestration и чужой materials worktree не редактировались.

Окружение: macOS arm64, Docker Engine 28.0.1, Compose 2.33.1. Docker Desktop был запущен для проверки. Проверены Linux arm64 images; amd64 отдельно не запускался.

## Фактические результаты

| Проверка | Результат |
|---|---|
| Root Compose + materials overlay + smoke overlay | `config --quiet` прошёл |
| frontend/nginx, Java21 backend, importer, ML images | Все 4 собраны |
| Java Maven package | BUILD SUCCESS; Java unit-тестов в текущем проекте нет |
| Полный synthetic smoke | Два успешных одноразовых прогона |
| PostgreSQL → импорт → Flyway → backend → ML → gateway | Пройдено |
| Async analysis, Idempotency-Key, профиль, лента, вопросы | Пройдено |
| Источники, hidden, feedback, actions, traces | Пройдено |
| Auth | Прямой backend без bearer 401; gateway использует локальный demo bearer |
| Nginx | CSP/nosniff/referrer/cache headers, Host/Origin отказ, отсутствие demo API fallback |
| Идентичный CSV | `unchanged` |
| Изменённый CSV | Отклонён, без `--replace-current` |
| Рестарт PostgreSQL/backend/ML | Полные записи operation/event/state/feedback/actions (включая result/trace) совпали по контрольным суммам; proxy доступен |
| Materials job | Synthetic saved news импортирована, повтор не изменил revision; check прошёл |
| Production ML + подготовленный synthetic snapshot | Успешная загрузка read-only корпуса без вызова модели |
| Cleanup | Контейнеры, сеть и volumes обоих `bi-smoke-*` удалены; чужие контейнеры/volumes сохранены |
| Frontend | 20 Python API tests + 3 Node suites прошли |
| ML | 115 pytest tests прошли |
| data-import | 6 unit tests прошли; отдельный legacy PG test skipped без BI_TEST_DATABASE_URL; importer реально проверен full-stack smoke |
| Wrapper | `init` создаёт секреты 0600 и не перезаписывает `.env`; shell syntax/config прошли |

Последний прогон: project `bi-smoke-e6b5394f5c`, локальный отчёт `.local/smoke-92_m49st/report.json`, подробные команды `.local/smoke-92_m49st/commands.log`. Его временный HTTP endpoint уже выключен. Production image frontend содержит ровно проверенные текущие `integration.js` и `backend-bindings.js` — SHA256 сверены после сборки.

Все smoke CSV, события, банковские фрагменты и LLM-ответы **синтетические**. Вызывался реальный код ML pipeline с `ScriptedClient`; внешний LLM не вызывался, расход токенов провайдера — 0. Проверка подтверждает интеграцию, а не качество/доступность live-модели, свежесть реальных новостей или пригодность PDF-корпуса. Реальные CSV/PDF не импортировались и не менялись в этой проверке.

## Исправления, подтверждённые проверками

- Nginx location-level Cache-Control подавлял наследование security headers. HTTP-проверка сначала упала на CSP; перенос Cache-Control на server-level дал успешный повтор.
- Gateway ограничен localhost/127.0.0.1 (и внутренним frontend), чужой Host/Origin отвергается до добавления bearer.
- Java требует абсолютные URL банковских PDF: overlay подставляет адрес локального gateway с WEB_PORT.
- Возобновление долгого вопроса использовало уже отменённый сигнал для загрузки ответа. Регрессия воспроизвела AbortError; исправление переносит новый сигнал и не повторяет платный POST.

Независимые reviews выполнены для инфраструктуры и всего изменения. Остаточные ограничения сервисов и feedback-read API перечислены в [README](README.md).

Нефатальные существующие предупреждения: Starlette сообщает deprecation использования httpx в TestClient; Python 3.14 тесты frontend предупреждают о cleanup ожидаемых HTTPError. Тесты проходят. Первые запускные команды unittest из корня требовали PYTHONPATH; исправлено окружение запуска, код сервисов для этого не менялся.
