# 把本地 rag-gate 推到 GitHub（需要你自己的 token；沙盒里跑不了）
#
#   .\publish_github.ps1 -Token ghp_xxx
#   # 或者
#   $env:GITHUB_TOKEN = "ghp_xxx"; .\publish_github.ps1
#
# 可重复执行：仓库/远程/标签已存在就跳过。
# 这个文件是纯 ASCII 以外含少量中文注释 —— 保存时保持 UTF-8 BOM，别用记事本另存。

[CmdletBinding()]
param(
    [string]$Token = $env:GITHUB_TOKEN,
    [string]$Owner = '1585164493a',
    [string]$Repo = 'rag-gate',
    [string]$Description = 'Turn RAG retrieval quality into a CI gate: compare against a baseline, fail the PR when it regresses.',
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'

if (-not $Token) {
    Write-Host "缺少 token。用法：" -ForegroundColor Yellow
    Write-Host "  .\publish_github.ps1 -Token ghp_xxx"
    Write-Host "  或先 `$env:GITHUB_TOKEN = 'ghp_xxx'"
    exit 2
}

$headers = @{
    Authorization          = "Bearer $Token"
    Accept                 = 'application/vnd.github+json'
    'X-GitHub-Api-Version' = '2022-11-28'
    'User-Agent'           = 'rag-gate-publish'
}

Write-Host "== 1/4 校验 token =="
try {
    $me = Invoke-RestMethod -Uri 'https://api.github.com/user' -Headers $headers -TimeoutSec 30
} catch {
    Write-Host "token 无效或网络不通：$($_.Exception.Message)" -ForegroundColor Red
    exit 2
}
Write-Host "   已登录：$($me.login)"

Write-Host "== 2/4 建仓库 $Owner/$Repo =="
$body = @{
    name        = $Repo
    description = $Description
    private     = $false
    has_issues  = $true
    has_wiki    = $false
    topics      = @('rag', 'llm', 'eval', 'testing', 'ci', 'retrieval', 'regression-testing')
} | ConvertTo-Json

try {
    $repoInfo = Invoke-RestMethod -Method Post -Uri 'https://api.github.com/user/repos' `
        -Headers $headers -Body $body -ContentType 'application/json' -TimeoutSec 60
    Write-Host "   已创建：$($repoInfo.html_url)"
} catch {
    $detail = $_.Exception.Message
    if ($detail -match '422') {
        Write-Host "   仓库已存在，跳过创建" -ForegroundColor Yellow
    } else {
        Write-Host "   建仓库失败：$detail" -ForegroundColor Red
        exit 2
    }
}

Write-Host "== 3/4 推代码 =="
& git -C $RepoRoot remote remove origin 2>$null | Out-Null
& git -C $RepoRoot remote add origin "https://github.com/$Owner/$Repo.git"
# token 只用于这一次推送，不写进配置文件
& git -C $RepoRoot -c "http.extraheader=Authorization: Bearer $Token" push -u origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "   推送失败：检查 token 是否有 Contents: write 权限" -ForegroundColor Red
    exit 2
}

Write-Host "== 4/4 推标签 v0.1.0 =="
& git -C $RepoRoot -c "http.extraheader=Authorization: Bearer $Token" push origin v0.1.0
Write-Host ""
Write-Host "完成：https://github.com/$Owner/$Repo" -ForegroundColor Green
Write-Host "下一步（PyPI）见 ops\README-publish.md"
