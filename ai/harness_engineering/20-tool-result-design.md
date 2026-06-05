# Ch 20 — Tool 結果設計

> **目標**：學會設計一個工具**回給模型什麼、怎麼回、錯誤怎麼回**。讀完你能說出 `tool_result` 區塊的完整結構（content 可以是字串也可以是多個 block、`is_error` 的真正用途）、為什麼「會讓模型自己修正的錯誤訊息」是工具設計裡最高槓桿的一塊、怎麼回傳「下一步提示」與「可串接的 ID」，並能分辨「工具執行失敗」和「執行成功但結果是空的」這兩種完全不同的狀況。

> **環境**：Python 3.11、`anthropic` Python SDK（最新版）。本章延續 [Ch 5 — Tool calling 協議](./05-tool-calling-protocol.md) 的 `tool_use` / `tool_result` 一來一回，以及 [Ch 4](./04-the-agent-loop.md) 的 `run_tool_uses`。

## 為什麼需要這個？先和 Ch 16 劃清界線

你可能會問：[Ch 16](./16-tool-result-pruning.md) 不是已經講過工具結果了嗎？是，但兩章看的是**不同的軸**：

- **Ch 16 站在「context 管理」的角度**，問的是「回**多少**」——怎麼裁剪、截斷、用 handle，讓工具結果別撐爆 context。那是**省 token** 的事。
- **Ch 20 站在「工具作者」的角度**，問的是「回**什麼、怎麼回、錯誤怎麼設計**」——讓模型**讀懂結果、據此做對下一步、出錯時能自己修**。那是**讓 agent 做對事** 的事。

一個工具結果可以又精簡（Ch 16 做到了）又難用（模型看不懂、出錯了不知道怎麼改）。本章補上 Ch 16 沒展開的那一半：結果的**語意設計**。這也兌現 [Ch 19](./19-tool-descriptions-as-prompt.md) 結尾埋的伏筆——「錯誤訊息是事後的 description」。

## 先建立直覺：tool_result 是「回給模型的對話」，不是「函式回傳值」

寫一般程式時，函式回傳值是給**另一段程式**讀的——型別固定、呼叫方知道怎麼解析。但 `tool_result` 是回給一個**會讀自然語言、會推理、但也會誤讀**的模型。它更像你回給一個同事的訊息，而不是 `return` 一個 struct。

這個差別帶出三件一般函式不太管、但 tool_result 必須管的事：

```
   一般函式回傳              vs        tool_result（回給模型）
   ┌────────────────┐               ┌──────────────────────────┐
   │ return data     │               │ 結果要「自我解釋」          │
   │ raise Exception │               │ 錯誤要「可行動、能自我修正」  │
   │ 呼叫方負責解讀    │               │ 最好附「下一步能做什麼」      │
   └────────────────┘               └──────────────────────────┘
```

整章心法：**設計 tool_result 時，想的不是「我的函式該 return 什麼」，而是「模型讀到這段，下一步會（該）做什麼」。** 結果要為「模型的下一個決策」服務。

## 一、tool_result 的解剖：它能裝什麼

先把協議層搞清楚。模型發出 `tool_use` 後，你回一則 **user** 訊息，裡面放 `tool_result` 區塊（Ch 5 看過配對規則）。這個區塊長這樣：

```python
{
    "type": "tool_result",
    "tool_use_id": block.id,    # ① 必須對應發起的那個 tool_use 的 id
    "content": "...",           # ② 回給模型的內容（見下）
    "is_error": False,          # ③ 選填：標記這是不是「執行錯誤」
}
```

三個欄位各有講究：

