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
4. **先入库档案、再提交代码**（RV-30 执行顺序纪律，F-20260923-01 自举例外教训）：**"入库"= 方案评审档案 + 功能登记完成 git commit 且 push 到远端**（audit 锚点以 git 入库时间为准，push 与否不影响本地审计，但惯例 push）——先提交 project-trace → 再提交 invoice-precheck 功能代码
5. 功能提交：message 以 `feat(` 开头且引用 `F-xxx` + 方案评审 `RV-ID`；钩子校验 F-xxx 存在已批准（adopted）的 phase=scheme 评审，缺失拒绝提交

**事后复核**：实施后发 `deepseek_gate.py`（默认 --phase review）评审实施结果 → 用户拍板 → 提交带 RV-ID（红线改动走 diff_hash 双闸门）。

**时间可审计**：`python3 scripts/trace_gate.py audit-scheme` 对比方案评审入库时间 vs 功能代码首次提交时间，产出"方案后补"清单（评审晚于代码=流程违规，exit 1）。不在 commit-msg 做墙钟比对（RV-22：commit 对象未创建，恒为假）。

**audit-scheme 已知限制（RV-33 落定）**：
- 浅历史/无 TRACE 时 `git log` 空返回 → 输出"无功能登记目录/档案未入库"且 RC=0，属**跳过语义**（非"审计通过"），依赖 CI `fetch-depth: 0` 兜底；本地浅克隆会踩到，勿将 RC=0 误读为合规
- 时间锚点用 committer date（`%ci`）= "入库时间"；rebase/squash 会更新 committer date 导致漂移；author date 不参与比较（全链路唯一时间源，混用会重演历史比较 bug）

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
- 门禁自改（scripts/trace_gate.py、.githooks/commit-msg、scripts/gate_rules.yaml、AGENTS.md 等）同样命中 gate_self 红线，必须评审；project-trace 侧 gate 工具（agent-communication-demo/deepseek_gate.py、evtools.py、tests/）由 #62 门禁承接（RV-107）。
- 事前对齐门禁（RV-22 诚实定位）：闸门 3 强制"功能提交必须有已批准方案评审"（防遗忘）；方案是否真的事前由 audit-scheme 事后审计（时间戳可查）；无法防止"用 fix 前缀包装功能提交"——该行为会绕过闸门 3，但会被用户监督/审计发现，属于恶意绕过范畴（不防）。

## 已知陷阱（RV-78 沉淀，可复用教训）
- **混合文件（HTML+JS+模板串）的区间判定：用显式哨兵锚点，不用花括号配平**。模板串内孤立 `{`/`}`、`${}` 插值、行/块注释都会让朴素配平失准（漏判或大面积误伤）。方案：`// @demo-data:begin/end` 哨兵对，区间由哨兵行号决定，零歧义；删改哨兵行本身=命中；找不到哨兵对必须 fail-closed（判命中），防删哨兵绕过。
- 关键词匹配做红线类别时，注意路径自触发（例：gate_rules.yaml 路径含 "rules" 误命中 rules_engine）。keywords 应精确到文件级（如 "rules.py"），避免宽泛子串。

## 执行者纪律（RV-15 采纳，用户批准后生效）
- 不得主动使用 `git commit --no-verify`、`git -c core.hooksPath=/dev/null`、修改 gate_self 文件来绕过门禁。
- 确需变更门禁机制：先提 RV 评审 → 用户明确批准 → 变更时在 commit message 留痕（RV-ID）。

## 数据安全红线（RV-96 事故沉淀 + RV-99/100 评审强化，2026-09-25 起企业级标准）
**真实数据（发票/票号/税号/公司名/金额/姓名/邮箱/密钥等）绝不写入任何将入库的文件，尤其 public 仓。** 本条目由「latest.json 真实字段入库 → public 泄露 → 全历史重写 + 转 private」事故产生，为最高优先级约束。

> 口径主从（RV-99 C3/C6）：密钥轮换与脱敏细节以 `trace/SECRETS_GUIDE.md` 与 `DECISIONS` 为唯一口径；本章节只写执行约束，不另起一套。本清单**防遗忘/误操作，不防恶意绕过**（`--no-verify`/手工改属诚实边界，与 §门禁边界 RV-14 一致）。

### 入库前必查清单（提交前逐项核对，可执行命令）
> **规则：本清单只写形态化规则，绝不写任何真实具体值**（票号/税号/公司名一律用形态/变量表达，防止"安全示例自身成为泄露源"——RV-100 摩擦2 教训）。
1. 改动文件红线特征扫描 **0 命中**（判定：以下命令 exit code 均为 1 才算通过——grep 无匹配时返回 1）：
   ```bash
   # 形态化规则（不依赖具体值）：20位数电票号 / 18位统一社会信用代码 / 密钥前缀 / 本地路径
   git diff --cached | grep -E '\b[0-9]{20}\b|[0-9A-Z]{18}\b|sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|/home/[a-z]+/invoice-private'
   git ls-files | grep -E '^benchmark/private/|^data/config\.json$|\.env$|\.csv$'
   # 公司名/税号等无法形态化的敏感词清单：存于本地私有文件（如 ~/invoice-private/sensitive-words.txt），不入仓库；
   # 提交前用循环 grep：while read -r w; do git diff --cached | grep -q "$w" && echo "命中: $w"; done < ~/invoice-private/sensitive-words.txt
   ```
