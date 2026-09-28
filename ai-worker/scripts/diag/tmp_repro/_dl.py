# -*- coding: utf-8 -*-
"""Download job 19 video using the worker's own downloader."""
import sys
sys.path.insert(0, r"C:\HealthMate\ai-worker")
from healthmate_worker.downloader import download_media

url = "https://636c-cloud1-d7gv5f8xpf862595e-1491045313.tcb.qcloud.la/healthmate/u1/video/2026-09-24/1790228479102-3q4wlt58.mp4"
try:
    path = download_media(url, original_name="job19.mp4")
    print("DL_OK", path)
except Exception as e:
    print("DL_ERR", type(e).__name__, str(e)[:200])