- **`tool_use_id`**：把結果和當初的呼叫配對。Ch 5 強調過：`tool_result` 必須在 user 訊息裡、緊接對應的 `tool_use`、且排在該訊息任何 text 之前。配對錯了 API 直接報錯。**模型一輪平行發出多個 `tool_use` 時**，所有對應的 `tool_result` 要放進**同一則** user 訊息的 content 陣列裡（全部排在 text 前），不要拆成多則 user 訊息——這也是為什麼第五節的 `run_tool_uses` 是把一輪所有結果收集成一則 user 訊息回傳。
- **`content`**：這是重點。它**不一定是字串**——可以是字串，也可以是一個 **content block 陣列**，裡面放 `text`、`document`、甚至 **`image`** block（工具回傳截圖、圖表時）。也就是說「工具回傳一張圖給模型看」是協議直接支援的：

  ```python
  # 工具回傳文字 + 一張圖（例如一個「畫圖表」或「截圖」工具）
  {
      "type": "tool_result",
      "tool_use_id": block.id,
      "content": [
          {"type": "text", "text": "已產生本季營收圖表："},
          {"type": "image", "source": {
              "type": "base64", "media_type": "image/png", "data": "<base64>"}},
      ],
  }
  ```
  > 多模態結果的細節（圖片佔多少 token、怎麼控大小）留到 [Ch 33 — 多模態輸入](./33-multimodal-input.md)。這裡只要知道：**tool_result 不是只能回字串**。另外 `content` 在 schema 裡其實是**選填**的，但實務上**永遠建議回點東西**——即使是空結果，也回一句「成功執行、結果為空」，別回一個空白讓模型猜（呼應第三節）。
- **`is_error`**：設 `True` 時，告訴模型「這次工具執行**失敗**了」。注意它的語意——見第三節，這是全章最容易用錯的地方。

> 我們前面章節（Ch 4、Ch 16）的 `run_tool_uses` 為了簡單，都用 `str(func(...))` 把結果壓成字串。那是**文字工具的簡化版**。一旦你的工具要回 image/document block，就不能再無腦 `str()`——得讓工具直接回一個 content block 陣列。本章的設計原則對兩種都適用，但實作上要記得這個分野。

## 二、結果要「自我解釋」：讓模型一眼知道發生了什麼

Ch 16 教過「精簡」，但精簡不等於好懂。一個精簡到只剩裸數字的結果，模型可能誤讀。好的結果要**自我解釋**：模型不必猜「這個值是什麼、這次到底成功沒、有幾筆」。

```python
# ❌ 精簡但不自我解釋：模型要猜 3 是什麼、true 是什麼意思
return "3, true, 1842"

# ✅ 一樣精簡，但每個值都有標籤、狀態明確
return (
    "搜尋成功，找到 3 筆訂單（共 1842 筆中符合條件的）：\n"
    "#1234 已出貨\n#1235 處理中\n#1238 已取消"
)
```

自我解釋的幾個要點：

- **講狀態**：這次呼叫是成功、部分成功、還是沒結果？別讓模型從資料形狀去反推。
- **給標籤**：每個值是什麼（`#1234 已出貨` 勝過 `1234 1`）。Ch 16 提過，這裡再強調：標籤是給模型的「欄位名」。
- **講脈絡**：「3 筆（共 1842 筆符合）」比單純「3 筆」資訊多——模型知道是不是該縮小查詢。
- **格式一致**：同一個工具每次回傳用同樣結構，模型才學得會穩定解讀它（多輪任務尤其重要）。

## 三、錯誤設計：全章最高槓桿的一節

工具會失敗——檔案不存在、參數非法、API 超時、權限不足。**怎麼把失敗回給模型，直接決定 agent 是「自己修好繼續跑」還是「卡死或瞎掰」。** 這是 tool_result 設計裡 CP 值最高的部分。

### `is_error` 到底該什麼時候設

先澄清一個高頻誤解。`is_error: True` 代表的是「**工具執行本身出錯了**」——例外、崩潰、無法完成。它**不是**用來表達「執行成功，但結果是負面的」。

