param(
    [string]$Python = "python",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$BuildArguments = @("--format", "exe", "--python", $Python)
if ($SkipInstall) { $BuildArguments += "--skip-install" }

# Native Windows/Linux/macOS packaging is centralized in the repository-root
# build.py. This wrapper keeps the familiar Windows PowerShell entry point.
& $Python (Join-Path $PSScriptRoot "build.py") @BuildArguments
exit $LASTEXITCODE
