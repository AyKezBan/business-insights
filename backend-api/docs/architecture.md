# Архитектура бэкенда

Диаграммы описывают текущую реализацию Java-бэкенда. Форматы запросов и ответов — в [OpenAPI](../src/main/resources/static/openapi.yaml), запуск — в [README](../README.md).

## 1. Компоненты и границы ответственности

```mermaid
flowchart TB
    User["Пользователь / клиент API"]
    Events["Подготовленные внешние события"]
    Import["data-import: нормализация CSV и загрузка"]

    subgraph Java["Java backend: Spring Boot"]
        Auth["DemoAuthFilter: bearer → personId"]
        API["BackendController: публичные DTO"]
        Service["InsightService: доступ, сценарии, очередь"]
        Data["CompanyProfileService + CompanyDataRepository"]
        Store["OperationStore: события, операции, результаты"]
        Client["HttpMlClient: ML DTO и проверка контрактов"]
        Mapper["InsightMapper: представление результата"]
        Auth --> API
        API --> Service
        Service --> Data
        Service --> Store
        Service --> Client
        Service --> Mapper
    end

    subgraph PostgreSQL["PostgreSQL"]
        Imported[("business_insights: нормализованный набор")]
        App[("backend_api: состояние приложения")]
    end

    ML["Внешний ML-сервис"]
    Library["Библиотека новостей и аналитики банка"]

    User -->|"HTTP / JSON"| Auth
    Events -->|"POST /api/v1/events"| Auth
    Import -->|"запись набора"| Imported
    Data -->|"только чтение / JDBC"| Imported
    Store -->|"чтение и запись / JDBC"| App
    Client -->|"HTTP: новости корпуса, анализ, вопросы, трассы"| ML
    Library --> ML
```

Java отвечает за чтение БД, расчёт признаков, сборку профиля и сериализацию ML-запроса. Flyway управляет только схемой `backend_api`. Сбор внешних новостей и наполнение банковской библиотеки находятся за границей этого сервиса. Java принимает готовые события и читает загруженный корпус ML при явном обновлении ленты, выполняет отбор и ставит анализ в очередь. [Правила процесса и версий](feed-refresh.md).

Bearer моделирует авторизованного пользователя. Проверка связи ФЛ–ЮЛ и владельца операции ограничивает доступ, но не удостоверяет реальные полномочия. Роль поступает явно в запросе; источники входного события маркируются как внешние новости, а не как аналитика банка.

## 2. PostgreSQL → CompanyProfile → ML

```mermaid
flowchart TB
    DB[("business_insights: metadata, компании, связи, платежи")]

    subgraph Java["Сборка профиля на Java"]
        Read["Согласованное чтение: REPEATABLE READ + ACCESS SHARE"]
        Validate["Проверка версии набора, доступности профиля и CLIENT"]
        Assemble["Атрибуты компании и доступной группы; расчёт платежных признаков"]
        Profile["CompanyProfile: ML DTO"]
        Evidence["Происхождение и качество: исходные поля, строки, исключения, формулы"]
        Snapshot["Сохранение входного снимка и события"]
        Enough{"Есть пригодные бизнес-признаки?"}
        Request["InsightRequest: profile + event + role"]
        Limited["INSUFFICIENT_DATA: без вызова ML"]
        Read --> Validate --> Assemble
        Assemble --> Profile
        Assemble --> Evidence
        Profile --> Snapshot
        Evidence --> Snapshot
        Snapshot --> Enough
        Enough -->|"нет"| Limited
        Enough -->|"да"| Request
        Profile --> Request
    end

    External["Событие и роль из API"]
    Provided["Необязательные сведения пользователя из API"]
    Stored[("backend_api.operation.profile_snapshot")]
    ML["ML-сервис"]
    DB --> Read
    Provided --> Assemble
    External --> Request
    External --> Snapshot
    Snapshot --> Stored
    Request --> ML
```

Блокировки берутся до чтения таблиц и удерживаются только на время сборки профиля, без ожидания ML. Они защищают чтение от замены набора импортёром через `TRUNCATE`.

ML DTO и диагностический снимок разделены: неизвестные значения остаются неизвестными, оба источника численности и исключённые платежи сохраняются. Частичная сумма выручек не становится консолидированной, а платежи и страны ФТС не устанавливают коммерческие отношения или направления торговли. Формулы и знаменатели — в [правилах профиля](profile-rules.md).

## 3. Асинхронный анализ

Событие предварительно регистрируется через `POST /api/v1/events`. Повтор `eventId` с другим содержимым отклоняется.

