# Контракт версии 2.0

Обновлён 22 октября 2026 года. UTF-8 JSON. Это контракт учебного стенда, не утверждённый банковский API.

## Bootstrap и профиль

GET /api/bootstrap возвращает schema_version=2.0, notice, companies, roles, views и profile:

~~~json
{"name":"Алексей","company_id":"retail","role":"cfo"}
~~~

Frontend использует сохранённый валидный профиль браузера, иначе — этот демопрофиль. Из URL нельзя изменить компанию и роль. Настоящий backend впоследствии должен получать пользователя и доступные компании из сессии, а не доверять localStorage.

Форма входа не вызывает API и не сохраняет credentials. Endpoint авторизации не реализован. Сохранение профиля, скрытых событий и оценок пока локальное; при интеграции нужны серверные методы и привязка к авторизованному пользователю.

## Чтение

| Запрос | Параметры | Ответ |
|---|---|---|
| GET /api/feed | company_id, role из профиля | context, items, as_of |
| GET /api/insight | company_id, role, event_id, view | context, insight_id, title, status, blocks, sources, missing_data, disclaimer |
| GET /sources/{id} | ID из ответа | HTML полного синтетического источника |

views: overview, consequences, risks, recommendations — в указанном порядке. insight_id имеет версию v2, например retail:rate:v2. Старая карточка v1 вызывает 409 при уточнении.

## Структура карточки

Общий обзор возвращает fact, open, bank, ai. В остальных разрезах fact отсутствует, а open/bank/ai содержат по одному подразделу выбранного типа. Текст подраздела совпадает с соответствующим текстом в полном обзоре.

~~~json
{
  "kind": "open",
  "synthetic": true,
  "source_ids": ["news-rate"],
  "sections": [
    {
      "kind": "consequences",
      "text": "Содержание последствий",
      "source_ids": ["news-rate"],
      "certainty": "inference"
    },
    {
      "kind": "risks",
      "text": "Содержание рисков",
      "source_ids": ["news-rate"],
      "certainty": "hypothesis"
    },
    {
      "kind": "recommendations",
      "text": "Содержание рекомендаций",
      "source_ids": ["news-rate"],
      "certainty": "recommendation"
    }
  ]
}
~~~

Метки certainty: fact — факт; inference — вывод модели по источникам; hypothesis — гипотеза; assessment — оценка в аналитическом материале; recommendation — следующий шаг; unavailable — данных нет. Это тип содержания, а не численная вероятность. При отсутствии банковского материала source_ids пуст, а каждый раздел банка явно сообщает об отсутствии данных.

Источники: id, title, date, page. UI строит ссылки только по серверному ID и выводит текст без HTML. disclaimer: «Аналитика не является инвестиционной рекомендацией».

## Уточнение

POST /api/clarifications, Content-Type: application/json, X-Demo-Client: d09-d10. Тело до 8192 байт; вопрос 1–1000 символов.

~~~json
{
  "company_id":"retail",
  "role":"cfo",
  "event_id":"rate",
  "view":"risks",
  "insight_id":"retail:rate:v2",
  "question":"Какие рекомендации?",
  "request_id":"frontend-uuid"
}
~~~

Ответ: context, insight_id, request_id, answer_id, status, text, sources, missing_data. company_id и role берутся из профиля, event_id и view — из маршрута. Frontend отменяет прежний запрос и проверяет epoch, request_id, insight_id, context перед отображением.

Режим ограничен подготовленными ответами по ключевым словам. Вопросы о рекомендациях, последствиях, рисках, основаниях и значимости поддерживают демонстрацию. Неподдерживаемый вопрос или отсутствие исходных данных дают no_answer. Это не универсальный диалог и не обработка D11.

## Ошибки

Форма ошибки: {"error":{"message":"Понятное сообщение"}}. 400 — неверные поля, 403 — origin/заголовок стенда, 404 — нет карточки/источника, 409 — конфликт версии, 413/415 — размер/тип тела. Frontend timeout — 15 секунд. При интеграции нужны согласованные 401/403 для сессии и прав, 429/5xx, а также job API для долгих операций.
