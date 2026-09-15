# Агент-писатель

Локальный агент для глав на русском (оригинал или чужая вселенная). UI в браузере.

## Запуск

1. Python 3.12+, ключи в `.env` (образец: `.env.example`). Файл `.env` в git не кладётся.
2. `pip install -r requirements.txt`
3. `run.bat` или `python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload --reload-dir app`
4. Открыть http://127.0.0.1:8765

Книги пишутся в `projects/` (тоже не в репозитории). Подробности — в `ХОД-ПРОЕКТА.md`.
