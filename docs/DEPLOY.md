# 部署文档（DEPLOY.md）

> 生效版本：RV-20261001-367 前置①（T-014 部署形态 A 案定案）。本文件此前为悬空引用
> （`app/main.py` 指向它但从未存在），现已补写为唯一权威部署说明。

## 1. 部署架构（A 案：本地后端 + Cloudflare Tunnel）

```
手机/电脑浏览器 ──HTTPS──> Cloudflare 边缘 ──Tunnel──> 本机 cloudflared ──> FastAPI (localhost:8000)
                                                                              │
                                                                              ├─ data/config.json   （企业主体+规则阈值）
                                                                              └─ data/audit.db      （append-only 审计+哈希链）
```

- **数据不出机**：真实发票、解析结果、审计日志全部留在本机 `data/` 目录；公网只经
  Cloudflare Tunnel 暴露 API（带 API Key 鉴权），无第三方服务器存储业务数据。
- **双端访问**：手机/电脑浏览器访问同一个隧道域名即可（无需在本机装客户端）。

## 2. 环境要求

- Python 3.10+（开发/生产同版本，避免依赖差异）
- 依赖安装：`pip install -r requirements.txt`（含 fastapi、uvicorn、pdf 解析依赖）
- `cloudflared`：本机安装（https://developers.cloudflare.com/cloudflared/），
  需 Cloudflare 账号或已有 Tunnel token

## 3. 启动（生产模式）

```bash
# 生产环境三件套（缺一即拒绝启动——RV-125 / RV-367 硬门）：
export INVOICE_ENV=prod                 # 生产环境标记
export INVOICE_API_KEY="$(openssl rand -hex 32)"   # 强 key（≥16 位）
export INVOICE_CORS_ORIGINS="https://your-tunnel.example.com"  # 前端实际访问源白名单
export INVOICE_LLM_ENABLED=0           # 建议保持 0（LLM 外发默认关闭）
export INVOICE_LLM_SEND_AMOUNT=0       # 建议保持 0（金额不外发，数据最小化）
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

> **关键安全硬门（代码强制，非文档约定）**
> - `INVOICE_ENV=prod` 时设置 `INVOICE_ALLOW_DEV_KEY=1` → **拒绝启动**（防默认 key 公网暴露）
> - `INVOICE_ENV=prod` 时 `INVOICE_API_KEY < 16 位` → **拒绝启动**（防弱 key）
> - `INVOICE_ENV=prod` 时未设 `INVOICE_CORS_ORIGINS` → CORS 仅放行 `localhost:8000`（fail-closed）

## 4. 开发模式（仅本机调试，严禁经隧道暴露）

```bash
export INVOICE_ALLOW_DEV_KEY=1          # 显式声明本机开发（允许开发默认 key + 通配 CORS）
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## 5. 公网暴露（Cloudflare Tunnel）

```bash
# 方式一：Cloudflare Zero Trust（推荐，有面板配置）
cloudflared tunnel --url http://127.0.0.1:8000   # quick tunnel（临时）：
# 正式隧道：cloudflared tunnel create <name> → config.yml 指向 localhost:8000 → cloudflared tunnel run <name>

# 方式二：quick tunnel（免费、无账号，域名随机）
cloudflared tunnel --url http://127.0.0.1:8000
```

启动后 `INVOICE_CORS_ORIGINS` 填隧道域名（如 `https://xxx.trycloudflare.com`）。

## 6. 安全加固清单（生产必查）

| 项 | 状态 | 说明 |
|---|---|---|
| API Key 鉴权 | ✅ 代码强制 | `X-API-Key` 头 + `hmac.compare_digest`（时序安全）；缺失/错误 401 |
| 启动硬门 | ✅ 代码强制 | 未设 key / prod+dev key / prod 弱 key → 拒绝启动 |
| CORS 白名单 | ✅ 代码强制 | `INVOICE_CORS_ORIGINS` 注入；prod 未设仅 localhost |
| 速率限制 | ✅ 代码强制 | 业务端点 60s 窗口 60 次/IP，超限 429（防 key 爆破/DoS） |
| 文件上限 | ✅ 代码强制 | 单文件 10MB / 单批 200 个 / 总 50MB |
| 错误文案白名单 | ✅ 代码强制 | 业务 ValueError 透出，其余统一安全文案 |
| 安全响应头 | ✅ 代码强制 | nosniff / frame DENY / no-referrer |
| 审计哈希链 | ✅ 代码强制 | `data/audit.db` append-only + event_hash 链（`GET /api/audit/verify` 自检） |
| PII 删除权 | ✅ API 就绪 | `DELETE /api/data/{ref}` 行使留痕；当前解析瞬态无本体，未来接本体删除 |
| LLM 外发 | ✅ 默认关闭 | `INVOICE_LLM_ENABLED=0` 全模板解释；金额 `INVOICE_LLM_SEND_AMOUNT=0` 不外发 |
| /healthz | ✅ 公开但收敛 | 仅返回 status/ruleset/config_initialized（无路径探测等敏感信息） |

**运维警示**：`INVOICE_ALLOW_DEV_KEY=1` 绝不可与隧道同时使用——它同时打开
通配 CORS + 已知默认 key（已由 `INVOICE_ENV=prod` 互斥硬门阻止，若需排障请走生产模式）。

## 7. 数据路径与备份

| 路径 | 内容 | 备份建议 |
|---|---|---|
| `data/config.json` | 企业主体 + 规则阈值（含 rules_hash 审计） | 随项目目录备份 |
| `data/audit.db` | append-only 审计（事件+哈希链） | 每日备份（副本即链完整快照） |
| `data/uploads/`（未来） | 原始发票留存（启用后） | 加密备份 |

备份命令示例（每日 cron）：
```bash
cp data/audit.db backups/audit-$(date +%F).db
```

## 8. 故障排查

| 症状 | 检查 |
|---|---|
| 启动即报错 | 读报错文案（硬门提示明确：key 未设 / prod 互斥 / 弱 key） |
| 手机访问 502/超时 | 隧道是否在跑；`curl https://<隧道域名>/healthz` |
| 401 | `X-API-Key` 是否与服务端 `INVOICE_API_KEY` 一致 |
| 429 | 限流触发（60s/60 次）；批量上传请分批 |
| 审计查询失败 | `GET /api/audit/verify` 返回 `ok:false` 时检查 `tampered_rows`（哈希链损坏=外部篡改证据） |
| 前端连不上后端 | 前端设置页 `后端地址` 填隧道域名 + `API Key`；CORS 白名单须含该域名 |

## 9. 演进方向（已立项未实施）

- **C 案迁移**（国内轻量云）：T-014 留档的长期演进；届时 `INVOICE_ENV=prod` + 强 key 不变，
  存储迁移 SQLite→PostgreSQL（审计表结构与哈希链逻辑兼容）。
- **原始文件留存**（T-056 剩余部分）：复核工作台回看原图需要；启用后 `DELETE /api/data/{ref}`
  从"留痕"升级为"本体删除 + 留痕"。
- **环境分级**（T-046）/ **备份演练**（T-047）：T-014 定案后解锁，排期见 project-trace/TODO.md。
