<#
.SYNOPSIS
    ShadowFrog Installer (PowerShell)
.DESCRIPTION
    Installs ShadowFrog skills and hooks for GitHub Copilot CLI and Claude Code.
    This is the Windows equivalent of install.sh.
.PARAMETER Project
    (Required) Target repository directory to install into.
.PARAMETER Agent
    Target agent conventions: 'copilot' (default) or 'claude'. Copilot uses
    .github/ + copilot-instructions.md; Claude uses .claude/ + CLAUDE.md.
.PARAMETER NoHooks
    Skip hook installation.
.PARAMETER NoContext
    Skip agent-context injection.
.EXAMPLE
    .\install.ps1 -Project C:\path\to\repo
.EXAMPLE
    .\install.ps1 -Project C:\path\to\repo -Agent claude
.EXAMPLE
    .\install.ps1 -Project C:\path\to\repo -NoHooks -NoContext
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, HelpMessage = "Target repository directory to install into.")]
    [string]$Project,

    [Parameter(HelpMessage = "Target agent: 'copilot' (default) or 'claude'.")]
    [ValidateSet('copilot', 'claude')]
    [string]$Agent = 'copilot',

    [switch]$NoHooks,
    [switch]$NoContext
)

# Closest equivalent to bash `set -euo pipefail`. Converts non-terminating
# errors to terminating ones. Native command exit codes (e.g. git) are not
# covered and are handled explicitly where it matters.
$ErrorActionPreference = 'Stop'

# Windows PowerShell 5.1 writes UTF-8 *with* BOM by default, which can break
# JSON parsers and leave invisible bytes in markdown. Write BOM-free UTF-8 via
# the .NET API so output is identical on PS 5.1 and PS 7+.
function Write-Utf8File {
    param([string]$Path, [string]$Content)
    [System.IO.File]::WriteAllText($Path, $Content, [System.Text.UTF8Encoding]::new($false))
}

# --- Script directory & source paths ---
# Multi-arg Join-Path is PS 7+ only, so use [IO.Path]::Combine for PS 5.1 safety.
$ScriptDir = $PSScriptRoot
$SkillsDir = [System.IO.Path]::Combine($ScriptDir, 'skills')
$HooksDir = [System.IO.Path]::Combine($ScriptDir, 'hook-templates')
$ContextFile = [System.IO.Path]::Combine($ScriptDir, 'agent-context.md')

Write-Host "ShadowFrog Installer" -ForegroundColor Green
Write-Host "===================="
Write-Host ""

# --- Validate project directory ---
# [Parameter(Mandatory)] guarantees a value was supplied, but not that it
# points at a real directory; mirror install.sh's explicit existence check.
if (-not (Test-Path -LiteralPath $Project -PathType Container)) {
    Write-Host "Error: $Project does not exist." -ForegroundColor Yellow
    exit 1
}
# Resolve to an absolute path so the git-index relative-path math below is
# correct even when -Project is given as a relative path.
$Project = (Resolve-Path -LiteralPath $Project).Path

Write-Host "Project install ($Agent) -> $Project" -ForegroundColor Blue
Write-Host ""

# --- Resolve per-agent conventions ---
# Copilot CLI uses .github/ and copilot-instructions.md; Claude Code uses
# .claude/ and CLAUDE.md.
if ($Agent -eq 'claude') {
    $SkillsTarget = [System.IO.Path]::Combine($Project, '.claude', 'skills')
    $HooksScriptsDir = [System.IO.Path]::Combine($Project, '.claude', 'hooks', 'scripts')
    $ContextTarget = [System.IO.Path]::Combine($Project, 'CLAUDE.md')
    $SkillsRelative = '.claude\skills'
    $ContextRelative = 'CLAUDE.md'
} else {
    $SkillsTarget = [System.IO.Path]::Combine($Project, '.github', 'skills')
    $HooksScriptsDir = [System.IO.Path]::Combine($Project, '.github', 'hooks', 'scripts')
    $ContextTarget = [System.IO.Path]::Combine($Project, '.github', 'copilot-instructions.md')
    $SkillsRelative = '.github\skills'
    $ContextRelative = '.github\copilot-instructions.md'
}

