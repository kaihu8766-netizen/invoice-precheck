"""FastAPI 入口（P1：上传数电票 XML → 一页风险报告）。

接口（DeepSeek 评审：/parse 与 /review 分离，可独立测试替换）：
- GET  /         单页前端（免责文案由后端注入，单一来源；公开）
- GET  /healthz  存活探活（公开，无业务数据）
- POST /parse    单文件解析 → NormalizedInvoice（需 X-API-Key）
- POST /review   整批解析+规则 → 一页风险报告（需 X-API-Key）

鉴权（D 阶段公网部署前置，方案 A：Cloudflare Tunnel 本地穿透）：
- API Key 经环境变量 INVOICE_API_KEY 注入；未设置时使用开发默认 key（启动打警告日志，
  仅限本地开发，公网部署必须设置强 key——部署脚本负责生成）
- 请求头 X-API-Key 比对（hmac.compare_digest 防时序攻击）；缺失/错误 → 401
- CORS 放行所有源 + X-API-Key 请求头（演示页跨域调用；生产部署应收紧白名单，见部署文档）

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
DEV_API_KEY = "dev-invoice-precheck-key"
API_KEY = os.environ.get("INVOICE_API_KEY") or DEV_API_KEY
if not os.environ.get("INVOICE_API_KEY"):
    logger.warning(
        "INVOICE_API_KEY 未设置，使用开发默认 key（仅限本地开发；公网部署必须设置强 key）"
    )

app = FastAPI(title="发票合规预审", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 演示期放行；生产收紧白名单（见 docs/DEPLOY.md）
    allow_methods=["*"],
    allow_headers=["X-API-Key", "Content-Type"],
)

# 产品前端统一用 docs/index.html（完整版：演示模式+实时模式+复核工作台+导出）；
# 旧 frontend/index.html（204 行精简版）废弃保留，不参与服务。
FRONTEND = Path(__file__).resolve().parent.parent / "docs" / "index.html"

# 无需鉴权的公开路径（仅静态页面与探活，无业务数据）
PUBLIC_PATHS = {"/", "/healthz"}


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
    return {
        "status": "ok",
        "ruleset": RULESET_VERSION,
        "config_initialized": initialized,
        "config_source": "file" if (Path(__file__).resolve().parent.parent / "data" / "config.json").exists() else "default",
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
    """读取当前企业主体配置（需 X-API-Key）。税号脱敏返回，避免前端全量回显。"""
    global _APP_CONFIG
    ents = []
    for e in _APP_CONFIG.get("company_entities", []):
        row = {k: v for k, v in e.items()}
        tid = str(row.get("tax_id") or "")
        row["tax_id"] = (tid[:4] + "****" + tid[-4:]) if len(tid) >= 8 else ""
        ents.append(row)
    return {"company_entities": ents, "r2": _APP_CONFIG.get("r2", {})}


@app.put("/api/config")
async def put_config(request: Request):
    """保存企业主体配置（需 X-API-Key）。校验后写本地 data/config.json 并热生效。

    入参：{"company_name": str, "tax_id": str}
    """
    global _APP_CONFIG
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体不是合法 JSON")
    name = str(body.get("company_name") or "").strip()
    taxid = str(body.get("tax_id") or "").strip().replace(" ", "")
    if not name and not taxid:
        raise HTTPException(400, "企业名称与税号不能同时为空")
    if taxid and not (8 <= len(taxid) <= 20 and taxid.isalnum()):
        raise HTTPException(400, "税号应为 8-20 位字母数字（统一社会信用代码 18 位）")
    ent = _APP_CONFIG["company_entities"][0]
    ent["name"] = name
    ent["tax_id"] = taxid
    try:
        save_config(_APP_CONFIG)
    except ValueError as e:
        raise HTTPException(500, str(e)) from e
    _APP_CONFIG = load_config()  # 重载（含环境变量覆盖语义）
    return {"status": "ok", "message": "企业主体配置已保存", "tax_id_masked": (taxid[:4] + "****" + taxid[-4:]) if len(taxid) >= 8 else ""}


@app.post("/parse")
async def parse(file: UploadFile = File(...)):
    """单文件解析（独立测试/调试用）。失败返回 400 + 明确原因。"""
    data = await file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, f"文件超过 {MAX_FILE_BYTES // 1024 // 1024}MB 上限")
    try:
        inv = parse_document(data)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
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

    findings, rule_states = run_rules_with_states(invoices, config=_current_rules_config())
    report = build_report(invoices, findings, failed, RULESET_VERSION,
                          file_count=len(files), rule_states=rule_states)
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
