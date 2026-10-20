#!/usr/bin/env bash
# Деплой на свой сервер: ./deploy.sh   (по умолчанию root@204.168.241.143, папка /opt/klopnews)
set -euo pipefail
SERVER="${SERVER:-root@204.168.241.143}"
REMOTE_DIR="${REMOTE_DIR:-/opt/klopnews}"
cd "$(dirname "$0")"

step() { printf "\n\033[1;34m→ %s\033[0m\n" "$1"; }

step "Синхронизация кода → $SERVER:$REMOTE_DIR"
ssh "$SERVER" "mkdir -p $REMOTE_DIR"
rsync -az --delete \
  --exclude '.git/' --exclude '.venv/' --exclude 'venv/' --exclude '.env' --exclude 'data/' \
  --exclude '__pycache__/' --exclude '.pytest_cache/' \
  ./ "$SERVER:$REMOTE_DIR/"

if ! ssh "$SERVER" "test -f $REMOTE_DIR/.env"; then
  echo "На сервере нет $REMOTE_DIR/.env — скопируйте .env.example, заполните и запустите снова:"
  echo "  scp .env $SERVER:$REMOTE_DIR/.env"
  exit 1
fi

step "Бэкап базы"
ssh "$SERVER" "cd $REMOTE_DIR && mkdir -p data/backups && if [ -f data/klopnews.db ]; then
  python3 -c \"import sqlite3,sys; sqlite3.connect('data/klopnews.db').backup(sqlite3.connect(sys.argv[1]))\" data/backups/klopnews-\$(date +%F-%H%M).db
  find data/backups -name '*.db' -mtime +14 -delete; echo ok; else echo 'базы ещё нет'; fi"

step "Сборка и запуск"
ssh "$SERVER" "cd $REMOTE_DIR && docker compose up -d --build --remove-orphans"

step "Проверка"
for i in $(seq 1 12); do
  sleep 5
  if ssh "$SERVER" "curl -fsS http://127.0.0.1:\$(grep -E '^WEB_PORT=' $REMOTE_DIR/.env | cut -d= -f2 || echo 8095)/health" >/dev/null 2>&1; then
    echo "ok: сайт отвечает"; exit 0
  fi
done
echo "Сайт не ответил за минуту — смотрите логи: ssh $SERVER 'cd $REMOTE_DIR && docker compose logs --tail 100'"
exit 1
