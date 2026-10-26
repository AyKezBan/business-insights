# Frontend: интерфейс персональной аналитики

Выбор компании и роли, лента и карточки инсайтов, источники, уточняющие вопросы, скрытие событий и обратная связь.

## Общий стек

Frontend подключён к Java Backend API. Запуск из корня репозитория:

```sh
./stack init
# Настройте DATA_DIR и при необходимости LLM_API_KEY в .env.
./stack up
```

Интерфейс: http://127.0.0.1:8088.

Подробности: [корневой README](../README.md), [инструкция инфраструктуры](../infra/README.md).

## Автономный деморежим

Из папки `frontend`, Python 3.11+:

```sh
python server.py
```

Интерфейс: http://127.0.0.1:8766. Остановка — Ctrl+C.

В этом режиме используются синтетические данные, настройки сохраняются в браузере. Java API и LLM не подключены. Форма входа демонстрационная: реальные учётные данные вводить не следует.

## Проверки

Из папки `frontend`:

```sh
python -m unittest discover -s tests -v
node --check web/app.js
node tests/test_frontend.cjs
node tests/test_integration.cjs
node tests/test_backend_bindings.cjs
```

## Документация

- [Передача проекта и запуск](GIT_AND_RUN.md)
- [Контракт frontend](API_CONTRACT.md)
- [Подготовка FE-08–FE-13](FE_08_13_PREPARATION.md)
- [Проверки интерфейса](VALIDATION.md)
- [Проверки общего стека](../infra/VERIFICATION.md)