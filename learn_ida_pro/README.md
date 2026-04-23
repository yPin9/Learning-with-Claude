# IDA Pro 學習筆記：從只會 F5 到寫自己的自動化腳本

> 給用過 IDA 但只敢按 F5 的逆向初學者，目標是把鍵盤練熟，再用 IDAPython 把重複動作全自動化。

這系列用 **IDA Pro 9.x** 為主，前半學情境快捷鍵（靜態分析、Decompiler、動態 debug、struct 還原、四大題材速查），後半寫 IDAPython 腳本把手動動作包成自動化，最後讓腳本掛到 hotkey 一鍵觸發。

## 為什麼學這個？

- **快捷鍵不是炫技，是生存**：逆向一個中等大小的 binary，滑鼠點一萬下手腕就廢了。鍵盤動線熟，分析速度差十倍。
- **IDAPython 是槓桿**：同樣的 pattern 改名、同樣的 struct 還原、同樣的字串解混淆 — 你手動做三次就該寫 script 了。
- **讀別人的 IDAPython 需要會寫**：GitHub 上一堆 malware 分析、CTF writeup 的 script，看懂它們等於拿到一堆可以改的工具。

## 課程地圖

### Part 1 — 介面與快捷鍵（分情境 cheatsheet）
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 IDA 世界觀與資料庫](./01-ida-worldview.md)
- [Ch 2 靜態分析情境快捷鍵](./02-static-analysis-hotkeys.md)
- [Ch 3 Decompiler (F5) 情境快捷鍵](./03-decompiler-hotkeys.md)
- [Ch 4 動態 debug 情境快捷鍵](./04-debugger-hotkeys.md)
- [Ch 5 Struct / enum / type 還原情境](./05-struct-type-recovery.md)
- [Ch 6 四大題材速查：CTF / malware / vuln / firmware](./06-scenario-cheatsheet.md)
- [練習 A：純鍵盤解一個 crackme](./practice-a-keyboard-only-crackme.md)

### Part 2 — IDAPython 腳本自動化
- [Ch 7 IDAPython 入門](./07-idapython-intro.md)
- [Ch 8 核心 API 地圖](./08-idapython-api-map.md)
- [Ch 9 批次改名與自動註解](./09-batch-rename-comment.md)
- [Ch 10 Xref 與 call graph 分析](./10-xref-callgraph.md)
- [Ch 11 Struct 自動推斷腳本](./11-struct-auto-recovery.md)
- [Ch 12 字串 / 常數解混淆](./12-string-const-deobfuscation.md)
- [練習 B：stripped binary 自動 annotate](./practice-b-auto-annotate.md)

### Part 3 — Decompiler API 與 UI 整合
- [Ch 13 Hex-Rays API 入門（ctree / vdui）](./13-hexrays-api.md)
- [Ch 14 把 script 包成一鍵觸發（action + hotkey）](./14-actions-and-hotkeys.md)
- [Final Project：malware unpacker helper](./final-project-unpacker-helper.md)

## 學習方式建議

1. **每章快捷鍵親手按一次**：別只讀表格，打開一個你以前看過的 binary，照每個快捷鍵按過去，肌肉記憶才會長出來。
2. **Part 1 先忍住不寫 script**：自動化之前要先會手動 — 不然你寫的 script 會把錯誤流程自動化十倍。
3. **Part 2 每章的範例都自己跑**：`ida_*` API 的參數順序很容易記錯，靠跑過一次 console 才會內化。
4. **看 IDA SDK 附的 `idasdk/plugins/` 原始碼**：官方的 C++ plugin 範例是最可靠的 API 字典，IDAPython 函式名幾乎一對一對應。

## 參考資料

- 《The IDA Pro Book, 2nd Edition》— Chris Eagle, No Starch（舊但扎實，工作流核心沒過時）
- 《Practical Binary Analysis》— Dennis Andriesse, No Starch（逆向心法補充）
- IDA 官方 Python API：<https://hex-rays.com/products/ida/support/idapython_docs/>
- Hex-Rays blog（官方寫的 decompiler plugin 範例）：<https://hex-rays.com/blog/>
- `idasdk` 隨安裝附的 `plugins/` 目錄：比線上文件更完整
