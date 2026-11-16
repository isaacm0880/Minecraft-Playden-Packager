#!/usr/bin/env bash
set -euo pipefail
root="$(mktemp -d)"
trap 'rm -rf "$root"' EXIT
mkdir -p "$root/internal/complete/java/bin" "$root/internal/incomplete" "$root/usb/usb-package/java/bin"
touch "$root/internal/complete/recipe.json" "$root/internal/complete/playden.args" "$root/internal/complete/java/bin/java.exe"
touch "$root/internal/incomplete/recipe.json"
touch "$root/usb/usb-package/recipe.json" "$root/usb/usb-package/playden.args" "$root/usb/usb-package/java/bin/java.exe"
cat > "$root/internal/complete/recipe.json" <<'JSON'
{"recipe_id":"community-imported-test-playden","recipe_version":1,"game":{"store":"imported","key":"test","name":"Test"},"launch_entries":[{"id":"program","label":"java.exe","executable":"java/bin/java.exe","arguments":"@../../playden.args"}],"executable":"java/bin/java.exe","architecture":"pe64","graphics":"opengl","community":{"format_version":1,"status":"untested"}}
JSON
printf '%s\\n' '-Xmx2G' > "$root/internal/complete/playden.args"
cp "$root/internal/complete/recipe.json" "$root/usb/usb-package/recipe.json"
printf '%s\\n' '-Xmx2G' > "$root/usb/usb-package/playden.args"
printf '{ invalid json' > "$root/internal/incomplete/recipe.json"
ln -s "$root/internal/complete" "$root/internal/linked"
cc -std=c11 -D_DEFAULT_SOURCE -Wall -Wextra -Werror -O2 \
  "-DPS5_INTERNAL_IMPORT=\"$root/internal\"" \
  "-DPS5_USB_IMPORT=\"$root/usb\"" \
  ps5/diagnostic/main.c -o "$root/diagnostic"
"$root/diagnostic" | tee "$root/output"
grep -q 'package_root=internal package=complete .*status=structure-present' "$root/output"
grep -q 'package_root=internal package=incomplete .*status=incomplete' "$root/output"
grep -q 'package_root=usb package=usb-package .*status=structure-present' "$root/output"
grep -q 'import_root=internal packages=2' "$root/output"
grep -q 'import_root=usb packages=1' "$root/output"
! grep -q 'package=linked ' "$root/output"
grep -q '^diagnostic_complete=true$' "$root/output"

grep -q 'package_validation_root=internal package=complete status=valid recipe_check=ok args_check=nonempty java_check=present' "$root/output"
grep -q 'package_validation_root=internal package=incomplete status=invalid recipe_check=recipe-invalid-json' "$root/output"
grep -q 'package_validation_root=usb package=usb-package status=valid recipe_check=ok' "$root/output"
