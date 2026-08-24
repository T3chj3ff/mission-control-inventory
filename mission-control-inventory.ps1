param(
  [Parameter(Mandatory = $true)]
  [string[]] $Root,
  [string] $Output = (Join-Path (Get-Location) "mission-control-projects.json"),
  [string] $Machine = $env:COMPUTERNAME
)

$ErrorActionPreference = "Stop"
$resolvedRoots = foreach ($item in $Root) {
  $resolved = Resolve-Path -LiteralPath $item
  if (-not (Test-Path -LiteralPath $resolved -PathType Container)) {
    throw "Not a folder: $item"
  }
  $resolved.Path
}

$projectFolders = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
foreach ($rootPath in $resolvedRoots) {
  if (Test-Path -LiteralPath (Join-Path $rootPath ".git")) {
    [void] $projectFolders.Add($rootPath)
  }
  Get-ChildItem -LiteralPath $rootPath -Directory -Filter ".git" -Recurse -Force -ErrorAction SilentlyContinue |
    ForEach-Object { [void] $projectFolders.Add($_.Parent.FullName) }
}

$filteredFolders = $projectFolders | Where-Object { $_ -notmatch '[\\/](node_modules|\.next|dist|build|work)[\\/]' }
$projects = foreach ($folder in ($filteredFolders | Sort-Object)) {
  $gitSafeFolder = $folder -replace '\\', '/'
  $remote = ""
  if (Get-Command git -ErrorAction SilentlyContinue) {
    $remote = (& git -c "safe.directory=$gitSafeFolder" -C $folder remote get-url origin 2>$null | Select-Object -First 1)
    if (-not $remote) { $remote = "" }
  }
  $branch = (& git -c "safe.directory=$gitSafeFolder" -C $folder rev-parse --abbrev-ref HEAD 2>$null | Select-Object -First 1)
  $dirtyFiles = @(& git -c "safe.directory=$gitSafeFolder" -C $folder status --porcelain 2>$null).Count
  $commitSeconds = (& git -c "safe.directory=$gitSafeFolder" -C $folder log -1 --format=%ct 2>$null | Select-Object -First 1)
  $lastCommitAt = if ($commitSeconds -match '^\d+$') { [int64]$commitSeconds * 1000 } else { $null }
  $stack = [System.Collections.Generic.List[string]]::new()
  if (Test-Path -LiteralPath (Join-Path $folder "package.json")) { $stack.Add("Node / JavaScript") }
  if ((Test-Path -LiteralPath (Join-Path $folder "pyproject.toml")) -or (Test-Path -LiteralPath (Join-Path $folder "requirements.txt"))) { $stack.Add("Python") }
  if (Test-Path -LiteralPath (Join-Path $folder "Cargo.toml")) { $stack.Add("Rust") }
  if (Test-Path -LiteralPath (Join-Path $folder "go.mod")) { $stack.Add("Go") }
  if (Get-ChildItem -LiteralPath $folder -Filter "*.sln" -File -ErrorAction SilentlyContinue) { $stack.Add(".NET") }
  [ordered]@{
    name = Split-Path -Leaf $folder
    summary = "Imported from a local Git working folder"
    status = "active"
    machine = $Machine
    localPath = $folder
    repoUrl = $remote
    nextAction = "Review current status and set the next action"
    gitBranch = $branch
    gitDirty = $dirtyFiles
    lastCommitAt = $lastCommitAt
    techStack = ($stack -join ", ")
  }
}

$projectList = @($projects)
$payload = [ordered]@{
  version = 3
  exportedAt = (Get-Date).ToUniversalTime().ToString("o")
  projects = $projectList
}
$payload | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Output -Encoding utf8
Write-Host "Wrote $($projectList.Count) projects to $Output"