2. 敏感载体仅允许存在于本地私有区且被 `.gitignore` 排除、绝不在 tracked 列表：`benchmark/private/`、`data/config.json`（企业主体配置=真实公司名/税号，同类风险）、`*.csv`、`.env`；仓库外真实数据只放 `~/invoice-private/`（DECISIONS 既定路径），不得改道。
3. 真实数据脱敏产物（`anonymize_real.py`/`redact.py` 输出）只含聚合/布尔/脱敏字段；**public 样例须有来源分层与许可标注**（`tests/corpus/manifest.json` 的 `source_tier` 逐核）。
4. 提交前重生成结果文件确认无真实字段：`python3 scripts/benchmark.py --json` 后按第 1 条重扫。
5. 脱敏/真实数据脚本改动（含 `scripts/redact.py`、`scripts/anonymize_real.py`、`scripts/anonymize_to_private.py`、`scripts/benchmark.py`）一律命中 `gate_rules.yaml` data_redline 红线，必须带已批准 RV + diff_hash 匹配（RV-13 双闸门）。`redact` 为保守加严关键词（RV-100 摩擦1），会连带命中 `test_redact.py` 等脱敏相关测试——属 fail-closed 方向，可接受误报，不必降级。

### 泄露应急预案（已演练，RV-96 全流程；RV-99 C3/C4 强化）
1. **立即**转 private 阻断新访问（GitHub API PATCH visibility）；
2. 定位泄露载体清单：**仓库文件、git 历史、CI artifacts/Actions 日志、导出物（Excel/PDF 报告）、本地 `~/invoice-private/`、备份 bundle、录屏/截图/聊天/邮件**——逐载体排查，不止扫仓库；涉凭据 → 执行 `trace/SECRETS_GUIDE.md` 轮换流程（新 key → .env/Secrets 更新 → 全历史零命中 → revoke 旧 key → 记录指纹）；真实姓名/邮箱/税号 = 第三方个人数据，评估通知当事人与留档说明；
3. 用户拍板后重写历史：`git-filter-repo --invert-paths --path <file>`（blob-callback 方式实测无效，必须用 invert-paths）+ force-push；
4. 全历史 + 远程扫描 0 命中后，重生成干净版入库；**收官归档**：事故档案 + DEC/ISS + 索引同步，hash 映射表落盘（旧→新 SHA），校验 START-HERE/索引/档案 `commits` 字段引用一致性；
5. **平台侧处置（正式步骤）**：提交 GitHub Support 工单请求强制 purge CDN 缓存 + 移除旧 commit 视图/代码搜索索引（公开期 fork/他人 clone/第三方 mirror 无法自行收回）；被动兜底：开启 secret scanning + push protection（新密钥/票号误提交时平台先拦）；
6. 残余风险与验收口径：raw.githubusercontent CDN 缓存可能保留公开期旧内容 → 期间保持 private，转回 public 前**复测旧 SHA 全部 404**（Pages 重新发布后缓存失效为验收口径）。

### 评审与档案敏感数据规则（RV-100 摩擦2/3 沉淀）
- **评审 prompt 不得引用任何真实具体值**（票号/税号/公司名/金额），一律用形态化描述（如"某出行科技公司"、"20 位数电票号"）——raw 审计档案会长期留存，真实值一旦写入即成为新的敏感载体。
- 测试/示例代码中的占位密钥必须显式标注（如 `sk-FAKE_...`），不得使用近似真实形态的可疑串。
- 既有含真实值的 raw 档案保留原样（sha256 完整性绑定），不回改；本规则生效后**零新增**真实值。

### RV-99 C1-C7 整改闭环声明（RV-103 有条件通过，2026-09-25）
- **C1** 新 RV 档案：RV-99/100/101/102/103 五轮评审闭环，本章节随 RV-103 提交 ✓
- **C2** gate_rules.yaml data_redline 补 scripts/redact.py、scripts/anonymize_real.py、keywords 补 redact ✓
- **C3** 密钥轮换引用 trace/SECRETS_GUIDE.md（唯一口径）+ GitHub Support 工单正式步骤 + secret scanning/push protection + CDN 验收口径 ✓
- **C4** 载体清单（CI artifacts/导出物/本地目录/备份 bundle）+ 影响面评估通报判定 + 收官归档（hash 映射落盘 + 引用一致性校验）✓
- **C5a** 清单命令化（grep 形态正则 + exit code 判定）+ 防忘不防绕标注 ✓
- **C5b** CI 红线扫描自动化：**○ 未完成 → OPEN_ISSUES #61**（RV-104 要求真实 ISSUE 编号；本地命令化已闭环，自动化承载待做）
- **门禁覆盖缺口**：project-trace 仓（gate 工具本体）无提交门禁 → **OPEN_ISSUES #62**（RV-106 暴露；装钩子复用 trace_gate，agent-communication-demo/tests 纳入强制评审）
- **C6** 敏感路径点名（benchmark/private、data/config.json、*.csv、.env、~/invoice-private）+ 来源许可标注（manifest source_tier）✓
- **C7** 删除未登记 ID「R1」，全部改为已登记 RV 引用 ✓

### 评审与档案敏感数据规则（RV-100 摩擦2/3 沉淀）
- **评审 prompt 不得引用任何真实具体值**（票号/税号/公司名/金额），一律用形态化描述（如"某出行科技公司"、"20 位数电票号"）——raw 审计档案会长期留存，真实值一旦写入即成为新的敏感载体。**机器强制**：deepseek_gate.py 发起评审前对 prompt 做形态化红线扫描，命中即拒绝（RV-102 闸门）。
- 测试/示例代码中的占位密钥必须显式标注（如 `sk-FAKE_...`），不得使用近似真实形态的可疑串。
- 既有含真实值的 raw 档案保留原样（sha256 完整性绑定），不回改；本规则生效后**零新增**真实值。
