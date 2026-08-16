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

# stop hook의 출력이 에이전트 컨텍스트에 들어가는지 확인한다. 들어간다면
# pager를 터미널에 찍어 넣을 이유가 사라진다.
#
# stop_hook_active가 참이면 이미 한 번 막은 뒤라 그대로 통과시킨다. 이 장치가
# 없으면 막을 때마다 다시 stop이 걸려 끝나지 않는다.
if [ "$event" = "stop" ]; then
  active="$(printf '%s' "$payload" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("stop_hook_active", True))
except Exception: print(True)' 2>/dev/null || echo True)"
  if [ "$active" = "False" ]; then
    # 실증 끝. 여기에 dispatch inbox 결과를 실으면 pager가 필요 없어진다.
    # 프로브 문구는 새 세션마다 끼어들므로 꺼 둔다.
    :
    exit 0
  fi
fi

echo '{}'
