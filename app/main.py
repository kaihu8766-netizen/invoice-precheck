"""FastAPI 入口（P1：上传数电票 XML → 一页风险报告）。

接口（DeepSeek 评审：/parse 与 /review 分离，可独立测试替换）：
- GET  /         单页前端（免责文案由后端注入，单一来源；公开）
- GET  /healthz  存活探活（公开，无业务数据）
- POST /parse    单文件解析 → NormalizedInvoice（需 X-API-Key）
- POST /review   整批解析+规则 → 一页风险报告（需 X-API-Key）

鉴权（D 阶段公网部署前置，方案 A：Cloudflare Tunnel 本地穿透）：
- API Key 经环境变量 INVOICE_API_KEY 注入；未设置时必须显式 INVOICE_ALLOW_DEV_KEY=1
  才允许开发默认 key（RV-125/#11 硬门：否则拒绝启动——防公网误用默认 key 暴露）
- 请求头 X-API-Key 比对（hmac.compare_digest 防时序攻击）；缺失/错误 → 401
- CORS：本地开发（INVOICE_ALLOW_DEV_KEY=1）放行所有源；否则默认收紧 localhost:8000
  （RV-125；生产白名单见部署文档）

安全边界（二轮审查 P0）：
- 文件数/单文件/总大小上限（防内存 DoS）；类型前置校验（parse_document 内 detect_type：
    xml 直接解析 / ofd 解包提取内嵌 XML / 其他类型明确报错）
- 文件级失败隔离：读取+解析全链路 try/except，坏文件不拖垮整批
- 对外错误文案白名单：业务 ValueError 透出，其余统一安全文案（不泄漏内部细节/输入片段）
- 安全响应头：nosniff / frame DENY / referrer
"""
from __future__ import annotations

import hmac
import logging
import os
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from .config_store import load_config, save_config, to_rules_config
from .llm_explain import LLM_ENABLED, explain_findings
from .parser import parse_document
from .report import DISCLAIMER, build_report, invoice_to_dict
from .rules import RULESET_VERSION, run_rules_with_states

logger = logging.getLogger("invoice-precheck")

# 企业主体配置（RV-42）：启动加载本地 data/config.json；PUT /api/config 热更新
_APP_CONFIG = load_config()


def _current_rules_config():
    return to_rules_config(_APP_CONFIG)

MAX_FILES = 200
MAX_FILE_BYTES = 10 * 1024 * 1024   # 10MB（与 parser.MAX_FILE_SIZE 一致）
MAX_TOTAL_BYTES = 50 * 1024 * 1024  # 50MB

# 鉴权配置（D 阶段）：生产 key 由部署方经环境变量注入；本地未设置时用开发默认 key
# RV-125（DISC-01/#11）：默认 key 必须显式声明本地开发才允许——公网误用默认 key 从概率问题变为不可能。
# 硬门：未设 INVOICE_API_KEY 且未显式 INVOICE_ALLOW_DEV_KEY=1 → 拒绝启动（不静默降级为 warning）。
DEV_API_KEY = "dev-invoice-precheck-key"
ENV = os.environ.get("INVOICE_ENV", "dev").lower()
ALLOW_DEV_KEY = os.environ.get("INVOICE_ALLOW_DEV_KEY") == "1"
# RV-367（T-014 缺口1）：生产环境禁止 ALLOW_DEV_KEY——防运维为排障带默认 key 起服务并经 Tunnel 暴露。
if ENV == "prod" and ALLOW_DEV_KEY:
    raise RuntimeError(
        "INVOICE_ENV=prod 与 INVOICE_ALLOW_DEV_KEY=1 互斥——生产部署禁止默认开发 key，拒绝启动（RV-367 硬门）"
    )
if not os.environ.get("INVOICE_API_KEY") and not ALLOW_DEV_KEY:
    raise RuntimeError(
        "INVOICE_API_KEY 未设置且未声明 INVOICE_ALLOW_DEV_KEY=1（仅限本机开发）——"
        "拒绝启动：防开发默认 key 在公网/内网暴露（RV-125 硬门）"
    )
