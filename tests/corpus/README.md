# 语料库（真实票样 + 官方实例 + 合成样本，已脱敏）v0.3

> P2 前置：DeepSeek 里程碑评审要求——"测试升级为语料库驱动"（≥3 省份/3 开票系统 + XBRL + 红冲 + 差额征税 + 多税率）。
> 本目录存放数电票 XML 样本（真实票样脱敏 / 官方 XBRL 实例脱敏 / 合成样本分目录），供 parser 回归测试。
> **结构化元数据见 `manifest.json`（省份/系统/版本/票种/场景/来源/脱敏状态/SHA256），README 仅为人工速览。**
> 合成样本构造规范见 `SYNTHETIC.md`（合法性边界/字段依据/构造硬规则/未核验项）。

## 元数据清单

### 真实票样（3 省 3 系统，已脱敏）

| 文件 | 省份 | 开票系统 | 场景 | 结构方言 | 来源 | 脱敏状态 |
|---|---|---|---|---|---|---|
| official-gd-special.xml | 广东（SWEI4400） | 标准税局系统（财政部样例） | 蓝字·增值税专用发票·一般纳税人·人力资源服务·6% | EInvoice 英文结构 | 财政部《电子凭证会计数据标准(试行版)》官方公开样例（商业新知 https://www.shangyexinzhi.com/article/8265084.html） | 已脱敏（公司/税号/电话/账号/开票人虚构） |
| gaode-js-taxi.xml | 江苏（SWEI3200） | 网约车平台（高德打车/365约车） | 蓝字·普通发票·交通运输服务·3%征收率·两明细行·**含退款负行** | EInvoice 英文结构（Version=0.32，含 ptbh 节点） | CSDN 公开 dump 真实发票（https://blog.csdn.net/Mikowoo007/article/details/163025285） | 已脱敏（销售方虚构；买方沿用原博客打码） |
| bj-platform-it.xml | 北京（SWEI1100） | 电子发票服务平台·网页开票 | 蓝字·增值税专用发票·一般纳税人·信息技术服务·6%·大额票（25万）·含签名子树 | EInvoice 英文结构（Version=0.33） | 影刀RPA社区公开源码示例（https://www.yingdao.com/community/detaildiscuss?id=735785349741531136） | 已脱敏（身份虚构；SignatureValue 清空） |

### 官方 XBRL 实例（财政部推广应用版附件3，已脱敏）

| 文件 | 内容 | 用途 |
|---|---|---|
| official-einv-xbrl-special.xml | 专票入账信息结构化数据（xbrli 根 + einv 命名空间 + context/unit + 记账分录） | XBRL 覆盖项（结构多样性归档） |
| official-einv-xbrl-ordinary.xml | 普票入账信息结构化数据（借贷分录 500 元） | XBRL 覆盖项（结构多样性归档） |

> 来源：财政部会计司《电子凭证会计数据标准——全面数字化的电子发票（推广应用版）》附件3 实例文档（http://kjs.mof.gov.cn/zt/kuaijixinxihuajianshe/dzpzkjsjbzshsd/sjbz/202505/t20250519_3964020.htm ）。
> **能力边界**：parser 当前不支持 xbrli 结构解析（root 识别/字段语义面向票面 XML）；本样本为 XBRL 覆盖项归档，解析能力登记 P2 待办，不得静默当作票面解析。

### 合成样本（schema 依据构造，`synthetic/` 子目录，构造规范见 SYNTHETIC.md）

| 文件 | 覆盖项 | 构造口径 |
|---|---|---|
| synthetic-red-letter-cn.xml | 红冲 + 中文标签方言 | 北京 2611；红字 -2500/-150/-2650；EI390 被红冲蓝字号码/EI391 确认单编号；备注红冲原因 |
| synthetic-differential-einv.xml | 差额征税（EInvoice） | 广东 2644；旅游服务；KCE=200.00；800×6%=48；备注"差额征税：200.00。" |
| synthetic-multirate-einv.xml | 多税率（EInvoice） | 江苏 2632；三行 6%/9%/13%→600/63/663 |
| synthetic-pinyin-abbrev.xml | 国标拼音缩写 | 江苏 2632；餐饮 3%→57.28/1.72/59.00；FPHM/KPRQ/HJJE/HJSE/JSHJXX |

