# A^2SCEND

Персональная навигация карьерного роста. MVP по исходному кейсу Career Quest для HackAlem AI / Halyk Bank.

Интерфейс адаптирован по образцу `design/career-quest/`: карта карьерного пути, выбор маршрута,
панель факторов и единое оформление сотрудника/HR. [Отчёт о переносе дизайна и проверках](docs/design-transfer.md).

Из этой папки, Python 3.10+:

```bash
python server.py
```

Откройте http://127.0.0.1:8000. Случайные пароли для `employee` и `hr` выводятся в терминале. На Unix можно использовать `python3`. Обязательных pip/npm-зависимостей нет.

```bash
python -m unittest discover -s tests -v
python evaluation.py
python benchmark.py
python ai_smoke_test.py --live
```

[Полный README](../README.md) описывает функции, архитектуру, PowerShell/Unix-конфигурацию, импорт и ограничения. [Сценарий защиты](../docs/demo-script.md). [Отчёт интеграции fuse и проверок](../docs/integration-report.md).

По умолчанию алгоритмический режим без сети и память без сохранения. Для SQLite задайте `CQ_STORAGE_PATH=./progress.sqlite`. Hybrid AI требует `OPENAI_API_KEY`, `OPENAI_MODEL`, `CQ_ALLOW_EXTERNAL_AI=1`. Live smoke дополнительно требует `--live`. Текущий статус live: **SKIPPED — Реальная AI-интеграция не проверена**.

Профиль и навыки → допустимые кандидаты → локальные факторы → опциональный LLM-reranking → локальная проверка. XP, gains и покрытие требований рассчитывает только engine.py. HR не выполняет действия за сотрудника. Данные встроенного набора и sample_upload синтетические; официальный starter kit не подтверждён.
