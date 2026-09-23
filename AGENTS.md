# AGENTS.md · 项目门禁与协作规范（DeepSeek 评审 #9 采纳）

> 本文件是**硬门禁**依据：任何大动作、任何提交，不满足本规范即拒绝执行/提交。
> 背景：执行 agent（豆包）会话上下文会被压缩/遗忘——规范写在此处，不依赖 agent 记忆。
> 溯源原则：**不查就提交不了；没 GATE-ID 不执行；commit 必须可追溯到决策。**

## 1. 每轮首行状态声明

豆包每轮回复第一行必须写（缺失用户立即发现并拒绝继续）：

```
TASK | RV | DEC | GATE | ISS
```

- `TASK-xxx`：当前任务卡编号（无任务卡=小动作可不写）
- `RV-日期-序号`：关联的 DeepSeek 评审（见 project-trace/03-会议与日志/DeepSeek评审/索引.md）
- `DEC-日期-序号`：关联的当前有效决策（见 project-trace/DECISIONS.md）
- `GATE-日期-序号`：本次动作的门禁放行 ID（大动作必须）
- `ISS-编号`：关联的开放问题（见 project-trace/OPEN_ISSUES.md）

## 2. 大动作清单（触发门禁）

以下任何一项 = 大动作，**必须先跑 `trace_gate.py gate` 拿 GATE-ID，经用户批准后才可执行**：

1. 改架构 / 接口契约 / 数据模型（影响其他模块或历史数据）
2. 增删依赖 / 换技术栈 / 引入外部服务
3. 数据迁移 / 批量删除 / 覆盖历史记录
4. 发布部署 / 对外承诺 / 成本变更 / 权限变更
5. 修改 DECISIONS.md 或覆盖历史评审结论
6. 涉及真实用户数据的操作（脱敏除外）
7. 单次批量文件操作 > 10 个（纯文档归档除外）
8. 任何"用户没拍板但我猜他会同意"的动作

非大动作（绿灯）：读文件、写纯文档/档案、本地小修改、测试、提交日常代码（但仍需引用关联 ID）。

## 3. 门禁命令（硬门禁）

```bash
# 大动作前（在 invoice-precheck 根）：
python3 scripts/trace_gate.py gate --task "TASK-xxx 做什么"

# 查重/看关联（讨论前先查，避免重复评审）：
python3 scripts/trace_gate.py ids --grep "OCR|PDF|部署"

# commit 校验（commit-msg 钩子自动调用，也可手动）：
python3 scripts/trace_gate.py check --message "你的 commit message"
```

- `gate`：读评审索引 + DECISIONS + OPEN_ISSUES → 输出相关 RV/DEC/ISS 与"是否需新评审"→ 生成并记录 `GATE-YYYYMMDD-NN`，写入 `03-会议与日志/门禁记录/`
- `check`：校验 commit message 含合法 ID 引用（`GATE-|RV-|DEC-|ISS-` 之一），缺失 exit 1
- 缺 GATE-ID 的大动作：用户应直接拒绝，不进入执行

## 4. 与 DeepSeek 讨论（双闸门 + 自动落盘）

- 大动作/里程碑：任务前方案评审 + 落地后评审（双闸门），都发 DeepSeek
- **必须用受控脚本** `agent-communication-demo/deepseek_gate.py` 调用（自动存原始请求/响应 + 生成 RV 档案 + 更新索引 + 返回 RV-ID），禁止裸调 API 后不落盘
- 评审结论：采纳/部分采纳/拒绝，由**用户拍板**；采纳后创建/更新 DEC；未决建 ISS
- 归档位置：`project-trace/03-会议与日志/DeepSeek评审/`（索引 + 档案，YAML frontmatter）
- 结论变更：更新档案状态 + DECISIONS，禁止只改一处

## 5. 提交规范

- commit message 必须含至少一个 `ID` 引用：`GATE-YYYYMMDD-NN` / `RV-YYYYMMDD-NN` / `DEC-YYYYMMDD-NN` / `ISS-NN`
- 建议格式：`feat|fix|docs|test|refactor(scope): 描述 (RV-YYYYMMDD-NN, GATE-YYYYMMDD-NN)`
- commit-msg 钩子（`.githooks/commit-msg`，`git config core.hooksPath .githooks`）校验失败即拒绝

## 6. 压缩恢复（会话上下文丢失时）

1. 先读 `project-trace/00-START-HERE.md`（恢复入口：最近决策/开放问题/评审索引/门禁清单）
2. 再读 `project-trace/DECISIONS.md` + `OPEN_ISSUES.md` + `03-会议与日志/DeepSeek评审/索引.md`
3. 恢复后首轮仍须输出首行状态声明；未恢复完整的重大上下文，先补 TASK/RV/DEC/GATE/ISS 再动手

## 7. 权限边界（DeepSeek 评审 #9）

- 豆包只有**提议权**，无直接大动作执行权；用户是决策者
- 用户否决 = 停止该分支，不绕过、不降级宣称完成