# --- Copy skills ---
# Project skills are committed, not symlinked, so sync = remove old + copy fresh.
Write-Host "Installing skills to $SkillsRelative\..." -ForegroundColor Blue

if (-not (Test-Path -LiteralPath $SkillsTarget)) {
    New-Item -Path $SkillsTarget -ItemType Directory -Force | Out-Null
}

# -LiteralPath keeps paths containing [, ], or * from being treated as wildcards.
Get-ChildItem -LiteralPath $SkillsDir -Directory -Filter 'shadow-frog*' | ForEach-Object {
    $skillName = $_.Name
    $target = [System.IO.Path]::Combine($SkillsTarget, $skillName)

    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
    Copy-Item -LiteralPath $_.FullName -Destination $target -Recurse
    Write-Host "  + $skillName" -ForegroundColor Green
}

# --- Copy hooks (default: yes) ---
if (-not $NoHooks) {
    Write-Host ""
    Write-Host "Installing hooks..." -ForegroundColor Blue

    if (-not (Test-Path -LiteralPath $HooksScriptsDir)) {
        New-Item -Path $HooksScriptsDir -ItemType Directory -Force | Out-Null
    }

    $hookScriptsSrc = [System.IO.Path]::Combine($HooksDir, 'scripts')
    Get-ChildItem -LiteralPath $hookScriptsSrc -Filter '*.sh' | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $HooksScriptsDir -Force
    }

    # `chmod +x` has no Windows equivalent. Set the executable bit in the Git
    # index instead, so the scripts keep their +x mode when committed and
    # checked out on Unix/cloud runners. Best-effort: never fail the install.
    # Note: unlike bash's `chmod +x`, `git update-index --add` also *stages*
    # these scripts (they show as `A` in `git status`); the rest of the install
    # stays unstaged and is picked up by the printed `git add` next-step.
    $isGitRepo = $false
    try {
        $gitCheck = git -C $Project rev-parse --is-inside-work-tree 2>$null
        if ($gitCheck -eq 'true') { $isGitRepo = $true }
    } catch { Write-Verbose "git repo check failed: $_" }

    if ($isGitRepo) {
        $hookScriptFiles = Get-ChildItem -LiteralPath $HooksScriptsDir -Filter '*.sh'
        foreach ($f in $hookScriptFiles) {
            $relativePath = $f.FullName.Substring($Project.TrimEnd('\', '/').Length + 1) -replace '\\', '/'
            try {
                git -C $Project update-index --add --chmod=+x -- $relativePath 2>$null
            } catch { Write-Verbose "git update-index failed for ${relativePath}: $_" }
        }
    }

    if ($Agent -eq 'claude') {
        # Claude Code: hooks live in .claude/settings.json, referencing the
        # scripts above via ${CLAUDE_PROJECT_DIR}. Merge (not overwrite) to
        # preserve any existing project hooks.
        $claudeSettingsPath = [System.IO.Path]::Combine($Project, '.claude', 'settings.json')
        $templatePath = [System.IO.Path]::Combine($HooksDir, 'claude-settings.json')

        $template = Get-Content -LiteralPath $templatePath -Raw | ConvertFrom-Json

        if (Test-Path -LiteralPath $claudeSettingsPath) {
            try {
                $settings = Get-Content -LiteralPath $claudeSettingsPath -Raw | ConvertFrom-Json
            } catch {
                Write-Host "  x Existing $claudeSettingsPath is not valid JSON; refusing to overwrite." -ForegroundColor Red
                exit 1
            }
        } else {
            $settingsDir = Split-Path $claudeSettingsPath -Parent
            if (-not (Test-Path -LiteralPath $settingsDir)) {
                New-Item -Path $settingsDir -ItemType Directory -Force | Out-Null
            }
            $settings = [PSCustomObject]@{}
        }

        # Ensure hooks property exists and is an object.
        if (-not ($settings.PSObject.Properties.Name -contains 'hooks')) {
            $settings | Add-Member -NotePropertyName 'hooks' -NotePropertyValue ([PSCustomObject]@{})
        }

        # Merge template hooks into settings, skipping handlers whose command
        # already exists for that event (idempotent re-apply).
        foreach ($eventProp in $template.hooks.PSObject.Properties) {
            $eventName = $eventProp.Name
            $templateGroups = @($eventProp.Value)

            if (-not ($settings.hooks.PSObject.Properties.Name -contains $eventName)) {
                $settings.hooks | Add-Member -NotePropertyName $eventName -NotePropertyValue @()
            }

            [System.Collections.ArrayList]$existingGroups = @($settings.hooks.$eventName)

            # Collect commands already present for this event.
            $existingCmds = @{}
            foreach ($g in $existingGroups) {
                $hooksProp = $g.PSObject.Properties['hooks']
                if ($hooksProp) {
                    foreach ($h in @($hooksProp.Value)) {
                        $cmd = ''
                        $cmdProp = $h.PSObject.Properties['command']
                        if ($cmdProp) { $cmd = $cmdProp.Value }
                        $existingCmds[$cmd] = $true
                    }
                }
            }

            # Add new groups containing only the non-duplicate handlers.
            foreach ($group in $templateGroups) {
                $newHandlers = @()
                $hooksProp = $group.PSObject.Properties['hooks']
                if ($hooksProp) {
                    foreach ($h in @($hooksProp.Value)) {
                        $cmd = ''
                        $cmdProp = $h.PSObject.Properties['command']
                        if ($cmdProp) { $cmd = $cmdProp.Value }
                        if (-not $existingCmds.ContainsKey($cmd)) {
                            $newHandlers += $h
                        }
                    }
                }
                if ($newHandlers.Count -gt 0) {
                    $merged = [PSCustomObject]@{}
                    foreach ($prop in $group.PSObject.Properties) {
                        if ($prop.Name -eq 'hooks') {
                            $merged | Add-Member -NotePropertyName 'hooks' -NotePropertyValue $newHandlers
                        } else {
                            $merged | Add-Member -NotePropertyName $prop.Name -NotePropertyValue $prop.Value
                        }
                    }
                    $existingGroups.Add($merged) | Out-Null
                }
            }

            $settings.hooks.$eventName = @($existingGroups)
        }

        # Use a high depth so deeply nested existing settings survive the
        # round-trip (install.sh's Python json has no depth cap; ConvertTo-Json
        # silently truncates past -Depth). Capture the truncation warning and
        # refuse to write rather than emit lossy JSON — mirrors the invalid-JSON
        # guard above.
        $jsonOutput = $settings | ConvertTo-Json -Depth 100 -WarningVariable jsonWarn -WarningAction SilentlyContinue
        if ($jsonWarn) {
            Write-Host "  x Existing $claudeSettingsPath is nested too deeply to merge safely; refusing to write to avoid data loss." -ForegroundColor Red
            exit 1
        }
        Write-Utf8File -Path $claudeSettingsPath -Content "$jsonOutput`n"
        Write-Host "  + hooks -> $claudeSettingsPath (+ scripts)" -ForegroundColor Green
    } else {
        # Copilot CLI: hooks.json + scripts under .github/hooks/
        $projectHooksDir = [System.IO.Path]::Combine($Project, '.github', 'hooks')
        if (-not (Test-Path -LiteralPath $projectHooksDir)) {
            New-Item -Path $projectHooksDir -ItemType Directory -Force | Out-Null
        }

        $hooksJsonTarget = [System.IO.Path]::Combine($projectHooksDir, 'hooks.json')

        # Windows represents symlinks/junctions as reparse points; remove one
        # if present so Copy-Item writes a real file (matches bash `[ -L ]`).
        if (Test-Path -LiteralPath $hooksJsonTarget) {
            $item = Get-Item -LiteralPath $hooksJsonTarget -Force
            if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                Remove-Item -LiteralPath $hooksJsonTarget -Force
            }
        }

        Copy-Item -LiteralPath ([System.IO.Path]::Combine($HooksDir, 'shadow-frog-hooks.json')) -Destination $hooksJsonTarget -Force
        Write-Host "  + hooks -> $projectHooksDir" -ForegroundColor Green
    }
}

