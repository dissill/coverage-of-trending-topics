# 怎么把这份代码推到 GitHub

仓库已经初始化好（本地已有 `.git` 和首次提交），你只需要在 GitHub 建一个空仓库，然后执行推送脚本。

## 前提

1. 已安装 [Git for Windows](https://git-scm.com/download/win)（`git --version` 能输出 2.x）。
2. 有一个 GitHub 账号。
3. 决定仓库名，例如 `astrbot_plugin_daily_news`。

## 第一步：在 GitHub 建空仓库

打开 <https://github.com/new>：

- Repository name：`astrbot_plugin_daily_news`
- 可见性：Public 或 Private 都行
- **不要**勾选 Add a README / Add .gitignore / Choose a license（本地已有这些文件，勾了会冲突）

创建后复制仓库地址，形如：

```text
https://github.com/你的用户名/astrbot_plugin_daily_news.git
```

## 第二步：准备 Personal Access Token（密码）

GitHub 不再接受账号密码推送，需要 PAT：

1. 打开 <https://github.com/settings/tokens> → Generate new token (classic)
2. 勾选 `repo` 权限，生成后复制 `ghp_...`
3. 推送时：用户名填 GitHub 用户名，**密码填这个 token**

（如果装了 Git Credential Manager，会弹浏览器登录，按提示授权即可，不用手动贴 token。）

## 第三步：推送

最简单的方式：双击 `scripts\push.bat`，按提示粘贴仓库地址；有代理就填代理，没有就回车。

或者用 PowerShell：

```powershell
# 在仓库根目录
.\scripts\init-and-push.ps1 -RepoUrl "https://github.com/你的用户名/astrbot_plugin_daily_news.git" -Push
```

带代理：

```powershell
.\scripts\init-and-push.ps1 -RepoUrl "https://github.com/你的用户名/astrbot_plugin_daily_news.git" -Proxy "http://127.0.0.1:7890" -Push
```

之后每次改完代码，用日常更新脚本：

```powershell
.\scripts\push.ps1 -Message "feat: 日报加天气"
# 或者双击 scripts\push.bat 再走一遍
```

## 这台机器上的两个坑

### 1) 证书/吊销检查错误

如果报：

```text
schannel: next InitializeSecurityContext failed: CRYPT_E_NO_REVOCATION_CHECK
```

说明系统或安全软件做了 TLS 中间人，git 的证书吊销检查过不去。两个办法：

- 加参数 `-InsecureTLS`（只对当前仓库关闭 `http.sslVerify`，推送完可恢复）：
  ```powershell
  .\scripts\init-and-push.ps1 -RepoUrl "..." -InsecureTLS -Push
  git config --local --unset http.sslVerify   # 推完恢复
  ```
- 或者安装正确的根证书、或走代理让吊销检查能连通。

### 2) 直连 GitHub 超时 / TLS 失败

需要本地代理时，脚本会自动给当前仓库设 `http.proxy`：

```powershell
.\scripts\init-and-push.ps1 -RepoUrl "..." -Proxy "http://127.0.0.1:7890" -Push
```

端口换成你自己的（Clash 常见 7890/7897，v2rayN 常见 10809）。

## 推送失败排查

| 报错 | 原因 | 处理 |
| --- | --- | --- |
| `Repository not found` | URL 错 / 仓库没建 / 没登录 | 检查 URL；重新输入 token |
| `403` | token 权限不够 | 重新生成，勾 `repo` |
| `failed to connect` / timeout | 网络不通 | 加 `-Proxy`，或开代理后重试 |
| `CRYPT_E_NO_REVOCATION_CHECK` | TLS 吊销检查失败 | 加 `-InsecureTLS` |
| `src refspec main does not match any` | 还没有提交 | 运行 `init-and-push.ps1` 生成首次提交 |
| `non-fast-forward` | 远程已有内容 | `git pull --rebase origin main` 后再推 |

## 手动命令（不想用脚本时）

```bash
cd astrbot_plugin_daily_news
git init -b main
git config user.name "你的名字"
git config user.email "你的邮箱"
git add -A
git commit -m "feat: AstrBot 每日新闻简报插件 v1.3.0"
git remote add origin https://github.com/你的用户名/astrbot_plugin_daily_news.git
git push -u origin main
```

## 推上去之后

- 在仓库 Settings → Pages 不需要开；
- 在 AstrBot 插件市场提交收录需要仓库带 `metadata.yaml`（本仓库已有）；
- 以后改插件源码：本地改 `main.py` / `news_sources.py`，同时同步回 AstrBot 的插件目录，再 `push.ps1`。
