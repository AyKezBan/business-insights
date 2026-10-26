# Приёмка материалов

Текущий запуск всей системы описан в [release-runbook](../../docs/materials-release-runbook.md), фактические release gates — в [materials-acceptance.md](../../docs/materials-acceptance.md). Эти инструкции не объявляют весь выпуск или live 9+9 принятыми.

## Банковский review и retrieval

Review 26.10.2026 покрывает все **64 физические страницы девяти PDF**. `bank-page-review.json` хранит page dispositions и отдельные visual witnesses фрагментов. `bank-review.json` содержит 72 verified решения в семи датированных документах; на 26 октября 61 фрагмент проходит eligibility по дате/180-дневному окну/expiry/supersession и покрывает девять claim-level topics. Проверенная страница не делает все её фрагменты verified. Steel 2021 остаётся историческим, gold digest — без установленной publication date. Полные тексты, PDF, OCR и изображения страниц хранятся приватно.

`bank-retrieval-gold.jsonl` — прежние десять запросов, сохранённые byte-for-byte. Дополнение содержит 12 positives и шесть near-negatives, замороженных до retrieval observation; hashes — в `bank-gold-freeze.json`. Текущий gate: **10/10** старых попаданий, **≥10/12** extension в top-3 и **6/6** отказов без false positives. Подробности review, limits и exact immutable OCR source для воспроизведения — в [BANK_REVIEW.md](BANK_REVIEW.md).

Из `ml-service`, с активным проектным окружением:

```sh
PYTHONPATH=src python eval/rebuild_bank_review.py \
  --source /absolute/private/prior-corpus --destination /absolute/private/new-corpus
PYTHONPATH=src python eval/run_bank_retrieval.py --store /absolute/private/new-corpus
```

Destination не должен существовать и должен находиться вне source. Rebuild проверяет exact PDF/page/extraction/fragment hashes и offsets; иной OCR или correction требует нового визуального review. Только metadata/hash-bound решения не заменяют оригиналы. Retrieval gate проверяет ранжирование и отказы; semantic/span/Java/browser проверки остаются отдельными.

## Release-проверка модели

[Замороженная rubric](release-rubric-2026-10-26.json) задаёт три издателя × три синтетических профиля и минимум девять QA, нулевую терпимость к фактическим/атрибуционным ошибкам и ручную оценку всех опубликованных полей. [Профили](release-profiles-2026-10-26.json) не являются реальными клиентскими CSV. Текущая budget policy, включая user override, находится в rubric; defaults цен не выдаются за реальные тарифы.

`run_release_acceptance.py` использует production agent/QA и exact corpus token/event hashes. По умолчанию это dry-run; provider calls требуют явного `--live`. Сначала проверьте интерфейс:

```sh
PYTHONPATH=src python eval/run_release_acceptance.py --help
```

Manifest, выбранные case IDs, exact revision, профили, разрешённую модель и output directory фиксируйте до paid calls. Private output включает prompts, ответы и provider observations; держите его вне Git. Synthetic/offline PASS проверяет интеграцию и не заменяет фактическую live и ручную смысловую приёмку. После найденного дефекта сохраняйте regression и выбирайте новый независимый holdout для его класса.

## Историческая проверка 24 октября

Тогда были сверены 11 фрагментов на четырёх страницах, действовал порог 8/10, фактический retrieval дал 10/10 и 3/3 no-coverage. Live использовал две другие отложенные статьи ЦБ × три синтетических профиля: пять limited и один failed, A10 не принят. Это прежние corpus/prompt/code и не текущий release gate. Metadata сохранены в `live-acceptance-2026-10-24.json`, подробности — в [архивном отчёте](../../docs/materials-acceptance-2026-10-24.md).
