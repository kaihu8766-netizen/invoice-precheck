# 公开仓发布检查报告（PUBLISH_CHECK_REPORT）

> 生成：2026-09-27 ｜ 净化仓 invoice-precheck（public 新建空仓）发布前自查 ｜ 证据文件供 DeepSeek 评审直接核验

## 1. manifest SHA256 逐条核验（manifest 值 vs 磁盘实际字节）

| 样本文件 | manifest sha256 | 磁盘实际 sha256 | 匹配 |
|---|---|---|---|
| official-gd-special.xml | `3888c6d6e5cb2f41…` | `3888c6d6e5cb2f41…` | YES |
| gaode-js-taxi.xml | `d707141398788a58…` | `d707141398788a58…` | YES |
| bj-platform-it.xml | `f6597f59dd6b447f…` | `f6597f59dd6b447f…` | YES |
| official-einv-xbrl-special.xml | `6d6b4d31841d469b…` | `6d6b4d31841d469b…` | YES |
| official-einv-xbrl-ordinary.xml | `9d9341e62bfd5515…` | `9d9341e62bfd5515…` | YES |
| synthetic/synthetic-red-letter-cn.xml | `4d32df2510ae4044…` | `4d32df2510ae4044…` | YES |
| synthetic/synthetic-differential-einv.xml | `cbca9b0e1c9a4219…` | `cbca9b0e1c9a4219…` | YES |
| synthetic/synthetic-multirate-einv.xml | `d3d2d5e4c1e143e3…` | `d3d2d5e4c1e143e3…` | YES |
| synthetic/synthetic-pinyin-abbrev.xml | `7607124cd6ef3cf5…` | `7607124cd6ef3cf5…` | YES |
| ofd/ofd-container-gd-sample.ofd | `2784d04c06d7f862…` | `2784d04c06d7f862…` | YES |

**全部匹配：是**（10 条样本）

## 2. 敏感特征扫描（全仓）

扫描对象：真实公司名 / 真实税号（18位） / 真实票号（20位） / 历史泄露数据特征 / 疑似地区识别前缀。
说明：为遵守『公开仓零敏感特征』纪律，扫描词表完整值仅存本机（`.gitignore` 保护区），**本报告不展示任何具体特征值**；扫描器（脚本）在本机运行，输出命中列表。

结果：**零命中**（排除 `.git/` 与 `benchmark/private/` 后全仓扫描）。

## 3. validate_manifest 输出

```text
OK：manifest.json 结构与数据一致性校验通过。
```

## 4. 全量测试输出

```text
.................
----------------------------------------------------------------------
Ran 187 tests in 43.926s
OK
```

注：`Ran N tests` 为 unittest 方法级计数；evidence-pack 中 pytest 21+186=207 含 subtest 计数，两者口径不同。

## 5. 净化仓边界声明

- 公开仓不含：`benchmark/private/`（gitignore:26）、`trace/`（内部规范已移本机）、`scripts/redact.py`、`scripts/anonymize_*.py`、`tests/test_redact.py`
- 样本口径：官方样例=已脱敏（主体虚化）；结构仿真（gaode/bj）=全虚构；合成=自构造
- 密钥形态：全仓扫描无 `sk-*` / `ghp_*` / `github_pat_*` 命中