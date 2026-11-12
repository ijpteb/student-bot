#!/bin/bash
# Запуск бота на Mac: дважды щёлкните этот файл в Finder.
cd "$(dirname "$0")" || exit 1
pause() { echo; read -r -p "Нажмите Enter, чтобы закрыть окно…"; }

# Docker Desktop иногда не добавляет команду docker в PATH — ищем её сами.
if ! command -v docker >/dev/null 2>&1 && [ -x /Applications/Docker.app/Contents/Resources/bin/docker ]; then
  export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "Не найден Docker. Установите Docker Desktop — см. README, шаг 2."
  pause; exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Запускаю Docker Desktop… (до 2 минут)"
  open -a Docker
  for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
  if ! docker info >/dev/null 2>&1; then
    echo "Docker не запустился. Откройте Docker Desktop вручную, дождитесь зелёного статуса и запустите этот файл снова."
    pause; exit 1
  fi
fi

# Если папка скачана через git — подтягиваем свежую версию кода.
if [ -d .git ] && command -v git >/dev/null 2>&1; then
  echo "Проверяю обновления кода…"
  git pull --ff-only -q || echo "(обновить не удалось — запускаю текущую версию)"
fi

# Первый запуск: создаём файл настроек и сами придумываем пароль базы.
if [ ! -f .env ]; then
  cp .env.example .env
  PW=$(LC_ALL=C tr -dc 'a-zA-Z0-9' </dev/urandom | head -c 32)
  sed -i '' "s/^POSTGRES_PASSWORD=$/POSTGRES_PASSWORD=$PW/" .env
  echo "Создан файл настроек .env."
fi
if ! grep -q '^BOT_TOKEN=..*' .env; then
  open -e .env
  echo
  echo "Открылся файл настроек. Вставьте токен бота сразу после BOT_TOKEN= (без пробелов),"
  echo "сохраните (Cmd+S), закройте файл и снова дважды щёлкните «Запустить бота.command»."
  pause; exit 0
fi

echo "Собираю и запускаю бота… (первый раз — несколько минут)"
if ! docker compose up -d --build; then
  echo
  echo "❌ Не удалось запустить. Скопируйте текст выше и пришлите его Claude."
  pause; exit 1
fi
echo
echo "✅ Бот запущен и работает в фоне — окно можно закрыть."
echo "Пока Mac не выключен и не спит, бот отвечает в Telegram."
echo "Ниже — журнал работы бота (чтобы перестать смотреть, закройте окно)."
echo
docker compose logs -f --tail 30 bot
