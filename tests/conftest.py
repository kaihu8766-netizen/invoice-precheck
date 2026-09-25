"""pytest 全局夹具：测试环境显式声明本地开发（RV-125/#11 硬门适配）。

main.py 现要求：未设 INVOICE_API_KEY 时必须显式 INVOICE_ALLOW_DEV_KEY=1 才启动
（防开发默认 key 公网暴露）。测试即本机开发，统一声明。
"""
import os

os.environ.setdefault("INVOICE_ALLOW_DEV_KEY", "1")
