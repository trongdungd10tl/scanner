"""
Ghi log ra tep, dung khuon cua gamefi/src/main/resources/log4j2.xml.

    logs/main.log     INFO tro len, xoay 1024MB hoac moi ngay, giu 7 ngay
    logs/error.log    chi ERROR,    xoay  300MB hoac moi ngay, giu 3 ngay
    logs/request.log  mot dong moi request, giu 3 ngay

Ban xoay duoc nen gz va dat ten y het log4j2: main.2026-09-15-1.log.gz.

contextId la ma cua mot request, tuong duong MDC ben Java. Python khong co MDC
nen dung ContextVar: moi request dat mot ma, va moi dong log sinh ra trong lan
xu ly do deu mang ma ay. Khong co no thi log cua vai request chay song song tron
vao nhau va khong tach ra duoc.

TimedRotatingFileHandler cua Python chi xoay theo thoi gian, RotatingFileHandler
chi xoay theo dung luong; log4j2 lam ca hai, cai nao den truoc thi xoay. Lop
_Rolling ben duoi ghep hai dieu kien do lai.
"""
import glob
import gzip
import logging
import os
import re
import shutil
import time
from contextvars import ContextVar
from logging.handlers import TimedRotatingFileHandler

LOG_DIR = os.getenv("LOG_DIR", "logs")
PATTERN = ("%(asctime)s [%(threadName)s] [%(contextId)s] %(levelname)-5s "
           "%(name)s.%(funcName)s:%(lineno)d - %(message)s")
DATEFMT = "%Y-%m-%d %H:%M:%S"

MB = 1024 * 1024

context_id = ContextVar("context_id", default="-")
request_log = logging.getLogger("request")


class _ContextId(logging.Filter):
    def filter(self, record):
        record.contextId = context_id.get()
        return True


def _gzip(source, target):
    with open(source, "rb") as fin, gzip.open(target, "wb") as fout:
        shutil.copyfileobj(fin, fout)
    os.remove(source)


class _Rolling(TimedRotatingFileHandler):
    """Xoay theo ngay VA theo dung luong, nen gz, xoa theo tuoi."""

    def __init__(self, path, max_bytes, keep_days):
        super().__init__(path, when="midnight", backupCount=0,
                         encoding="utf-8", delay=True)
        self.max_bytes = max_bytes
        self.keep_days = keep_days
        self.rotator = _gzip
        # KHONG dat ten phuong thuc nay la _name: logging.Handler.__init__ gan
        # san self._name = None (cho lo ten cua handler), va thuoc tinh the hien
        # do che mat phuong thuc cung ten - namer se nhan None va ban xoay ra ten
        # mac dinh, khong nen gz. Da dinh dung vay mot lan.
        self.namer = self._rolled_name

    def _rolled_name(self, default_name):
        # Python dat ten "main.log.2026-09-15"; doi thanh
        # "main.2026-09-15-<n>.log.gz" cho khop filepattern cua log4j2.
        base, _, stamp = default_name.rpartition(".")
        root = base[:-4] if base.endswith(".log") else base
        number = 1
        while os.path.exists("%s.%s-%d.log.gz" % (root, stamp, number)):
            number += 1
        return "%s.%s-%d.log.gz" % (root, stamp, number)

    def shouldRollover(self, record):
        if super().shouldRollover(record):
            return 1
        if self.max_bytes <= 0:
            return 0
        if self.stream is None:
            self.stream = self._open()
        line = self.format(record) + self.terminator
        return 1 if self.stream.tell() + len(line.encode("utf-8")) >= self.max_bytes else 0

    def doRollover(self):
        super().doRollover()
        self._purge()

    def _purge(self):
        if self.keep_days <= 0:
            return
        root = re.sub(r"\.log$", "", self.baseFilename)
        cutoff = time.time() - self.keep_days * 86400
        for path in glob.glob(root + ".*.log.gz"):
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass


def _file(name, level, max_bytes, keep_days):
    handler = _Rolling(os.path.join(LOG_DIR, name), max_bytes, keep_days)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(PATTERN, DATEFMT))
    handler.addFilter(_ContextId())
    return handler


def setup():
    os.makedirs(LOG_DIR, exist_ok=True)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(PATTERN, DATEFMT))
    console.addFilter(_ContextId())

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(_file("main.log", logging.INFO, 1024 * MB, 7))
    root.addHandler(_file("error.log", logging.ERROR, 300 * MB, 3))

    # Log request di duong rieng, khong lan sang main.log - giong additivity=false.
    request_log.setLevel(logging.INFO)
    request_log.handlers.clear()
    request_log.propagate = False
    request_log.addHandler(_file("request.log", logging.INFO, 1024 * MB, 3))

    # uvicorn tu gan handler cua no; bo di de moi thu cung chay qua root.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
