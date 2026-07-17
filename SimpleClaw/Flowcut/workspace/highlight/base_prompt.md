# AI 漫剧高光底层分析 Prompt

这是 `highlight` workflow 的底层共享 prompt。它不直接决定最终投放形态，而是为后续两个流程提供统一的视频理解、拆镜、高光候选评分和结尾衔接性分析：

- `hook_to_original`：高光前置后接回原片。
- `hook_to_digital_human`：高光片段与数字人口播/广告话术拼接。

## 核心目标

从完整视频中找到最适合放到开头或与数字人拼接的高光片段。高光片段必须同时满足：

- 开头能快速吸引用户。
- 结尾能自然衔接后续内容。
- 观众即使没有完整前情，也能理解关键问题或产生继续观看动机。
- 不因为前置而破坏剧情因果、人物情绪和信息顺序。

不要只选择情绪最强或画面最刺激的片段。一个片段如果结尾无法衔接，或者高度依赖前文，即使很精彩，也不能作为首选高光。

## 垂类识别与视觉信号

在拆镜前，先判断视频所属垂类，并在整个分析过程中应用对应的视觉信号规则。

**垂类一：都市爽文·逆袭型**（重卡/职场/商战/身份隐藏/家庭逆袭等）

以下动作帧一旦出现，`genre_signal` 必须标记，且该段 `hook_strength` 不得低于 7：

| 信号名 | 识别描述 |
|--------|---------|
| `face_slap` | 手掌/巴掌挥出 + 对方脸部转向的连贯动作 |
| `kneel_down` | 对立方/小人屈膝跪地，哪怕持续不足 2 秒 |
| `identity_reveal` | 主角报出真实身份/资产，旁人反应骤变 |
| `crowd_shock` | 围观人群集体后退、惊呼、议论的反应特写 |
| `throw_smash` | 摔/砸东西等肢体爆发，冲突激化的前置信号 |

**垂类二：古风/仙侠/玄幻·升级型**（修仙/御兽/穿越/异世界等）

以下视觉帧一旦出现，`genre_signal` 必须标记，且该段 `hook_strength` 不得低于 7：

| 信号名 | 识别描述 |
|--------|---------|
| `mass_battle` | 多人/多兽混战，全屏粒子/爆炸/冲击波特效，无论有无台词 |
| `beast_reveal` | 神兽/灵宠全身显形，镜头从局部拉到全景 |
| `skill_burst` | 功法/元素爆发（火/冰/雷/光柱/灵气漩涡），持续 ≥ 3 秒的特效段落 |
| `combo_rush` | 高速连招，画面残影/运动模糊密集，剪辑频率骤然加密（帧间切换 < 0.5 秒） |
| `suppress_finish` | 主角将对手踩在脚下或单手制服，打斗段落的视觉终点 |
| `awakening` | 觉醒/突破瞬间，BGM 骤变 + 特效全场压制 |

**视觉信号的优先级说明：**

- 视觉信号段落即使 `context_dependency` 较高，也不能因此降低 `hook_strength`。这类片段的吸引力来自画面冲击力，不依赖台词前情。
- `mass_battle`、`skill_burst` 类段落即使无台词，也应被标记为 `narrative_role: action` 或 `emotional_peak`，不得标为 `transition` 或 `setup`。
- 含 `face_slap`、`kneel_down` 的段落，结尾状态应优先描述动作结果（”对方跪倒”/”巴掌落下后的沉默”），而非泛泛的情绪描述。

---

## 高光定义

高光不是普通精彩片段，而是短视频开头可用的 hook。优先选择具备以下特征的片段：

- 明确冲突：人物之间有对立、质疑、误解、争执或强行动作。
- 明确危机：出现损失、危险、时间压力、机会窗口、失败风险。
- 明确悬念：片段会让观众追问”为什么””接下来怎么办””他说的是真的吗”。
- 情绪张力：人物状态强烈，如焦急、震惊、愤怒、委屈、恐惧、坚定。
- 信息密度高：几秒内交代关键 stakes，而不是松散铺垫。
- 可独立理解：不需要大量前情才能看懂基本冲突。
- 结尾可承接：结尾留有问题、动作延续、解释空间或自然转场点。

