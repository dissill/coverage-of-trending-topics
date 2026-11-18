#!/usr/bin/env pwsh
<#
.SYNOPSIS
  初始化本地 Git 仓库并推送到 GitHub。

.DESCRIPTION
  在仓库根目录执行：检查 git、初始化、配置提交身份、加远程、可选关闭 TLS 校验、
  提交当前内容并推送。不会保存任何 token；推送时由 Git Credential Manager 或
  终端提示输入用户名/密码（密码填 Personal Access Token）。

.EXAMPLE
  .\scripts\init-and-push.ps1 -RepoUrl "https://github.com/yourname/astrbot_plugin_daily_news.git" -Push

.EXAMPLE
  .\scripts\init-and-push.ps1 -RepoUrl "https://github.com/yourname/astrbot_plugin_daily_news.git" -Proxy "http://127.0.0.1:7890" -Push
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepoUrl,

    [string]$Branch = "main",

    [string]$UserName = "张英豪",

    [string]$Email = "",

    [string]$Proxy = "",

    [switch]$InsecureTLS,

    [switch]$Push,

    [string]$Message = "chore: 初始化 AstrBot 每日新闻简报插件仓库"
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
Write-Host "仓库目录：$root"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "找不到 git，请先安装 Git for Windows：https://git-scm.com/download/win"
}

if (-not (Test-Path (Join-Path $root ".git"))) {
    Write-Step "初始化 Git 仓库（分支 $Branch）"
    git init -b $Branch | Out-Host
}
else {
    Write-Host "已是 Git 仓库，跳过 init。"
}

if (-not $Email) {
    if ($RepoUrl -match 'github\.com[/:]([^/]+)/') {
        $Email = "$($Matches[1])@users.noreply.github.com"
    }
    else {
        $Email = "user@users.noreply.github.com"
    }
}
$currentName = (git config --local user.name) 2>$null
if (-not $currentName) {
    git config --local user.name $UserName
}
$currentEmail = (git config --local user.email) 2>$null
if (-not $currentEmail) {
    git config --local user.email $Email
}
Write-Host "提交身份：$(git config --local user.name) <$(git config --local user.email)>"

if ($Proxy) {
    Write-Step "设置仓库级 HTTP 代理：$Proxy"
    git config --local http.proxy $Proxy
}

if ($InsecureTLS) {
    Write-Step "关闭该仓库的 TLS 证书校验（http.sslVerify=false）"
    Write-Warning "仅建议在系统 CA 不被 git 信任（如公司/杀软中间人证书）时临时使用；推送完可执行：git config --local --unset http.sslVerify"
    git config --local http.sslVerify false
}

Write-Step "暂存文件"
git add -A
$status = git status --porcelain
if ($status) {
    Write-Host "将提交以下变更："
    $status | Out-Host
    git commit -m $Message | Out-Host
}
else {
    Write-Host "没有需要提交的变更。"
}

$existing = git remote
if ($existing -contains "origin") {
    $old = git remote get-url origin
    if ($old -ne $RepoUrl) {
        git remote set-url origin $RepoUrl
        Write-Host "已更新 origin：$RepoUrl"
    }
}
else {
    git remote add origin $RepoUrl
    Write-Host "已添加 origin：$RepoUrl"
}

if (-not $Push) {
    Write-Host ""
    Write-Host "仓库已准备好。请先在 GitHub 网页上创建一个空仓库（不要勾选 README / .gitignore / License），然后执行：" -ForegroundColor Yellow
    Write-Host "  git push -u origin $Branch" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "推送时提示登录：用户名填 GitHub 用户名，密码填 Personal Access Token（不是账号密码）。"
    return
}

Write-Step "推送到 GitHub"
git push -u origin $Branch
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Warning "推送失败。按下面顺序排查："
    Write-Host "1) GitHub 上是否已创建空仓库，仓库名与 URL 是否一致；"
    Write-Host "2) 网络是否需要代理：重新运行并加 -Proxy `"http://127.0.0.1:7890`"（端口换成你自己的）；"
    Write-Host "3) 是否报证书错误：加 -InsecureTLS 再试；"
    Write-Host "4) 是否报 403/认证失败：密码要用 Personal Access Token，且勾选 repo 权限；"
    Write-Host "5) 查看远程：git remote -v"
    exit 1
}

Write-Host ""
Write-Host "推送完成：$RepoUrl" -ForegroundColor Green
Write-Host "分支：$Branch"
