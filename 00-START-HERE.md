
### 双闸门（RV-13）
- 闸门 1：任何提交必须带 ID 引用。
- 闸门 2：红线文件（解析/规则/OCR/版式/脱敏）改动必须带"已批准且 diff_hash 匹配"的 RV-ID，否则拒绝提交并提示先评审。
- 工作流：git add → classify --staged 看命中 → deepseek_gate.py 评审（自动记录 hash）→ commit 带 RV。
