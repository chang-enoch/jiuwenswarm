param(
    [string]$PythonExe = 'C:/Users/newcs/data/code/jiuwenclaw-xiaoyao/jiuwenclaw/.venv/Scripts/python.exe',
    [string]$CoreRoot = 'C:/Users/newcs/data/code/agent-core-br_0.1.16.post2.hotfix/.security'
)

$ErrorActionPreference = 'Stop'
$swarmRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw "Python not found: $PythonExe" }
if (-not (Test-Path -LiteralPath (Join-Path $CoreRoot 'openjiuwen/harness/security/skill_install.py'))) {
    throw "Use the security implementation worktree for CoreRoot: $CoreRoot"
}
$runRoot = Join-Path ([IO.Path]::GetTempPath()) ('xiaoyi-skill-install-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $runRoot | Out-Null
$savedPythonPath = $env:PYTHONPATH
$savedDataDir = $env:JIUWENSWARM_DATA_DIR
$testExitCode = 1
Push-Location -LiteralPath $swarmRoot
try {
    $env:PYTHONPATH = "$CoreRoot;$swarmRoot"
    $env:JIUWENSWARM_DATA_DIR = Join-Path $runRoot 'data'
    Write-Output 'Component tests: real skill installer, simulated desktop/cloud decisions, isolated files.'
    Write-Output "Test artifacts: $runRoot"
    & $PythonExe -m pytest -o addopts= -p no:cacheprovider `
        tests/unit_tests/agentserver/test_behavior_security.py `
        -k 'actual_builtin_install or install_confirmation or real_core_callback_chain_install or skillnet_worker' `
        --basetemp (Join-Path $runRoot 'pytest') -v --tb=short
    $testExitCode = $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $savedPythonPath
    $env:JIUWENSWARM_DATA_DIR = $savedDataDir
    Pop-Location
}
if ($testExitCode -ne 0) { throw "Skill installation checks failed (exit $testExitCode)." }
Write-Output 'PASS: installation checkpoint checks. This does not prove desktop UI or real cloud acceptance.'
