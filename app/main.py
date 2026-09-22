"""FastAPI 入口（P1：上传数电票 XML → 一页风险报告）。

接口（DeepSeek 评审：/parse 与 /review 分离，可独立测试替换）：
- GET  /        单页前端（免责文案由后端注入，单一来源）
- POST /parse   单文件解析 → NormalizedInvoice（调试/独立测试用）
- POST /review  整批解析+规则 → 一页风险报告（主流程）

安全边界（二轮审查 P0）：
- 文件数/单文件/总大小上限（防内存 DoS）；类型前置校验（parse_xml 内 detect_type）
- 文件级失败隔离：读取+解析全链路 try/except，坏文件不拖垮整批
- 对外错误文案白名单：业务 ValueError 透出，其余统一安全文案（不泄漏内部细节/输入片段）
- 安全响应头：nosniff / frame DENY / referrer
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from .parser import parse_xml
from .report import DISCLAIMER, build_report, invoice_to_dict
from .rules import RULESET_VERSION, run_rules

logger = logging.getLogger("invoice-precheck")

MAX_FILES = 200
MAX_FILE_BYTES = 10 * 1024 * 1024   # 10MB（与 parser.MAX_FILE_SIZE 一致）
MAX_TOTAL_BYTES = 50 * 1024 * 1024  # 50MB

app = FastAPI(title="发票合规预审", version="0.1.0")

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


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


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """前端页面：免责文案由后端注入（单一来源，防两处漂移）。"""
    if not FRONTEND.exists():
        raise HTTPException(500, "前端页面缺失，请检查安装")
    html = FRONTEND.read_text(encoding="utf-8")
    html = html.replace("{{DISCLAIMER}}", DISCLAIMER).replace("{{RULESET_VERSION}}", RULESET_VERSION)
    return HTMLResponse(html)


@app.post("/parse")
async def parse(file: UploadFile = File(...)):
    """单文件解析（独立测试/调试用）。失败返回 400 + 明确原因。"""
    data = await file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, f"文件超过 {MAX_FILE_BYTES // 1024 // 1024}MB 上限")
    try:
        inv = parse_xml(data)
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
            invoices.append(parse_xml(data))
        except HTTPException:
            raise
        except Exception as e:  # 隔离边界：读取+解析全链路，坏文件不拖垮整批
            logger.exception("parse failed: %s", _safe_name(f.filename))
            failed.append({"name": _safe_name(f.filename), "error": _public_error(e)})

    findings = run_rules(invoices)
    report = build_report(invoices, findings, failed, RULESET_VERSION, file_count=len(files))
    report["batch_id"] = batch_id
    logger.info(
        "batch=%s files=%d parsed=%d failed=%d findings=%d elapsed_ms=%.0f ruleset=%s",
        batch_id, len(files), len(invoices), len(failed), len(findings),
        (time.time() - t0) * 1000, RULESET_VERSION,
    )
    return report