## 验收覆盖（v0.3：DeepSeek M1 仍 = partial）

| 要求 | 覆盖状态 | 说明 |
|---|---|---|
| ≥3 省份 | ✅ 3 省（广东/江苏/北京） | 真实票样 |
| ≥3 开票系统 | ⚠️ 3 系统（标准税局/网约车平台/服务平台·网页开票） | 北京系统类型 inferred/unknown，不计入硬结论 |
| 版本分叉 | ✅ 0.2 / 0.32 / 0.33 | 真实票样 |
| 每省 ≥2 份 | ❌ 每省 1 份（真实票） | v1 硬门（P1-1）；合成不计入 |
| XBRL | ✅ 官方实例×2（structure archive） | 覆盖项达成（official）；解析能力 P2 待办 |
| 红冲 | ✅ 合成样本（中文方言） | 覆盖项达成（synthetic）；真实 EInvoice 红冲结构待真实票核验 |
| 差额征税 | ✅ 合成样本（KCE/备注格式） | 覆盖项达成（synthetic）；真实票面布局待核验 |
| 多税率 | ✅ 合成样本（6/9/13%） | 覆盖项达成（synthetic）；真实票待补 |
| 中文标签/拼音方言 | ✅ 合成样本×2 | 覆盖项达成（synthetic） |

**P2 能力声明（DeepSeek P0-7，v0.3 更新）**：
- parser/API 对**红冲/差额/多税率**票面结构可解析（合成样本已进回归），但**业务真实性未核验**（合成构造，真实票一到须回填核验并修订）；
- **XBRL 实例文档解析不支持**（样本仅归档，不得静默当作票面解析）；
- 每省 ≥2 份真实票仍未达成，为 v1 硬门。

**来源分级（DeepSeek P0-2/P0-4）**：

| 文件 | source_tier | authority | license / redistribution |
|---|---|---|---|
| official-gd-special.xml | official_public_sample | high | 官方公开样例，可引用 / permitted |
| gaode-js-taxi.xml | public_blog_secondary_redacted | medium | 公开可见内容已二次脱敏，再分发需审计 / restricted |
| bj-platform-it.xml | community_public_secondary_redacted | low | unknown/需审计，默认版权保留 / restricted；系统类型 inferred |
| official-einv-xbrl-*.xml | official_public_sample | high | 官方公开实例文档，可引用 / permitted |
| synthetic/*.xml | synthetic_schema_based | medium | 自构造（合成标注），无版权问题 / permitted |

## 使用规则

1. **脱敏纪律**：真实票样一律先脱敏再入库（名称虚构、税号打码、电话/账号/开票人删除或虚构；金额/税率/日期保留；数字签名/证书类内容一律清空）；官方实例主体信息同样虚化。
2. **不采集他人发票**：语料仅来自①官方公开样例/实例 ②公开社区/博客源码示例（二次脱敏）③用户自有票样（本人/亲友同意）。
3. **合成样本显式标注**：`synthetic/` 子目录 + manifest `synthetic=true`/`source_tier=synthetic_schema_based`/`construction_basis`；合成样本不得计入真实票来源统计，不得冒充真实票。
4. **来源必标 + SHA256 绑定**：manifest.json 记录每条样本的来源 URL、脱敏状态与哈希；测试断言哈希匹配（防文件被静默改动）。
5. **版本化**：本目录不可变；新增票样/场景走 v0.3+，manifest.json `corpus_version` 同步升级。
6. 本目录进入公开仓库（invoice-precheck 为 Public），确保不含任何可识别真实主体信息。
