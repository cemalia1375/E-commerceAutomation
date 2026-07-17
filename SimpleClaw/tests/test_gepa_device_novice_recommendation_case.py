from __future__ import annotations

import yaml

from script.gepa.dataset import load_case_file


CASE_PATH = "script/gepa/cases/mirror_device_novice_recommendation_10x20_v1.yaml"
CONTRACT_PATH = "script/gepa/contracts/mirror_device_dialogue_contracts.yaml"


def test_gepa_device_novice_recommendation_case_is_10_by_20() -> None:
    case = load_case_file(CASE_PATH)

    assert case.id == "mirror_device_novice_recommendation_10x20_v1"
    assert case.prompt_surface == "device"
    assert len(case.examples) == 10
    assert all(len(example.turns) == 20 for example in case.examples)


def test_gepa_device_capture_contract_matches_current_photo_ritual() -> None:
    contract = yaml.safe_load(open(CONTRACT_PATH, encoding="utf-8")) or {}
    check = contract["check_library"]["capture_ritual_only_v1"]

    assert "3、2、1" in check["any_of"]
    assert "咔嚓" in check["none_of"]
    assert "3...2...1" in check["none_of"]


def test_gepa_device_capture_contract_uses_real_photo_result_action() -> None:
    case = load_case_file(CASE_PATH)
    contract_text = open(CONTRACT_PATH, encoding="utf-8").read()

    assert "device_capture_photo_ready_v1" in contract_text
    assert "tool_result_action: device_command:photo_ready" in contract_text
    assert "device_capture_dispatched_v1" not in contract_text
    assert "device_command:dispatched" not in contract_text
    assert all(
        {"ref": "device_capture_dispatched_v1"} not in turn.hard_assertions
        for example in case.examples
        for turn in example.turns
    )


def test_gepa_device_contract_accepts_natural_boundary_phrasing() -> None:
    contract = yaml.safe_load(open(CONTRACT_PATH, encoding="utf-8")) or {}
    checks = contract["check_library"]

    assert "会不会" in checks["returned_photo_followup_v1"]["any_of"]
    assert "先别用" in checks["ingredient_conflict_boundary_v1"]["any_of"]
    assert "刺痛" in checks["product_ingredient_fit_v1"]["any_of"]
    assert "具体品牌" not in checks["no_brand_sku_or_negative_labels_v1"]["none_of"]
    assert "我就会帮你持续监控" in checks["product_ingredient_fit_v1"]["none_of"]
