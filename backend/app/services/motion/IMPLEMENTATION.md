# 当前动作视频分析

小程序CloudBase上传→MediaAsset→AIJob(motion_pose)→本机主动轮询→OpenCV/MediaPipe CPU姿态→独立Squat/Pushup/Lunge analyzer→事件timestamp/指标→MySQL→小程序。

事件包括起始/最高位、每rep最低点/伸展完成、最深位、最大躯干倾角和最大风险时刻。当前没有持久JPG artifact，不提供虚假图片URL。二维阈值是健身辅助估算，不宣称医学/运动学精度。

旧FFmpeg帧差模块仅保留历史代码，没有挂到当前路由作为没有Worker时的假姿态降级。/media/analyze-motion兼容路由创建异步AIJob；开发也需要真实Worker。Worker离线保留queued，普通云业务仍可用。

独立analyzer单测、真实MediaPipe静态视频与HTTP任务闭环已通过；真实运动视频准确率待人工标注验收。详见docs/LOCAL_AI_WORKER.md、FIX_REPORT.md、VERIFICATION.md。
