# paper_survey/logging_setup.py
import logging
import sys
from typing import Optional

def setup_logging(level: str = "INFO") -> None:
    """
    Configure root logger once.
    - level: DEBUG/INFO/WARNING/ERROR/CRITICAL (case-insensitive)
    """
    # 避免重复配置
    if getattr(setup_logging, "_configured", False):
        return

    fmt = "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
    datefmt = "%H:%M:%S"

    root = logging.getLogger()
    # 清掉 uvicorn 初始 handler（如果有的话）
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt=fmt, datefmt=datefmt))
    root.addHandler(handler)

    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    setup_logging._configured = True
