# Документация

Общая документация проекта: архитектура, границы ответственности компонентов, принятые решения и инструкции по совместному запуску.

Начните с [release-runbook материалов](materials-release-runbook.md): запуск всего Compose-стека из свежего checkout с приватными CSV/PDF, проверенным банковским корпусом и тремя издателями.

- [Сквозная проверка](materials-e2e.md): каталог, очередь, источники, QA, refresh и browser evidence.
- [Приёмка](materials-acceptance.md): фактические статусы и ограничения; [архив 24 октября](materials-acceptance-2026-10-24.md) относится к указанному там историческому SHA.
- [Подготовка PDF и ingestion](../ml-service/docs/material-ingestion.md): review, исправления OCR, sync и версии корпуса.
- [Инфраструктура](../infra/README.md), [offline fixture CI](../infra/ci/README.md) и [исторический DevOps smoke](../infra/VERIFICATION.md).
- [Утверждённый план D03/D04](superpowers/plans/2026-10-26-domains-03-04-finalization.md).

Инструкции по отдельным компонентам размещаются в их README. Offline fixtures, приватные оригиналы и ответы реальной модели — разные уровни проверки; успешный smoke не закрывает live или ручную смысловую приёмку.