```python
# 情境 A：搜尋執行成功，只是沒找到東西 → 這「不是」錯誤
#   is_error 應為 False，content 講清楚「成功執行、結果為空」
{"type": "tool_result", "tool_use_id": id,
 "content": "搜尋成功，但沒有符合『xyz』的訂單。可試試放寬關鍵字。",
 "is_error": False}

# 情境 B：搜尋這個動作本身炸了（資料庫連不上） → 這才是錯誤
{"type": "tool_result", "tool_use_id": id,
 "content": "搜尋失敗：訂單資料庫連線逾時，請稍後再試或回報問題。",
 "is_error": True}
```

為什麼要分清楚？因為模型對這兩者的反應**該不一樣**：情境 A（沒找到）模型該調整查詢再試或告訴使用者「查無此項」；情境 B（系統炸了）模型該重試或停下來報告，**不該**把「資料庫逾時」當成「沒有這筆訂單」去回答使用者。把空結果誤標成 `is_error`，會誤導模型；把系統錯誤偽裝成正常空結果，更糟——模型會拿著錯誤的前提繼續跑。

### 好錯誤訊息的三個要素

對比一個爛錯誤和一個好錯誤：

```python
# ❌ 爛：opaque、模型無從修起
return "Error: code 2"
# ❌ 也爛：整段 traceback 倒給模型，訊號被雜訊淹沒
return traceback.format_exc()

# ✅ 好：說清楚「錯在哪、為什麼、怎麼改」，而且明確標成執行錯誤
return ToolResult(
    content=(
        "讀檔失敗：找不到 '/data/report.txt'。\n"
        "目前 /data/ 下存在的檔案：report_2026.txt, summary.txt。\n"
        "請確認檔名後重試。"
    ),
    is_error=True,    # ← 這段文字是 content；外層一定要一起標 is_error=True
)
```

> 注意上面那段話只是 `tool_result` 的 **`content`**——錯誤訊息寫得再好，**漏掉 `is_error=True` 等於沒告訴模型「這是失敗」**。content 講「怎麼修」，`is_error` 講「這是個錯」，兩個都要。（用第五節的 `ToolResult` 信封就能讓工具自己把兩者一起帶出來。）

一個能讓模型自我修正的錯誤訊息，要有三件事：

1. **錯在哪（what）**：哪個操作、哪個參數出問題。「`date` 參數格式不對」勝過「invalid input」。
2. **為什麼（why）**：「需要 YYYY-MM-DD，你給的是 'June 5'」——點出模型實際給的值和期望的差距，它才知道怎麼改。
3. **怎麼改（how / next step）**：可能的話，直接給可行動的方向——「目前存在的檔案是…」「請改用 ISO 格式」「可放寬關鍵字重試」。

這三件事讓錯誤訊息變成 [Ch 19](./19-tool-descriptions-as-prompt.md) 說的「**事後的 description**」——模型用錯了，你即時補一段教學，它下一輪就能修對。別只丟 error code（模型不知道 2 是什麼），也別倒整段 traceback（關鍵那行被淹沒，還浪費 token）。

> **安全提醒**：錯誤訊息會進 context、可能被模型轉述給使用者。別在錯誤裡洩漏內部路徑全貌、連線字串、secret、堆疊裡的敏感資訊。「可行動」和「過度暴露」之間要拿捏——這條到 [Ch 36 — prompt injection 與安全](./36-prompt-injection-security.md) 會更系統地談。

## 四、結果要為「下一步」服務：affordance 與可串接

最好的工具結果不只報告「發生了什麼」，還暗示「接下來能做什麼」。這在互動設計裡叫 **affordance**（可供性）——結果本身提示了下一步的可能動作。

**回傳「下一步提示」**：

```python
# 讀了一份大文件的工具，回摘要時順帶告訴模型怎麼拿細節
return (
    "已讀取 report.pdf（32 頁）。摘要：本季營收成長 12%，主要來自亞太區。\n"
    "需要特定章節細節，可用 read_section(doc_id='doc_a1b2', page=N)。"  # ← 下一步提示
)
```