不适合作为主高光的片段：

- 只有环境铺垫，没有剧情问题。
- 只有人物出场，没有冲突或风险。
- 只在原片中后段成立，缺少前情会显得突兀。
- 台词说到一半，结尾没有完整语义。
- 画面/台词很强，但结尾无法自然接回原片、数字人或广告。
- 信息重复，前置后会导致正片里马上重复同一段而显得拖沓。

特别注意 `hook_to_original` 场景：

- “接回原片开头”不是让原片开头本身获得高衔接分。
- 原片开头的铺垫段通常只能作为 `context_only`，不能因为它本来就在开头就给 `followup_fit.original_video=10`。
- 如果某段就是原片开头/铺垫，且没有强冲突或强悬念，应把 `candidate_use` 标为 `context_only` 或 `avoid`，`hook_strength` 不应虚高。
- 对于原片开头本身，`followup_fit.original_video` 应理解为“如果把该段当前置高光后，再接回原片开头是否有新增价值且不重复”，而不是时间线连续性。若会形成重复或没有重排价值，分数应低。
- 如果原片 0 秒附近本身已经是合格高光，应明确说明“不需要高光前置重排”，后续组装应直接输出原片或只做轻量标准化，不要推荐“开头高光 + 原片开头”的重复结构。
- 只有当某个中后段高光片段前置后，能够自然接回原片开头，并且不会让观众觉得重复、倒叙混乱或信息割裂，才可以给较高 `original_video` 适配分。

## 拆镜要求

先完整观看视频，再按镜头和剧情动作拆分。每段通常 2-6 秒。满足以下任一条件时开新段：

- 切景。
- 主体变化。
- 景别变化。
- 动作逻辑变化。
- 情绪阶段变化。
- 冲突阶段变化。
- 关键信息点出现。
- 对话对象或叙事功能变化。

每段必须保留可剪辑时间戳，不要只给剧情摘要。

## 逐段字段

每个 segment 输出：

- `idx`：从 0 开始的段序号。
- `start_time`：秒，数字。
- `end_time`：秒，数字。
- `visual`：纯画面描述，只写人物、场景、动作、构图、镜头语言、屏幕文字。不要写口播台词。
- `copy`：人物、旁白、字幕中的主要台词，尽量逐字转录。没有则为空字符串。
- `narrative_role`：该段在剧情中的作用。可选值：
  - `setup`
  - `conflict`
  - `reveal`
  - `emotional_peak`
  - `action`
  - `transition`
  - `resolution`
  - `other`
- `hook_strength`：0-10，作为开头高光的吸引力。
- `context_dependency`：0-10，对前情的依赖程度。越高越不适合直接前置。
- `ending_connectability`：0-10，该段结尾衔接后续内容的能力。越高越容易接。
- `continuity_risk`：0-10，前置后产生割裂、重复、因果混乱的风险。越高风险越大。
- `ending_state`：该段结尾停在什么状态，例如“危机被抛出”“问题被质疑”“动作正在继续”“人物情绪爆发后停顿”。
- `open_question`：该段结尾留给观众的问题。如果没有，填空字符串。
- `bridge_text`：如果该段作为高光后需要一句衔接话术，给出一句自然短句；不需要则填空字符串。
- `genre_signal`：本段是否触发垂类视觉信号。如果触发，填入信号名（见「垂类识别与视觉信号」一节），如 `face_slap`、`mass_battle`；未触发则填 `null`。一段可触发多个信号，用数组表示。
- `candidate_use`：可选值：
  - `primary_hook`
  - `secondary_hook`
  - `context_only`
  - `avoid`
- `reason`：为什么这样评分，必须具体到画面/台词/情绪/衔接点。如果触发了 `genre_signal`，必须在 reason 中说明该信号对评分的影响。
- `followup_fit`：对象，分别判断该段结尾与不同衔接对象的适配性：
  - `original_video`：0-10，作为前置高光后接回原片开头的自然程度和重排价值。不要把原片开头本身的时间线连续性当成高分。
  - `digital_human`：0-10，接数字人口播/讲解的自然程度。
  - `ad`：0-10，接广告或下载引导的自然程度。
  - `reason`：一句话说明主要衔接依据。

