#!/usr/bin/env bash
# Installs the verification gate for one repository on this machine. Idempotent.
#
#   ./install.sh [--repo PATH] [--worktree PATH]
#
# Defaults: --repo ~/work/ipaas, --worktree ~/work/ipaas_worktrees/gate. The same two
# values are read from IPAAS_GATE_REPOSITORY and IPAAS_GATE_WORKTREE by gate.py, so the
# installer exports them before calling it.
#
# Steps 1 to 4 are ipaas-specific (the shared services its specs touch). Steps 5 to 9 are
# the generic wiring any project needs: git hooks, git excludes, the protocol file, the
# Claude Code hook registration, and gate.py setup.
set -euo pipefail

GATE_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
REPO="${IPAAS_GATE_REPOSITORY:-$HOME/work/ipaas}"
WORKTREE="${IPAAS_GATE_WORKTREE:-$HOME/work/ipaas_worktrees/gate}"
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --worktree) WORKTREE="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
export IPAAS_GATE_REPOSITORY="$REPO" IPAAS_GATE_WORKTREE="$WORKTREE"

REDIS_CONTAINER=ipaas-gate-redis; REDIS_PORT=26380; REDIS_IMAGE=valkey/valkey:8-alpine
GIT_CONTAINER=ipaas-gate-git; GIT_PORT=23232; GIT_IMAGE=ipaas-git-test:latest
KEY_DIR="$REPO/git-server/ssh-user"

say() { printf '\n== %s\n' "$*"; }
die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

say "1. prerequisites"
for tool in rg ast-grep ctags python3 docker git ssh-keyscan; do
  command -v "$tool" >/dev/null || die "$tool is missing (brew install ripgrep ast-grep universal-ctags; Docker Desktop for docker)"
done
ctags --version | grep -q 'Universal Ctags' || die "ctags must be Universal Ctags (brew install universal-ctags)"
docker info >/dev/null 2>&1 || die "the Docker daemon is not running"
[ -e "$REPO/.git" ] || die "$REPO is not a git repository"
[ -f "$REPO/platform/.env.test.local" ] || die "$REPO/platform/.env.test.local is missing: set up the ipaas dev environment first"
echo "ok: rg, ast-grep, universal-ctags, python3, docker, git; repo $REPO"

say "2. gate Redis ($REDIS_CONTAINER on $REDIS_PORT)"
if ! docker ps -a --format '{{.Names}}' | grep -qx "$REDIS_CONTAINER"; then
  docker run -d --name "$REDIS_CONTAINER" --restart unless-stopped -p "$REDIS_PORT:6379" "$REDIS_IMAGE" >/dev/null
  echo "created"
else
  docker start "$REDIS_CONTAINER" >/dev/null; echo "present"
fi

say "3. gate git server ($GIT_CONTAINER on $GIT_PORT)"
if ! docker image inspect "$GIT_IMAGE" >/dev/null 2>&1; then
  echo "building $GIT_IMAGE from $REPO/git-server/Dockerfile-test"
  docker build -q -t "$GIT_IMAGE" -f "$REPO/git-server/Dockerfile-test" "$REPO/git-server" >/dev/null
fi
mkdir -p "$KEY_DIR"
for key in ipaas-admin ipaas-runner; do
  [ -f "$KEY_DIR/${key}_ed25519" ] || { ssh-keygen -q -t ed25519 -C "$key" -N '' -f "$KEY_DIR/${key}_ed25519"; echo "generated $KEY_DIR/${key}_ed25519 (platform/.env.local must point IPAAS_GIT_PRIVATE_KEY at the admin key)"; }
done
if ! docker ps -a --format '{{.Names}}' | grep -qx "$GIT_CONTAINER"; then
  docker run -d --name "$GIT_CONTAINER" --restart unless-stopped -p "$GIT_PORT:23231" \
    -e SOFT_SERVE_INITIAL_ADMIN_KEYS="$(cat "$KEY_DIR/ipaas-admin_ed25519.pub")" \
    -e SOFT_SERVE_DATA_PATH=/data/ -e CI=1 -e SOFT_SERVE_CONFIG_LOCATION=/soft-serve/config.yaml \
    -e REPOS_ROOT=/data/repos -e SOFT_SERVE_SSH_PUBLIC_URL="ssh://ipaas-test:23231" "$GIT_IMAGE" >/dev/null
  echo "created"
else
  docker start "$GIT_CONTAINER" >/dev/null; echo "present"
