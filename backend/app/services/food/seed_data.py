"""Seed rows for the audited local nutrition table (capability plan §6.4).

IMPORTANT HONESTY NOTE — read before trusting any number here.

These values are **reference averages** drawn from public food-composition
conventions (per 100 g edible portion). They are intentionally coarse and are
flagged ``reviewed_at = None`` + ``source_id = "seed_unreviewed"`` so the system
never presents them as laboratory-verified values. A nutritionist review pass must
replace ``source_id``/``source_note``/``reviewed_at`` before this table may be used
for any claim stronger than "粗略草稿".

The purpose of shipping them is architectural, not nutritional: final nutrient
values must be *computed deterministically from this table* rather than taken from
a vision model's free text. Swapping in a reviewed table changes no code.
"""

from __future__ import annotations

SEED_SOURCE_ID = "seed_unreviewed"
SEED_SOURCE_NOTE = (
    "参考公开食物成分表的每 100g 平均值，未经过营养师逐项复核；"
    "仅用于确定性计算的架构验证，不得作为营养学结论。"
)

# food_key, name_zh, group, kcal, protein, carbs, fat, fiber, density_g_per_ml
# density is for the common serving vessel (bowl/cup) when relevant.
SEED_FOODS: tuple[tuple, ...] = (
    ("rice_cooked", "米饭（熟）", "staple", 116.0, 2.6, 25.9, 0.3, 0.3, None),
    ("rice_congee", "白粥", "staple", 46.0, 1.1, 10.0, 0.1, 0.1, None),
    ("noodle_cooked", "面条（熟）", "staple", 137.0, 4.5, 27.5, 0.6, 0.8, None),
    ("steamed_bun", "馒头", "staple", 223.0, 7.0, 47.0, 1.1, 1.3, None),
    ("whole_wheat_bread", "全麦面包", "staple", 246.0, 9.0, 41.0, 3.3, 6.0, None),
    ("sweet_potato_steamed", "蒸红薯", "staple", 90.0, 1.6, 20.7, 0.2, 3.0, None),
    ("oat_cooked", "燕麦粥", "staple", 68.0, 2.4, 12.0, 1.4, 1.7, None),
    ("corn_cooked", "玉米（煮）", "staple", 112.0, 4.0, 22.8, 1.2, 2.9, None),
    ("chicken_breast_skinless", "鸡胸肉（去皮）", "meat", 133.0, 24.6, 0.6, 3.2, 0.0, None),
    ("chicken_leg_skinless", "鸡腿肉（去皮）", "meat", 161.0, 20.0, 0.0, 8.5, 0.0, None),
    ("pork_lean", "猪里脊", "meat", 155.0, 20.2, 1.5, 7.9, 0.0, None),
    ("pork_belly", "五花肉", "meat", 508.0, 9.3, 0.0, 53.0, 0.0, None),
    ("beef_lean", "瘦牛肉", "meat", 125.0, 20.2, 1.2, 4.2, 0.0, None),
    ("lamb_lean", "瘦羊肉", "meat", 118.0, 20.5, 0.2, 3.9, 0.0, None),
    ("duck_roast", "烤鸭", "meat", 436.0, 16.6, 6.0, 38.4, 0.0, None),
    ("salmon", "三文鱼", "seafood", 139.0, 17.2, 0.0, 7.8, 0.0, None),
    ("bass_steamed", "清蒸鲈鱼", "seafood", 105.0, 18.6, 0.0, 3.4, 0.0, None),
    ("shrimp", "虾仁", "seafood", 93.0, 18.6, 2.8, 0.8, 0.0, None),
    ("egg_boiled", "水煮蛋", "egg", 144.0, 13.3, 2.8, 8.8, 0.0, None),
    ("egg_fried", "煎蛋", "egg", 200.0, 13.0, 1.5, 15.5, 0.0, None),
    ("tofu_firm", "北豆腐", "soy", 116.0, 12.2, 3.8, 6.4, 0.5, None),
    ("soy_milk_unsweetened", "无糖豆浆", "soy", 31.0, 3.0, 1.2, 1.6, 1.1, 1.0),
    ("milk_whole", "全脂牛奶", "dairy", 65.0, 3.3, 4.9, 3.6, 0.0, 1.03),
    ("milk_skim", "脱脂牛奶", "dairy", 35.0, 3.4, 5.0, 0.2, 0.0, 1.03),
    ("yogurt_plain", "无糖酸奶", "dairy", 72.0, 2.5, 9.3, 2.7, 0.0, 1.03),
    ("broccoli", "西兰花", "vegetable", 36.0, 4.1, 4.3, 0.6, 1.6, None),
    ("bok_choy", "小白菜", "vegetable", 15.0, 1.5, 2.7, 0.3, 1.1, None),
    ("spinach", "菠菜", "vegetable", 28.0, 2.6, 4.5, 0.3, 1.7, None),
    ("cucumber", "黄瓜", "vegetable", 16.0, 0.8, 2.9, 0.2, 0.5, None),
    ("tomato", "番茄", "vegetable", 20.0, 0.9, 4.0, 0.2, 0.5, None),
    ("mushroom_shiitake", "香菇", "vegetable", 26.0, 2.2, 5.2, 0.3, 3.3, None),
    ("potato_stir", "炒土豆丝", "vegetable", 122.0, 2.0, 17.0, 5.0, 1.2, None),
    ("apple", "苹果", "fruit", 53.0, 0.2, 13.5, 0.2, 1.2, None),
    ("banana", "香蕉", "fruit", 93.0, 1.4, 22.0, 0.2, 1.2, None),
    ("orange", "橙子", "fruit", 48.0, 0.8, 11.1, 0.2, 0.6, None),
    ("peanut", "花生", "nut", 574.0, 24.8, 21.7, 44.3, 5.5, None),
    ("almond", "杏仁", "nut", 578.0, 22.5, 19.9, 45.4, 8.0, None),
    ("walnut", "核桃", "nut", 646.0, 14.9, 19.1, 58.8, 9.5, None),
    ("cooking_oil", "食用油", "fat", 899.0, 0.0, 0.0, 99.9, 0.0, None),
    ("sugar_white", "白砂糖", "condiment", 400.0, 0.0, 99.9, 0.0, 0.0, None),
    ("soy_sauce", "生抽", "condiment", 63.0, 5.6, 9.9, 0.1, 0.2, 1.15),
    ("sesame_paste", "芝麻酱", "condiment", 630.0, 19.2, 22.7, 52.7, 5.9, None),
    ("mayonnaise", "蛋黄酱", "condiment", 724.0, 1.0, 2.6, 79.0, 0.0, None),
    ("milk_tea", "奶茶（全糖）", "beverage", 78.0, 1.2, 14.0, 2.0, 0.0, 1.0),
    ("cola", "可乐", "beverage", 43.0, 0.0, 10.8, 0.0, 0.0, 1.0),
)

