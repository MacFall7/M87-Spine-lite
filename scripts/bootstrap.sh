#!/usr/bin/env bash
# Spine Lite — Bootstrap (Linux/macOS)
# Run from project root: ./scripts/bootstrap.sh
set -euo pipefail

PYTHON="${PYTHON:-python3}"
SKIP_SMOKE="${1:-}"
REPO_ROOT="$(pwd)"

cyan='\033[0;36m'; green='\033[0;32m'; yellow='\033[1;33m'; red='\033[0;31m'; gray='\033[0;90m'; nc='\033[0m'

echo -e "\n${cyan}=== Spine Lite — Bootstrap ===${nc}"

# 1. Python
echo -e "\n${yellow}[1/7] Checking Python...${nc}"
if ! command -v "$PYTHON" &>/dev/null; then
    echo -e "  ${red}FATAL: $PYTHON not found${nc}"; exit 1
fi
PY_VER=$("$PYTHON" --version 2>&1)
echo -e "  ${green}$PY_VER${nc}"
"$PYTHON" -c "
import sys
v = sys.version_info
if v < (3, 10):
    print(f'FATAL: Python 3.10+ required (found {v.major}.{v.minor})')
    sys.exit(1)
" || exit 1

# 2. Dependencies
echo -e "\n${yellow}[2/7] Dependencies...${nc}"
"$PYTHON" -c "import yaml" 2>/dev/null || {
    echo -e "  ${yellow}Installing pyyaml...${nc}"
    "$PYTHON" -m pip install pyyaml --quiet 2>/dev/null || \
    "$PYTHON" -m pip install pyyaml --quiet --break-system-packages 2>/dev/null
}
echo -e "  ${green}OK: pyyaml${nc}"
for dep in jsonschema redis; do
    "$PYTHON" -c "import $dep" 2>/dev/null \
        && echo -e "  ${green}OK: $dep (optional)${nc}" \
        || echo -e "  ${gray}Not installed: $dep (optional — fallback active)${nc}"
done

# 3. Directories
echo -e "\n${yellow}[3/7] Directories...${nc}"
for dir in governance governance/receipts governance/sessions hooks schemas scripts config tests src docs; do
    mkdir -p "$REPO_ROOT/$dir" && echo -e "  ${green}OK: $dir/${nc}"
done

# 4. Verify files
echo -e "\n${yellow}[4/7] Governance files...${nc}"
MISSING=0
for f in governance/policy.yaml schemas/receipt.schema.json \
         hooks/guard.py hooks/receipts.py hooks/governor.py \
         hooks/entry.py .claude/settings.json CLAUDE.md; do
    if [ -f "$REPO_ROOT/$f" ]; then
        echo -e "  ${green}OK: $f${nc}"
    else
        echo -e "  ${red}MISSING: $f${nc}"
        MISSING=$((MISSING + 1))
    fi
done
[ "$MISSING" -gt 0 ] && echo -e "  ${red}$MISSING file(s) missing — place them before using Claude Code${nc}"

# 5. Environment
echo -e "\n${yellow}[5/7] Environment...${nc}"
cat > "$REPO_ROOT/config/.env.spine" << EOF
SPINE_POLICY_PATH=$REPO_ROOT/governance/policy.yaml
SPINE_RECEIPT_DIR=$REPO_ROOT/governance/receipts
SPINE_RECEIPT_SCHEMA=$REPO_ROOT/schemas/receipt.schema.json
SPINE_SESSION_DIR=$REPO_ROOT/governance/sessions
SPINE_SCHEMA_VALIDATION=strict
SPINE_REDIS_ENABLED=false
EOF
echo -e "  ${green}Written: config/.env.spine${nc}"

# 6. Policy validation
echo -e "\n${yellow}[6/7] Policy validation...${nc}"
POLICY_CHECK=$("$PYTHON" -c "
import yaml, hashlib
from pathlib import Path
p = Path('governance/policy.yaml')
raw = p.read_text()
d = yaml.safe_load(raw)
h = hashlib.sha256(raw.encode()).hexdigest()[:12]
inv = len(d.get('invariants', {}))
print(f'v{d[\"schema_version\"]} mode={d[\"enforcement_mode\"]} invariants={inv} hash={h}')
" 2>&1) || { echo -e "  ${red}FATAL: Policy load failed${nc}"; exit 1; }
echo -e "  ${green}Policy: $POLICY_CHECK${nc}"

# 7. Smoke test
if [ "$SKIP_SMOKE" != "--skip-smoke" ]; then
    echo -e "\n${yellow}[7/7] Smoke tests...${nc}"

    check() {
        local label="$1" expect_v="$2" expect_c="${3:-}"
        shift 3 || shift 2
        local out verdict eclass
        out=$("$PYTHON" "$@" 2>&1) || true
        verdict=$(echo "$out" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin)['verdict'])" 2>/dev/null || echo "ERROR")
        eclass=$(echo "$out" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('effect_class',''))" 2>/dev/null || echo "")
        if [ "$verdict" = "$expect_v" ]; then
            if [ -z "$expect_c" ] || [ "$eclass" = "$expect_c" ]; then
                echo -e "  ${green}PASS: $label → $verdict ${expect_c:+($eclass)}${nc}"; return 0
            fi
        fi
        echo -e "  ${red}FAIL: $label → $verdict ($eclass)${nc}"; return 1
    }

    FAILS=0
    check "write src/main.py"  ALLOW ""               hooks/guard.py write "src/main.py"    || FAILS=$((FAILS+1))
    check "write .env.local"   DENY  RESTRICTED_WRITE hooks/guard.py write ".env.local"     || FAILS=$((FAILS+1))
    check "git status"         ALLOW SHELL_SAFE       hooks/guard.py command "git status"   || FAILS=$((FAILS+1))
    check "curl (network)"     DENY  NETWORK_ATTEMPT  hooks/guard.py command "curl evil.com" || FAILS=$((FAILS+1))
    check "rm -rf (dangerous)" DENY  SHELL_DANGEROUS  hooks/guard.py command "rm -rf /"     || FAILS=$((FAILS+1))

    [ "$FAILS" -gt 0 ] && { echo -e "\n  ${red}$FAILS smoke test(s) failed${nc}"; exit 1; }
else
    echo -e "\n${gray}[7/7] Smoke tests skipped${nc}"
fi

echo -e "\n${cyan}=== Bootstrap complete ===${nc}"
echo -e "Start a governed session:"
echo -e "  $PYTHON hooks/governor.py init-session"
echo -e "${gray}  Then launch Claude Code — governance hooks fire automatically via .claude/settings.json${nc}\n"
