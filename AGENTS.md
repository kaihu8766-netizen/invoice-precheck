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

## 4. 与 DeepSeek 讨论（事前对齐 + 事后复核 双保险，RV-22 定稿）

**功能开发四步（事前对齐门禁，commit-msg 闸门 3 强制）**：
1. `python3 scripts/trace_gate.py preflight --desc "<功能描述>"` → 立项，生成 `F-YYYYMMDD-NN` 功能登记（定位=发号器，RV-22 修正）
2. 方案评审（事前对齐）：`deepseek_gate.py --phase scheme --feature F-xxx --topic "<方案>" --prompt "..."` → DeepSeek 评审方案 → 用户批准（档案 status=adopted）
3. 开发实施
4. 功能提交：message 以 `feat(` 开头且引用 `F-xxx` + 方案评审 `RV-ID`；钩子校验 F-xxx 存在已批准（adopted）的 phase=scheme 评审，缺失拒绝提交

**事后复核**：实施后发 `deepseek_gate.py`（默认 --phase review）评审实施结果 → 用户拍板 → 提交带 RV-ID（红线改动走 diff_hash 双闸门）。

**时间可审计**：`python3 scripts/trace_gate.py audit-scheme` 对比方案评审入库时间 vs 功能代码首次提交时间，产出"方案后补"清单（评审晚于代码=流程违规，exit 1）。不在 commit-msg 做墙钟比对（RV-22：commit 对象未创建，恒为假）。

**事前对齐门禁口径（RV-23 落定，改代码/流程须先评审）**：
1. 触发正则：`^feat(\([^)]+\))?!?:`（feat:/feat(scope):/feat!:/feat(scope)!: 均算功能提交）
2. message 中出现的**全部** F-xxx 都必须有 adopted scheme-RV（防挂靠包装）
3. adopted 由执行者按用户拍板填写——**属诚实边界：防忘不防绕**（RV-23 口径 3 方案 A；不自称防绕）
4. 一个 feature 多个 scheme-RV：存在任一 adopted 即可（保留多轮评审历史）
5. 方案档案与代码同次提交：check 读工作区档案，存在即通过（预期行为；**只证明工作区有档案，不证明档案随本次提交入库**——可"git add 后仅提交代码路径"制造通过，属方案 A 边界，接受）
6. 评审时间锚点=档案首次入库 git 提交时间（--reverse 取首条、committer date、不可自填，git 时间难伪造），非 frontmatter date；档案与代码同次提交时两者相等，判"方案先行"（空锚点分支：档案未入库=待审计、代码未提交=未提交代码）
7. 代码锚点=main 分支首次引用 F-xxx 的提交 committer date（--reverse 取首条；merge 进 main 的提交计入范围；--fixed-strings 防 F 号正则歧义）。**%ci（committer date）是全链路唯一时间源**（口径 6/7 统一），比较前按时区归一化为 UTC epoch（RV-26：+0800 与 +0000 不得直接比较字符串）；rebase/squash 会更新 committer date（代码时间被推后 → 只会判得更"安全"，不会漏判后补）。git 时间戳可被 filter-branch/环境变量改写——**无技术防线**，仅靠口径 9 的防绕声明（诚实边界，不构成技术分层兜底）
8. audit-scheme 检索范围：main 分支（不扫 --all，避免 rebase 残留干扰）
9. 同类绕过全部列入诚实边界：`--no-verify`、手工改 adopted、自行改钩子、fix 前缀包装功能提交、`git config core.hooksPath` 改指向、临时置空 hooks 目录、`git commit-tree`+`update-ref` plumbing 绕过、改 git 时间戳（filter-branch/环境变量）——均属防绕范畴，方案 A 不拦但列全

- 大动作/里程碑：任务前方案评审 + 落地后评审（双闸门），都发 DeepSeek
- **必须用受控脚本** `agent-communication-demo/deepseek_gate.py` 调用（自动存原始请求/响应 + 生成 RV 档案 + 更新索引 + 返回 RV-ID），禁止裸调 API 后不落盘
- 评审结论：采纳/部分采纳/拒绝，由**用户拍板**；采纳后创建/更新 DEC；未决建 ISS
- 归档位置：`project-trace/03-会议与日志/DeepSeek评审/`（索引 + 档案，YAML frontmatter，含 phase/feature 字段）
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

## 红线提交流程（RV-13 双闸门，2026-09-23 起强制）
改动命中评审红线（app/parser.py、app/pdf.py、app/ocr.py、app/rules.py、脱敏/基准脚本，见 scripts/gate_rules.yaml）时：
1. `git add` 改动 → `python3 scripts/trace_gate.py classify --staged` 确认命中类别；
2. 用 deepseek_gate.py 发起评审（自动记录 staged diff_hash）→ 批准后档案含 diff_hash；
3. 重提交：message 带 `RV-<id>`，钩子校验 diff_hash 与当前 staged 完全一致才放行。
- 评审完成前不得再改红线文件（改过 hash 即失效，需重新评审）。
- 非红线文件仅需常规 ID 引用（GATE/RV/DEC/ISS）。

## 门禁边界（RV-14 诚实标注）
- 门禁防"遗忘/误操作"，不防恶意绕过（git commit --no-verify、git -c core.hooksPath=/dev/null 等 git 原生逃逸通道存在，项目信任执行者）。
- 门禁自改（scripts/trace_gate.py、.githooks/commit-msg、scripts/gate_rules.yaml、deepseek_gate.py）同样命中 gate_self 红线，必须评审。
- 事前对齐门禁（RV-22 诚实定位）：闸门 3 强制"功能提交必须有已批准方案评审"（防遗忘）；方案是否真的事前由 audit-scheme 事后审计（时间戳可查）；无法防止"用 fix 前缀包装功能提交"——该行为会绕过闸门 3，但会被用户监督/审计发现，属于恶意绕过范畴（不防）。

## 执行者纪律（RV-15 采纳，用户批准后生效）
- 不得主动使用 `git commit --no-verify`、`git -c core.hooksPath=/dev/null`、修改 gate_self 文件来绕过门禁。
- 确需变更门禁机制：先提 RV 评审 → 用户明确批准 → 变更时在 commit message 留痕（RV-ID）。
