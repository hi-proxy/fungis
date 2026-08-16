#!/bin/sh
# Dispatch hook 프로토타입.
#
# cmux가 하는 일을 직접 해보려는 것이다. cmux는 ~/.cmux/hooks에 같은 모양의
# 스크립트를 깔아 두고 표준 hook 이벤트를 받아 세션 정보를 파일에 모은다.
# 여기서 확인하려는 것은 둘이다.
#   1. hook만으로 세션을 발견할 수 있는가 (sessionId·pid·cwd·tty)
#   2. stop hook의 출력이 에이전트 컨텍스트에 들어가는가
#
# 계약: stdin으로 JSON payload를 받고 stdout으로 JSON을 돌려준다.
set -u
event="${1:-unknown}"
store="${DISPATCH_HOOK_STORE:-$HOME/.dispatch}"
mkdir -p "$store" 2>/dev/null || true

payload="$(cat 2>/dev/null || true)"

# 무엇이 오는지 그대로 남긴다. 필드 이름을 추측하지 않기 위해서다.
printf '%s\n' "{\"event\":\"$event\",\"at\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"pid\":$PPID,\"tty\":\"$(ps -o tty= -p $PPID 2>/dev/null | tr -d ' ')\",\"payload\":$payload}" \
  >> "$store/hook-events.jsonl" 2>/dev/null || true

echo '{}'
