# 发票合规预审 · Web MVP

> 报销/进项发票合规预审产品（ADR-005/006）。定位：数电票时代的发票合规预审工具——上传数电票 XML，返回一页风险报告。
> 团队：用户（决策）+ 豆包助手（执行 A）+ DeepSeek（执行 B，联合执行）。
> 状态：P1 闭环完成（XML 上传 → 解析 → 规则 → 一页风险报告，35 项测试通过 + 真实服务验证）。

> **⚠️ 使用声明：本项目当前仅限本地/内网学习与演示使用，未实现认证、多租户隔离、验签/验真与持久化；报告仅为规则预审风险提示，不构成任何合规结论。**

## 在线演示

- 演示页（GitHub Pages，内置脱敏样例模拟后端返回）：`https://kaihu8766-netizen.github.io/invoice-precheck/`
- 演示页源码：`docs/index.html`（与产品前端一致；真实解析需本地运行后端）

## 目录结构

```
invoice-precheck/
├── README.md            # 本文件
├── docs/
│   ├── index.html        # GitHub Pages 演示页（内置脱敏样例）
│   └── P1-技术方案.md    # P1 技术方案（含 DeepSeek 评审意见与采纳决策）
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
| P2 前置 | 语料库 v0.3（3省真实票样 + 官方 XBRL×2 + 合成×4）+ P0 治理八项闭环 | ✅ 完成（69 测试） |
| P2 | OFD + PDF（打印版）+ 图片；LLM 辅助 | 后置 |
| P3 | 批量、导出 PDF/Excel、企业标准配置 | 后置 |
| P4 | 部署上线 | 待用户提供资源 |

## 语料库与工具链（P2 前置 · 2026-09-23）

- **语料库**：`tests/corpus/`（真实票样脱敏 3 省 3 系统 + 官方 XBRL 实例 + `synthetic/` 合成样本），元数据见 `manifest.json`（SHA256 绑定、来源分级、合成标注），构造规范见 `tests/corpus/SYNTHETIC.md`。
- **`scripts/generate_synthetic.py`**（P2-1）：确定性合成样本生成器（默认参数逐字节复现现有样本；可参数化生成变体；`--verify` 校验可复现性）。
- **`scripts/redact.py`**（P0-3）：数电票 XML 脱敏流水线（税号打码/票号替换/名称映射/签名清理，幂等）。
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
