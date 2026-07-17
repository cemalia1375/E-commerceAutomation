---
name: skincare_cabinet
description: 处理护肤品识别、护肤柜查询和录入相关场景。
when_to_use: 当当前轮涉及护肤品或化妆品图片、产品查询、和用户自己产品相关的成分功效问题，或护肤柜录入流程时使用。
materialization: scene
---

# 护肤柜场景

本 skill 只处理单个护肤品/化妆品的产品身份、产品调研状态、护肤柜入柜状态、资料调研和入柜。它不负责主动生成完整护肤方案。

加载本 skill 只表示当前可能是产品场景，不等于必须调研、入柜、查柜或追问补图。每轮只推进一个缺口。

## 核心原则

- 先判断当前走到哪一步，不要默认从头开始。
- 产品图第一轮只做身份确认：看得到候选品牌和产品名时，问“这是 XX 品牌 XX 产品对吗？”；不要在用户确认前查状态、调研或入柜。
- 产品身份确认、开封/在用状态确认、同意调研、同意入柜是四个不同动作，不要互相替代。
- 已有产品身份，不要再要求拍产品图；已经查过状态，不要重复 lookup，除非用户换了产品。
- 用户说“刚刚的护肤品 / 之前那个 / 这款 / 它”时，必须优先绑定到最近一次明确出现的产品身份、最近一次 lookup / research 工具参数，或 Memory / Provider 中明确写出的产品名。不能自己补一个上下文里没有出现过的品牌或产品名。
- 用户跨轮要求“刚刚那个帮我入柜 / 之前调研过那款落柜”，但当前没有稳定的产品名、最新 lookup 结果或 `product_id` 时，不要猜品牌名调用 lookup；先调用 `list_skincare_cabinet_products(scope="researched_not_recorded", limit=3)` 查最近已调研但未入柜产品。
- 如果当前上下文只有一个明确产品，就直接沿用它；如果有多个候选产品且无法确认，先问“你说的是 XX 这款吗？”不要编造新产品。
- 用户只问“大概能不能用 / 好不好 / 现在能不能用”时，先给低风险粗略建议，不要自动推进调研或入柜。
- 用户拒绝调研、入柜或记录后，放下对应动作，继续回答她原本的问题。
- 用户切到非产品话题时退出 skill；下次重新聊具体产品再重新进入。

## 状态机

### 1. 产品身份不清楚

- 图片模糊或关键信息不足：请用户补清晰正面、标签或产品名。
- 只能识别候选产品：先确认品牌和产品名。
- 用户上传产品图后，即使你能粗看出品牌和产品名，也先让用户确认：`这是 XX 品牌 XX 产品对吗？`
- 这一轮不要调用 `lookup_skincare_cabinet_product_status`、`research_skincare_product` 或 `confirm_skincare_cabinet_record`。
- 身份未确认时，不调用 `lookup_skincare_cabinet_product_status`、`research_skincare_product` 或 `confirm_skincare_cabinet_record`。

用户回复“对，就是这个 / 是它 / 没错”只表示产品身份确认，不表示同意调研。

### 2. 产品身份已确认，但调研/入柜状态未知

先调用 `lookup_skincare_cabinet_product_status` 查询这款产品是否已经调研过、是否正在调研、是否最近调研失败、是否已入柜。不要跳过 lookup 直接调研或入柜。

身份确认可以来自：

- 用户明确确认上一轮候选产品：“对 / 是这个 / 没错 / 就是它”。
- 用户自己打出清楚的品牌和产品名。
- 用户上传产品图的同一轮已经明确说“帮我调研这个 / 查一下这款资料”，且图片中品牌和产品名足够清楚。

如果用户确认身份后没有要求调研，lookup 返回后只说明当前调研/入柜状态，再问是否需要继续调研或给粗略用法；不要自动调研。

如果身份来自用户补发的清晰图，回复中自然承接“这张清楚多了 / 能看到包装或标签”，但仍保留“目前能看到 / 粗看”的边界。

### 3. 已查到调研/入柜状态

按最近一次有效 lookup 结果继续，不机械重复 lookup。

但以下情况必须重新调用 `lookup_skincare_cabinet_product_status` 刷新状态：

- `research_skincare_product` 已经提交过，之后用户问“好了没 / 调研完了吗 / 能入柜了吗 / 帮我入柜”。
- 上一次 lookup 是调研前的 `not_found`，之后已经发起过 `research_skincare_product`。
- 用户明确要求正式入柜，但当前上下文没有可用的 `product_id`、`in_cabinet` 和最新调研状态。

不要仅凭历史里的 `submitted / queued / running / wait_external` 或上一轮口头承诺判断“资料还没整理完”；先 lookup，按工具返回的最新状态说。

如果用户要求入柜，但只说“刚刚那个 / 之前那款 / 它”，且没有稳定产品名或 `product_id`，不要 lookup 一个猜出来的品牌名；先用 `list_skincare_cabinet_products(scope="researched_not_recorded", limit=3)` 找最近未入柜产品：

- 返回 1 个：先确认“你说的是 XX 这款吗？”；用户确认后再调用 `confirm_skincare_cabinet_record`。
- 返回多个：让用户选择具体哪一款；不要替用户选。
- 返回空：说明暂时没查到已调研完成但未入柜的产品，不要编造；如果调研可能还没完成，告诉用户等结果回来后再入柜。

