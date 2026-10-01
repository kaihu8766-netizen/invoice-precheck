# 发票合规预审 · Web MVP

> 报销/进项发票合规预审产品（ADR-005/006）。定位：数电票时代的发票合规预审工具——上传数电票 XML，返回一页风险报告。
> 团队：用户（决策）+ 豆包助手（执行 A）+ DeepSeek（执行 B，联合执行）。
> 状态：P1 闭环完成（XML 上传 → 解析 → 规则 → 一页风险报告，35 项测试通过 + 真实服务验证）。

> **⚠️ 使用声明：本项目当前仅限本地/内网学习与演示使用，未实现认证、多租户隔离、验签/验真与持久化；报告仅为规则预审风险提示，不构成任何合规结论。**

## 在线演示

- 演示页（GitHub Pages，内置演示样例模拟后端返回）：`https://kaihu8766-netizen.github.io/invoice-precheck/`
- 演示页源码：`docs/index.html`（与产品前端一致；真实解析需本地运行后端）

## 目录结构

```
invoice-precheck/
├── README.md            # 本文件
├── docs/
│   ├── index.html        # GitHub Pages 演示页（内置演示样例）
│   └── （内部方案文档已移至私有仓，公开仓不含）
├── app/
│   ├── models.py        # 数据契约（NormalizedInvoice / Finding）
│   ├── parser.py        # 数电票 XML 解析
│   ├── rules.py         # 规则引擎服务化（R1/R2/R3/R4/R6/R7）
│   ├── report.py        # 风险报告组装
│   └── main.py          # FastAPI 入口
├── frontend/            # 单页前端（报告页）
└── tests/               # 测试（复用合成样本）
```

## 里程碑

| 阶段 | 内容 | 状态 |
|---|---|---|
| P1 | XML 上传→解析→规则→报告页面 | ✅ 闭环完成 |
| P2 前置 | 语料库 v0.4（结构仿真×2 + 官方样例 + 合成×4，全仓零真实数据）+ P0 治理八项闭环 | ✅ 完成（69 测试） |
| P2 | OFD + PDF（打印版）+ 图片；LLM 辅助 | 后置 |
| P3 | 批量、导出 PDF/Excel、企业标准配置 | 后置 |
| P4 | 部署上线 | 待用户提供资源 |

## 语料库与工具链（P2 前置 · 2026-09-23）

- **语料库**：`tests/corpus/`（结构仿真虚构样例×2 + 官方公开样例 + `synthetic/` 合成样本，全虚构/公开），元数据见 `manifest.json`（SHA256 绑定、来源分级、合成标注），构造规范见 `tests/corpus/SYNTHETIC.md`。
- **`scripts/generate_synthetic.py`**（P2-1）：确定性合成样本生成器（默认参数逐字节复现现有样本；可参数化生成变体；`--verify` 校验可复现性）。
- **`scripts/validate_manifest.py`**（P0-5）：manifest schema 校验（AUTO 哈希拒绝/合成标注强制/SHA256 绑定/集合一致性）。
- **CI**（P0-6）：`.github/workflows/ci.yml`——push/PR 自动跑全量测试 + manifest 校验 + 合成样本可复现校验（Python 3.11/3.12）。
- 规则 R8（P0-8）：金额异常类型化（勾稽不符/负数非红冲/差额征税占位 EI386/超阈值）。

## 本地运行

```bash
cd invoice-precheck
pip install -r requirements.txt
uvicorn app.main:app --reload     # 浏览器打开 http://127.0.0.1:8000
```

## 护栏（不变）

- 免责："辅助提示，不替代专业财务/审计判断"（R-08）
- 结论措辞："公开+合成集通过，真实分布未验证"（R-11）
- 规则分级：A 级上线 / B 级等真实数据 / 红线不碰

## 数据红线声明

- 本仓库不含任何真实发票。真实发票**本地看、本地验**，原始文件验完即删。
- 脱敏副本受控保留在本机 `~/invoice-private/`（仓库外，通过环境变量 `INVOICE_PRIVATE_DIR` 注入），**永不入库/不上 Pages/不进第三方/不用于训练**。
- 基准集构成：`benchmark/synthetic`（自造，进 Git）· `benchmark/public`（官方公告样张等公开素材，标注来源许可）· `benchmark/private`（仅存脱敏映射与结果归档，.gitignore 永不入库）。

