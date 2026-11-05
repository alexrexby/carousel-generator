#!/usr/bin/env bash
# Приёмка выкатки — положительный контроль, а не `docker compose ps`.
# `compose ps` показывает Up и для контейнера в рестарт-петле, поэтому смотрим
# после окна стабилизации: процесс жив, перезапусков 0, приложение отвечает 200.
#
#   deploy/accept.sh <контейнер> <url> [окно_сек]
#   deploy/accept.sh htmlhost-app http://127.0.0.1:8000/ 15
#
# Код возврата 0 — принято, иначе — отказ (deploy.yml откатывает на :prev).
set -u
c="$1"; url="$2"; window="${3:-15}"
sleep "$window"
running=$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null || echo missing)
restarts=$(docker inspect -f '{{.RestartCount}}' "$c" 2>/dev/null || echo -1)
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$url" 2>/dev/null || true)
echo "accept: container=$c running=$running restarts=$restarts http=$code (окно ${window}с)"
[ "$running" = "true" ] && [ "$restarts" = "0" ] && [ "$code" = "200" ]
