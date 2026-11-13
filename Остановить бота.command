#!/bin/bash
# Остановка бота на Mac: дважды щёлкните этот файл в Finder. Данные сохраняются.
cd "$(dirname "$0")" || exit 1
if ! command -v docker >/dev/null 2>&1 && [ -x /Applications/Docker.app/Contents/Resources/bin/docker ]; then
  export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
fi
# Пароль нужен docker compose только для чтения настроек; для остановки подойдёт любой,
# если файла .env нет (например, папку скачали заново).
if ! grep -q '^POSTGRES_PASSWORD=..*' .env 2>/dev/null; then export POSTGRES_PASSWORD=unused; fi
docker compose stop && echo "⏹ Бот остановлен. Все данные сохранены."
echo; read -r -p "Нажмите Enter, чтобы закрыть окно…"