API_KEY = os.environ.get("INVOICE_API_KEY") or DEV_API_KEY

# T-014 前置③ / T-056：企业级审计（SQLite append-only + 哈希链，数据不出机）
from .audit import AuditStore
AUDIT_DB = Path(__file__).resolve().parent.parent / "data" / "audit.db"
AUDIT = AuditStore(AUDIT_DB)

def _fp(data: bytes) -> str:
    """文件指纹（SHA256 前 16 位）——审计标识原始文件，不存明文。"""
    import hashlib as _hl
    return _hl.sha256(data).hexdigest()[:16]


def _audit_name(name: str | None) -> str:
    """审计用文件名脱敏（RV-368 条件2）：只保留扩展名——文件名可能含 PII（如'张三发票.jpg'）。"""
    return (Path(name or "").suffix or "?")[:16].lower()
if not os.environ.get("INVOICE_API_KEY"):
    logger.warning(
        "INVOICE_API_KEY 未设置，已显式声明 INVOICE_ALLOW_DEV_KEY=1（仅限本机开发；公网部署必须设置强 key）"
    )
# 生产环境强制强 key（≥16 位），拒绝弱 key（RV-367：文档声明不如代码强制）
if ENV == "prod" and len(API_KEY or "") < 16:
    raise RuntimeError("INVOICE_ENV=prod 要求 INVOICE_API_KEY ≥ 16 位——拒绝弱 key 启动（RV-367 硬门）")

app = FastAPI(title="发票合规预审", version="0.1.0")

# RV-367（T-014 缺口1）：CORS 源由 INVOICE_CORS_ORIGINS 白名单注入（逗号分隔）。
# prod 未设白名单 → 仅 localhost（fail-closed）；dev 且 ALLOW_DEV_KEY=1 → 通配（本地调试）。
_cors_env = os.environ.get("INVOICE_CORS_ORIGINS", "").strip()
if _cors_env:
    _cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
elif ENV == "prod":
    _cors_origins = ["http://localhost:8000"]
else:
    _cors_origins = ["*"] if ALLOW_DEV_KEY else ["http://localhost:8000"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["X-API-Key", "Content-Type"],
)

# 产品前端统一用 docs/index.html（完整版：演示模式+实时模式+复核工作台+导出）；
# 旧 frontend/index.html（204 行精简版）废弃保留，不参与服务。
FRONTEND = Path(__file__).resolve().parent.parent / "docs" / "index.html"
_DOCS = FRONTEND.parent  # docs/：仅显式白名单文件对外（PWA 资源），不整目录挂载
_PWA_STATIC = {
    "/manifest.json": _DOCS / "manifest.json",
    "/service-worker.js": _DOCS / "service-worker.js",
    "/assets/icon-192.png": _DOCS / "assets" / "icon-192.png",
    "/assets/icon-512.png": _DOCS / "assets" / "icon-512.png",
}

# 无需鉴权的公开路径（仅静态页面与探活，无业务数据）
PUBLIC_PATHS = {"/", "/healthz", *(_PWA_STATIC.keys())}


