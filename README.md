# dst-chatroom · 饥荒（DST）网页聊天室 / DST Web Chatroom

[中文](#简介) | [English](#introduction)

## 简介

为《饥荒联机版》（Don't Starve Together）专用服务器打造的轻量网页聊天室，基于 Flask + SQLite。

玩家在浏览器登录后，发言实时以 `[游戏名] 内容` 的形式广播进游戏世界；游戏内玩家无需安装任何客户端插件，即可与网页端互通。登录账号必须与游戏内角色名实时匹配，杜绝冒名发言。

**核心能力**

- 游戏内在线校验登录，管理员账号 `host` 免校验、以 `[Admin] …` 发言
- 登录密码自动跟随游戏房间密码（实时读取 `cluster.ini`），改密码零重启同步
- 单设备唯一登录互踢、密码连错 3 次封 IP 2 小时、登录接口 Nginx 层限流
- 聊天记录 2 小时自动销毁，SQLite 轻量存储，手机浏览器可用

## 快速开始

```bash
pip install -r requirements.txt
export SECRET_KEY="$(python3 -c 'import os; print(os.urandom(24).hex())')"
python3 app.py          # 默认监听 127.0.0.1:8083
```

用 Nginx 反代对外提供服务（配置模板见 `deploy/nginx/`），并与 DST 游戏服务器部署在同一台机器。完整步骤见下文 [部署](#部署)。

---

## 详细文档

### 功能特性

- **游戏内在线校验**：登录时实时向服务器控制台注入 Lua 指令抓取在线名单，账号必须与游戏内角色名完全一致才能登录，杜绝伪造他人身份；管理员账号 `host` 免校验，发言显示为 `[Admin] …`。
- **密码自动跟随游戏房间**：登录密码实时读取集群 `cluster.ini` 的 `cluster_password`，在游戏侧改房间密码后，网页聊天室自动同步，无需重启任何服务；也可用环境变量 `CHAT_PASSWORD` 手动指定。
- **单设备唯一登录**：同一账号在第二台设备登录会立刻挤掉第一台（旧端轮询收到 401 被踢回登录页）。
- **防暴力破解（三层）**：
  1. Nginx 对 `/login` 限流（10 次/分 + 突发 20），洪泛请求在网关层直接 429；
  2. 应用内密码连错 3 次封禁来源 IP 2 小时，锁定期由进程内存直接应答，不产生数据库开销；
  3. 配合 Cloudflare 代理时自动还原真实客户端 IP（`CF-Connecting-IP`），锁定无法用伪造请求头绕过。
- **轻量持久化**：SQLite 存储聊天记录（仅保留最近 2 小时、上限 200 条）、会话与封禁记录。
- **移动端适配**：手机浏览器可用，在线玩家列表收纳在侧边抽屉。

### 工作原理

```
浏览器 ──HTTPS──> Nginx(限流/真实IP还原) ──> Flask(app.py) ──> SQLite(chat.db)
                                              │
                                              ├─ screen -X stuff 注入 Lua ──> DST 服务器控制台
                                              └─ tail server_log.txt 抓取在线名单/回包
```

- 在线名单获取：向 DST 的 screen 会话注入 `TheNet:GetClientTable()` 打印指令，随后解析 `server_log.txt` 中的 `WEBCHAT|userid|name|prefab` 行。
- 消息进游戏：`TheNet:Announce("[发送者] 内容")` 注入控制台。
- 因此本服务必须与游戏服务器运行在**同一台机器**，且对游戏的 screen 会话有写权限。

### 部署

#### 1. 运行聊天服务

```bash
pip install -r requirements.txt

# 生产环境务必设置 SECRET_KEY（用于签名会话 Cookie）
export SECRET_KEY="$(python3 -c 'import os; print(os.urandom(24).hex())')"
export CHAT_PASSWORD="你的聊天室密码"   # 可选；不设置则自动跟随游戏 cluster.ini

python3 app.py   # 默认监听 127.0.0.1:8083
```

生产环境建议用 `deploy/systemd/dstweb.service` 托管（按需修改其中的路径，与本文件所在目录一致）。

#### 2. Nginx 反向代理

参考 `deploy/nginx/`：

* `dstweb.conf`：HTTPS 站点 + `/login` 限流。将 `dst.example.com` 替换为你的域名，证书路径按 Certbot 实际位置调整。
* `cloudflare-realip.conf`：若域名经 Cloudflare 小黄云代理，引入此文件以还原真实客户端 IP（内置 CF 官方网段，直连部署可跳过）。
* `game-block.conf`：可选。若另有一个直连域名专供游戏 UDP 使用，用此配置拒绝该域名的所有 Web 访问，避免域名间交叉访问。

#### 3. 游戏侧要求

* DST 集群（如 `Cluster_1`）的 `Master` 分服需运行在一个可写的 screen 会话中，会话名通过环境变量 `DST_SCREEN_SESSION` 配置（默认 `DST_Cluster_1_Master`）。
* 集群路径默认为 `/root/.klei/DoNotStarveTogether/Cluster_1/cluster.ini`，可在 `app.py` 顶部 `CLUSTER_INI` 处修改。

### 环境变量

| 变量 | 必填 | 说明 |
| :--- | :--- | :--- |
| `SECRET_KEY` | 强烈建议 | Flask 会话签名密钥，不设置将使用不安全的内置默认值 |
| `CHAT_PASSWORD` | 可选 | 聊天室登录密码；不设置时实时跟随游戏 `cluster.ini` 的 `cluster_password` |
| `DST_SCREEN_SESSION` | 可选 | 游戏 `Master` 分服所在的 screen 会话名，默认 `DST_Cluster_1_Master` |
| `PORT` / 监听地址 | 可选 | 默认 `127.0.0.1:8083`，需修改请编辑 `app.py` 末尾 |

### 安全说明

* 登录失败连续 3 次将封禁来源 IP 2 小时（`login_attempts` 表），解锁：`DELETE FROM login_attempts;` 后重启服务。
* 所有页面禁用缓存（`Cache-Control: no-store`），会话 Token 存于服务端而非 Cookie。
* 聊天记录默认 2 小时自动清理，数据库不含长期敏感数据。

---

## Introduction

A lightweight web chatroom for [Don't Starve Together](https://www.klei.com/games/dont-starve-together) dedicated servers, built with Flask and SQLite.

Players log in from a browser and their messages are broadcast into the game world as `[PlayerName] message`; in-game players can talk with web users without installing any client-side mod. Login names must match in-game character names in real time, so impersonation is impossible.

**Highlights**

- Login validated against the live in-game player list; admin account `host` bypasses the check and speaks as `[Admin] …`
- Chat password automatically follows the game cluster password (read live from `cluster.ini`)
- Single-session enforcement (a new login kicks the old one), 3 failed attempts = 2-hour IP ban, plus Nginx-level login rate limiting
- Chat history auto-expires after 2 hours; lightweight SQLite storage; mobile-friendly UI

## Quick Start

```bash
pip install -r requirements.txt
export SECRET_KEY="$(python3 -c 'import os; print(os.urandom(24).hex())')"
python3 app.py          # listens on 127.0.0.1:8083 by default
```

Put it behind Nginx (templates in `deploy/nginx/`) and run it on the **same machine** as the DST server. See [Deployment](#deployment) for details.

---

## Documentation

### Features

- **In-game presence check**: on login, the app injects Lua into the server console to fetch the live client table; the login name must exactly match an online character. The admin account `host` skips this check and messages appear as `[Admin] …`.
- **Password follows the game cluster**: the login password is read live from `cluster_password` in the cluster's `cluster.ini`; changing the room password syncs instantly, no restart needed. Alternatively set `CHAT_PASSWORD` manually.
- **Single-device sessions**: logging in on a second device immediately invalidates the first (the old client receives 401 and is redirected to the login page).
- **Brute-force protection (three layers)**:
  1. Nginx rate-limits `/login` (10 requests/min, burst 20); floods are answered with 429 at the gateway;
  2. 3 wrong passwords = 2-hour IP ban, served from an in-process cache with zero database overhead while locked;
  3. When proxied through Cloudflare, the real client IP is restored via `CF-Connecting-IP`, so bans cannot be bypassed with forged headers.
- **Lightweight persistence**: SQLite stores chat history (only the last 2 hours, capped at 200 messages), sessions, and ban records.
- **Mobile friendly**: works in mobile browsers; the online player list lives in a slide-in drawer.

### How It Works

```
Browser ──HTTPS──> Nginx(rate limit / real IP) ──> Flask(app.py) ──> SQLite(chat.db)
                                                     │
                                                     ├─ screen -X stuff injects Lua ──> DST console
                                                     └─ tail server_log.txt to fetch players/replies
```

- Player list: injects a `TheNet:GetClientTable()` print command into the DST screen session, then parses `WEBCHAT|userid|name|prefab` lines from `server_log.txt`.
- Messages into the game: injects `TheNet:Announce("[sender] content")` into the console.
- Therefore this service must run on the **same machine** as the game server, with write access to its screen session.

### Deployment

#### 1. Run the chat service

```bash
pip install -r requirements.txt

# Always set SECRET_KEY in production (used to sign session cookies)
export SECRET_KEY="$(python3 -c 'import os; print(os.urandom(24).hex())')"
export CHAT_PASSWORD="your-password"   # optional; defaults to the game cluster.ini password

python3 app.py   # listens on 127.0.0.1:8083 by default
```

For production, use `deploy/systemd/dstweb.service` (adjust paths to match your install directory).

#### 2. Nginx reverse proxy

See `deploy/nginx/`:

* `dstweb.conf`: HTTPS site + `/login` rate limiting. Replace `dst.example.com` with your domain and adjust certificate paths.
* `cloudflare-realip.conf`: include this if your domain is proxied by Cloudflare, to restore real client IPs (contains the official CF ranges; skip for direct deployments).
* `game-block.conf`: optional. If you also have a direct-connect DNS name dedicated to the game's UDP ports, this rejects all web access on that name.

#### 3. Game-side requirements

* The `Master` shard of your DST cluster (e.g. `Cluster_1`) must run inside a writable screen session; configure its name via the `DST_SCREEN_SESSION` environment variable (default `DST_Cluster_1_Master`).
* The cluster path defaults to `/root/.klei/DoNotStarveTogether/Cluster_1/cluster.ini`; edit `CLUSTER_INI` at the top of `app.py` if needed.

### Environment Variables

| Variable | Required | Description |
| :--- | :--- | :--- |
| `SECRET_KEY` | Strongly recommended | Flask session signing key; an insecure built-in default is used otherwise |
| `CHAT_PASSWORD` | Optional | Chatroom login password; when unset it follows the game's `cluster.ini` live |
| `DST_SCREEN_SESSION` | Optional | Screen session name of the game's `Master` shard, default `DST_Cluster_1_Master` |
| `PORT` / bind address | Optional | Defaults to `127.0.0.1:8083`; edit the end of `app.py` to change |

### Security Notes

* 3 consecutive failed logins ban the source IP for 2 hours (stored in `login_attempts`); to unban, run `DELETE FROM login_attempts;` and restart the service.
* All pages are served with `Cache-Control: no-store`; session tokens live server-side, not in cookies.
* Chat history is pruned after 2 hours by default; the database holds no long-term sensitive data.

---

## 开源许可 | License

本项目基于 [GNU General Public License v3.0（GPL-3.0）](LICENSE)发布。 / Released under the [GNU General Public License v3.0](LICENSE).
