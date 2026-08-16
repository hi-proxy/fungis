#!/bin/sh
# Dispatch hook.
#
# cmux 없이도 에이전트 세션을 발견하고 메시지를 전하려는 것이다. 표준 hook
# 이벤트만 쓰므로 cmux와 공존하고, cmux가 없는 환경에서도 그대로 동작한다.
#
# stop hook이 decision block과 reason을 돌려주면 그 reason이 에이전트 컨텍스트에
# 들어간다는 것을 확인했다. 그래서 pager를 터미널에 찍어 넣지 않는다. 터미널에
# 아무것도 넣지 않으므로 입력 안전성(명세 3.4)이 걸리는 지점이 없다.
#
# 전달은 읽기만 한다. 채팅 타임라인에는 아무것도 남기지 않는다.
set -u
event="${1:-unknown}"
repo="$(cd "$(dirname "$0")/../.." && pwd)"
store="${DISPATCH_HOOK_STORE:-$HOME/.dispatch}"
mkdir -p "$store" 2>/dev/null || true

payload="$(cat 2>/dev/null || true)"

printf '%s\n' "{\"event\":\"$event\",\"at\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"payload\":$payload}" \
  >> "$store/hook-events.jsonl" 2>/dev/null || true

if [ "$event" != "stop" ]; then
  echo '{}'
  exit 0
fi

# stop_hook_active가 참이면 이미 한 번 막은 뒤다. 다시 막으면 끝나지 않는다.
DISPATCH_REPO="$repo" python3 - "$payload" <<'PY' 2>/dev/null || echo '{}'
import json, os, subprocess, sys

try:
    payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
except Exception:
    payload = {}

if payload.get("stop_hook_active", True):
    print("{}")
    raise SystemExit

repo = os.environ.get("DISPATCH_REPO", ".")
cli = os.path.join(repo, ".venv", "bin", "dispatch")
if not os.access(cli, os.X_OK):
    print("{}")
    raise SystemExit

try:
    done = subprocess.run(
        [cli, "inbox"], cwd=repo, capture_output=True, text=True, timeout=20
    )
    # inbox는 stdout에 JSON 하나만 낸다. 안내는 stderr로 나간다.
    data = json.loads(done.stdout.strip().splitlines()[-1])
    messages = data.get("messages") or []
except Exception:
    messages = []

if not messages:
    print("{}")
    raise SystemExit

lines = ["[dispatch] 새 메시지 %d건이다. 답은 dispatch reply로 보낸다." % len(messages)]
for m in messages:
    lines.append(
        "#%s %s: %s" % (m.get("seq"), m.get("from"), (m.get("body") or "").strip())
    )
print(json.dumps({"decision": "block", "reason": "\n\n".join(lines)}, ensure_ascii=False))
PY
