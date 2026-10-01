#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""invoice-precheck 真实模式一键启动（T-014 A 案落地，RV-20261001-368 后）。

流程（自动、跨平台）：
  0) --check 模式：只探测环境（Python 版本/依赖/cloudflared），不执行
  1) 探测并安装缺失依赖（pip install -r requirements.txt）
  2) 读取/生成 .env（INVOICE_API_KEY=强随机 ≥16 位；INVOICE_ENV=prod）
  3) 探测 cloudflared；缺失则自动下载官方二进制到本脚本目录（不写入系统）
  4) 启动 cloudflared quick tunnel（匿名免费）→ 解析 trycloudflare.com 地址
  5) 以隧道地址设置 INVOICE_CORS_ORIGINS → 启动 uvicorn (127.0.0.1:8000)
  6) healthz 自检 → 打印访问地址 + API Key + 使用说明

安全说明：
  - 密钥只写入本地 .env（已在 .gitignore，绝不入库）
  - 生产硬门保持生效（prod + 强 key + CORS 白名单 = 隧道地址）
  - quick tunnel 为临时地址（重启后变化）；长期使用请换正式隧道（DEPLOY.md §5 方式一）
"""
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.request
import urllib.error
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
REQ_FILE = ROOT / "requirements.txt"

CLOUDFLARED_URLS = {
    "win32": "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe",
    "darwin": "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64.tgz",
    "linux": "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
}

IS_WIN = sys.platform.startswith("win")


def log(msg: str):
    print(f"[invoice-precheck] {msg}", flush=True)


def find_cloudflared() -> Path | None:
    """.runtime 优先（自动下载位），其次 PATH。"""
    local = (SCRIPTS / ".runtime") / ("cloudflared.exe" if IS_WIN else "cloudflared")
    if local.exists():
        return local
    for cand in ("cloudflared", "cloudflared.exe"):
        try:
            r = subprocess.run([cand, "--version"], capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                return Path(cand)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def download_cloudflared() -> Path:
    """下载官方二进制到 scripts/.runtime/（已 gitignore，防误提交；RV-369 供应链条件）。
    macOS 为 tar.gz 需解压；下载后打印 SHA-256 供人工与官方 checksums 核对。"""
    import hashlib, tarfile, io
    url = CLOUDFLARED_URLS[sys.platform]
    rt = SCRIPTS / ".runtime"
    rt.mkdir(exist_ok=True)
    raw = rt / ("cloudflared.dl" + (".tgz" if sys.platform == "darwin" else ".exe" if IS_WIN else ".bin"))
    target = rt / ("cloudflared.exe" if IS_WIN else "cloudflared")
    log(f"未检测到 cloudflared，自动下载官方二进制 → {target.name}（约 40MB）")
    log(f"来源：{url}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "invoice-precheck-deploy"})
        with urllib.request.urlopen(req, timeout=300) as resp, open(raw, "wb") as f:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(
            f"cloudflared 下载失败（{e}）。请手动下载官方版本后放到 scripts/.runtime/ 目录：\n  {url}"
        )
    h = hashlib.sha256(raw.read_bytes()).hexdigest()
    if sys.platform == "darwin":
        with tarfile.open(raw, "r:gz") as t:
            member = next(m for m in t.getmembers() if m.isfile() and "cloudflared" in m.name)
            f = t.extractfile(member)
            target.write_bytes(f.read())
    else:
        raw.replace(target)
    if not IS_WIN:
        os.chmod(target, 0o755)
    log(f"下载完成，SHA-256={h}（可到 Cloudflare 官方 checksums 页面人工比对；脚本不自动校验证书链）")
    return target


def read_env() -> dict:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def ensure_env() -> dict:
    """读取/生成 .env，并强制生产安全默认（RV-20261001-370 修复）。

    RV-370（隔日自检）：原 setdefault 缺陷——陈旧 .env 含 dev/LLM=1 时被保留，
    会"静默以 dev 语义启动"且涉税数据可能经 LLM 出境。现强制重置为生产安全值
    （INVOICE_ENV=prod / LLM=0 / 金额=0），旧值不同时告警——一键启动=生产语义，
    LLM 外发必须是用户事后显式手动修改 .env 的行为。
    """
    env = read_env()
    changed = False
    forced = []
    for k, safe in (("INVOICE_ENV", "prod"),
                    ("INVOICE_LLM_ENABLED", "0"),
                    ("INVOICE_LLM_SEND_AMOUNT", "0")):
        old_v = env.get(k)
        if old_v is not None and old_v != safe:
            forced.append(f"{k}: {old_v} → {safe}")
        env[k] = safe
    if not env.get("INVOICE_API_KEY") or len(env["INVOICE_API_KEY"]) < 16:
        env["INVOICE_API_KEY"] = secrets.token_hex(32)
        changed = True
    env.setdefault("INVOICE_HOST", "127.0.0.1")
    env.setdefault("INVOICE_PORT", "8000")
    if forced:
        log(f"⚠ 已强制重置 .env 安全默认（原值不保留）：{'；'.join(forced)}。"
            "如需 LLM 解释请手动编辑 .env 后重启（显式行为）。")
    if changed or forced:
        lines = [f"{k}={v}" for k, v in env.items()]
        ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log("已更新 .env（密钥仅存本地，不入 Git）")
    return env


def check_python() -> bool:
    v = sys.version_info
    if v < (3, 10):
        log(f"Python 版本过低：{v.major}.{v.minor}（需要 ≥3.10）。请到 https://www.python.org/downloads/ 安装后重试。")
        return False
    log(f"Python OK：{v.major}.{v.minor}.{v.micro}")
    return True


def check_deps() -> list:
    missing = []
    import importlib
    for mod in ("fastapi", "uvicorn", "pymupdf", "zxing_cpp", "reportlab", "rapidocr_onnxruntime", "multipart"):
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)
    return missing


def install_deps() -> None:
    log("安装缺失依赖（首次约 2-5 分钟，含 RapidOCR 模型下载）…")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(REQ_FILE)],
                       cwd=ROOT)
    if r.returncode != 0:
        raise RuntimeError("依赖安装失败，请检查网络后重试（或手动执行：pip install -r requirements.txt）")


def _parse_tunnel_url(line: str) -> str | None:
    """从 cloudflared 输出行提取隧道 URL（RV-371：只接受含完整 https URL 的行；
    日志行如 'Requesting new quick Tunnel on trycloudflare.com' 不含 https → 返回 None）。"""
    import re as _re
    m = _re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line or "")
    return m.group(0) if m else None


def start_tunnel(cf: Path):
    """起 quick tunnel，轮询输出解析 trycloudflare.com 地址。返回 (url, proc)。

    RV-371：stdout 用 daemon 线程收集（readline 阻塞会令超时形同虚设），
    主循环轮询 + 真超时（120s）。
    """
    log("启动 Cloudflare Tunnel（quick，匿名免费）…")
    env = dict(os.environ)
    proc = subprocess.Popen(
        [str(cf), "tunnel", "--url", "http://127.0.0.1:8000", "--no-autoupdate"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=env, bufsize=1
    )
    collected = []
    t = threading.Thread(target=lambda: [collected.append(l.strip()) for l in proc.stdout],
                         daemon=True)
    t.start()
    url = None
    lines = []
    deadline = time.time() + 120
    while time.time() < deadline:
        time.sleep(0.2)
        while collected:
            ln = collected.pop(0)
            lines.append(ln)
            url = _parse_tunnel_url(ln)
            if url:
                break
        if url:
            break
        if proc.poll() is not None and not collected:
            break
    if not url:
        tail = "\n".join(lines[-8:])
        raise RuntimeError(
            f"未能获取隧道地址（120s 超时/退出）。最近输出：\n{tail}\n"
            "请检查网络能否访问 Cloudflare；或换正式隧道（DEPLOY.md §5 方式一）。"
        )
    log(f"隧道已建立：{url}")
    return url, proc


def start_backend(url: str, env: dict) -> subprocess.Popen:
    os.environ.update(env)
    os.environ["INVOICE_CORS_ORIGINS"] = url
    host = env.get("INVOICE_HOST", "127.0.0.1")
    port = env.get("INVOICE_PORT", "8000")
    log(f"启动后端（{host}:{port}，CORS 白名单={url}）…")
    out = open(ROOT / "data" / "server.log", "a", encoding="utf-8") if (ROOT / "data").exists() else None
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", host, "--port", port],
        cwd=ROOT, env=os.environ, stdout=out or subprocess.DEVNULL, stderr=subprocess.STDOUT
    )
    return proc


def health_check(host: str, port: str, tries: int = 30) -> dict:
    import urllib.request
    for i in range(tries):
        try:
            with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=3) as r:
                return json.loads(r.read().decode())
        except Exception:
            time.sleep(1)
    return {}


def main() -> int:
    log(f"工作目录：{ROOT}")
    if not check_python():
        return 2

    if "--check" in sys.argv:
        cf = find_cloudflared()
        missing = check_deps()
        print("\n===== 环境检查结果 =====")
        print(f"Python        : OK ({sys.version.split()[0]})")
        print(f"依赖缺失      : {', '.join(missing) if missing else '无（全部就绪）'}")
        print(f"cloudflared   : {'已就绪' if cf else '未安装（启动时将自动下载）'}")
        print(f".env          : {'存在' if ENV_FILE.exists() else '不存在（启动时自动生成）'}")
        print("========================\n")
        return 0

    # 0) 环境净化（RV-369 条件：清除 shell 继承干扰，避免 dev key/CORS 残留破坏硬门语义）
    for _k in ("INVOICE_ALLOW_DEV_KEY", "INVOICE_CORS_ORIGINS", "INVOICE_API_KEY"):
        os.environ.pop(_k, None)

    # 0.5) 端口占用检测（RV-369 条件：多实例防护）
    _host = "127.0.0.1"; _port = int(read_env().get("INVOICE_PORT", "8000"))
    with __import__("socket").socket(__import__("socket").AF_INET, __import__("socket").SOCK_STREAM) as _s:
        if _s.connect_ex((_host, _port)) == 0:
            raise RuntimeError(f"端口 {_port} 已被占用（可能已有后端在跑）。请先关闭旧实例（任务管理器结束 python/uvicorn 进程），再重新启动。")

    # 1) 依赖
    missing = check_deps()
    if missing:
        install_deps()

    # 2) .env
    env = ensure_env()

    # 3) cloudflared
    cf = find_cloudflared()
    if cf is None or not Path(cf).exists():
        if cf is not None and str(cf) in ("cloudflared", "cloudflared.exe"):
            pass  # PATH 中有
        else:
            cf = download_cloudflared()

    # 4) 隧道（先起，拿 URL 再起后端——CORS 依赖隧道地址）
    url, tunnel_proc = start_tunnel(cf)

    # 5) 后端
    backend_proc = start_backend(url, env)
    time.sleep(4)
    host = env.get("INVOICE_HOST", "127.0.0.1")
    port = env.get("INVOICE_PORT", "8000")
    hz = health_check(host, port)

    print("\n" + "=" * 64)
    print("  真实模式已启动")
    print("=" * 64)
    print(f"  · 访问地址（手机/电脑浏览器）：{url}")
    print(f"  · 后端健康检查  : {hz}")
    print(f"  · 服务端审计    : {url}/api/audit/verify")
    print(f"  · API Key（前端设置页需填）：{env['INVOICE_API_KEY']}")
    print(f"  · 前端设置步骤  : 打开访问地址 → 设置 → 后端地址填 {url} → API Key 填上方 Key")
    print(f"  · 密钥文件      : {ENV_FILE}（请勿分享/入库）")
    print("=" * 64)
    print("  停止：关闭本窗口即可（后端与隧道会一并退出）。")
    print("  注意：quick tunnel 地址为临时随机，重启后需重新复制到前端设置。")
    try:
        tunnel_proc.wait()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        log(f"启动失败：{e}")
        sys.exit(1)
