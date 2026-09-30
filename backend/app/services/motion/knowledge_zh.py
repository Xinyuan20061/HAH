# -*- coding: utf-8 -*-
"""动作中文讲解条目（规格 §6.1 knowledge_keys 钩子；C 包编写）。

键与 ``motion_catalog/catalog.yaml`` 中每个动作的 ``knowledge_keys`` 一一对应，
也与生成物 ``catalog_data.py`` 中 ``ACTIONS[*].knowledge_keys`` 对齐。

供两处消费：
  * ``vision_review.run_visual_review``：把对应动作的中文要点喂给视觉模型，
    让它"先独立描述，再对照要点"，而不是替它下结论。
  * ``text_summary``：视觉不可用/被语义校验降级时，按动作家族与已有事实
    拼装本地可读文案。卧姿、俯卧、静态、靠墙动作不得套用共用的"站稳/全身
    入镜"模板。

本模块纯数据、无网络调用。新增动作时由目录包在 yaml 登记 knowledge_keys，
C 包在此补齐对应条目（键名必须完全一致）。
"""

from __future__ import annotations

from typing import Any

from . import catalog

__all__ = [
    "KNOWLEDGE_ZH",
    "ACTION_FAMILY",
    "knowledge_for_action",
    "action_family",
    "knowledge_text_for_action",
]

# 动作家族：决定降级模板是否使用"站稳/全身入镜"等站姿话术。
#   standing  站姿动作（腿或手持器械）
#   supine    仰卧动作
#   prone     俯卧/俯撑动作
#   wall      靠墙/静力保持
ACTION_FAMILY: dict[str, str] = {
    "squat": "standing",
    "lunge": "standing",
    "leg_abduction": "standing",
    "calf_raise": "standing",
    "arm_abduction": "standing",
    "bicep_curl": "standing",
    "hammer_curl": "standing",
    "front_raise": "standing",
    "lateral_raise": "standing",
    "shoulder_press": "standing",
    "row": "standing",
    "deadlift": "standing",
    "jumping_jack": "standing",
    "bench_press": "supine",
    "crunch": "supine",
    "hip_bridge": "supine",
    "pushup": "prone",
    "plank": "prone",
    "side_plank": "prone",
    "mountain_climber": "prone",
    "arm_vw": "wall",
}

