# 部署指南（D 阶段·方案 A：本地穿透演示）

> 目标：把交互版（真实 FastAPI 后端）暴露到公网，手机/他人可直接访问。
> GitHub Pages 只能托管静态页面，跑不了 Python 后端——方案 A 用 Cloudflare Tunnel
> 在本地电脑和 Cloudflare 边缘网络之间建加密隧道，无需公网 IP、无需备案。

## 架构

```
用户浏览器 → https://xxx.trycloudflare.com → Cloudflare 边缘(加密隧道) → 你电脑 localhost:8000 (uvicorn)
```

## 方案 A：本地穿透（零成本，最快）

### 1. 一次性准备

1. 安装 Python 3.11+（安装时勾选 "Add Python to PATH"）
2. 安装 cloudflared：
   - 打开 https://github.com/cloudflare/cloudflared/releases
   - 下载 `cloudflared-windows-amd64.exe` → 重命名 `cloudflared.exe` → 放到 `invoice-precheck\` 目录
3. 打开 `invoice-precheck\` 目录（Windows 资源管理器地址栏输入 `cmd` 回车，或右键"在终端打开"）

### 2. 一键启动（推荐）

双击 `start-demo.bat`，脚本自动：
- 安装依赖 → 生成/读取 API Key → 启动后端（新窗口）→ 启动公网隧道

看 cloudflared 输出中的 `https://xxx.trycloudflare.com`，复制即可访问。

**注意**：隧道地址每次启动都会变；电脑关机隧道即断。演示期够用。

### 3. 手动启动（可选，便于看日志）

```bat
set INVOICE_API_KEY=你的强key
uvicorn app.main:app --host 0.0.0.0 --port 8000
:: 另开一个终端：
cloudflared tunnel --url http://localhost:8000
```

## 鉴权说明（必须了解）

- 业务接口（`/parse`、`/review`）**必须**携带请求头 `X-API-Key: <key>`
- key 来源：环境变量 `INVOICE_API_KEY`；未设置时后端使用开发默认 key 并打警告（仅限本地）
- 公开路径仅 `/`（前端页）和 `/healthz`（探活），无业务数据
- 401 时序安全比对（hmac.compare_digest），无 key/错 key 一律拒绝

## 安全清单（公网暴露前自查）

- [ ] 设置了强 `INVOICE_API_KEY`（≥20 位随机串，不用 start-demo 自动生成的演示 key 对外）
- [ ] CORS 白名单收窄（当前为 `*` 演示期放行；正式部署改为你的前端域名，改 `app/main.py` 中
      `allow_origins`）
- [ ] 不要在公网 URL 上展示/分享 API Key（演示页会把 key 存浏览器 localStorage，仅个人使用）
- [ ] 关闭本机防火墙对该端口的非必要暴露（Cloudflare Tunnel 是出站连接，本机无需开放入站端口）
- [ ] token / key 用后即撤（GitHub PAT、DeepSeek key 用完撤销）

## 方案 B/C 简介（后续阶段再上）

- **方案 B（国外 PaaS）**：Render 免费 Web Service 或 Railway（$5 首月+每月 $1）连接本仓库
  main 分支自动部署 FastAPI；国内访问偶慢，免费实例可能休眠。
- **方案 C（国内服务器）**：学生认证购买腾讯云轻量 2核2G（约 30 元/3 个月）→ 域名 ICP 备案
  （约 2-3 周）→ Dockerfile + systemd 常驻。长期对外、真机验证用。

## 演示页对接后端（D 阶段）

`docs/index.html` 当前为静态模拟响应；接入真实后端时：
- 页面 JS 从 `localStorage` 读 key，fetch 请求带 `X-API-Key` 头
- 后端地址配置为你的 trycloudflare 地址（页面需支持填写/记忆后端地址）
- 注意 CORS：当前后端已放行所有源 + `X-API-Key` 头，GitHub Pages 页面可直接调用