這正是 Ch 16 handle 模式的另一面：handle 解決「大資料不進 context」，而**在結果裡明講「怎麼用這個 handle 拿更多」**，是讓模型真的會去用它的關鍵。

**回傳可串接的 ID**：當一個工具的輸出常常是下一個工具的輸入，就把那個「鍵」明確回傳：

```python
# create_order 回傳新訂單 id，讓模型能接著呼叫 add_item / confirm_order
return "已建立訂單 #5012（狀態：草稿）。可用 add_item(order_id=5012, ...) 加入商品。"
```

模型要串接多步操作（建單→加品項→結帳）時，每一步的結果都明確帶出下一步需要的 ID，它才接得起來。如果 `create_order` 只回「成功」，模型就不知道要拿哪個 ID 去 `add_item`。

## 五、把這些接進 harness

回顧 Ch 16 的 `run_tool_uses`，它用 `str(func(...))` + 固定 `is_error: False` 把結果收進統一關卡。但那是**文字工具的簡化版**——本章兩個重點（工具可回 image block、工具自己決定 `is_error`）它都做不到。所以這裡升級：讓工具回一個**回傳信封（envelope）**，由它自己帶 `content` 和 `is_error`，harness 只負責「裁剪、配對、保底」。

```python
from dataclasses import dataclass

@dataclass
class ToolResult:
    content: object          # str，或 content block 陣列（可含 image，見第一節）
    is_error: bool = False   # 工具自己決定這次算不算「執行失敗」

def run_tool_uses(content_blocks):
    results = []
    for block in content_blocks:
        if block.type != "tool_use":
            continue
        func = TOOL_FUNCTIONS.get(block.name)
        if func is None:
            # 連工具都不存在：執行層錯誤，明確標 is_error 並講清楚
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": f"未知工具 '{block.name}'。可用工具：{list(TOOL_FUNCTIONS)}",
                            "is_error": True})
            continue
        try:
            out = func(**block.input)
            # 工具可回 ToolResult（自帶 is_error / block 陣列），也可回裸字串（視為成功）
            result = out if isinstance(out, ToolResult) else ToolResult(content=str(out))
        except Exception as e:
            # 保底：工具沒自己接住的例外。給可行動訊息，不倒整段 traceback
            result = ToolResult(
                content=f"工具 '{block.name}' 執行失敗：{e}（請檢查參數或稍後重試）",
                is_error=True)
        # clip（Ch 16）只作用在字串；block 陣列（含 image）不可被字串化，原樣放行
        content = clip(result.content) if isinstance(result.content, str) else result.content
        results.append({"type": "tool_result", "tool_use_id": block.id,
                        "content": content, "is_error": result.is_error})
    return {"role": "user", "content": results}
```

分層原則（和 Ch 16 的裁剪分層同構）：

- **harness 層**做「機制與保底」——配對 `tool_use_id`、對字串型結果裁剪、接住未知工具與漏網例外（標 `is_error`、不洩漏 traceback）。這是安全網。
- **個別工具**做「語意」——自己決定回什麼 `content`（字串或 block 陣列）、自己用 try/except 接住可預期的失敗並回 `ToolResult(content="…what+why+how…", is_error=True)`。因為只有工具自己知道「結果該長怎樣、怎麼改才對」。

別只靠 harness 的保底——它只能說「執行失敗：<例外字串>」，給不出「目前存在的檔案是…」這種針對性指引，也回不了 image。

## 對比與取捨

| 設計選擇 | 偏 A | 偏 B | 怎麼選 |
|---|---|---|---|
| 結果格式 | 裸值（`"3, true"`） | 自我解釋（帶標籤+狀態） | 永遠自我解釋；精簡不等於裸值 |
| 空結果怎麼標 | `is_error: True` | **`is_error: False` + 講清楚「成功但為空」** | 沒找到≠執行錯誤，標 False |
| 錯誤內容 | error code / 整段 traceback | **what + why + how** | 給可自我修正的訊息 |
| 下一步 | 只報「成功/失敗」 | 附 affordance（怎麼拿更多、ID 給好） | 多步任務一定要帶下一步資訊 |
| 錯誤處理放哪 | 全靠 harness 保底 | 工具精緻 + harness 保底 | 兩層：可預期失敗工具自己接 |

