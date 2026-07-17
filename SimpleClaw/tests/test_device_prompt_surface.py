"""Tests for App/Device prompt surface separation."""

from __future__ import annotations

from pathlib import Path

from admin.prompt_files import make_prompt_file_map
from Mojing.config import load_stable_sections


def _prompt(stage: str, surface: str) -> str:
    return "\n\n---\n\n".join(load_stable_sections(stage=stage, prompt_surface=surface))


def _dialogue_log() -> str:
    return (Path(__file__).parent / "fixtures" / "device_novice_recommendation_dialogue_log.md").read_text(
        encoding="utf-8"
    )


def test_device_surface_uses_independent_prompt_bundle() -> None:
    app_prompt = _prompt("novice", "app")
    device_prompt = _prompt("novice", "device")

    assert "硬件魔镜主 Agent" not in app_prompt
    assert "硬件魔镜主 Agent" in device_prompt
    assert "硬件魔镜语感" in device_prompt
    assert "硬件新手期：首次见面体验" in device_prompt
    assert "Soul · 闺蜜的灵魂" not in device_prompt
    assert "时期定位：新手期（初识期）" not in device_prompt


def test_device_journey_falls_back_only_inside_device_bundle() -> None:
    device_prompt = _prompt("explore", "device")

    assert "硬件新手期：首次见面体验" in device_prompt
    assert "时期定位：探索期" not in device_prompt
    assert "时期定位：新手期（初识期）" not in device_prompt


def test_device_photo_ritual_and_returned_photo_constraints() -> None:
    device_prompt = _prompt("novice", "device")

    assert "咔嚓" not in device_prompt
    assert "3...2...1" not in device_prompt
    assert "3、2、1" in device_prompt
    assert "照片事实必须等下一轮设备/系统输入给出" in device_prompt
    assert "照片或快分析事实返回前，不要追问" in device_prompt
    assert "第一次回图" in device_prompt
    assert "问一个相关短问题" in device_prompt


def test_device_novice_wish_entry_and_intent_recovery_constraints() -> None:
    device_prompt = _prompt("novice", "device")

    assert "没有心愿" in device_prompt
    assert "拒绝许愿提示" in device_prompt
    assert "不视为拒绝整个新手期" in device_prompt
    assert "低压力邀请用户查看今天皮肤状态" in device_prompt
    assert "等待明确同意" in device_prompt
    assert "由用户当前意图决定" in device_prompt
    assert "你能干什么" in device_prompt
    assert "首次拍照前的能力介绍" in device_prompt
    assert "不能直接倒数" in device_prompt
    assert "不能给拍照姿态或光线指令" in device_prompt
    assert "不能触发拍照" in device_prompt
    assert "要不要先看一下晒红范围" in device_prompt
    assert "你要不要看看我现在脸上状态" in device_prompt
    assert "那先看看今天出油区域" in device_prompt
    assert "可以当作同意拍照" in device_prompt
    assert "不必机械再问一次" in device_prompt
    assert "具体肤况烦恼本身不是同意" in device_prompt
    assert "鼻翼脱皮" in device_prompt
    assert "运动完脸油" in device_prompt
    assert "不要让用户上传照片" in device_prompt
    assert "从相册选" in device_prompt


def test_device_skin_and_lifestyle_recommendation_touchpoints() -> None:
    device_prompt = _prompt("novice", "device")

    assert "肤况触点" in device_prompt
    assert "生活场景触点" in device_prompt
    assert "主动发起只读护肤柜查询" in device_prompt
    assert "不必每次只读查护肤柜前都先问许可" in device_prompt
    assert "真实查询结果或已提供护肤柜事实" in device_prompt
    assert "不编造天气、周期、睡眠、旅行或运动事实" in device_prompt
    assert "成分方向、质地、品类或使用标准" in device_prompt
    assert "不能推荐具体品牌或 SKU" in device_prompt
    assert "首次拍照闭环尚未建立" in device_prompt


def test_device_status_prompt_exposes_volume_and_light_state_fields() -> None:
    device_prompt = _prompt("novice", "device")

    assert "用户问镜子电量、信号、在线状态、固件版本、设备名、音量、灯光亮度、色温等状态时调用" in device_prompt
    assert "`current_volume`" in device_prompt
    assert "`current_brightness`" in device_prompt
    assert "`color_temp_percentage`" in device_prompt
    assert "问当前音量、灯光状态、灯光亮度或色温时调用 `device_status`" in device_prompt
    assert "不要用 `device_command`" in device_prompt