## 候选高光组合

候选高光可以是一段，也可以是连续多段。不要跨越太远的非连续片段，除非上层流程明确允许混剪。

每个候选输出：

- `segment_ids`：连续段 ID。
- `start_time` / `end_time`。
- `duration`。
- `hook_strength`。
- `context_dependency`。
- `ending_connectability`。
- `continuity_risk`。
- `ending_state`。
- `open_question`。
- `bridge_text`。
- `why_it_works`：为什么它能作为高光。
- `why_it_may_fail`：它可能失败或割裂的地方。
- `best_followup_type`：该高光结尾最适合接什么。可选：
  - `original_video`
  - `digital_human`
  - `ad`
  - `landing_page`
  - `not_recommended`

候选的综合判断不要只看 `hook_strength`。优先级应为：

1. 高光吸引力足够。
2. 上下文依赖不能过高。
3. 结尾可衔接性强。
4. 连续播放不破坏因果。
5. 后续接原片、数字人或广告时能给出明确桥接策略。

## 衔接分析

必须单独分析高光结尾，而不是只分析高光开头。

对每个强候选都要回答：

- 这个片段结尾是完整语义，还是半句话/半个动作？
- 结尾是否留下自然问题？
- 结尾适合接回原片开头吗？
- 结尾适合切到数字人口播吗？
- 结尾适合接广告/下载引导吗？
- 如果衔接有风险，是否能用一句 `bridge_text` 修复？
- 如果不能修复，必须降低推荐级别。

## 输出协议

输出必须是严格 JSON，不要添加 markdown、代码块或解释文字。

基础输出结构：

```json
{
  "video_summary": {
    "story": "一句话概括剧情",
    "main_conflict": "核心冲突",
    "stakes": "损失/风险/欲望/目标",
    "emotional_arc": "情绪走向",
    "genre": "urban_reversal | xianxia_upgrade | urban_emotion | other",
    "genre_signals_detected": ["face_slap", "kneel_down"]
  },
  "segments": [
    {
      "idx": 0,
      "start_time": 0,
      "end_time": 2.5,
      "visual": "纯画面描述",
      "copy": "台词",
      "narrative_role": "setup",
      "hook_strength": 0,
      "context_dependency": 0,
      "ending_connectability": 0,
      "continuity_risk": 0,
      "ending_state": "结尾状态",
      "open_question": "留下的问题",
      "bridge_text": "可选桥接话术",
      "genre_signal": null,
      "candidate_use": "context_only",
      "reason": "具体理由",
      "followup_fit": {
        "original_video": 0,
        "digital_human": 0,
        "ad": 0,
        "reason": "衔接判断依据"
      }
    }
  ],
  "hook_candidates": [
    {
      "rank": 1,
      "segment_ids": [1],
      "start_time": 2.5,
      "end_time": 8.5,
      "duration": 6,
      "hook_strength": 8.5,
      "context_dependency": 3,
      "ending_connectability": 8,
      "continuity_risk": 2,
      "ending_state": "危机被明确抛出",
      "open_question": "为什么必须现在行动？",
      "bridge_text": "先看这场危机怎么爆发，再回到事情开始。",
      "why_it_works": "冲突、风险和时间压力明确",
      "why_it_may_fail": "接回原片时可能形成倒叙，需要桥接",
      "best_followup_type": "original_video"
    }
  ],
  "global_risks": [
    "整体高光前置可能带来的重复、割裂或信息顺序风险"
  ]
}
```

## 质量要求

- 所有评分必须能从画面、台词或剧情功能中解释，不要空泛。
- 不要把“画面好看”当作主要高光理由。
- 不要只选最靠前的片段；必须比较至少 3 个候选或说明候选不足。
- 如果没有合格高光，要明确返回低分候选，并说明不建议前置。
- 如果高光结尾不适合衔接，不能推荐为主高光。
- 如果一个片段适合数字人但不适合接原片，要明确区分。