@app.exception_handler(Exception)
async def unhandled_exception(request, exc):
    """全局兜底：未捕获异常一律返回通用文案，不泄漏堆栈/内部细节（里程碑评审安全项）。"""
    logger.exception("unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "服务内部错误，请稍后重试"})


@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


# ---- 限流（RV-367 缺口2：公网暴露后 key 爆破与 DoS 面）----
# IP 维度滑动窗口：业务端点 60 秒窗口；窗口内超限 → 429。
import collections
import ipaddress as _ipaddr

_RATE_LIMIT_WINDOW = 60          # 秒
_RATE_LIMIT_MAX = 60             # 每窗口最大请求（业务端点合计；静态页不受限）
_ratelimit: dict[str, list[float]] = collections.defaultdict(list)

_PROTECTED_PREFIXES = ("/parse", "/review", "/api/", "/export", "/healthz")


def _client_ip(request: Request) -> str:
    """取客户端 IP（Tunnel 部署经 X-Forwarded-For 头，信任最近一跳；本地取直连地址）。

    RV-368 条件3（暴露面说明）：XFF 可伪造——本限流只防"非定向刷量"，不防分布式
    伪造攻击；生产必须仅经 Cloudflare Tunnel 暴露（CF 覆写 XFF 为真实用户 IP），
    禁止直连端口暴露（见 docs/DEPLOY.md §6 运维警示）。
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        parts = [p.strip() for p in xff.split(",")]
        if parts and parts[-1]:
            return parts[-1]
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def rate_limit(request, call_next):
    if not request.url.path.startswith(_PROTECTED_PREFIXES) or request.method == "OPTIONS":
        return await call_next(request)
    now = time.time()
    key_ip = _client_ip(request)
    bucket = _ratelimit[key_ip]
    bucket[:] = [t for t in bucket if now - t < _RATE_LIMIT_WINDOW]
    if len(bucket) >= _RATE_LIMIT_MAX:
        logger.warning("rate limit hit: ip=%s path=%s", key_ip, request.url.path)
        return JSONResponse(status_code=429, content={"detail": "请求过于频繁，请稍后再试"})
    bucket.append(now)
    return await call_next(request)


@app.middleware("http")
async def api_key_auth(request, call_next):
    """API Key 鉴权：公开路径与 CORS 预检（OPTIONS）放行；其余校验 X-API-Key（时序安全比对）。

    OPTIONS 预检由浏览器发起（跨域自定义头触发），不携带业务语义与自定义头——
    放行交由 CORS middleware 处理，否则跨域调用会 Failed to fetch（实测发现）。
    """
    if request.method == "OPTIONS" or request.url.path in PUBLIC_PATHS:
        return await call_next(request)
    key = request.headers.get("X-API-Key", "")
    if not hmac.compare_digest(key, API_KEY):
        return JSONResponse(status_code=401, content={"detail": "API Key 缺失或错误"})
    return await call_next(request)


def _safe_name(name: str | None) -> str:
    """文件名消毒：去路径、截断、空值兜底。"""
    if not name:
        return "未命名文件"
    return Path(name).name[:128]


def _public_error(e: Exception) -> str:
    """对外错误文案白名单：业务 ValueError 透出（解析层保证文案安全），其余统一文案。"""
    if isinstance(e, ValueError):
        return str(e)
    return "文件无法解析（格式或编码不支持）"


@app.get("/healthz")
async def healthz():
    """存活探活（公开；无业务数据）。含主体配置初始化状态（RV-44：暴露 initialized 防静默未配置）。"""
    ent = _APP_CONFIG.get("company_entities") or [{}]
    e0 = ent[0] if ent else {}
    initialized = bool(e0.get("name") and e0.get("tax_id"))
    # RV-367 缺口3：healthz 是公开端点——收敛信息，去掉 config_source（本地路径存在性探测敏感）
    return {
        "status": "ok",
        "ruleset": RULESET_VERSION,
        "config_initialized": initialized,
    }


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """前端页面：免责文案由后端注入（单一来源，防两处漂移）。"""
    if not FRONTEND.exists():
        raise HTTPException(500, "前端页面缺失，请检查安装")
    html = FRONTEND.read_text(encoding="utf-8")
    html = html.replace("{{DISCLAIMER}}", DISCLAIMER).replace("{{RULESET_VERSION}}", RULESET_VERSION)
    return HTMLResponse(html)


@app.get("/api/config")
async def get_config():
    """读取当前配置（需 X-API-Key）：企业主体（税号脱敏）+ 规则阈值 + 审计 meta。"""
    global _APP_CONFIG
    ents = []
    for e in _APP_CONFIG.get("company_entities", []):
        row = {k: v for k, v in e.items()}
        tid = str(row.get("tax_id") or "")
        row["tax_id"] = (tid[:4] + "****" + tid[-4:]) if len(tid) >= 8 else ""
        ents.append(row)
    return {
        "company_entities": ents,
        "r2": _APP_CONFIG.get("r2", {}),
        "rules": _APP_CONFIG.get("rules", {}),
        "meta": _APP_CONFIG.get("meta", {"rules_updated_at": "", "rules_hash": ""}),
    }

@app.put("/api/config")
async def put_config(request: Request):
    """保存配置（需 X-API-Key）：企业主体 + 规则阈值。校验后写本地 data/config.json 并热生效。

    入参：{"company_name": str, "tax_id": str, "rules": {白名单字段可选}}
    rules 段整包原子校验（RV-48：任一字段非法整体拒绝；未知字段拒绝）。
    """
    import time as _time
    from .config_store import rules_hash, rules_sha256_full, validate_rules_payload

    global _APP_CONFIG
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体不是合法 JSON")
    name = str(body.get("company_name") or "").strip()
    taxid = str(body.get("tax_id") or "").strip().replace(" ", "")
    if not name and not taxid and "rules" not in body:
        raise HTTPException(400, "企业名称与税号不能同时为空")
    if taxid and not (8 <= len(taxid) <= 20 and taxid.isalnum()):
        raise HTTPException(400, "税号应为 8-20 位字母数字（统一社会信用代码 18 位）")
    if "rules" in body:
        cleaned, err = validate_rules_payload(body.get("rules"))
        if err:
            raise HTTPException(400, f"规则配置无效：{err}")
        _APP_CONFIG["rules"] = cleaned
    if name or taxid:
        ent = _APP_CONFIG["company_entities"][0]
        ent["name"] = name
        ent["tax_id"] = taxid
    # 审计 meta（RV-48：变更留痕，不含税号明文；RV-50：追加完整 SHA256 可还原全量摘要）
    _APP_CONFIG["meta"] = {
        "rules_updated_at": _time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "rules_hash": rules_hash(_APP_CONFIG),
        "rules_sha256": rules_sha256_full(_APP_CONFIG),
    }
    try:
        save_config(_APP_CONFIG)
    except ValueError as e:
        raise HTTPException(500, str(e)) from e
    _APP_CONFIG = load_config()  # 重载（含环境变量覆盖语义）
    AUDIT.log("config_changed", {"fields": sorted(k for k in ("company_name", "tax_id") if k in body),
                                 "rules_changed": "rules" in body,
                                 "rules_hash": _APP_CONFIG.get("meta", {}).get("rules_hash", "")})
    return {
        "status": "ok",
        "message": "配置已保存",
        "tax_id_masked": (taxid[:4] + "****" + taxid[-4:]) if len(taxid) >= 8 else "",
        "rules_hash": rules_hash(_APP_CONFIG),
    }


@app.post("/parse")
async def parse(file: UploadFile = File(...)):
    """单文件解析（独立测试/调试用）。失败返回 400 + 明确原因。"""
    data = await file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, f"文件超过 {MAX_FILE_BYTES // 1024 // 1024}MB 上限")
    fp = _fp(data)
    try:
        inv = parse_document(data)
    except ValueError as e:
        AUDIT.log("parsed", {"file_fp": fp, "ok": False, "n_findings": 0, "error": "bad_format"})
        raise HTTPException(400, str(e)) from e
    AUDIT.log("file_uploaded", {"file_fp": fp, "size": len(data), "name": _audit_name(file.filename)})
    AUDIT.log("parsed", {"file_fp": fp, "ok": True, "n_findings": 0})
    return invoice_to_dict(inv)


@app.post("/review")
async def review(files: list[UploadFile] = File(...)):
    """整批解析 + 规则预审 → 一页风险报告（主流程）。"""
    t0 = time.time()
    batch_id = uuid.uuid4().hex[:12]
    if len(files) > MAX_FILES:
        raise HTTPException(413, f"单批最多 {MAX_FILES} 个文件")

    invoices, failed = [], []
    total_bytes = 0
    for f in files:
        try:
            data = await f.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                failed.append({"name": _safe_name(f.filename),
                               "error": f"文件超过 {MAX_FILE_BYTES // 1024 // 1024}MB 上限"})
                continue
            total_bytes += len(data)
            if total_bytes > MAX_TOTAL_BYTES:
                raise HTTPException(413, "本批总大小超限，请分批上传")
            invoices.append(parse_document(data))
        except HTTPException:
            raise
        except Exception as e:  # 隔离边界：读取+解析全链路，坏文件不拖垮整批
            logger.exception("parse failed: %s", _safe_name(f.filename))
            failed.append({"name": _safe_name(f.filename), "error": _public_error(e)})

    # T-014 前置③：审计埋点（batch 维度；不含明文 PII——文件内容不落审计，仅指纹/数量）
    AUDIT.log("file_uploaded", {"file_fp": f"batch:{batch_id}", "size": total_bytes,
                                "n_files": len(files)})
    AUDIT.log("parsed", {"file_fp": f"batch:{batch_id}", "ok": (len(failed) == 0),
                         "n_files": len(files), "n_failed": len(failed)})

    findings, rule_states = run_rules_with_states(invoices, config=_current_rules_config())
    # RV-48：报告标注规则阈值来源（自定义阈值时附 rules_hash 审计快照）
    from .config_store import is_default_rules, rules_hash
    rules_note, rules_h = "", ""
    if not is_default_rules(_APP_CONFIG):  # RV-50：逐叶子比较，空对象/全默认不误标
        rules_note = "阈值来自自定义配置（企业设置面板校准）"
        rules_h = rules_hash(_APP_CONFIG)
    report = build_report(invoices, findings, failed, RULESET_VERSION,
                          file_count=len(files), rule_states=rule_states,
                          rules_config_note=rules_note, rules_hash=rules_h)
    report["batch_id"] = batch_id
    logger.info(
        "batch=%s files=%d parsed=%d failed=%d findings=%d elapsed_ms=%.0f ruleset=%s",
        batch_id, len(files), len(invoices), len(failed), len(findings),
        (time.time() - t0) * 1000, RULESET_VERSION,
    )
    return report


@app.post("/v1/findings/explain")
async def explain(request: Request):
    """受控 LLM 解释（F）：对 review 响应中的 findings 生成财务人员可读的通俗解释。

    请求体：{"findings": [review 响应里的 finding dict, ...]}
    响应：{"explanations": [...与输入对齐...], "engine": "llm|template", "llm_enabled": bool}
    受控边界：LLM 只解释不判定；失败/超时/校验不过 → 模板解释（不阻塞、不报错）。
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")
    findings = body.get("findings")
    if not isinstance(findings, list) or len(findings) > 50:
        raise HTTPException(400, "findings 必须是不超过 50 条的列表")
    for f in findings:
        if not isinstance(f, dict):
            raise HTTPException(400, "findings 每项必须是对象")

    from .rules import RULESET_META
    rules_meta = {m["rule_id"]: m for m in RULESET_META["rules"]}
    explanations = explain_findings(findings, rules_meta)
    engine = "llm" if any(e.get("source", "").startswith("llm:") for e in explanations) else "template"
    return {
        "explanations": explanations,
        "engine": engine,
        "llm_enabled": LLM_ENABLED,
        "prompt_version": "explain-v1",
        "count": len(explanations),
    }


# ================= T-014 前置③ / T-056：企业级审计 API =================

@app.post("/api/audit/review-decision")
async def audit_review_decision(request: Request):
    """复核决定落服务端 append-only（取代 localStorage 单机态——T-056 核心）。

    入参：{"finding_id": str, "decision": "pass|reject|rework", "note": str(≤200)}
    payload 不含发票明文；note 截断 200 字符；审计事件含哈希链。
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体不是合法 JSON")
    finding_id = str(body.get("finding_id") or "").strip()
    decision = str(body.get("decision") or "").strip()
    if decision not in {"pass", "reject", "rework"}:
        raise HTTPException(400, "decision 应为 pass/reject/rework 之一")
    if not finding_id:
        raise HTTPException(400, "finding_id 不能为空")
    note = str(body.get("note") or "")[:200]
    ev = AUDIT.log("finding_reviewed", {
        "finding_id": finding_id[:80],
        "decision": decision,
        "note": note,
        "client_ip_masked": "**",
    })
    return {"status": "ok", "event": ev["event_id"], "event_hash": ev["event_hash"]}


@app.get("/api/audit/events")
async def audit_events(limit: int = 50, offset: int = 0, event_type: str | None = None):
    """审计事件查询（需 X-API-Key；分页；倒序最新在前）。"""
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    rows = AUDIT.list_events(limit=limit, offset=offset, event_type=event_type)
    return {"total": AUDIT.count(event_type), "limit": limit, "offset": offset,
            "events": rows}


@app.get("/api/audit/verify")
async def audit_verify():
    """哈希链完整性自检（审计可溯源核验；隔日自检任务可调用）。

    tail_hash 供外部锚记录（RV-368 条件3）：把 tail_hash+total 记入外部档案
    （如 project-trace DailySummary），比对可发现尾部截断/整链重算——内部一致性
    无法单独证明未篡改，需外部锚联合。
    """
    ok, bad = AUDIT.verify_chain()
    last = AUDIT._last_hash()
    return {"ok": ok, "chain_ok": ok, "tampered_rows": bad, "total": AUDIT.count(),
            "tail_hash": last, "tail_is_genesis": last == "GENESIS"}


@app.post("/api/audit/export")
async def audit_export(request: Request):
    """导出留痕（RV-368 条件1）：前端导出 CSV 时调用；payload 仅记条数与类型，无明文。"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体不是合法 JSON")
    kind = str(body.get("kind") or "csv")[:20]
    n = int(body.get("n_rows") or 0)
    n = max(0, min(n, 10_000_000))
    ev = AUDIT.log("exported", {"kind": kind, "n_rows": n})
    return {"status": "ok", "event": ev["event_id"]}


