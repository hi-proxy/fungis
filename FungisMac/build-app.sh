#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# 디버그와 릴리스가 .build를 같이 쓰면 릴리스가 Foundation을 못 찾는 일이
# 생긴다(FileManager.default가 없다는 식으로 뜬다). 8/18에 두 번 겪었다.
# 자리를 갈라 두면 안 난다.
scratch_dir="$project_dir/.build-release"
app_dir="$project_dir/build/Fungis.app"
contents_dir="$app_dir/Contents"
module_cache="/private/tmp/fungis-swift-module-cache"

env CLANG_MODULE_CACHE_PATH="$module_cache" \
  swift build --package-path "$project_dir" --scratch-path "$scratch_dir" -c release

mkdir -p "$contents_dir/MacOS" "$contents_dir/Resources"
install -m 755 "$scratch_dir/release/FungisMac" "$contents_dir/MacOS/FungisMac"
install -m 644 "$project_dir/Resources/Info.plist" "$contents_dir/Info.plist"

# 어느 코드로 지었는지를 번들에 적는다. 이게 없으면 "지금 도는 앱이 최신인가"
# 를 바이너리를 뒤져 답해야 하는데, 짧은 한글 문자열은 Swift 가 인라인으로
# 넣어 grep 에 안 걸린다. 그것 때문에 8/24 에 멀쩡한 빌드를 세 번 의심했다.
stamp=$(git -C "$project_dir" describe --always --dirty 2>/dev/null || echo unknown)
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $stamp" "$contents_dir/Info.plist"

codesign --force --sign - "$app_dir"

# 옛 앱이 살아 있으면 macOS 는 같은 번들을 두 번 열지 않는다. 그래서 새로
# 지어도 화면은 그대로고, 빌드를 의심하게 된다. --run 을 주면 갈아 끼운다.
if [ "${1:-}" = "--run" ]; then
  osascript -e 'tell application id "me.homil.fungis" to quit' 2>/dev/null || true
  # 종료가 끝나기 전에 열면 옛 프로세스가 그대로 남는다.
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    pgrep -f "Fungis.app/Contents/MacOS" >/dev/null || break
    sleep 1
  done
  open "$app_dir"
fi

echo "$app_dir ($stamp)"
