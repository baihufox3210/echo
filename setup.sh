#!/bin/sh
# 建立 .env 並自動產生 SEARXNG_SECRET（不需要 openssl）
set -eu
cd "$(dirname "$0")"

if [ ! -f .env ]; then
    cp .env.example .env
    echo "已從 .env.example 建立 .env"
fi

# 修復先前追加時黏在其他行尾的 SEARXNG_SECRET，並確保檔尾有換行
sed -i.bak 's/\(.\)SEARXNG_SECRET=.*$/\1/' .env && rm -f .env.bak
if [ -s .env ] && [ -n "$(tail -c 1 .env)" ]; then
    echo >> .env
fi

current=$(grep -E '^SEARXNG_SECRET=' .env | tail -n 1 | cut -d= -f2- || true)
if [ -z "$current" ] || [ "$current" = "change_me" ]; then
    secret=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
    # 移除所有舊的 SEARXNG_SECRET 行後寫入新值（避免重複的 key）
    grep -v -E '^SEARXNG_SECRET=' .env > .env.tmp || true
    mv .env.tmp .env
    echo "SEARXNG_SECRET=$secret" >> .env
    echo "已產生 SEARXNG_SECRET"
else
    echo "SEARXNG_SECRET 已設定，略過"
fi

missing=""
for key in DISCORD_TOKEN BASE_URL API_KEY MODEL; do
    value=$(grep -E "^$key=" .env | tail -n 1 | cut -d= -f2- || true)
    case "$value" in
        "" | your_*) missing="$missing $key" ;;
    esac
done

if [ -n "$missing" ]; then
    echo "請編輯 .env 填入：$missing"
    exit 1
fi
echo "設定完成，執行 docker compose up -d --build 啟動"
