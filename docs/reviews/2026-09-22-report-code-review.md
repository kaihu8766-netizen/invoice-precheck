# 代码审查记录：report / main / frontend（DeepSeek 二轮）

- 日期：2026-09-22
- 审查人：DeepSeek（联合执行者，协作模式 v2.0 硬门）
- 结论：暂不可提交 → 修复后 25 项测试通过，真实服务闭环验证通过。

## 高危处理（P0，全部完成）

| 编号 | 问题 | 处理 |
|---|---|---|
| H-1 | 前端模板未全量转义 → 上传型 XSS（invoice_no/field/rule_id 直接插 HTML，severity 拼 class） | esc() 完整转义（含引号）+ 枚举白名单映射（SEV_CLS/CONF_ALLOW），所有插值统一 esc |
| H-2 | catch 里 e.message 未转义 → 反射型 XSS | 转义 + 服务端 _public_error 错误文案白名单（业务 ValueError 透出，其余统一安全文案） |
| H-3 | 无文件数/大小/总量限制，全量读内存 → DoS | MAX_FILES=200 / MAX_FILE_BYTES=10MB / MAX_TOTAL_BYTES=50MB，超限 413 或入 failed；parse_xml 内 detect_type 前置类型校验 |
| H-4 | 异常隔离不完备：read() 在 try 外、仅捕 ValueError | 读取+解析全链路 try/except Exception，坏文件不拖垮整批（测试：坏文件混入 200 且正常票出报告） |
| H-5 | "通过"绿标签 + 0 票显示"未发现风险点" → 隐性合规结论 | 措辞改"未见异常/未见规则命中"；0 票批次红色警示块"不构成任何合规结论"；pass_count → no_finding_count |

## 中危处理（P1，已完成）

- M-1 状态判定纳入 parse_warnings（字段缺失 → "待复核（字段缺失）"，明细带告警数）✓
- M-2 _money 防护：None/非 Decimal/NaN → "—"，统一普通计数法 ✓
- M-3 summary 增 file_count，标签注明"成功解析（共 N 个文件）" ✓
- M-4 免责文案单一来源：后端注入前端 {{DISCLAIMER}}，测试断言页面含原文 ✓
- M-5 raw_fields 默认不外传（invoice_list 移除；invoice_to_dict 收敛）✓
- M-6 安全响应头中间件（nosniff/frame DENY/referrer）✓（强 CSP 待静态资源外置后做，记录）
- M-8 a11y：sr-only 文件输入 + focus-within、aria-live、表格 caption/thead/scope + overflow-x、低对比度标签修复 ✓
- M-9 报告增 generated_at/batch_id/rules 规则清单 ✓
- M-7 前端容错（非 JSON 兜底/60s 超时/render 包裹）✓（部分）

## 遗留（下一迭代）

- M-7 完整版、ZIP 打包上传提示、重复票金额口径、批次级 Finding 归属、导出（PDF/CSV/JSON）、强 CSP、审计指标、分页/虚拟滚动、编码提示（GB18030/GBK 转 UTF-8）、并发幂等/限流、时区统一、`<noscript>`。

## 验证

- 单元+集成测试 25 项全过（API 8：含 0 票短路/超限/坏文件隔离/注入串存活/免责注入）
- 真实服务实测：uvicorn 起服务 → GET / 返回页面；POST /review 重复票命中 R1 + PDF 拒绝，报告完整（batch_id/ruleset 输出）
