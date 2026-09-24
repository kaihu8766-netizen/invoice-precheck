# 密钥管理规范（SECRETS_GUIDE）

> 状态：生效（与作品集仓 ai-finance-portfolio F-20260924-04 规范对齐，2026-09-24）
> 本文件位于 trace/（Pages 源为 /docs，本目录不对外发布），属内部运维规范

## 原则（P0 级约束）

1. **密钥绝不入库**：任何真实 key（含前缀、完整串）不得出现在 commit、档案、raw 响应、文档、示例中
2. **公开渠道零片段**：公开仓 README、issue、评论、Pages（docs/ 站点）中不出现任何 key 片段（包括"已脱敏前缀"这类表述——证明方式用"扫描命令+命中计数"，不用原文）
3. **单一读取通道**：代码只通过环境变量 `DEEPSEEK_API_KEY` 读取（`os.environ.get`），禁止硬编码、禁止写死在脚本/配置里
4. **分级存储**：本地用 `.env`（.gitignore 排除），CI 用 GitHub Actions Secrets，两者不同步

## 存储位置

| 场景 | 位置 | 说明 |
|---|---|---|
| 本地开发 | `.env`（不入仓） | 复制 `.env.example` 填写；`git status` 应永不显示 `.env` |
| CI 复核 | GitHub Actions Secrets → `DEEPSEEK_API_KEY` | 仓库 Settings → Secrets and variables → Actions |
| 备份/其他 | 无 | 不备份到云盘/聊天/邮件 |

## 使用流程

```bash
# 本地（从 .env 读取）
cp .env.example .env
# 编辑 .env 填入真实 key
# 调用时（app/llm_explain.py 自动读环境变量）：
export $(cat .env | xargs)   # 或 source .env
python3 -m uvicorn app.main:app
```

## CI 接入（未来启用）

```yaml
# 仅当未来 CI 需要调用 DeepSeek 时，在对应 job 加：
env:
  DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
```

## 轮换流程（key 疑似暴露或定期）

1. 平台生成新 key
2. 本地 `.env` 更新为新 key（旧 key 停用）
3. GitHub Secrets 更新（若 CI 已启用）
4. 全历史扫描确认旧 key 零命中：
   ```bash
   git rev-list --all | while read c; do git grep -l "<旧key前缀>" "$c" 2>/dev/null; done | sort -u
   ```
5. 平台作废旧 key（revoke）
6. 记录轮换时间+key 指纹（不可逆摘要，非后 4 位）+存放点，写入 trace/ 决策记录

## 审计与对账

- 每次轮换后，在 trace/ 记录：操作时间、存放点清单、旧 key 指纹（不可逆摘要）、零命中证据（命令+计数）
- 定期（每月或大版本发布前）执行全仓 `sk-` 扫描：`grep -rEn "sk-[a-zA-Z0-9]{20,}" --include=*.py --include=*.md --include=*.json . | grep -v .git`
- 扫描结果 0 命中 = 通过；任何命中 = 立即按轮换流程处理

## 已记录事实（可审计）

- 2026-09-24：DEEPSEEK_API_KEY 已写入两仓 GitHub Secrets（ai-finance-portfolio + invoice-precheck），HTTP 201
- 当前在用 key：全历史零命中（从未进 git），符合不轮换继续使用条件
