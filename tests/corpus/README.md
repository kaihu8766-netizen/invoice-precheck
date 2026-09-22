# 语料库（真实票样，已脱敏）v0.2

> P2 前置：DeepSeek 里程碑评审要求——"测试升级为语料库驱动"（≥3 省份/3 开票系统 + XBRL + 红冲 + 差额征税 + 多税率）。
> 本目录存放**真实结构**的数电票 XML 票样（脱敏后），供 parser 回归测试。合成样本（validation-week）与真实票样分目录管理。
> **结构化元数据见 `manifest.json`（省份/系统/版本/票种/场景/来源/脱敏状态/SHA256），README 仅为人工速览。**

## 元数据清单

| 文件 | 省份 | 开票系统 | 场景 | 结构方言 | 来源 | 脱敏状态 |
|---|---|---|---|---|---|---|
| official-gd-special.xml | 广东（SWEI4400） | 标准税局系统（财政部样例） | 蓝字·增值税专用发票·一般纳税人·人力资源服务·6% | EInvoice 英文结构 | 财政部《电子凭证会计数据标准(试行版)》官方公开样例（商业新知 https://www.shangyexinzhi.com/article/8265084.html） | 已脱敏（公司/税号/电话/账号/开票人虚构） |
| gaode-js-taxi.xml | 江苏（SWEI3200） | 网约车平台（高德打车/365约车） | 蓝字·普通发票·交通运输服务·3%征收率·两明细行·**含退款负行**（Amount=-0.96 不含税 / TotaltaxIncludedAmount=-0.99 含税） | EInvoice 英文结构（Version=0.32，含 ptbh 节点） | CSDN 公开 dump 真实发票（https://blog.csdn.net/Mikowoo007/article/details/163025285） | 已脱敏（销售方虚构；买方沿用原博客打码） |
| bj-platform-it.xml | 北京（SWEI1100） | 电子发票服务平台·网页开票 | 蓝字·增值税专用发票·一般纳税人·信息技术服务·6%·大额票（25万）·单明细行·含完整签名子树 | EInvoice 英文结构（Version=0.33，SellerAuthentication=01） | 影刀RPA社区公开源码示例（https://www.yingdao.com/community/detaildiscuss?id=735785349741531136） | 已脱敏（身份字段虚构/沿用原帖打码；SignatureValue 清空） |

## 验收覆盖（DeepSeek 评审：M1 = partial，2026-09-23 v0.2 增量评审）

| 要求 | 覆盖状态 | 评审状态 |
|---|---|---|
| ≥3 省份 | ✅ 3 省（广东/江苏/北京） | 成立（北京由 SWEI1100 + TaxBureauCode=11100000000 支撑） |
| ≥3 开票系统 | ⚠️ 3 系统（标准税局/网约车平台/服务平台·网页开票） | 北京系统类型为 inferred/unknown（UndefinedLabel 上下文），不计入硬结论 |
| 版本分叉 | ✅ 0.2 / 0.32 / 0.33 | 成立 |
| 票种覆盖 | ✅ 专票×2 + 普票×1（含负行） | 成立 |
| 每省 ≥2 份 | ❌ 每省 1 份 | v1 硬门（P1-1） |
| XBRL | ❌ 待建 | v1 硬门（P1-3） |
| 红冲 | ❌ 待真实票样 | v1 硬门（P1-2） |
| 差额征税 | ❌ 待票样（EI386 字典已备） | v1 硬门（P1-2） |
| 多税率 | ❌ 待收集 | v1 硬门（P1-2） |
| 中文标签/拼音方言 | ❌ 待建 | v1 硬门（P1-3） |

**P2 能力声明（DeepSeek P0-7）**：本仓库 P2 阶段**不支持**红冲、差额征税、多税率、XBRL、中文标签方言、拼音缩写方言；parser/API 对这些场景不得静默通过，须返回 unsupported/confidence low。

**来源分级（DeepSeek P0-2/P0-4）**：

| 文件 | source_tier | authority | license / redistribution |
|---|---|---|---|
| official-gd-special.xml | official_public_sample | high | 官方公开样例，可引用 / permitted |
| gaode-js-taxi.xml | public_blog_secondary_redacted | medium | 公开可见内容已二次脱敏，再分发需审计 / restricted |
| bj-platform-it.xml | community_public_secondary_redacted | low | unknown/需审计，默认版权保留 / restricted；系统类型 inferred/unknown，待官方/沙箱验证或替换 |

## 使用规则

1. **脱敏纪律**：真实票样一律先脱敏再入库（名称虚构、税号打码、电话/账号/开票人删除或虚构；金额/税率/日期保留——规则验证需要；数字签名/证书类内容一律清空）。
2. **不采集他人发票**：语料仅来自①官方公开样例 ②公开社区/博客源码示例（二次脱敏）③用户自有票样（本人/亲友同意）。
3. **合成样本分开**：`validation-week/` 合成样本测"问题形态命中/误报"，本目录测"真实结构可解析"。
4. **来源必标 + SHA256 绑定**：manifest.json 记录每条票样的来源 URL、脱敏状态与哈希；测试断言哈希匹配（防文件被静默改动）。
5. **版本化**：本目录不可变；新增票样/场景走 v0.2+，manifest.json `corpus_version` 同步升级。
6. 本目录进入公开仓库（invoice-precheck 为 Public），确保不含任何可识别真实主体信息。