```mermaid
sequenceDiagram
    actor User as Клиент API
    participant API as API / InsightService
    participant Store as OperationStore / PostgreSQL
    participant Worker as ThreadPoolExecutor
    participant Profile as CompanyProfileService
    participant ML as ML-сервис

    User->>API: POST /analyses: companyId, eventId, role
    API->>Profile: Проверить доступ к компании
    API->>Store: Сохранить операцию QUEUED
    API->>Worker: Поставить новую операцию в очередь
    API-->>User: 202, Location, Retry-After

    Worker->>Store: QUEUED → RUNNING
    Worker->>Profile: Повторно проверить доступ и собрать профиль
    Profile-->>Worker: ProfileSnapshot + качество + достаточность
    Worker->>Store: Сохранить входной снимок и событие

    alt Нет пригодных бизнес-признаков
        Worker->>Store: INSUFFICIENT_DATA, трасса UNAVAILABLE
    else Данные позволяют запросить анализ
        Worker->>ML: POST /v1/insights
        alt Ошибка связи, таймаут или недопустимый ответ
            Worker->>Store: FAILED + код ошибки
        else Получен допустимый Insight
            ML-->>Worker: Результат и статус ML
            Worker->>Store: Сохранить результат и итоговый статус
            Worker->>ML: GET /v1/runs/{runId}/trace
            alt Трасса доступна
                ML-->>Worker: Шаги ML
                Worker->>Store: trace_state = AVAILABLE
            else Трасса недоступна
                Worker->>Store: trace_state = UNAVAILABLE + предупреждение
            end
        end
    end

    loop Опрос до терминального статуса
        User->>API: GET /operations/{id}
        API->>Store: Прочитать операцию владельца
        API-->>User: Статус, ошибки, предупреждения и insightId при наличии
    end
    User->>API: GET /insights/{id} для опубликованного результата
    API->>Store: Прочитать сохранённый результат
    API-->>User: Публичный DTO с источниками, типами утверждений и дисклеймером
```

Результат сохраняется до получения трассы: её недоступность не удаляет инсайт. `Idempotency-Key` с тем же входом возвращает существующую операцию без нового ML-вызова; другой вход с тем же ключом даёт `409`. Переполненная очередь отклоняет новый запрос либо переводит уже созданную операцию в `FAILED/QUEUE_FULL` при отказе постановки.

Вопрос создаёт отдельную операцию `QUESTION` со ссылкой на исходный анализ. Используются сохранённый профиль и ML-идентификатор инсайта; ответ и собственная трасса вопроса сохраняются отдельно. Если контекст в памяти ML утрачен, операция завершается `ML_CONTEXT_EXPIRED`. Трассы обоих видов операций доступны через `GET /operations/{id}/trace`.

## 4. Состояния операций

```mermaid
stateDiagram-v2
    [*] --> QUEUED: Операция сохранена
    QUEUED --> RUNNING: Worker начал обработку
    QUEUED --> FAILED: Отказ постановки или перезапуск
    RUNNING --> SUCCEEDED: Допустимый результат без ограничений
    RUNNING --> LIMITED: Ограниченный результат или предупреждения
    RUNNING --> NO_RESULT: Вопрос без ответа или событие исправлено до анализа
    RUNNING --> INSUFFICIENT_DATA: Нет пригодных признаков для анализа
    RUNNING --> FAILED: Ошибка, ML failed или перезапуск
    SUCCEEDED --> [*]
    LIMITED --> [*]
    NO_RESULT --> [*]
    INSUFFICIENT_DATA --> [*]
    FAILED --> [*]
```

`NO_RESULT` соответствует ответу ML `no_answer` или прекращению устаревшего анализа до вызова ML. `INSUFFICIENT_DATA` означает недостаточный профиль без вызова ML. Лента публикует только актуальные, непросроченные анализы `SUCCEEDED` и `LIMITED`, по одной последней карточке на событие. Сортировка идёт по релевантности, влиянию и времени создания. Состояние трассы (`PENDING`, `AVAILABLE`, `UNAVAILABLE`) хранится независимо от результата.

Очередь находится в памяти одного экземпляра backend: по умолчанию два worker-потока и 16 мест ожидания. После перезапуска незавершённые операции становятся `FAILED/BACKEND_RESTARTED`. Автоматического повторного ML-вызова нет; при таймауте завершение внешней операции неизвестно.

## 5. Хранение состояния приложения

Основные связи и поля схемы `backend_api`:

```mermaid
erDiagram
    event |o--o{ operation : event_id
    event ||--|{ event_revision : versions
    event_revision ||--o{ operation : captured_version
    operation |o--o{ operation : parent_id
    operation ||--o{ insight_state : operation_id
    operation ||--o{ feedback : operation_id
    operation ||--o{ action_intent : operation_id

    event {
        text event_id PK
        text content_hash
        jsonb payload
        bigint current_version
    }
    event_revision {
        text event_id PK, FK
        bigint version PK
        text content_hash
        jsonb payload
    }
    operation {
        uuid id PK
        text owner_id
        text company_id
        text role
        text kind
        uuid parent_id FK
        text event_id FK
        bigint event_version FK
        boolean stale
        timestamptz expires_at
        int relevance_score
        jsonb relevance_reasons
        text request_key
        text request_hash
        text status
        jsonb payload
        jsonb profile_snapshot
        jsonb result
        jsonb trace
        text trace_state
        jsonb warnings
        text error_code
    }
    insight_state {
        uuid operation_id PK, FK
        text owner_id PK
        boolean hidden
        timestamptz opened_at
    }
    feedback {
        uuid operation_id PK, FK
        text owner_id PK
        text rating
        text comment
    }
    action_intent {
        uuid id PK
        uuid operation_id FK
        text owner_id
        text action
        text status
    }
```

Уникальность `(owner_id, kind, request_key)` обеспечивает идемпотентность операций. Внешние ключи на таблицы импортированного набора не создаются: сохраняются идентификаторы и снимок использованных данных. `action_intent` фиксирует намерение локально, без отправки менеджеру или в CRM.

Исходные ограничения ML и правила проверки ответов описаны в [совместимости ML](ml-compatibility.md).
