# Контракты

Общие спецификации взаимодействия компонентов: API, JSON Schema, форматы запросов и ответов.

Здесь описываются профиль компании `CompanyProfile` и контракты Backend ↔ ML. Изменения согласуются между разработчиками соответствующих компонентов.
JSON Schema в `ml/` экспортируются командой `python -m insight_agent.export_schemas contracts/ml` из установленного ML-пакета. `materials.schema.json` описывает versioned corpus, `bank-fragment.schema.json` — проверяемое банковское утверждение. Пример mapping профиля data-import — `ml/examples/profile-from-import.json`.

`news-page.schema.json` описывает `GET ML /v1/materials/events`: страницу текущего загруженного корпуса с `corpus_revision`, `items` (NewsEvent с расширенными SourceRef и conflicts) и `next_offset`. Чтение не вызывает LLM. Java использует этот маршрут при явном обновлении персональной ленты.

`source_id` — непрозрачная строка, включая `:`/`_`. UI сохраняет её без преобразования и открывает явный `SourceRef.url`; для PDF URL содержит `#page=N`. Все claims в массиве сохраняются вместе с kind/source_ids. Непроверенный OCR не может стать assessment. Подробности интеграции: [приёмка D03/D04](../docs/materials-acceptance.md).
