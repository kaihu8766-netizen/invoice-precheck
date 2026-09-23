"""企业主体配置存储（RV-42 落地，F-20260923-04）。

- 本地 data/config.json 为主入口（不入 Git；前端设置面板写入）
- 环境变量 INVOICE_COMPANY_NAME / INVOICE_COMPANY_TAXID 高级覆盖（Docker/批量用）
- 首版单主体；company_entities 保持数组结构，后续多主体/角色扩展不换协议
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONFIG_PATH = DATA_DIR / "config.json"

DEFAULT_CONFIG: dict = {
    "company_entities": [
        {"id": "default", "name": "", "tax_id": "", "enabled": True},
    ],
    "r2": {
        "tax_id_mismatch_level": "high",
        "name_mismatch_level": "low",
        "missing_field_level": "low",
    },
}


def _deep_copy(obj):
    return json.loads(json.dumps(obj, ensure_ascii=False))


def load_config() -> dict:
    """加载配置：默认值 <- 本地文件 <- 环境变量覆盖。任何异常回退默认，不崩溃。"""
    cfg = _deep_copy(DEFAULT_CONFIG)
    try:
        if CONFIG_PATH.exists():
            d = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for k in ("company_entities", "r2"):
                if isinstance(d.get(k), type(cfg[k])):
                    cfg[k] = d[k]
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


def to_rules_config(cfg: dict):
    """把存储配置映射为 rules.RulesConfig（延迟 import 避免循环）。"""
    from .rules import RulesConfig

    rc = RulesConfig()
    ent = active_entity(cfg)
    if ent and (ent.get("name") or ent.get("tax_id")):
        rc.company_name = normalize_name(ent.get("name") or "")
        rc.company_taxid = normalize_tax_id(ent.get("tax_id") or "")
    return rc