@app.delete("/api/data/{ref}")
async def data_delete(ref: str):
    """PII 删除权（合规：留存期限与删除权流程定义——T-014 前置③）。

    当前真实模式解析为瞬态（不持久化原始文件），无本体可删：
    本端点行使"删除权留痕"语义——记录 data_deleted 审计事件（保留指纹，不保留内容），
    并返回删除权行使凭据。未来启用原始文件留存时，此处接本体删除（同一审计语义）。
    """
    ref = str(ref).strip()[:80]
    if not ref:
        raise HTTPException(400, "ref 不能为空")
    ev = AUDIT.log("data_deleted", {"ref_fp": ref, "ref_kind": "invoice", "has_body": False})
    return {"status": "ok", "message": "删除权已行使并留痕（当前无持久化本体；审计事件保留指纹不保留内容）",
            "event_id": ev["event_id"]}


@app.get("/{pwa_path:path}")
async def pwa_static(pwa_path: str):
    """PWA 静态资源白名单（F-06：manifest/SW/icons；仅列出的文件对外，防目录泄露）。"""
    f = _PWA_STATIC.get("/" + pwa_path)
    if not f or not f.exists():
        raise HTTPException(404, "Not Found")
    media = {"service-worker.js": "text/javascript",
             "manifest.json": "application/manifest+json"}.get(pwa_path)
    return FileResponse(f, media_type=media)