# Cooking-method adjustments (capability plan §6.4). Values are *extra* grams of
# oil absorbed per 100 g of food, plus optional sauce sugar. Deliberately coarse
# and stored per reference row so a reviewer can correct one dish at a time.
DEFAULT_COOKING_ADJUSTMENTS: dict[str, dict] = {
    "steamed": {"oil_g_per_100g": 0.0, "label": "清蒸"},
    "boiled": {"oil_g_per_100g": 0.0, "label": "水煮"},
    "cold_mix": {"oil_g_per_100g": 2.0, "label": "凉拌"},
    "stir_fried": {"oil_g_per_100g": 5.0, "label": "普通炒制"},
    "light_stir_fried": {"oil_g_per_100g": 3.0, "label": "少油炒制"},
    "deep_fried": {"oil_g_per_100g": 12.0, "label": "油炸"},
    "braised": {"oil_g_per_100g": 4.0, "sugar_g_per_100g": 1.5, "label": "红烧"},
    "grilled": {"oil_g_per_100g": 1.0, "label": "烤制"},
    "raw": {"oil_g_per_100g": 0.0, "label": "生食"},
}

# Alias map: a vision model's free-text label -> a reference food_key. Only
# reviewed mappings live here; an unmapped label stays unmapped instead of being
# forced onto the nearest row.
SEED_ALIASES: dict[str, tuple[str, ...]] = {
    "rice_cooked": ("米饭", "白米饭", "大米饭", "steamed rice", "rice"),
    "rice_congee": ("粥", "稀饭", "白粥", "congee"),
    "noodle_cooked": ("面条", "面", "拉面", "noodles"),
    "steamed_bun": ("馒头", "包子皮"),
    "whole_wheat_bread": ("全麦面包", "面包", "toast", "bread"),
    "sweet_potato_steamed": ("红薯", "地瓜", "sweet potato"),
    "oat_cooked": ("燕麦", "燕麦粥", "oatmeal", "oats"),
    "corn_cooked": ("玉米", "corn"),
    "chicken_breast_skinless": ("鸡胸肉", "鸡胸", "chicken breast"),
    "chicken_leg_skinless": ("鸡腿肉", "鸡腿", "chicken leg"),
    "pork_lean": ("猪里脊", "瘦肉", "里脊", "pork"),
    "pork_belly": ("五花肉", "红烧肉", "pork belly"),
    "beef_lean": ("牛肉", "瘦牛肉", "beef"),
    "lamb_lean": ("羊肉", "lamb"),
    "duck_roast": ("烤鸭", "鸭肉", "duck"),
    "salmon": ("三文鱼", "鲑鱼", "salmon"),
    "bass_steamed": ("鲈鱼", "清蒸鱼", "鱼", "fish"),
    "shrimp": ("虾", "虾仁", "shrimp", "prawn"),
    "egg_boiled": ("水煮蛋", "鸡蛋", "煮蛋", "boiled egg"),
    "egg_fried": ("煎蛋", "荷包蛋", "fried egg"),
    "tofu_firm": ("豆腐", "北豆腐", "tofu"),
    "soy_milk_unsweetened": ("豆浆", "soy milk"),
    "milk_whole": ("牛奶", "全脂牛奶", "milk"),
    "milk_skim": ("脱脂牛奶", "skim milk"),
    "yogurt_plain": ("酸奶", "无糖酸奶", "yogurt"),
    "broccoli": ("西兰花", "西蓝花", "broccoli"),
    "bok_choy": ("小白菜", "青菜", "油菜", "bok choy"),
    "spinach": ("菠菜", "spinach"),
    "cucumber": ("黄瓜", "cucumber"),
    "tomato": ("番茄", "西红柿", "tomato"),
    "mushroom_shiitake": ("香菇", "蘑菇", "mushroom"),
    "potato_stir": ("土豆丝", "炒土豆丝"),
    "apple": ("苹果", "apple"),
    "banana": ("香蕉", "banana"),
    "orange": ("橙子", "橘子", "orange"),
    "peanut": ("花生", "peanut"),
    "almond": ("杏仁", "almond"),
    "walnut": ("核桃", "walnut"),
    "cooking_oil": ("食用油", "油", "cooking oil"),
    "sugar_white": ("白砂糖", "糖", "sugar"),
    "soy_sauce": ("生抽", "酱油", "soy sauce"),
    "sesame_paste": ("芝麻酱", "sesame paste"),
    "mayonnaise": ("蛋黄酱", "沙拉酱", "mayonnaise"),
    "milk_tea": ("奶茶", "milk tea"),
    "cola": ("可乐", "汽水", "cola"),
}
