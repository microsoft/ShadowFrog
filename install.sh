#!/usr/bin/env bash
# ShadowFrog Installer
# Installs ShadowFrog skills and hooks for GitHub Copilot CLI and Claude Code.
#
# Usage:
#   ./install.sh --project /path/to/repo  Install skills + hooks + context into a repo
#   ./install.sh --project /path/to/repo --no-hooks --no-context  Skip hooks/context

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS_DIR="$SCRIPT_DIR/skills"
HOOKS_DIR="$SCRIPT_DIR/hook-templates"
CONTEXT_FILE="$SCRIPT_DIR/agent-context.md"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse arguments
PROJECT_DIR=""
NO_HOOKS=false
NO_CONTEXT=false
AGENT="copilot"   # which agent's conventions to target: copilot (default) | claude
USAGE="Usage: $0 --project <repo-dir> [--agent copilot|claude] [--no-hooks] [--no-context]"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --agent)
            if [[ $# -lt 2 ]]; then
                echo "Error: --agent requires a value (copilot or claude)." >&2
                echo "$USAGE" >&2
                exit 1
            fi
            AGENT="$2"
            shift 2
            ;;
        --project)
            if [[ $# -lt 2 ]]; then
                echo "Error: --project requires a directory argument." >&2
                echo "$USAGE" >&2
                exit 1
            fi
            PROJECT_DIR="$2"
            shift 2
            ;;
        --no-hooks)
            NO_HOOKS=true
            shift
            ;;
        --no-context)
            NO_CONTEXT=true
            shift
            ;;
        -h|--help)
            echo "$USAGE"
            echo ""
            echo "  --agent AGENT     Target agent conventions: 'copilot' (default) or"
            echo "                    'claude'. Copilot uses .github/ + copilot-instructions.md;"
            echo "                    Claude uses .claude/ + CLAUDE.md."
            echo "  --project DIR     (Required) Install skills, hooks, and context into DIR"
            echo "                    for local + cloud agent use."
            echo "  --no-hooks        Skip hook installation."
            echo "  --no-context      Skip agent-context injection."
            exit 0
            ;;
        *)
            echo -e "${YELLOW}Unknown argument: $1${NC}"
            echo "$USAGE"
            exit 1
            ;;
    esac
done

if [[ "$AGENT" != "copilot" && "$AGENT" != "claude" ]]; then
    echo "Error: --agent must be 'copilot' or 'claude' (got '$AGENT')." >&2
    echo "$USAGE" >&2
    exit 1
fi

if [ -z "$PROJECT_DIR" ]; then
    echo "Error: --project <repo-dir> is required." >&2
    echo "ShadowFrog installs into a specific repository, not globally — its" >&2
    echo "shadow-edit hooks should only fire inside projects you opted in." >&2
    echo "$USAGE" >&2
    exit 1
fi

echo -e "${GREEN}ShadowFrog Installer${NC}"
echo "===================="
echo ""

# =============================================================================
# Project install: copy skills, hooks, and context into a repo
# =============================================================================