# --- Inject agent-context (default: yes) ---
if ((-not $NoContext) -and (Test-Path -LiteralPath $ContextFile)) {
    Write-Host ""
    Write-Host "Injecting agent-context into $ContextRelative..." -ForegroundColor Blue

    $contextDir = Split-Path -Parent $ContextTarget
    if ($contextDir -and -not (Test-Path -LiteralPath $contextDir)) {
        New-Item -Path $contextDir -ItemType Directory -Force | Out-Null
    }

    $markerStart = '<!-- shadowfrog:agent-context -->'
    $markerEnd = '<!-- /shadowfrog:agent-context -->'
    # TrimEnd to match bash, where $(cat file) strips trailing newlines.
    $contextContent = (Get-Content -LiteralPath $ContextFile -Raw).TrimEnd("`r", "`n")

    # Remove any existing block (idempotent re-apply), then build the final
    # content in memory for a single atomic write. (?s) = DOTALL; (?:\r?\n)*
    # tolerates both LF and CRLF line endings.
    $cleaned = ''
    if (Test-Path -LiteralPath $ContextTarget) {
        $existing = Get-Content -LiteralPath $ContextTarget -Raw
        $pattern = '(?s)(?:\r?\n)*<!-- shadowfrog:agent-context -->.*?<!-- /shadowfrog:agent-context -->(?:\r?\n)*'
        $cleaned = [regex]::Replace($existing, $pattern, "`n").Trim()
    }

    $newBlock = "$markerStart`n$contextContent`n$markerEnd`n"

    if ($cleaned.Length -gt 0) {
        Write-Utf8File -Path $ContextTarget -Content "$cleaned`n`n$newBlock"
    } else {
        Write-Utf8File -Path $ContextTarget -Content $newBlock
    }

    Write-Host "  + agent-context -> $ContextTarget" -ForegroundColor Green
}

# --- Completion message & next steps ---
Write-Host ""
Write-Host "Project install complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Commit and push so future agent sessions find the skills:"
Write-Host "     cd $Project"

if ($Agent -eq 'claude') {
    $gitAddPaths = '.claude/skills/'
    if (-not $NoHooks) { $gitAddPaths += ' .claude/settings.json .claude/hooks/' }
    if (-not $NoContext) { $gitAddPaths += ' CLAUDE.md' }
} else {
    $gitAddPaths = '.github/skills/'
    if (-not $NoHooks) { $gitAddPaths += ' .github/hooks/' }
    if (-not $NoContext) { $gitAddPaths += ' .github/copilot-instructions.md' }
}

Write-Host "     git add $gitAddPaths"
Write-Host "     git commit -m 'Add ShadowFrog skills, hooks, and context'"
Write-Host ""
Write-Host "  2. Run /shadow-frog-init in your agent session to create the shadow"
Write-Host ""
Write-Host "  3. To run dream via delegate:"
Write-Host "     /delegate Run /shadow-frog-dream on this codebase"
Write-Host ""

# Explicit clean exit: without it, $LASTEXITCODE could retain a non-zero value
# from an earlier native command (e.g. a git check on a non-git directory).
exit 0
