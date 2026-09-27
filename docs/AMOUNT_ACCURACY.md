# 金额与时间精度口径（#32）

> 本文件定义本项目金额/日期计算的全链路口径，作为测试与实现的单一依据（RV-131 采纳 + #32 边界补全）。

## 金额口径

| 环节 | 口径 | 依据 |
|---|---|---|
| 解析层 | 全链路 `Decimal(str)`，**禁 float** | app/parser.py 头部注释 |
| 勾稽校验 | `abs((amount + tax) - total) > 0.01` 才记"勾稽异常"；**容差 ±0.01 为闭区间**（差额恰 0.01 不报） | app/parser.py:301 |
| 行级税额 | `(amount × rate).quantize("0.01")`，**默认 ROUND_HALF_EVEN（银行家舍入，非四舍五入）** | app/rules.py:656 |
| 前端汇总 | `Math.round(Number(x) * 100)` 整数分计算后 ÷100；summary 字段**存元**（RV-125） | docs/index.html |
| 半舍边界示例 | 0.005→0.00；0.015→0.02；0.025→0.02；0.035→0.04（期望值在测试中显式写死，防伪测试） | tests/test_parser.py |

## 时间口径（R7 跨期）

| 规则 | 口径 | 边界 |
|---|---|---|
| 跨期报销 | 开票距报销 `> 365 天` 才报；**恰 365 天不报**（闭区间语义与容差一致） | tests/test_rules.py |
| 未来日期 | 开票晚于今天 → 报 | app/rules.py:415 |
| 跨月/跨年/闰年 | 按 `datetime.date` 真实天数差计算，跨月短间隔不误报，闰年 2 月按实际天数 | tests/test_rules.py |

## 回归命令

```bash
INVOICE_ALLOW_DEV_KEY=1 python3 -m unittest discover -s tests   # 期望 192 passed
```
