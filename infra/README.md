# Инфраструктура

Конфигурация инфраструктуры и развёртывания проекта: PostgreSQL, настройки окружения и вспомогательные сервисы.
Локальный запуск корпуса и ML: `compose.ml-materials.yml`, команды в [material-ingestion.md](../ml-service/docs/material-ingestion.md). Подготовка пишет новый snapshot, ML читает закреплённый snapshot через readonly volume; после публикации нужен restart. Java/UI и PostgreSQL здесь не подменяются.
