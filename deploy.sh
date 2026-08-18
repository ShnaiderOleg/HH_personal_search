#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

SERVICE="hhsearch"

if command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
else
  SUDO=""
fi

echo "=== 1/3 Обновление кода ==="
if ! git pull; then
  echo "ОШИБКА: git pull не удался (вероятно, локальные изменения)."
  echo "Проверьте: git status / git stash, затем повторите."
  exit 1
fi

if [[ -x .venv/bin/pip ]]; then
  PIP=".venv/bin/pip"
elif [[ -x venv/bin/pip ]]; then
  PIP="venv/bin/pip"
else
  PIP=""
fi

echo ""
read -rp "Обновить зависимости (pip install -r requirements.txt)? [y/N] " answer
if [[ "$answer" =~ ^[YyДд] ]]; then
  echo "=== 2/3 Обновление зависимостей ==="
  if [[ -n "$PIP" ]]; then
    "$PIP" install -r requirements.txt
  else
    echo "Не найдено виртуальное окружение (.venv/venv), пропускаю установку."
  fi
else
  echo "Зависимости не обновляем."
fi

echo "=== 3/3 Перезапуск сервиса ==="
$SUDO systemctl restart "$SERVICE"
echo "=== Готово: код обновлён, сервис $SERVICE перезапущен ==="