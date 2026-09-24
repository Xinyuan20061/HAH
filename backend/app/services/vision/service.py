class VisionNotConfigured(RuntimeError):
    pass


async def analyze_food_image(file, user=None):
    raise VisionNotConfigured(
        "识餐由本地 VLM Worker 完成：请先 /media/upload 或 register-cloud，再 /vision/food-jobs。"
    )