if [ -n "$PROJECT_DIR" ]; then
    if [ ! -d "$PROJECT_DIR" ]; then
        echo -e "${YELLOW}Error: $PROJECT_DIR does not exist.${NC}"
        exit 1
    fi

    echo -e "${BLUE}Project install ($AGENT) → $PROJECT_DIR${NC}"
    echo ""

    # Resolve per-agent conventions. Copilot CLI uses .github/ and
    # copilot-instructions.md; Claude Code uses .claude/ and CLAUDE.md.
    if [ "$AGENT" = "claude" ]; then
        SKILLS_TARGET="$PROJECT_DIR/.claude/skills"
        HOOKS_SCRIPTS_DIR="$PROJECT_DIR/.claude/hooks/scripts"
        CONTEXT_TARGET="$PROJECT_DIR/CLAUDE.md"
    else
        SKILLS_TARGET="$PROJECT_DIR/.github/skills"
        HOOKS_SCRIPTS_DIR="$PROJECT_DIR/.github/hooks/scripts"
        CONTEXT_TARGET="$PROJECT_DIR/.github/copilot-instructions.md"
    fi

    # --- Copy skills ---
    echo -e "${BLUE}Installing skills to ${SKILLS_TARGET#"$PROJECT_DIR"/}/...${NC}"
    mkdir -p "$SKILLS_TARGET"
    for skill_dir in "$SKILLS_DIR"/shadow-frog*; do
        [[ -e "$skill_dir" ]] || continue
        skill_name=$(basename "$skill_dir")
        target="$SKILLS_TARGET/$skill_name"

        # Sync: remove old and copy fresh (project skills are committed, not symlinked)
        rm -rf "$target"
        cp -r "$skill_dir" "$target"
        echo -e "  ${GREEN}✓${NC} $skill_name → $target"
    done

    # --- Copy hooks (default: yes) ---
    if [ "$NO_HOOKS" = false ]; then
        echo ""
        echo -e "${BLUE}Installing hooks...${NC}"
        mkdir -p "$HOOKS_SCRIPTS_DIR"
        cp -f "$HOOKS_DIR"/scripts/*.sh "$HOOKS_SCRIPTS_DIR/" || { echo -e "  ${RED}✗${NC} Failed to copy hook scripts"; exit 1; }
        chmod +x "$HOOKS_SCRIPTS_DIR"/*.sh

        if [ "$AGENT" = "claude" ]; then
            # Claude Code: hooks live in .claude/settings.json (the shared,
            # committable project settings), referencing the scripts above via
            # ${CLAUDE_PROJECT_DIR}. Merge (not overwrite) to preserve any
            # existing project hooks.
            CLAUDE_SETTINGS="$PROJECT_DIR/.claude/settings.json"
            SF_CLAUDE_SETTINGS="$CLAUDE_SETTINGS" SF_CLAUDE_TEMPLATE="$HOOKS_DIR/claude-settings.json" python3 - <<'PYEOF' || { echo -e "  ${RED}✗${NC} Failed to write .claude/settings.json"; exit 1; }
import json, os

settings_path = os.environ["SF_CLAUDE_SETTINGS"]
template_path = os.environ["SF_CLAUDE_TEMPLATE"]

with open(template_path) as f:
    template = json.load(f)

if os.path.isfile(settings_path):
    with open(settings_path) as f:
        try:
            settings = json.load(f)
        except json.JSONDecodeError:
            raise SystemExit(f"Existing {settings_path} is not valid JSON; refusing to overwrite.")
else:
    settings = {}

settings.setdefault("hooks", {})

def command_of(handler):
    return (handler or {}).get("command", "")

for event, groups in template["hooks"].items():
    existing_groups = settings["hooks"].setdefault(event, [])
    existing_cmds = {
        command_of(h)
        for g in existing_groups
        for h in g.get("hooks", [])
    }
    for group in groups:
        new_handlers = [h for h in group.get("hooks", []) if command_of(h) not in existing_cmds]
        if new_handlers:
            merged = dict(group)
            merged["hooks"] = new_handlers
            existing_groups.append(merged)

with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")
PYEOF
            echo -e "  ${GREEN}✓${NC} hooks → $CLAUDE_SETTINGS (+ scripts)"
        else
            # Copilot CLI: hooks.json + scripts under .github/hooks/
            PROJECT_HOOKS_DIR="$PROJECT_DIR/.github/hooks"
            [ -L "$PROJECT_HOOKS_DIR/hooks.json" ] && rm "$PROJECT_HOOKS_DIR/hooks.json"
            cp -f "$HOOKS_DIR/shadow-frog-hooks.json" "$PROJECT_HOOKS_DIR/hooks.json" || { echo -e "  ${RED}✗${NC} Failed to copy hooks.json"; exit 1; }
            echo -e "  ${GREEN}✓${NC} hooks → $PROJECT_HOOKS_DIR"
        fi
    fi

    # --- Inject agent-context (default: yes) ---
    if [ "$NO_CONTEXT" = false ] && [ -f "$CONTEXT_FILE" ]; then
        echo ""
        echo -e "${BLUE}Injecting agent-context into ${CONTEXT_TARGET#"$PROJECT_DIR"/}...${NC}"
        INSTRUCTIONS_FILE="$CONTEXT_TARGET"
        mkdir -p "$(dirname "$INSTRUCTIONS_FILE")"

        MARKER_START="<!-- shadowfrog:agent-context -->"
        MARKER_END="<!-- /shadowfrog:agent-context -->"
        CONTEXT_CONTENT=$(cat "$CONTEXT_FILE")

        # Remove existing block if present (idempotent re-apply).
        # Pass the path via the environment (not string interpolation) so
        # project paths containing quotes don't break the Python source.
        if [ -f "$INSTRUCTIONS_FILE" ]; then
            SF_INSTRUCTIONS_FILE="$INSTRUCTIONS_FILE" python3 - <<'PYEOF'
import os, re
path = os.environ["SF_INSTRUCTIONS_FILE"]
with open(path) as f:
    content = f.read()
pattern = r'\n*<!-- shadowfrog:agent-context -->.*?<!-- /shadowfrog:agent-context -->\n*'
content = re.sub(pattern, '\n', content, flags=re.DOTALL).strip()
with open(path, 'w') as f:
    f.write(content + '\n')
PYEOF
        fi

        # Append marked block
        {
            [ -f "$INSTRUCTIONS_FILE" ] && [ -s "$INSTRUCTIONS_FILE" ] && echo ""
            echo "$MARKER_START"
            echo "$CONTEXT_CONTENT"
            echo "$MARKER_END"
        } >> "$INSTRUCTIONS_FILE"
        echo -e "  ${GREEN}✓${NC} agent-context → $INSTRUCTIONS_FILE"
    fi

    echo ""
    echo -e "${GREEN}Project install complete!${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. Commit and push so future agent sessions find the skills:"
    echo "     cd $PROJECT_DIR"
    if [ "$AGENT" = "claude" ]; then
        GIT_ADD_PATHS=".claude/skills/"
        [ "$NO_HOOKS" = false ] && GIT_ADD_PATHS="$GIT_ADD_PATHS .claude/settings.json .claude/hooks/"
        [ "$NO_CONTEXT" = false ] && GIT_ADD_PATHS="$GIT_ADD_PATHS CLAUDE.md"
    else
        GIT_ADD_PATHS=".github/skills/"
        [ "$NO_HOOKS" = false ] && GIT_ADD_PATHS="$GIT_ADD_PATHS .github/hooks/"
        [ "$NO_CONTEXT" = false ] && GIT_ADD_PATHS="$GIT_ADD_PATHS .github/copilot-instructions.md"
    fi
    echo "     git add $GIT_ADD_PATHS"
    echo "     git commit -m 'Add ShadowFrog skills, hooks, and context'"
    echo ""
    echo "  2. Run /shadow-frog-init in your agent session to create the shadow"
    echo ""
    echo "  3. To run dream via delegate:"
    echo "     /delegate Run /shadow-frog-dream on this codebase"
    echo ""
    exit 0
fi