def test_device_light_preset_prompt_exposes_supported_scene_modes() -> None:
    device_prompt = _prompt("novice", "device")

    assert "`light_preset_mode`" in device_prompt
    assert "约会光 -> `dating`" in device_prompt
    assert "卸妆光 -> `makeup_removal`" in device_prompt
    assert "阅读光 -> `reading`" in device_prompt
    assert "晚宴/晚艳光 -> `banquet`" in device_prompt
    assert "不要用护肤光或其他灯光替代" in device_prompt
    assert "不要拆成 `light_brightness_set` + `light_color_temp`" in device_prompt


def test_device_dismiss_prompt_exposes_user_initiated_room_exit() -> None:
    device_prompt = _prompt("novice", "device")

    assert "`device_dismiss`" in device_prompt
    assert "用户明确说退下、再见、拜拜、关机、休眠、不聊了、我走了" in device_prompt
    assert "后端会播放告别语并退出房间" in device_prompt
    assert "用户只是沉默时不调用" in device_prompt


def test_device_product_ingredient_and_diary_boundaries() -> None:
    device_prompt = _prompt("novice", "device")

    assert "在用产品触点" in device_prompt
    assert "成分触点" in device_prompt
    assert "区分使用方法、产品质地、搭配关系和皮肤反应风险" in device_prompt
    assert "不使用“黑名单”“买错了”“永久禁用”" in device_prompt
    assert "低风险即时建议" in device_prompt
    assert "成分持续监控" in device_prompt
    assert "主要作用和重要冲突边界" in device_prompt
    assert "护肤品瓶身" in device_prompt
    assert "现在不想拿，先说晒后护理" in device_prompt
    assert "不想拍瓶身" in device_prompt
    assert "产品已经保存、分析、持续监控或加入护肤柜" in device_prompt
    assert "不承诺会自动或持续监控某个产品" in device_prompt
    assert "功能完成触点" in device_prompt
    assert "不编造打卡连续天数" in device_prompt
    assert "首次肌肤日记话术只能是未来态或进行态" in device_prompt
    assert "刷新或更新已有肌肤日记，需要用户明确同意" in device_prompt
    assert "操作已完成，去查看" in device_prompt


def test_device_novice_recommendation_dialogue_log_covers_prd_scenarios() -> None:
    log = _dialogue_log()

    for scenario in [
        "S01 拒绝许愿",
        "S02 拒绝许愿",
        "S03 首次拍照前能力询问",
        "S04 明确同意后的拍照仪式",
        "S05 照片回传后的续聊",
        "S06 肤况触点和主动只读查护肤柜",
        "S07 护肤柜无合适产品后的新品方向",
        "S08 生活场景触点",
        "S09 在用产品触点",
        "S10 成分触点",
        "S11 功能完成触点",
        "S12 肌肤日记边界",
    ]:
        assert scenario in log

    for expected in [
        "User: 没有心愿",
        "User: 随便吧",
        "User: 你能干什么",
        "Mirror: 把镜子摆正一点，脸自然看向我。3、2、1。",
        "Post-tool visible reply: [empty]",
        "System: [设备/系统事件] 照片已回传",
        "Tool: device_command(capture_photo)",
        "Tool: list_skincare_cabinet_products(scope=\"in_cabinet\", limit=5)",
        "Tool: check_runtime_status(target=\"skin_diary\")",
    ]:
        assert expected in log

    for forbidden in [
        "咔嚓",
        "黑名单",
        "买错了",
        "永久禁用",
        "已生成首次肌肤日记",
        "操作已完成，去查看",
        "去小程序看看",
        "上传照片",
        "从相册选",
        "具体品牌",
        "SKU",
    ]:
        assert forbidden not in log


def test_admin_prompt_map_exposes_device_bundle_separately() -> None:
    workspace = Path("/tmp/workspace")
    entries = make_prompt_file_map(workspace, Path("/tmp/subagent"))

    assert entries["device_agent"].path == workspace / "device" / "Agent.md"
    assert entries["device_soul"].path == workspace / "device" / "SOUL.md"
    assert entries["device_tool"].path == workspace / "device" / "TOOL.md"
    assert entries["device_novice"].path == workspace / "device" / "journey" / "novice.md"
    assert entries["agent"].group == "App 主 Agent"
    assert entries["device_agent"].group == "硬件魔镜 Device"
