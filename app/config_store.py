"""配置存储（F-04 主体配置 + F-05 规则校准）。

- 本地 data/config.json 为主入口（不入 Git；前端设置面板写入）
- 环境变量 INVOICE_COMPANY_NAME / INVOICE_COMPANY_TAXID 高级覆盖（Docker/批量用；必须成套）
- rules 段：R3/R4/R6/R8 阈值白名单校准（RV-48）；叶子字段白名单，未知字段拒绝；缺失回退默认
- 优先级：默认值 < 本地文件 < 环境变量（env 只覆盖主体，不参与规则）
- 首版单主体单实例；company_entities 保持数组结构，后续多主体/角色扩展不换协议
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONFIG_PATH = DATA_DIR / "config.json"

# 规则阈值白名单（RV-48 校验区间表 + RV-50 复核放宽）：叶子字段级，禁止提交任意 rules 对象
# RV-50 语义说明：serial_max_gap 是"相邻票号最大差值"；0 = 完全同号聚合（重复检测），建议业务配置 ≥1
RULES_WHITELIST: dict[str, dict] = {
    "serial_tail_len": {"type": "int", "min": 2, "max": 8, "default": 4},
    "serial_max_gap": {"type": "int", "min": 0, "max": 10, "default": 2},
    "serial_min_count": {"type": "int", "min": 2, "max": 500, "default": 3},
    "category_limits.travel": {"type": "money", "min": 1, "max": 100_000_000, "default": 5000},
    "category_limits.entertain": {"type": "money", "min": 1, "max": 100_000_000, "default": 3000},
    "category_limits.office": {"type": "money", "min": 1, "max": 100_000_000, "default": 2000},
    "category_limits.transport": {"type": "money", "min": 1, "max": 100_000_000, "default": 1000},
    "category_limits.other": {"type": "money", "min": 1, "max": 100_000_000, "default": 5000},
    "default_limit": {"type": "money", "min": 1, "max": 100_000_000, "default": 5000},
    "concentrate_threshold": {"type": "int", "min": 2, "max": 100, "default": 6},
    "max_plausible_total": {"type": "money", "min": 1_000, "max": 1_000_000_000, "default": 30_000_000},
}

# 中文类别 → 稳定英文 key（前端展示中文，存储/规则用英文枚举）
CATEGORY_KEYS = {"差旅": "travel", "招待": "entertain", "办公": "office", "交通": "transport", "其他": "other"}

DEFAULT_RULES: dict = {
    "serial_tail_len": 4,
    "serial_max_gap": 2,
    "serial_min_count": 3,
    "category_limits": {"travel": 5000, "entertain": 3000, "office": 2000, "transport": 1000, "other": 5000},
    "default_limit": 5000,
    "concentrate_threshold": 6,
    "max_plausible_total": 30_000_000,
}


def _deep_copy(obj):
    return json.loads(json.dumps(obj, ensure_ascii=False))


# RV-125（DISC-01/#31）：schema 版本——升版旧配置可迁移；未知高版本拒绝加载（不静默）
SCHEMA_VERSION = 1

DEFAULT_CONFIG: dict = {
    "schema_version": SCHEMA_VERSION,
    "company_entities": [
        {"id": "default", "name": "", "tax_id": "", "enabled": True},
    ],
    "r2": {
        "tax_id_mismatch_level": "high",
        "name_mismatch_level": "low",
        "missing_field_level": "low",
    },
    "rules": _deep_copy(DEFAULT_RULES),
    "meta": {"rules_updated_at": "", "rules_hash": ""},
}


def rules_hash(cfg: dict) -> str:
    """当前 rules 段内容哈希（审计快照：报告标注/前端回显/变更留痕）。"""
    import hashlib
    return hashlib.sha256(json.dumps(cfg.get("rules", {}), sort_keys=True,
                                     ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def rules_sha256_full(cfg: dict) -> str:
    """完整 SHA256（RV-50 审计加强：rules_hash 前 16 位之外可还原全量摘要）。"""
    import hashlib
    return hashlib.sha256(json.dumps(cfg.get("rules", {}), sort_keys=True,
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def is_default_rules(cfg: dict) -> bool:
    """RV-50 意见 7：逐叶子比较生效值，全部等于默认才算"未自定义"（防部分字段/空对象误标 custom）。"""
    r = cfg.get("rules") or {}
    for key, spec in RULES_WHITELIST.items():
        if key.startswith("category_limits."):
            cat = key.split(".", 1)[1]
            cl = r.get("category_limits") or {}
            val = cl.get(cat)
            if val is None:
                val = DEFAULT_RULES["category_limits"].get(cat)
        else:
            val = r.get(key)
            if val is None:
                val = DEFAULT_RULES.get(key)
        if val is None:
            continue
        if isinstance(spec.get("default"), str):
            if str(val) != str(spec["default"]):
                return False
        elif val != spec["default"]:
            return False
    return True


def load_config() -> dict:
    """加载配置：默认值 <- 本地文件 <- 环境变量覆盖。任何异常回退默认，不崩溃。

    RV-125（#31）schema 版本语义：
    - 文件版本 < SCHEMA_VERSION → 按版本分支迁移（v0→v1：补默认字段）
    - 文件版本 > SCHEMA_VERSION → 拒绝加载、回退默认并显式告警（不静默用旧数据覆盖新语义）
    """
    cfg = _deep_copy(DEFAULT_CONFIG)
    try:
        if CONFIG_PATH.exists():
            d = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            ver = int(d.get("schema_version", 0) or 0)
            if ver > SCHEMA_VERSION:
                import logging as _lg
                _lg.getLogger("invoice-precheck").error(
                    "config.json schema_version=%s 高于当前支持 %s——拒绝加载，回退默认（升级程序后再读）",
                    ver, SCHEMA_VERSION)
                return cfg
            # 迁移：v0 → v1（无字段变化，仅补版本号；未来 v1→v2 在此分支）
            if ver < SCHEMA_VERSION:
                import logging as _lg
                _lg.getLogger("invoice-precheck").warning(
                    "config.json schema_version=%s 低于当前 %s——按迁移路径升级", ver, SCHEMA_VERSION)
            for k in ("company_entities", "r2", "rules", "meta"):
                if k in d and isinstance(d[k], type(cfg[k])):
                    cfg[k] = d[k]
            cfg["schema_version"] = SCHEMA_VERSION
    except Exception:  # 文件损坏/格式异常 → 用默认并保持可写
        pass
    env_name = os.environ.get("INVOICE_COMPANY_NAME", "").strip()
    env_taxid = os.environ.get("INVOICE_COMPANY_TAXID", "").strip()
    if env_name and env_taxid:
        # RV-43：env 必须成套出现（防"A 公司名+B 税号"混合主体）；只设一个时忽略两个
        ent = cfg["company_entities"][0]
        ent["name"] = env_name
        ent["tax_id"] = env_taxid
    elif env_name or env_taxid:
        import logging as _lg
        _lg.getLogger("invoice-precheck").warning(
            "INVOICE_COMPANY_NAME / INVOICE_COMPANY_TAXID 必须成套设置，本次均忽略"
        )
    return cfg


def save_config(cfg: dict) -> None:
    """原子保存到本地 data/config.json（RV-43/44：tmp+fsync+os.replace，防写中断截断）。

    - POSIX：data/ 0700、文件 0600（os.open 创建即限权）；目录 fsync 保证落盘
    - Windows：mode 无效（权限靠目录 ACL，文档声明）、目录 fsync 不可用（跳过）、
      os.replace 偶发共享冲突 → 指数退避重试 3 次
    """
    import time
    cfg = dict(cfg)
    cfg["schema_version"] = SCHEMA_VERSION  # RV-125（#31）：保存时固化版本，杜绝旧文件无版本
    payload = json.dumps(cfg, ensure_ascii=False, indent=2).encode("utf-8")
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    try:
        DATA_DIR.mkdir(mode=0o700, exist_ok=True)
    except OSError:
        DATA_DIR.mkdir(exist_ok=True)  # Windows 无 mode 语义
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        # 目录 fsync：POSIX 有效；Windows os.open 目录必失败 → 跳过
        if hasattr(os, "O_DIRECTORY"):
            try:
                dfd = os.open(DATA_DIR, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            except OSError:
                pass
        # replace：同目录原子；Windows 偶发 WinError 5 → 指数退避重试 3 次
        for attempt in range(3):
            try:
                os.replace(tmp, CONFIG_PATH)
                break
            except OSError:
                if attempt == 2:
                    raise
                time.sleep(0.2 * (attempt + 1))
        if hasattr(os, "chmod"):
            try:
                os.chmod(CONFIG_PATH, 0o600)
            except OSError:
                pass
    except OSError as e:
        raise ValueError(f"配置保存失败：{e}") from e
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def normalize_tax_id(s: str) -> str:
    """税号归一：去空白/全半角统一/大小写统一。"""
    if not s:
        return ""
    out = []
    for ch in str(s):
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out).replace(" ", "").upper()


def normalize_name(s: str) -> str:
    """名称归一：去空白（含全半角）、统一括号、大小写统一（税号/字母）。"""
    if not s:
        return ""
    out = []
    for ch in str(s):
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out).replace(" ", "")


_PLACEHOLDER_SET = None


def _placeholder_set():
    """惰性构造占位符集合：'0'*18 等形态。"""
    global _PLACEHOLDER_SET
    if _PLACEHOLDER_SET is None:
        _PLACEHOLDER_SET = {
            "0" * n for n in range(15, 21)
        } | {
            "X" * n for n in range(15, 21)
        } | {
            "N/A", "NA", "-", "--", "无", "暂无", "待补充", "空", "NULL",
            "None", "undefined", "nan"
        }
    return _PLACEHOLDER_SET


def is_plausible_tax_id(s) -> bool:
    """无效税号识别（RV-43/44：占位符/缺失不得参与强校验，避免误判高风险）。

    判定顺序（先归一后判定）：归一 → 空 → 白名单 ^[0-9A-Z]+$（normalize 后，
    全角/小写/空白/连字符已统一）→ 长度区间 [15,20]（18 位统一码 / 15 位老税号）
    → 全 0 / 全 X / 占位词（N/A、-、无、NULL 等）。
    """
    import re
    if not s:
        return False
    s2 = normalize_tax_id(s)
    if not re.fullmatch(r"[0-9A-Z]+", s2):
        return False
    if not (15 <= len(s2) <= 20):
        return False
    if s2 in _placeholder_set():
        return False
    return True


def active_entity(cfg: dict) -> dict | None:
    """首个启用的主体；无配置/未启用 → None。"""
    ents = cfg.get("company_entities") or []
    for e in ents:
        if e.get("enabled"):
            return e
    return None


def validate_rules_payload(rules: dict) -> tuple[dict | None, str]:
    """校验前端提交的 rules 段（RV-48）：叶子白名单 + 区间；整包原子，任一非法整体拒绝。

    返回 (清洗后的 rules, None) 或 (None, 错误文案)。允许只提交部分字段（缺失回退默认）。
    """
    if not isinstance(rules, dict):
        return None, "规则配置必须是对象"
    out = _deep_copy(DEFAULT_RULES)
    for key, spec in RULES_WHITELIST.items():
        # 支持 category_limits.travel 点路径
        if "." in key:
            section, sub = key.split(".")
            if section not in out or not isinstance(out.get(section), dict):
                continue
            if sub in rules.get(section, {}):
                val = rules[section][sub]
                ok, err = _check_one(val, spec, key)
                if not ok:
                    return None, err
                out[section][sub] = val
        else:
            if key in rules:
                val = rules[key]
                ok, err = _check_one(val, spec, key)
                if not ok:
                    return None, err
                out[key] = val
    # 未知字段：拒绝并报错（防拼写错误静默失效）
    known = set(RULES_WHITELIST) | {"category_limits"}
    for k in rules:
        if k not in known:
            return None, f"未知规则字段：{k}"
    if "category_limits" in rules:
        for k in rules["category_limits"]:
            if f"category_limits.{k}" not in RULES_WHITELIST:
                return None, f"未知类别字段：{k}"
    return out, None


def _check_one(val, spec: dict, key: str) -> tuple[bool, str]:
    try:
        if spec["type"] == "int":
            v = int(val)
            if not (spec["min"] <= v <= spec["max"]):
                return False, f"{key} 应在 {spec['min']}-{spec['max']} 之间"
            return True, ""
        # money：支持数字或字符串数字（前端可传 "30000000"），拒绝 float 语义混淆
        v = int(str(val).strip())
        if not (spec["min"] <= v <= spec["max"]):
            return False, f"{key} 应在 {spec['min']}-{spec['max']} 元之间"
        return True, ""
    except (ValueError, TypeError):
        return False, f"{key} 必须是整数（{spec['min']}-{spec['max']}）"


def to_rules_config(cfg: dict):
    """把存储配置映射为 rules.RulesConfig（延迟 import 避免循环）。含规则阈值覆盖（RV-48）。"""
    from decimal import Decimal as _D
    from .rules import RulesConfig

    rc = RulesConfig()
    ent = active_entity(cfg)
    if ent and (ent.get("name") or ent.get("tax_id")):
        rc.company_name = normalize_name(ent.get("name") or "")
        rc.company_taxid = normalize_tax_id(ent.get("tax_id") or "")
    # 规则阈值覆盖（白名单已验证入库；缺失回退默认）
    r = cfg.get("rules") or {}
    rc.serial_tail_len = int(r.get("serial_tail_len", DEFAULT_RULES["serial_tail_len"]))
    rc.serial_max_gap = int(r.get("serial_max_gap", DEFAULT_RULES["serial_max_gap"]))
    rc.serial_min_count = int(r.get("serial_min_count", DEFAULT_RULES["serial_min_count"]))
    rc.concentrate_threshold = int(r.get("concentrate_threshold", DEFAULT_RULES["concentrate_threshold"]))
    rc.max_plausible_total = _D(str(r.get("max_plausible_total", DEFAULT_RULES["max_plausible_total"])))
    rc.default_limit = _D(str(r.get("default_limit", DEFAULT_RULES["default_limit"])))
    cl = r.get("category_limits") or {}
    zh2en = {v: k for k, v in CATEGORY_KEYS.items()}
    rc.category_limits = {
        zh2en.get(en, en): _D(str(cl.get(en, DEFAULT_RULES["category_limits"].get(en, 5000))))
        for en in ("travel", "entertain", "office", "transport", "other")
    }
    return rc
