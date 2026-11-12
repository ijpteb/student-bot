#!/bin/bash
# Остановка бота на Mac: дважды щёлкните этот файл в Finder. Данные сохраняются.
cd "$(dirname "$0")" || exit 1
if ! command -v docker >/dev/null 2>&1 && [ -x /Applications/Docker.app/Contents/Resources/bin/docker ]; then
  export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
fi
docker compose stop && echo "⏹ Бот остановлен. Все данные сохранены."
echo; read -r -p "Нажмите Enter, чтобы закрыть окно…"