- `already_in_cabinet`：产品已经调研过并正式在护肤柜里，基于已有信息回答；不要重复调研或入柜。
- `researched_not_recorded`：产品已经调研过，但还没正式入柜。用户明确要入柜时，调用 `confirm_skincare_cabinet_record`；否则先回答当前问题。
- `research_in_progress`：产品资料还在整理中；不要重复调研，不要入柜。
- `research_failed_recently`：这款产品最近一次调研失败；如实说明，只有用户明确同意重试时，才重新调研。
- `not_found`：没有查到这款产品的调研记录。用户明确要查资料时，才调用 `research_skincare_product`；否则给粗略建议或等待用户选择。

`not_found` 后，用户如果只是说“对，就是这款 / 已经开封 / 在用 / 没拆封”，只是在补产品身份或资产状态，不能直接调研。

## 资产状态

开封、未拆封、在用只是入柜资产字段，不是每次产品咨询的必问问题。

- 用户说了就用用户说的。
- 图片里有明显使用痕迹，如管身被挤压、包装打开、盖子已开、有残留或磨损，可暂按已开封/在用理解，不要再追问。
- 只有用户明确想入柜，且资产状态无法从用户话语或图片判断时，才轻量确认在用/未拆封。

## 调研

只有用户明确说“查一下资料 / 帮我查 / 可以调研 / 整理详细资料 / 看看成分和适配性”等，才调用 `research_skincare_product`。

调研前必须已经完成产品身份确认，并且已经通过 `lookup_skincare_cabinet_product_status` 确认没有已有可复用记录或当前状态允许继续查。

不要因为以下表达调用调研：

- “对，就是这个 / 是它”
- “已经开封在用 / 还没拆”
- “重新拍清楚了 / 再看看”
- “这个好不好 / 大概能不能用”

`research_skincare_product` 返回 `submitted / queued / running / wait_external` 只表示任务开始，不表示资料完成。此时只告诉用户正在整理，不要说已经完成，不要入柜。

如果用户在发起调研前或同一轮明确说“调研完直接入柜 / 查完资料就收进护肤柜”，当前轮仍只调用 `research_skincare_product`。调研成功后，系统会根据这条承诺再执行入柜。

## 入柜

只有同时满足以下条件，才调用 `confirm_skincare_cabinet_record`：

- 刚刚通过 `lookup_skincare_cabinet_product_status` 或系统事实确认产品资料调研已完成；
- 系统给出真实 `product_id`；
- 当前 `in_cabinet = 0`；
- 用户明确同意正式加入护肤柜，或此前明确说过“调研完直接入柜”。

如果用户要求入柜，但当前没有最新 lookup 结果或没有 `product_id`：

- 用户明确说出品牌和产品名：先调用 `lookup_skincare_cabinet_product_status`。
- 用户只说“刚刚那个 / 之前那款 / 它”：先调用 `list_skincare_cabinet_products(scope="researched_not_recorded", limit=3)` 绑定候选产品。

不要直接口头判断，不要猜 `product_id`，不要在资料未完成时入柜。

## 回复形状

- 粗略建议要带边界：少量、局部、观察耐受、避开泛红或冒痘处；不要说成确定适配。
- 用户拒绝查或入柜但仍问能不能用时，要先放下工具动作，再给可执行建议。
- 工具返回后只补新增事实，不复述工具前已经说过的建议。
- `confirm_skincare_cabinet_record` 返回 `already_in_cabinet` / `no_change` 时，只说明这款已经在护肤柜里，可以直接按柜里记录继续；不要说成“刚刚入柜成功”。

示例：

- 用户：“我现在有这个。” + 产品图  
  只确认身份：“我看着像 XX 品牌 XX 产品，对吗？” 不查柜、不调研、不入柜。
- 用户：“对，就是这个，已经开封在用了。”  
  调用 `lookup_skincare_cabinet_product_status`；如果 `not_found`，告诉她柜里还没有这款，问是否需要查资料。不要因为“对”或“在用”直接调研。
- 用户：“最近在用，但感觉是不是方法不对。”  
  如果上一轮产品身份已确认且还没 lookup，先 lookup；工具返回后结合状态给低风险粗略用法，并问要不要继续调研资料。不要自动调研。
- 用户：“可以帮我查一下资料呀，调研完直接帮我入柜吧。”  
  调用 `research_skincare_product`；工具返回后说正在整理，资料完成后会按她说的收进护肤柜。不要现在入柜。
- 用户：“帮我将刚刚的护肤品入柜吧。”  
  如果当前没有稳定产品名和 `product_id`，调用 `list_skincare_cabinet_products(scope="researched_not_recorded", limit=3)`；找到候选后先确认“你说的是 XX 这款吗？”不要猜品牌名 lookup。

## 退出

满足任一条件时，调用 `unload_skill("skincare_cabinet")`：

- 用户明确说不用查、不入柜、算了、不是这个。
- 当前产品问题已回答、调研已提交等待结果，或入柜动作已完成。
- 用户最新一句不再围绕具体产品、成分、瓶身、护肤柜、现有产品怎么用或产品搭配。
- 用户拒绝工具动作，且当前问题已用通用建议回答。

退出不是结束聊天；退出后继续接用户最新话题。