# 每条目：title 阶段名 / point 为什么留意 / tip 下一遍怎么做。
KNOWLEDGE_ZH: dict[str, dict[str, str]] = {
    # ---- 深蹲 ----
    "squat_setup": {
        "title": "站姿准备",
        "point": "双脚与肩同宽、脚尖略向外，下蹲前先让髋膝踝同时预备弯曲。",
        "tip": "下一次开始前先站定，确认双脚踩实再下蹲，不要急着往下。",
    },
    "squat_depth": {
        "title": "下蹲幅度",
        "point": "留意大腿是否大致与地面平行，腰背保持中立，而不是只蹲一小截。",
        "tip": "下一遍试着蹲到大腿接近平行地面再起身，不必勉强更深。",
    },
    "squat_balance": {
        "title": "起身稳定",
        "point": "起身时重心落在脚掌中部，膝盖与脚尖方向一致，避免内扣。",
        "tip": "下一遍起身时想象用脚跟把地面蹬开，看看膝盖是否还跟着脚尖走。",
    },
    # ---- 俯卧撑 ----
    "pushup_setup": {
        "title": "支撑准备",
        "point": "双手略宽于肩，身体从肩到脚跟成一条线，先收腹再下放。",
        "tip": "下一次撑好后先停顿一拍，确认腰背没有塌下去再开始下降。",
    },
    "pushup_elbow": {
        "title": "肘部角度",
        "point": "下放时大臂与身体约成四十五度，肘部不要完全向外打开成平线。",
        "tip": "下一遍留意肘部位置，让它自然向后下方走，而不是向两侧横开。",
    },
    "pushup_bodyline": {
        "title": "身体一线",
        "point": "整个过程中躯干保持绷紧，臀部不要翘起或腰不要往下塌。",
        "tip": "下一次把腹部轻微收紧，保持肩、髋、脚跟在一条斜线上。",
    },
    # ---- 弓步蹲 ----
    "lunge_setup": {
        "title": "弓步站距",
        "point": "前后脚分开一步左右，前脚掌踩实，后脚脚尖点地，躯干直立。",
        "tip": "下一次先把前后步幅站舒服再下蹲，步幅太小会让前膝压力偏大。",
    },
    "lunge_depth": {
        "title": "下蹲深度",
        "point": "下蹲时前腿大腿接近平行、后膝自然向下靠近地面，不必追求触地。",
        "tip": "下一遍蹲到前腿发力顺畅即可，重点是控制而不是蹲得更低。",
    },
    "lunge_stability": {
        "title": "前膝稳定",
        "point": "前膝大致对齐脚尖，上下过程中不要左右晃动或过度内扣。",
        "tip": "下一次慢一点下落，观察前膝是否稳定地顺着脚尖方向走。",
    },
    # ---- 站姿腿外展 ----
    "leg_abduction_setup": {
        "title": "站姿准备",
        "point": "单脚站稳或扶稳支撑物，动作腿放松伸直，从髋部向外打开。",
        "tip": "下一次先扶稳再抬腿，把注意力放在髋部发力，而不是身体歪斜。",
    },
    "leg_abduction_control": {
        "title": "控制还原",
        "point": "腿向外打开后有控制地回到中间，不要靠惯性甩出去又掉回来。",
        "tip": "下一遍在最高点稍停半拍再慢慢放下，看看身体是否还保持中立。",
    },
    # ---- 站姿臂外展 ----
    "arm_abduction_setup": {
        "title": "站姿准备",
        "point": "双手持物自然垂于体侧，肘部微屈，从肩部把双臂向两侧抬起。",
        "tip": "下一次先让肩膀放松下沉，再开始抬臂，避免耸肩借力。",
    },
    "arm_abduction_control": {
        "title": "控制下落",
        "point": "抬到大约与肩同高即可，然后有控制地放下，不要自由落体。",
        "tip": "下一遍放下时数两拍，看看能否让手臂慢慢回到大腿两侧。",
    },
    # ---- 靠墙静力 ----
    "arm_vw_setup": {
        "title": "靠墙准备",
        "point": "背部贴墙，下巴微收，手臂与背保持贴墙位置，先站稳再开始计时。",
        "tip": "下一次先把后背贴实墙面，再进入保持姿势，不要中途离开墙面。",
    },
    "arm_vw_hold": {
        "title": "静力保持",
        "point": "保持期间躯干贴墙、不塌腰，均匀呼吸，不要憋气。",
        "tip": "下一遍保持时把呼吸放长，留意后背是否始终贴着墙面。",
    },
    # ---- 哑铃弯举 ----
    "bicep_curl_setup": {
        "title": "站姿准备",
        "point": "双脚站稳，大臂贴在身体两侧不动，双手持物从大腿前侧开始弯曲肘。",
        "tip": "下一次开始前先确认上臂贴着身体，不要让它跟着前后摆动。",
    },
    "bicep_curl_control": {
        "title": "上臂固定控制",
        "point": "弯曲肘关节把前臂向上带，最高点控制住再慢慢放下，上臂始终贴体侧。",
        "tip": "下一遍放下时数两拍，并留意上臂是否还稳稳停在身体两侧。",
    },
    # ---- 锤式弯举 ----
    "hammer_curl_setup": {
        "title": "中立握准备",
        "point": "双手手心相对握持，大臂贴体侧，从肘部开始弯举。",
        "tip": "下一次先确认握法是手心相对，再开始动作，避免大臂外移。",
    },
    "hammer_curl_control": {
        "title": "轨迹控制",
        "point": "前臂沿竖直方向起落，最高点略停，放下时不要借身体摆动。",
        "tip": "下一遍在最高点稍停，再慢慢放下，看看躯干是否还保持稳定。",
    },
    # ---- 前平举 ----
    "front_raise_setup": {
        "title": "站姿准备",
        "point": "双手持物垂于大腿前，肘部微屈，从肩部把双臂向前上方抬起到肩高。",
        "tip": "下一次先让肩膀放松，再向前抬臂，避免耸肩或身体后仰。",
    },
    "front_raise_control": {
        "title": "前平举控制",
        "point": "抬到大约肩高即可，然后有控制地下放，不要靠惯性甩动。",
        "tip": "下一遍在最高点稍停半拍，再慢慢放回腿前，体会肩部的控制。",
    },
    # ---- 侧平举 ----
    "lateral_raise_setup": {
        "title": "站姿准备",
        "point": "双手持物垂于体侧，肘部微屈，从肩部把双臂向两侧抬起到肩高。",
        "tip": "下一次先沉肩再抬臂，留意手腕不要高过肘部。",
    },
    "lateral_raise_control": {
        "title": "侧平举控制",
        "point": "抬到与肩同高、肘部略低于手腕即可，然后有控制地放下。",
        "tip": "下一遍放下时数两拍，看看手臂能否匀速回到身体两侧。",
    },
    # ---- 肩推 ----
    "shoulder_press_setup": {
        "title": "坐姿/站姿准备",
        "point": "双手持物举到耳侧，肘部朝前下，核心收紧，躯干保持中立。",
        "tip": "下一次先把腹部轻微收紧，再向上推起，避免腰部过度反弓。",
    },
    "shoulder_press_control": {
        "title": "推举轨迹",
        "point": "把器械竖直向上推到手臂接近伸直，再有控制地落回耳侧。",
        "tip": "下一遍推起时想象把重量向上顶向天花板，不要让它前后绕圈。",
    },
    # ---- 划船 ----
    "row_setup": {
        "title": "俯身准备",
        "point": "髋部后铰链、背部挺直，双手持物自然下垂，先稳住躯干再开始拉。",
        "tip": "下一次先让背部打直、髋部后移，确认没有弓背再开始拉桨。",
    },
    "row_pull": {
        "title": "拉桨",
        "point": "肘部贴着身体向后拉，肩胛向后收，然后有控制地向前放回。",
        "tip": "下一遍把肘部顺着身体向后带，放回时数两拍，不要靠甩动。",
    },
    # ---- 硬拉 ----
    "deadlift_setup": {
        "title": "硬拉准备",
        "point": "双脚与肩同宽，器械贴近小腿，屈膝、髋后移、背部保持中立。",
        "tip": "下一次先让腰背放平、重心落在脚掌中部，再开始起身。",
    },
    "deadlift_hinge": {
        "title": "髋铰链",
        "point": "起身时用脚跟蹬地、髋部向前顶，躯干保持中立，不要先弓背再拉。",
        "tip": "下一遍留意髋部是否先向前送，而不是先把腰弓起来。",
    },
    # ---- 卧推 ----
    "bench_press_setup": {
        "title": "卧姿准备",
        "point": "仰卧在凳上，双脚踩实，背部微微收紧，器械举到肩关节正上方。",
        "tip": "下一次先踩稳脚、收紧背部，再开始下放器械，不要让身体晃动。",
    },
    "bench_press_path": {
        "title": "器械轨迹",
        "point": "把器械有控制地下放到胸口附近，再竖直推回到肩上方。",
        "tip": "下一遍下放时数两拍，看看器械是否落在胸口正中而不是脖子附近。",
    },
    # ---- 卷腹 ----
    "crunch_setup": {
        "title": "仰卧准备",
        "point": "仰卧屈膝、双脚踩地，下背贴地，双手轻放耳侧或胸前。",
        "tip": "下一次先确认下背贴地，再开始卷起，不要用手掰脖子。",
    },
    "crunch_flexion": {
        "title": "卷腹幅度",
        "point": "用腹部力量把上背卷离地面，肩胛略微抬起即可，再慢慢放下。",
        "tip": "下一遍在最高点稍停半拍，再慢慢放回，不要靠甩头起身。",
    },
    # ---- 平板支撑 ----
    "plank_setup": {
        "title": "俯撑准备",
        "point": "前臂撑地，肘在肩下方，身体从肩到脚跟成一条直线，腹部收紧。",
        "tip": "下一次先把前臂撑稳、腹部收紧，再进入保持，不要急着塌腰。",
    },
    "plank_hold": {
        "title": "平板保持",
        "point": "保持期间髋部不塌也不翘高，均匀呼吸，臀部与肩踝成一线。",
        "tip": "下一遍保持时把呼吸放长，留意臀部是否还停在肩踝连线上。",
    },
    # ---- 侧平板 ----
    "side_plank_setup": {
        "title": "侧卧准备",
        "point": "侧卧、前臂撑地，肘在肩下方，把髋部抬起，身体保持一条斜线。",
        "tip": "下一次先把髋部抬离地面，再进入保持，不要让髋往下掉。",
    },
    "side_plank_hold": {
        "title": "侧桥保持",
        "point": "保持期间髋部不掉线，肩、髋、脚踝成一条斜线，呼吸均匀。",
        "tip": "下一遍留意髋部位置，把它保持在肩踝连线上方，不要下坠。",
    },
    # ---- 臀桥 ----
    "hip_bridge_setup": {
        "title": "仰卧准备",
        "point": "仰卧屈膝、双脚踩地与肩同宽，脚跟靠近臀部，手臂平放身体两侧。",
        "tip": "下一次先把脚踩实、脚跟靠近臀部，再开始抬髋。",
    },
    "hip_bridge_lift": {
        "title": "臀桥抬起",
        "point": "用脚跟蹬地把髋部向上顶到肩髋膝成一条线，再有控制地落下。",
        "tip": "下一遍在顶端稍停半拍，感受臀部收紧，再慢慢放下髋部。",
    },
    # ---- 提踵 ----
    "calf_raise_setup": {
        "title": "站姿准备",
        "point": "双脚与肩同宽、踩实地面（可扶稳），从踝关节把脚跟向上提起。",
        "tip": "下一次先站稳扶稳，再踮起脚跟，避免身体前后晃。",
    },
    "calf_raise_control": {
        "title": "提踵控制",
        "point": "踮到最高点稍停，再有控制地放下，不要自由落体砸回地面。",
        "tip": "下一遍放下时数两拍，看看能否让脚跟匀速落到地面。",
    },
    # ---- 开合跳 ----
    "jumping_jack_setup": {
        "title": "开合跳准备",
        "point": "双脚并拢站立，手臂自然垂于体侧，准备随跳跃同时打开手脚。",
        "tip": "下一次先从较小幅度开始，落地时膝盖微屈缓冲。",
    },
    "jumping_jack_rhythm": {
        "title": "开合节奏",
        "point": "跳起时手脚同时向两侧打开，落地时同时收回，保持均匀节奏。",
        "tip": "下一遍试着用同一个节拍完成开合，看看节奏是否比上一遍更顺。",
    },
    # ---- 登山跑 ----
    "mountain_climber_setup": {
        "title": "俯撑准备",
        "point": "双手撑地、手在肩下方，身体成一条斜线，腹部收紧，准备交替提膝。",
        "tip": "下一次先把身体撑成一条斜线，再开始提膝，不要先塌腰。",
    },
    "mountain_climber_rhythm": {
        "title": "提膝节奏",
        "point": "左右腿交替向胸前提膝，髋部保持稳定，不要左右扭胯。",
        "tip": "下一遍保持臀部高度稳定，用较慢的交替节奏先把动作做顺。",
    },
}


def action_family(canonical_id: str | None) -> str:
    """返回动作家族（standing/supine/prone/wall）。未知动作按 standing 处理。"""
    if not canonical_id:
        return "standing"
    return ACTION_FAMILY.get(canonical_id, "standing")


def knowledge_for_action(canonical_id: str | None) -> list[dict[str, str]]:
    """返回某动作按 catalog knowledge_keys 排序后的中文讲解条目。"""
    if not canonical_id:
        return []
    keys = catalog.knowledge(canonical_id)
    out: list[dict[str, str]] = []
    for k in keys:
        entry = KNOWLEDGE_ZH.get(k)
        if entry:
            out.append({"key": k, **entry})
    return out


def knowledge_text_for_action(canonical_id: str | None) -> list[str]:
    """返回喂给视觉模型的中文要点文本列表（title + point）。"""
    return [f"{e['title']}：{e['point']}" for e in knowledge_for_action(canonical_id)]
