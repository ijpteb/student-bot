#!/bin/bash
# Запуск бота на Mac: дважды щёлкните этот файл в Finder.
cd "$(dirname "$0")" || exit 1
set -o pipefail
pause() { echo; read -r -p "Нажмите Enter, чтобы закрыть окно…"; }
fail() {
  echo
  echo "❌ $1"
  echo "Если непонятно, что делать, скопируйте текст из этого окна и пришлите его Claude (токен в нём скрыт)."
  pause; exit 1
}
# Всё, что показываем на экране, пропускаем через «вырезание» токена бота.
redact() { sed -E 's/[0-9]{6,}:[A-Za-z0-9_-]{30,}/***/g'; }

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

# Файл настроек: при первом запуске создаём из образца.
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Создан файл настроек .env."
fi
# Пароль базы данных придумываем сами, если он ещё не задан.
if ! grep -q '^POSTGRES_PASSWORD=..*' .env; then
  PW=$(LC_ALL=C tr -dc 'a-zA-Z0-9' </dev/urandom | head -c 32)
  if grep -q '^POSTGRES_PASSWORD=' .env; then
    sed -i '' "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$PW/" .env
  else
    printf '\nPOSTGRES_PASSWORD=%s\n' "$PW" >> .env
  fi
fi
if ! grep -q '^BOT_TOKEN=..*' .env; then
  open -e .env
  echo
  echo "Открылся файл настроек. Вставьте токен бота сразу после BOT_TOKEN= (без пробелов),"
  echo "сохраните (Cmd+S), закройте файл и снова дважды щёлкните «Запустить бота.command»."
  pause; exit 0
fi
# Проверяем, что файл настроек читается (например, нет незакрытой кавычки).
if ! ERR=$(docker compose config -q 2>&1); then
  echo "$ERR" | redact
  open -e .env
  fail "Ошибка в файле .env (номер строки — в тексте выше). Открыл файл: исправьте, сохраните (Cmd+S) и запустите этот файл снова."
fi

echo "Собираю и запускаю бота… (первый раз — несколько минут)"
docker compose up -d --wait db 2>&1 | redact \
  || fail "Не запустилась база данных. Проверьте интернет (при первом запуске Docker скачивает нужные программы) и запустите этот файл снова."
# Пароль базы задаётся только при её создании. Если файл .env создали заново (например, папку
# скачали ещё раз), приводим пароль в базе к паролю из .env. Данные при этом не трогаются.
printf "ALTER USER bot PASSWORD :'pw';\n" \
  | docker compose exec -T db sh -c 'psql -q -v ON_ERROR_STOP=1 -v pw="$POSTGRES_PASSWORD" -U bot -d bot' >/dev/null 2>&1 \
  || echo "(не удалось сверить пароль базы — продолжаю)"

docker compose up -d --build --force-recreate --no-deps bot 2>&1 | redact \
  || fail "Не удалось собрать или запустить бота. Проверьте интернет и запустите этот файл снова."

# Ждём, пока бот действительно подключится к Telegram (до 1,5 минуты).
# Контейнер бота только что создан заново, поэтому в его журнале — только этот запуск.
echo "Проверяю, что бот подключился к Telegram…"
STATUS=""
for _ in $(seq 1 45); do
  LOGS=$(docker compose logs --no-color bot 2>/dev/null | redact)
  if echo "$LOGS" | grep -q "запущен, сервер Bot API"; then STATUS=ok; break; fi
  if echo "$LOGS" | grep -q -e "не смог запуститься" -e "Ошибка в файле .env" -e "Неверный пароль базы"; then STATUS=error; break; fi
  sleep 2
done

if [ "$STATUS" != ok ]; then
  echo
  echo "Последние строки журнала бота:"
  echo "$LOGS" | tail -15
  REASON=$(echo "$LOGS" | grep -e "не смог запуститься" -e "Ошибка в файле .env" -e "Неверный пароль базы" | tail -1 | sed -E 's/^[^|]*\| *//; s/^[0-9-]+ [0-9:,.]+ [A-Z]+ [A-Za-z0-9_.]+: //')
  echo
  if [ -n "$REASON" ]; then
    echo "Причина: $REASON"
  else
    echo "Бот не сообщил, что подключился к Telegram. Частая причина — нет доступа к Telegram из вашей сети."
  fi
  if echo "$REASON" | grep -q -e "BOT_TOKEN" -e "файле .env"; then
    open -e .env
    fail "Бот не запустился. Открыл файл настроек: исправьте названную строку, сохраните (Cmd+S) и запустите этот файл снова."
  fi
  fail "Бот не запустился."
fi

echo
echo "✅ Бот запущен и работает в фоне — окно можно закрыть."
echo "Пока Mac не выключен и не спит, бот отвечает в Telegram."
echo "Ниже — журнал работы бота (чтобы перестать смотреть, закройте окно)."
echo
docker compose logs -f --tail 5 bot 2>&1 | redact
