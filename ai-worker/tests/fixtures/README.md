# 离线样例视频（同类离线样例验证，非原始视频）

本目录的 `bicep_curl_offline_sample.mp4` 由 `make_bicep_curl_fixture.py` 程序化
合成：深色背景上一根简化人物线条，前臂以正弦周期做肘关节屈伸（模拟哑铃弯举）。

- 它**不是**用户截图对应的真实视频，也不用于宣称识别准确率。
- 唯一用途：离线验证 V2 证据管线全链路（解码 → 通用证据池 → 真实帧预览 →
  MotionWorkerResultV2 回执），全程不调用任何外部模型。
- 重新生成：`ai-worker\.venv\Scripts\python.exe tests\fixtures\make_bicep_curl_fixture.py`
