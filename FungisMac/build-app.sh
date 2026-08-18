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
codesign --force --sign - "$app_dir"

echo "$app_dir"