## 基准集（goldset）——真值比对一键跑分（T-053 · RV-20261001-360 修订）

`benchmark/goldset/goldset.json`（v0.1.0，2026-10-01）冻结 43 条样本：L1 官方公开样例 5 / L2 合成 7 / L3 真实脱敏指针 31。

- **口径**：解析成功率、字段准确率（发票号/日期/金额/税额/合计/购方/销方逐字段比对真值）、漏报率（派生标签该报而未报）、误报率（不该报而报）、端到端耗时（avg/P95）；分层报告，**绝不合并单一准确率**。
- **派生标签**（`scripts/gold_labeler.py`）只从真值的数学/格式关系派生（勾稽容差 ±0.005、日期合法、票号 20 位、金额/税额非负），**独立于规则引擎 R1-R11**，杜绝"规则自己定标签又自己跑分"的循环论证。
- **L3 复现条件**：清单只含指针 + private 索引 SHA256（真实金额值留在本地 `benchmark/private/real_private_index.json`，不入库）；样本本体在仓外 `~/invoice-private/`。缺副本时 L3 标记 `skipped`，不计入指标。
- **⚠ 旧 `benchmark.py` 的 reconcile 是"自洽"（synthetic 无真值文件、official 组硬编码），与 goldset 真值比对**不可同表比较**（旧 latest.json 的 100% 属先行版，非准确率证据）。

跑分：`python3 scripts/run_goldset.py --json`（结果落 `benchmark/results/goldset_report.json`）。

## 企业主体配置（F-20260923-04 · RV-46/47）

R2 抬头/税号校验要真正工作，必须配置本公司主体（前端"真实模式 → 连接后端 → 企业主体设置"，或环境变量）。

**配置优先级**：默认值 < 本地 `data/config.json`（设置面板写入，不入 Git）< 环境变量 `INVOICE_COMPANY_NAME` + `INVOICE_COMPANY_TAXID`（**必须成套**，只设一个时忽略并告警）。

**R2 判定矩阵**（收票场景，校验购买方 buyer；税号归一=全半角/空格/大小写统一，票面与配置共用实现）：

| 配置 | 票面税号 | 票面名称 | 判定 |
|---|---|---|---|
| 未配置 | — | — | 低·未执行（防误报降级） |
| 已配置 | 无效/缺失 | 一致 | 低·无法校验 |
| 已配置 | 无效/缺失 | 不一致/缺失 | 中·可疑（防 fail-open） |
| 已配置 | 有效·一致 | 任意 | 通过（名称差异/缺失→低·名称差异提示） |
| 已配置 | 有效·不一致 | 任意（含名称一致） | 高·抬头/税号不符 |

- 税号合理性：归一后白名单 `^[0-9A-Z]+$` + 长度 [15,20]（18 位统一码/15 位老税号）+ 占位词表（全 0/全 X/N-A/无/暂无 等）
- 明确**不做**"有限公司/有限责任公司"同义合并（防漏报；名称差异压低档，不报高）
- 首版仅支持**单主体**（集团多法人/代开场景暂不覆盖）；配置结构 `company_entities` 数组预留多主体扩展
- 安全：`data/` 0700、config.json 0600 原子写（tmp+fsync+os.replace，Windows 重试）；`/api/config` 需 X-API-Key，税号脱敏返回；`/healthz` 暴露 `config_initialized`
- 部署约束：首版单实例（多副本下本地 config.json 不共享）

## 轻量评审通道（[LIGHT]）

> F-20260926-01：小改动（纯文档/纯文案）可走轻量评审——`docs: ... [LIGHT] (RV-...)` 提交。
> 门禁重算 staged diff：白名单制（md 文档 / docs/index.html 非 script 区间）、限额（3 文件/30 行/单行 500 字符）、
> 配额（同文件 7 天 50 行 / 每周 5 次）、必带 adopted RV 且 diff_hash 匹配。任一不满足 → 拒绝并升级全量。
> 详见 `scripts/gate_rules.yaml` 的 `light_whitelist`。