## 踩雷集錦

1. **把「空結果/負面結果」標成 `is_error`**：搜尋沒找到、清單為空，這些是**成功執行**，`is_error` 該是 `False`，content 講清楚「成功但為空」。標成錯誤會誤導模型把「查無此項」當成「系統壞了」。
2. **錯誤只回 error code 或整段 traceback**：`"Error 2"` 模型無從修起；整段 traceback 則用雜訊淹沒訊號又燒 token。要給「錯在哪、為什麼、怎麼改」。
3. **結果是裸值、不自我解釋**：`"3, true, 1842"` 逼模型猜每個值的意義，容易誤讀。每個值給標籤、講清楚狀態。
4. **多步任務不回可串接的 ID/handle**：`create_order` 只回「成功」，模型不知道拿哪個 ID 去 `add_item`，整條鏈斷掉。下一步要用的鍵，這一步就要明確回傳。
5. **同一工具每次回傳格式飄移**：這次條列、下次 JSON、再下次純文字，模型學不會穩定解讀。固定一個結構。
6. **錯誤訊息洩漏敏感資訊**：把完整內部路徑、連線字串、secret 塞進錯誤訊息，會進 context 也可能被轉述。可行動但不過度暴露。
7. **以為 tool_result 只能回字串**：它的 `content` 可以是 block 陣列、可含 image。要回圖時別硬塞 base64 進文字，用 image block。

## 進階：再往深一層

- **部分成功（partial success）怎麼回**：批次操作（刪 10 個檔，成功 7 個失敗 3 個）最棘手——整體標 `is_error: True` 會讓模型以為全失敗，標 `False` 又可能掩蓋那 3 個。這裡沒有協議規則，取決於**工具的契約**：如果契約是「逐項回報狀態」，用 `is_error: False` + content 分列「成功 7 筆：…；失敗 3 筆：…（各自原因）」，把判斷權交回模型；如果契約是交易式的「全部成功才算完成」，那部分失敗就該視為執行失敗，設 `is_error: True` 並在 content 列出已成功/已回滾/未完成項目。關鍵是 content 一定要分列清楚——整體成敗是個光譜，別讓那一個布林把細節吃掉。
- **結果的「時效」與半衰期**：Ch 12/Ch 16 講過 tool_result 半衰期短。設計結果時可以順手想：這個結果幾輪後還有用嗎？像「目前時間」這種一次性的，模型用完就過期，可考慮在後續裁剪時優先壓縮它（Ch 16 的舊結果二次裁剪）。
- **結果設計與 eval 的關係**：錯誤訊息好不好、affordance 夠不夠，是**可以量化**的。準備一組「會觸發工具失敗」的任務，比較不同錯誤訊息設計下，agent 平均要幾輪才修對（甚至修不修得對）。好的錯誤訊息能把「3 輪試錯」壓到「1 輪修正」。這是 [Ch 34 — Eval](./34-eval.md) 的直接應用。
- **結果格式與 prompt injection**：當工具結果來自外部（網頁、使用者上傳、第三方 API），結果內容裡可能藏著「假裝是指令」的文字。設計結果格式時要考慮怎麼讓模型分清「這是資料、不是給我的指令」——例如明確標註資料邊界。這條留到 [Ch 36](./36-prompt-injection-security.md)。

## 動手練習

