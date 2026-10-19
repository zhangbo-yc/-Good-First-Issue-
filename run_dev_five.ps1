$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

$TaskIds = @(
    'ManimCommunity/manim#52'
    'sympy/sympy#5839'
    'dask/dask#8878'
    'ray-project/ray#15501'
    'napari/napari#6251'
)

$RunArgs = @('--output', 'experiments/frozen_20261017/dev_five_v2',
    'run', '--variants', 'A', 'B', 'C', 'D')
foreach ($TaskId in $TaskIds) {
    $RunArgs += @('--task-id', $TaskId)
}

& (Join-Path $ProjectRoot '.venv/Scripts/python.exe') 'research.py' @RunArgs
if ($LASTEXITCODE -ne 0) {
    throw "Research run failed with exit code $LASTEXITCODE"
}