fi
for _ in $(seq 1 30); do nc -z localhost "$GIT_PORT" 2>/dev/null && break; sleep 1; done
mkdir -p ~/.ssh; ssh-keygen -R "[localhost]:$GIT_PORT" >/dev/null 2>&1 || true
ssh-keyscan -p "$GIT_PORT" localhost 2>/dev/null >> ~/.ssh/known_hosts
admin() { ssh -F /dev/null -o IdentitiesOnly=yes -i "$KEY_DIR/ipaas-admin_ed25519" -p "$GIT_PORT" localhost "$@"; }
admin user set-username admin ipaas-admin >/dev/null 2>&1 || true
admin settings allow-keyless false >/dev/null 2>&1 || true
admin settings anon-access no-access >/dev/null 2>&1 || true
admin user create ipaas-runner >/dev/null 2>&1 || true
admin user add-pubkey ipaas-runner "$(cat "$KEY_DIR/ipaas-runner_ed25519.pub")" >/dev/null 2>&1 || true
echo "users: $(admin user list 2>/dev/null | tr '\n' ' ')"

say "4. gate databases: created by gate.py setup (db:test:prepare) in step 9"

say "5. git hooks in $REPO"
HOOKS_DIR="$(git -C "$REPO" rev-parse --git-path hooks)"; HOOKS_DIR="$(cd "$REPO" && cd "$HOOKS_DIR" && pwd)"
for hook in pre-push prepare-commit-msg; do
  target="$HOOKS_DIR/$hook"
  if [ -e "$target" ] && [ "$(readlink -f "$target")" != "$GATE_DIR/$hook" ]; then
    die "$target exists and is not the gate's; move it aside first"
  fi
  ln -sfn "$GATE_DIR/$hook" "$target"; echo "$hook -> $GATE_DIR/$hook"
done

say "6. local git excludes"
EXCLUDE="$(git -C "$REPO" rev-parse --git-path info/exclude)"; EXCLUDE="$(cd "$REPO" && python3 -c 'import os,sys;print(os.path.abspath(sys.argv[1]))' "$EXCLUDE")"
mkdir -p "$(dirname "$EXCLUDE")"; touch "$EXCLUDE"
for line in '**/.claude/proof/' '/CLAUDE.local.md' '**/.serena/'; do
  grep -qxF "$line" "$EXCLUDE" || echo "$line" >> "$EXCLUDE"
done
echo "excluded: **/.claude/proof/  /CLAUDE.local.md  **/.serena/"

say "7. protocol file"
if [ -f "$REPO/CLAUDE.local.md" ]; then echo "$REPO/CLAUDE.local.md present, left alone"; else cp "$GATE_DIR/CLAUDE.local.md" "$REPO/CLAUDE.local.md"; echo "copied CLAUDE.local.md"; fi

say "8. Claude Code hooks in $REPO/.claude/settings.local.json"
mkdir -p "$REPO/.claude"
python3 - "$REPO/.claude/settings.local.json" "$GATE_DIR" <<'PY'
import json, sys, os
path, gate_dir = sys.argv[1], sys.argv[2]
settings = json.load(open(path)) if os.path.exists(path) else {}
def command(sub): return {"type": "command", "command": f"python3 {gate_dir}/gate.py {sub}"}
hooks = settings.setdefault("hooks", {})
def install(event, matcher, sub):
    entries = [e for e in hooks.get(event, []) if not any("gate.py" in h.get("command", "") for h in e.get("hooks", []))]
    entry = {"hooks": [command(sub)]}
    if matcher: entry["matcher"] = matcher
    hooks[event] = entries + [entry]
install("PostToolUse", "Edit|Write", "launch")
install("Stop", None, "stop")
install("UserPromptSubmit", None, "surface")
json.dump(settings, open(path, "w"), indent=2); open(path, "a").write("\n")
print("registered: PostToolUse Edit|Write -> launch, Stop -> stop, UserPromptSubmit -> surface")
PY

say "9. gate.py setup (worktree, env, databases)"
python3 "$GATE_DIR/gate.py" setup

say "9a. local phase workflow: hook symlink, links into the main clone and every existing worktree"
HOOKS_DIR="$(cd "$REPO" && git rev-parse --git-path hooks)"
case "$(cd "$REPO" && readlink "$HOOKS_DIR/post-checkout" 2>/dev/null)" in
  "$GATE_DIR/post-checkout") ;;
  "") ln -s "$GATE_DIR/post-checkout" "$REPO/$HOOKS_DIR/post-checkout" 2>/dev/null || ln -s "$GATE_DIR/post-checkout" "$HOOKS_DIR/post-checkout"; echo "hook: post-checkout linked" ;;
  *) echo "WARNING: a foreign post-checkout hook exists at $HOOKS_DIR/post-checkout; add: python3 $GATE_DIR/gate.py post-checkout \"\$@\"" ;;
esac
(cd "$REPO" && git worktree list --porcelain | awk '/^worktree /{print $2}') | while IFS= read -r wt; do
  python3 "$GATE_DIR/gate.py" link-worktree "$wt" | sed "s|^|  |"
done

say "9b. checks worktree: one slot for finalize, specs and the live checks (minutes, creates databases)"
python3 "$GATE_DIR/gate.py" setup-checks

say "10. smoke test (read-only)"
(cd "$REPO" && python3 "$GATE_DIR/gate.py" references --name RateLimiter | head -3)
echo
echo "done. The gate is live for $REPO. Protocol: $REPO/CLAUDE.local.md"
