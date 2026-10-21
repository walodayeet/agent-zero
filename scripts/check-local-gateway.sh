#!/usr/bin/env bash
# Probe a local OpenAI-compatible gateway (cliproxyapi, Ollama, LM Studio, …).
#
#   ./scripts/check-local-gateway.sh http://127.0.0.1:8317/v1 [api-key] [model]
#
# Prefer 127.0.0.1 over localhost: gateways often bind IPv4 only, and A0
# normalizes localhost to 127.0.0.1 on bare metal anyway.
set -u
BASE="${1:-http://127.0.0.1:8317/v1}"
KEY="${2:-}"
MODEL="${3:-}"
BASE="${BASE%/}"

say() { printf '%s\n' "$*"; }
ok() { printf 'OK   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; }

case "$BASE" in
  *localhost*) say "note: using 127.0.0.1 is more reliable than localhost (IPv4/6 binding)." ;;
esac

for var in HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy NO_PROXY no_proxy; do
  [ -n "${!var:-}" ] && say "env  $var=${!var}"
done
case ",${NO_PROXY:-},${no_proxy:-}," in
  *",127.0.0.1,"*|*",localhost,"*) ;;
  *) [ -n "${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-}" ] && \
       say "warn: a proxy is set and loopback is not in NO_PROXY; add localhost,127.0.0.1,::1." ;;
esac

auth=()
[ -n "$KEY" ] && auth=(-H "Authorization: Bearer $KEY")

say "GET  $BASE/models"
body="$(curl -sS -m 10 "${auth[@]}" "$BASE/models" -w '\n%{http_code}' 2>&1)" || true
code="$(printf '%s' "$body" | tail -n1)"
payload="$(printf '%s' "$body" | sed '$d')"
if [ "$code" = "200" ]; then
  ok "models reachable"
  printf '%s\n' "$payload" | head -c 400; say
  if [ -z "$MODEL" ]; then
    MODEL="$(printf '%s' "$payload" | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin)
    items=d.get("data") or []
    print(items[0].get("id","") if items else "")
except Exception:
    print("")' 2>/dev/null)"
  fi
else
  bad "models HTTP $code"
  printf '%s\n' "$payload" | head -c 400; say
fi

if [ -n "$MODEL" ]; then
  say "POST $BASE/chat/completions (model=$MODEL)"
  req="{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":8}"
  resp="$(curl -sS -m 30 "${auth[@]}" -H 'Content-Type: application/json' -d "$req" \
    "$BASE/chat/completions" -w '\n%{http_code}' 2>&1)" || true
  rcode="$(printf '%s' "$resp" | tail -n1)"
  rbody="$(printf '%s' "$resp" | sed '$d')"
  if [ "$rcode" = "200" ]; then
    ok "chat completion reachable"
    printf '%s\n' "$rbody" | head -c 400; say
  else
    bad "chat HTTP $rcode"
    printf '%s\n' "$rbody" | head -c 400; say
  fi
else
  say "skip: no model id (pass one as the third argument)"
fi

cat <<'EOF'

Tips:
- Bare metal A0: use http://127.0.0.1:PORT/v1; localhost is normalized too.
- Docker A0 (launcher/CLI image): localhost is the container. Use
  http://host.docker.internal:PORT/v1 and start the container with
  --add-host=host.docker.internal:host-gateway (Linux), or --network host.
- cliproxyapi usually needs the /v1 suffix and its API key in the provider key.
EOF
