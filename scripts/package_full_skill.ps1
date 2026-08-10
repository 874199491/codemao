$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stage = Join-Path $env:TEMP ("codemao-package-" + $stamp)
$pkg = Join-Path $stage 'codemao_teacher_workbench_template'
New-Item -ItemType Directory -Path $pkg -Force | Out-Null

# Copy the current workbench, then remove runtime and teacher-specific data.
Get-ChildItem $root -Force | Where-Object { $_.Name -notin @('.git','.gitignore','data','tmp','artifacts','.chrome-debug-profile','.chrome-crm-listen-profile','.chrome-dingtalk-profile','node_modules') -and $_.Extension -ne '.zip' } | ForEach-Object {
  Copy-Item $_.FullName $pkg -Recurse -Force
}
$data = Join-Path $pkg 'data'
New-Item -ItemType Directory -Path $data -Force | Out-Null
foreach ($name in @('crm-cookies.example.json','new-class-group-send-cancel-config.json','workbench-update-source.example.json','fetch-new-class-student-list.mjs')) {
  $source = Join-Path $root ('data\' + $name)
  if (Test-Path $source) { Copy-Item $source $data -Force }
}

# Include the complete course-data code, but never ship credentials or caches.
$sourceSkill = Join-Path $root 'skills\codemao-course-data'
$targetSkill = Join-Path $pkg 'skills\codemao-course-data'
New-Item -ItemType Directory -Path (Split-Path $targetSkill) -Force | Out-Null
Copy-Item $sourceSkill $targetSkill -Recurse -Force
if (Test-Path (Join-Path $targetSkill 'codemao-course-data')) {
  Remove-Item (Join-Path $targetSkill 'codemao-course-data') -Recurse -Force
}
Remove-Item (Join-Path $targetSkill '.workbuddy') -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $targetSkill '.codebuddy') -Recurse -Force -ErrorAction SilentlyContinue
foreach ($name in @('config.json','crm_cookies.json','dingtalk_structure.json','table_ids_cache.json','userid_externalid_mapping.json','reset_batches.json','update_batches.json','sync_log.txt','check_result.txt','course42_incomplete.txt','course42_incomplete_detail.txt','all-lessons-incomplete.txt')) {
  Remove-Item (Join-Path $targetSkill $name) -Force -ErrorAction SilentlyContinue
}
Get-ChildItem $targetSkill -Filter '*.json' | Where-Object { $_.Name -ne 'config.template.json' } | Remove-Item -Force
Get-ChildItem $targetSkill -File | Where-Object { $_.Extension -in @('.txt','.log') } | Remove-Item -Force

# Replace embedded MCP values in the two core modules with local environment variables.
foreach ($name in @('sync.py','dingtalk_sync.py')) {
  $path = Join-Path $targetSkill $name
  if (Test-Path $path) {
    $lines = Get-Content $path -Encoding UTF8
    $safe = foreach ($line in $lines) {
      if ($line -match '^\s*MCP_URL\s*=') { 'MCP_URL = os.environ.get("DINGTALK_MCP_URL", "")' }
      elseif ($line -match '^\s*ACCESS_TOKEN\s*=') { 'ACCESS_TOKEN = os.environ.get("DINGTALK_MCP_TOKEN", "")' }
      else { $line }
    }
    Set-Content $path $safe -Encoding UTF8
  }
}

$out = Join-Path $root ("codemao_teacher_workbench_template-" + $stamp + '-full-skill.zip')
Compress-Archive -Path (Join-Path $pkg '*') -DestinationPath $out -CompressionLevel Optimal
Get-Item $out | Select-Object FullName, Length
