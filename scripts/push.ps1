#!/usr/bin/env pwsh
<#
.SYNOPSIS
  日常更新：提交本地改动并 push 到已配置的 origin。

.EXAMPLE
  .\scripts\push.ps1

.EXAMPLE
  .\scripts\push.ps1 -Message "feat: 日报加天气" -Proxy "http://127.0.0.1:7890"
#>
[CmdletBinding()]
param(
    [string]$Message = "",
    [string]$Proxy = "",
    [switch]$InsecureTLS,
    [string]$Branch = "main"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "找不到 git，请先安装 Git for Windows。"
}
if (-not (Test-Path (Join-Path $root ".git"))) {
    throw "当前目录不是 Git 仓库，请先运行 scripts\init-and-push.ps1。"
}

$remotes = git remote
if (-not ($remotes -contains "origin")) {
    throw "没有配置 origin，请先运行 scripts\init-and-push.ps1。"
}

if (-not $Message) {
    $Message = "chore: 更新于 $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
}

if ($Proxy) {
    git config --local http.proxy $Proxy
    Write-Host "HTTP 代理：$Proxy"
}
if ($InsecureTLS) {
    git config --local http.sslVerify false
    Write-Warning "已关闭该仓库 TLS 校验。"
}

git add -A
$status = git status --porcelain
if (-not $status) {
    Write-Host "没有改动，无需提交。"
}
else {
    git commit -m $Message | Out-Host
}

git push origin $Branch
if ($LASTEXITCODE -ne 0) {
    Write-Warning "push 失败：检查网络/代理、远程地址（git remote -v）和 GitHub 登录状态。"
    exit 1
}
Write-Host "已推送：$(git remote get-url origin)（$Branch）" -ForegroundColor Green
