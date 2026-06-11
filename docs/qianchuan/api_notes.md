# 千川后台接口逆向笔记（Phase 0 产出）

> 录制源：`dev/qc_traffic/qc_traffic_20260528_171405.jsonl`（1115 条接口）
> 录制账号：`advertiser_id=1852831850913418`
> 录制时间：2026-05-28
> 状态：✅ 主接口已锁定 / ⚠ 关键报表（按视频维度）实测为空数据

---

## 一、核心结论

**所有报表数据都走同一个接口**：`POST /ad/api/data/v1/common/statQuery`
不同视图（按视频 / 按计划 / 按账户 / 按时间）通过不同 `DataSetKey` 区分。

---

## 二、接口契约

### URL
```
POST https://qianchuan.jinritemai.com/ad/api/data/v1/common/statQuery
```

### 请求头要求
- Cookie（passport_csrf_token / uid_tt / sessionid 等抖音 SSO 全套）
- Content-Type: application/json
- 我们走 Playwright + 真实页面，cookie 自动带，无需手工组装

### 请求体（PascalCase）
```jsonc
{
  "DataSetKey": "roi2_video_material_analysis",
  "Dimensions": ["material_id", "material_name_v2", "material_content_v2"],
  "Metrics": ["stat_cost_for_roi2", "total_pay_order_count_for_roi2", "..."],
  "Filters": {
    "ConditionRelationshipType": 1,
    "Conditions": [
      {"Field": "advertiser_id",   "Values": ["1852831850913418"], "Operator": 7},
      {"Field": "marketing_goal",  "Values": ["2"],                "Operator": 7},
      {"Field": "adlab_mode_fork", "Values": ["1"],                "Operator": 7},
      {"Field": "material_type",   "Values": ["3"],                "Operator": 7}
    ]
  },
  "StartTime": "2026-05-21 00:00:00",
  "EndTime":   "2026-05-27 23:59:59",
  "refer": "video_material_analysis"
}
```

### 响应体
```jsonc
{
  "status_code": 0,
  "message": "",
  "data": {
    "StatsData": {
      "Totals":    { "<metric>": { "Value": 123.4, "ValueStr": "123.40" } },
      "Rows": [
        {
          "Dimensions": { "material_id":      { "Value": "7412345...", "ValueStr": "..." },
                          "material_name_v2": { "Value": "fc-8421-xxx.mp4", "ValueStr": "..." } },
          "Metrics":    { "stat_cost_for_roi2": { "Value": 123.4, "ValueStr": "123.40" },
                          "total_pay_order_count_for_roi2": { "Value": 5, "ValueStr": "5" } }
        }
      ],
      "TotalCount": "1"
    }
  }
}
```

提取范式：
```python
rows = resp["data"]["StatsData"]["Rows"]
for row in rows:
    material_id   = row["Dimensions"]["material_id"]["Value"]
    material_name = row["Dimensions"]["material_name_v2"]["Value"]
    cost          = row["Metrics"]["stat_cost_for_roi2"]["Value"]
```

---

## 三、可用的 DataSetKey 速查

| DataSetKey | 维度 | 用途 |
|---|---|---|
| `roi2_video_material_analysis` | material_id / material_name_v2 | **★ 短视频物料维度分析（MVP 主接口）** |
| `overall_roi_promotion_post_overview_for_live` | marketing_goal / stat_time_hour | 直播间投放总览（按小时） |
| `roi2_boost_show` | advertiser_id | "全域推广"账户级汇总（展示/点击/订单/GMV） |
| `overall_data_live_aweme_list` | （无）| 直播间所属短视频列表 |
| `home_cost_total_prom_trend` | （无）| 首页消耗趋势 |
| `common_overall_roi2_live_product_service_fee` | （无）| 平台服务费 |

---

## 四、可用 Metric 速查（按需取）

MVP 我们需要的核心指标（从录到的请求体里抽出）：

| 业务字段 | metric_name | 备注 |
|---|---|---|
| 消耗 | `stat_cost_for_roi2` | 元 |
| 支付订单数 | `total_pay_order_count_for_roi2` | 笔 |
| 支付 GMV | `total_pay_order_gmv_for_roi2` | 元 |
| 单均成本 | `total_cost_per_pay_order_for_roi2` | 元/单 |
| CPC | `total_cpc_for_roi2` | 元 |
| eCPM | `total_ecpm_for_roi2` | 元 |
| ROI（成单/消耗比） | （需用 GMV/Cost 算） | — |

> ⚠ 录到的 metric list 是直播 ROI2 体系，**没有独立"展示"和"点击"原始数**。如果 MVP 要展示展示/点击，需要额外探索（或换成业务更看重的"消耗/订单/GMV/ROI"）。

---

## 五、Filter 写法

`Operator` 推测含义（未完全验证，但符合现场观察）：
- `7` = IN（多值或等于）
- `Field` 常见取值：`advertiser_id`, `marketing_goal`, `material_type`, `adlab_mode_fork`

`marketing_goal` 取值（从录到的请求中观察）：
- `2` = 直播间投放（live）
- 其他值待验证（短视频带货可能是 `1`）

`material_type`：
- `3` = 视频物料（推测）

---

## 六、⚠ 已知约束（不是阻塞）

1. **`roi2_video_material_analysis` 只展示"有 ROI2 消耗的物料"**
   - 物料仅上传未投放 → Rows 为空
   - 投放但消耗为 0 → Rows 为空
   - 真投放产生消耗 → Rows 出现，含 material_id + material_name_v2 + metrics
   - MVP 决策：代码先写完，等账号开投自然有数据。不再做单独的"素材发现"接口。

2. **`marketing_goal` 值映射**
   - `2` = 直播间投放（live ROI2）
   - 短视频带货推测是 `1`（待真投放后确认）
   - MVP 实现：暂不在 Filters 里固定 marketing_goal，让接口返回全部物料类型
     （或仅过滤 advertiser_id + StartTime/EndTime，让千川自然过滤）

3. **`refer` 字段含义未定**
   - 录到的值：`video_material_analysis`、`uni-prom-creative-tab-list` 等
   - 看起来是前端页面来源标识，**对响应不影响**
   - MVP 实现：填一个固定值（如 `flowcut_sync`）

---

## 七、下一步（写 scraper 时直接用）

```python
QC_REPORT_API = "https://qianchuan.jinritemai.com/ad/api/data/v1/common/statQuery"

PAYLOAD_TEMPLATE = {
    "DataSetKey": "roi2_video_material_analysis",
    "Dimensions": ["material_id", "material_name_v2", "material_content_v2"],
    "Metrics": [
        "stat_cost_for_roi2",
        "total_pay_order_count_for_roi2",
        "total_pay_order_gmv_for_roi2",
        "total_cost_per_pay_order_for_roi2",
    ],
    "Filters": {
        "ConditionRelationshipType": 1,
        "Conditions": [
            {"Field": "advertiser_id", "Values": ["<ADVERTISER_ID>"], "Operator": 7},
            # marketing_goal 等过滤待商业前提确认后补
        ],
    },
    "StartTime": "<YYYY-MM-DD 00:00:00>",
    "EndTime":   "<YYYY-MM-DD 23:59:59>",
    "refer": "video_material_analysis",
}
```

**抓取策略**：
- 不直接 POST 该接口（怕缺 CSRF / 风控）
- 走 Playwright → 打开"按视频分析"页面 → NetworkRecorder 监听该 URL 的 response → 提取 Rows
- 翻页：观察 `Page` / `PageSize` 参数（本次录到的请求里没显式分页，可能要触发翻页动作再补录）