1. 拿練習 A 的 `read_text_file`，把它在「檔案不存在」時的回傳，從 `"Error"` 改成第三節那種「what + why + how」（含「目前目錄下有哪些檔」）。跑一個故意讀錯檔名的任務，看模型會不會自己改用對的檔名。
2. 寫一個 `search_orders`，刻意製造「沒找到」的情境。先用 `is_error: True` 跑一次、再用 `is_error: False` + 「成功但為空」跑一次，對比模型的下一步反應差異。
3. 設計一條三步工具鏈（`create_order` → `add_item` → `confirm_order`），讓每一步的結果都帶出下一步需要的 ID。故意把 `create_order` 改成只回「成功」（不回 ID），看鏈在哪裡斷掉。
4. 寫一個會「部分成功」的批次工具（刪一組檔、有些不存在），設計它的 content 怎麼分列成功與失敗，讓模型能正確地只對失敗項採取行動。

## 本章重點整理

- Ch 16 管「回多少」（省 token），本章管「回什麼、怎麼回、錯誤怎麼設計」（讓 agent 做對事）——同一份結果可以又精簡又難用。
- `tool_result` 的 `content` 不只是字串，可以是含 image 的 block 陣列；`tool_use_id` 必須配對、排在 text 前。
- 結果要**自我解釋**：講狀態、給標籤、講脈絡、格式一致。
- 錯誤設計是最高槓桿：`is_error` 只標「執行失敗」（空結果不是錯誤）；好錯誤要有 what + why + how，讓模型自我修正。
- 結果要為下一步服務：附 affordance（怎麼拿更多）、回可串接的 ID。
- 分層：工具自己接可預期失敗回精緻錯誤，harness 保底接漏網例外、不洩漏 traceback。

## 自我檢核

- [ ] 我能說清楚 Ch 16 和本章看工具結果的不同軸（多少 vs 什麼/怎麼/錯誤）
- [ ] 不看本章，我能說出 `tool_result` 三個欄位各自的作用，以及 content 可以裝什麼
- [ ] 面試被問「`is_error` 什麼時候設」，我能用「空結果 vs 系統錯誤」的對比回答
- [ ] 我能把一個 `"Error: code 2"` 改寫成讓模型能自我修正的錯誤訊息，並說出三要素
- [ ] 我能解釋為什麼多步工具鏈要在每步結果回傳可串接的 ID

## 延伸閱讀

### 部落格 / 技術文章

- **[Writing effective tools for AI agents（Anthropic）](https://www.anthropic.com/engineering/writing-tools-for-agents)** — Anthropic Engineering
  - **這篇說什麼**：從 agent 角度設計工具，**特別有一段專門談工具回傳什麼、錯誤訊息怎麼寫才能讓 agent 自我修正、回傳要 token 高效**——和本章幾乎逐點對應。
  - **讀哪裡**：談 tool response / error handling / returning meaningful context 的段落。
  - **為什麼值得讀**：本章「錯誤是事後的 description」「結果要為下一步服務」的權威背書與更多實例。
  - **前提知識**：Ch 5、Ch 16、Ch 19 看完即可。

### 官方文件

- **[Anthropic — Handle tool use results](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)**
  - **讀哪裡**：`tool_result` 區塊的結構、`is_error` 的用法、content 可放 text/image block 的說明。
  - **能學到什麼**：本章第一節協議細節的權威來源——尤其「content 不只是字串」「is_error 表示執行錯誤」這兩點。
  - **前提知識**：Ch 5 看完即可。

- **[Anthropic — Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)**
  - **讀哪裡**：tool best practices 裡關於回傳格式與錯誤處理的建議。
  - **能學到什麼**：把工具的「輸入設計」（Ch 18/19）和「輸出設計」（本章）對照著看，補齊一個工具的全貌。
  - **前提知識**：Ch 18、Ch 19 看完即可。

下一章我們把工具設計的原則，用在一組最常見、也最危險的工具上：檔案系統操作。讀、寫、列目錄、改檔——你會看到本章的錯誤設計、affordance、可串接 ID，全部派上用場，還會碰到路徑安全這個新問題。

→ [Ch 21 檔案系統工具](./21-filesystem-tools.md)
