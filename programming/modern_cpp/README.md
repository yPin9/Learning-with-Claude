# modern_cpp

給「有 C 基礎、想學 C++20」的人的速成課。目標：**讀得懂現代 C++ code，並能寫出符合現代風格的 C++**。

不做元編程深入（TMP、SFINAE 哲學、custom allocator 設計），專注在日常開發會用到的現代特性。

## 前提
- 熟悉 C（pointer、struct、malloc/free、header、make）
- 熟悉命令列編譯
- **不必**有舊 C++ (pre-C++11) 經驗——舊 C++ 和現代 C++ 差異很大，沒學過反而沒包袱

## 目標編譯器
**gcc 13+**（C++20 支援最完整）。每章程式碼都用以下指令測過：
```bash
g++ -std=c++20 -Wall -Wextra -O2 file.cpp -o file
```

少數需要 `-fcoroutines`（coroutines）或 `-fmodules-ts`（modules）會個別註明。

## 章節地圖

### Part 1 — 從 C 的視角看 C++
- [Ch0: 環境設定](00-environment-setup.md)
- [Ch1: C → C++ 的心態與陷阱](01-c-to-cpp-mindset.md)
- [Ch2: References 與 const 正確性](02-references-and-const.md)

### Part 2 — 物件生命週期（C++ 的心臟）
- [Ch3: RAII](03-raii.md)
- [Ch4: 建構/解構/複製/移動 (Rule of 0/3/5)](04-ctor-dtor-copy-move.md)
- [Ch5: Move semantics 與 rvalue references](05-move-semantics.md)
- [Ch6: Smart pointers](06-smart-pointers.md)

### Part 3 — 泛型與函式物件
- [Ch7: Templates 入門](07-templates-basics.md)
- [Ch8: auto、decltype、結構化綁定](08-auto-decltype-bindings.md)
- [Ch9: Lambdas](09-lambdas.md)

### Part 4 — STL
- [Ch10: STL containers](10-stl-containers.md)
- [Ch11: STL algorithms 與 iterators](11-stl-algorithms.md)
- [Ch12: constexpr / consteval / if constexpr](12-constexpr.md)

### Part 5 — C++20 新特性
- [Ch13: Concepts](13-concepts.md)
- [Ch14: Ranges 與 views](14-ranges.md)
- [Ch15: Modules](15-modules.md)
- [Ch16: Coroutines](16-coroutines.md)
- [Ch17: std::format](17-format.md)

### Part 6 — 現代 C++ 工程實務
- [Ch18: std::optional 與錯誤處理](18-optional-error-handling.md)
- [Ch19: std::span 與 std::string_view](19-span-string-view.md)
- [Ch20: Exceptions vs error codes](20-exceptions-vs-error-codes.md)
- [Ch21: 並行（thread/atomic/jthread）](21-concurrency.md)
- [Ch22: 除錯與品質工具](22-debugging-tools.md)

### 練習
- [Practice A: 把 C 程式重寫成 RAII 風格](practice-a-c-to-raii.md)
- [Practice B: 自己實作 unique_ptr](practice-b-implement-unique-ptr.md)
- [Practice C: ranges + concepts 資料 pipeline](practice-c-ranges-pipeline.md)
- [Final Project: Coroutine-based TCP echo server](final-project-tcp-echo-coroutine.md)

## 建議學習順序

**快速路徑（讀得懂 code 就好）**：Ch0→Ch1→Ch2→Ch3→Ch6→Ch8→Ch9→Ch10→Ch11→Ch13→Ch14→Ch17

**完整路徑**：按順序讀。Ch4/Ch5 是整個 C++ 的核心，不要跳過。Coroutines (Ch16) 可以最後讀。

## 風格與取捨

- 每章開頭先說「C 程式員可能會怎麼想」，再點出 C++ 的做法差在哪
- 範例優先簡短可編譯，次要追求完整
- 不避諱「這是舊 C++ 寫法，別用」——現代 C++ 很多舊寫法該淘汰
- 遇到 gcc 實作坑（modules、coroutines 標準庫支援）會直說
