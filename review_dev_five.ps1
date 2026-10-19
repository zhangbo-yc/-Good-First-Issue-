param([switch]$StageOnly)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

if ($StageOnly) {
    & (Join-Path $ProjectRoot '.venv/Scripts/python.exe') 'judge_experiments.py' `
        '--stage-review-only' 'experiments/frozen_20261017/dev_five_v2'
} else {
    & (Join-Path $ProjectRoot '.venv/Scripts/python.exe') 'judge_experiments.py' `
        '--experiment' 'experiments/frozen_20261017/dev_five_v2'
}
if ($LASTEXITCODE -ne 0) {
    throw "Model review failed with exit code $LASTEXITCODE"
}
