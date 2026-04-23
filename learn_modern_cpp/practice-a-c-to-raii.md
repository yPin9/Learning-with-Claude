# Practice A: 把 C 程式重寫成 RAII 風格

**目標**：把一段典型 C 風格的 code 改寫成現代 C++ RAII。
**用到**：Ch1-Ch6（C→C++ 心態、references、const、RAII、ctor/dtor、smart pointers）

## 題目

你有一個 C 風格的 HTTP log 解析器（簡化版）：

```c
// log_parser.c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    char* ip;
    int status;
    char* url;
} LogEntry;

typedef struct {
    LogEntry* entries;
    size_t count;
    size_t capacity;
} LogEntries;

LogEntries* log_entries_create(void) {
    LogEntries* l = malloc(sizeof(LogEntries));
    if (!l) return NULL;
    l->capacity = 16;
    l->count = 0;
    l->entries = malloc(sizeof(LogEntry) * l->capacity);
    if (!l->entries) { free(l); return NULL; }
    return l;
}

int log_entries_push(LogEntries* l, const char* ip, int status, const char* url) {
    if (l->count == l->capacity) {
        size_t new_cap = l->capacity * 2;
        LogEntry* new_buf = realloc(l->entries, sizeof(LogEntry) * new_cap);
        if (!new_buf) return -1;
        l->entries = new_buf;
        l->capacity = new_cap;
    }
    LogEntry* e = &l->entries[l->count];
    e->ip = strdup(ip);
    e->status = status;
    e->url = strdup(url);
    if (!e->ip || !e->url) {
        free(e->ip); free(e->url);
        return -1;
    }
    l->count++;
    return 0;
}

void log_entries_destroy(LogEntries* l) {
    if (!l) return;
    for (size_t i = 0; i < l->count; i++) {
        free(l->entries[i].ip);
        free(l->entries[i].url);
    }
    free(l->entries);
    free(l);
}

LogEntries* parse_file(const char* path) {
    FILE* f = fopen(path, "r");
    if (!f) return NULL;

    LogEntries* entries = log_entries_create();
    if (!entries) { fclose(f); return NULL; }

    char line[1024];
    while (fgets(line, sizeof(line), f)) {
        char ip[64]; int status; char url[512];
        if (sscanf(line, "%63s %d %511s", ip, &status, url) == 3) {
            if (log_entries_push(entries, ip, status, url) != 0) {
                log_entries_destroy(entries);
                fclose(f);
                return NULL;
            }
        }
    }

    fclose(f);
    return entries;
}

int main(int argc, char* argv[]) {
    if (argc != 2) { fprintf(stderr, "usage: %s <file>\n", argv[0]); return 1; }
    LogEntries* entries = parse_file(argv[1]);
    if (!entries) { fprintf(stderr, "failed\n"); return 1; }

    for (size_t i = 0; i < entries->count; i++) {
        printf("%s %d %s\n",
            entries->entries[i].ip,
            entries->entries[i].status,
            entries->entries[i].url);
    }

    log_entries_destroy(entries);
    return 0;
}
```

## 要求

改寫成 C++20 RAII 風格，達到：

1. **零 `malloc` / `free` / `new` / `delete`**
2. **零 `strdup`**（用 `std::string`）
3. **無 `NULL` check 地獄**（用 exception 或 `std::expected`）
4. **所有錯誤 path 自動清理**（無 leak、無 double free）
5. **程式碼行數至少減半**

## 進階要求
- 用 `std::string_view` 做 parser 傳參（零分配 substring）
- 用 `std::ranges` 過濾 / 排序輸出
- 用 `std::format` 取代 `printf`

## 骨架

```cpp
#include <fstream>
#include <iostream>
#include <string>
#include <vector>
#include <format>
#include <sstream>

struct LogEntry {
    std::string ip;
    int status;
    std::string url;
};

// 1. 不用 class 包裝 vector——std::vector<LogEntry> 本身就是 RAII

std::vector<LogEntry> parse_file(const std::string& path);

int main(int argc, char* argv[]) {
    // TODO
}
```

## 參考答案

<details>
<summary>展開</summary>

```cpp
#include <format>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

struct LogEntry {
    std::string ip;
    int status;
    std::string url;
};

std::vector<LogEntry> parse_file(const std::string& path) {
    std::ifstream f{path};
    if (!f) throw std::runtime_error{std::format("cannot open {}", path)};

    std::vector<LogEntry> entries;
    std::string line;
    while (std::getline(f, line)) {
        std::istringstream iss{line};
        LogEntry e;
        if (iss >> e.ip >> e.status >> e.url) {
            entries.push_back(std::move(e));
        }
    }
    return entries;
    // f 解構：自動 fclose
    // 任何 exception：entries 和 f 都自動解構
}

int main(int argc, char* argv[]) {
    if (argc != 2) {
        std::cerr << std::format("usage: {} <file>\n", argv[0]);
        return 1;
    }

    try {
        auto entries = parse_file(argv[1]);
        for (const auto& e : entries) {
            std::cout << std::format("{} {} {}\n", e.ip, e.status, e.url);
        }
    } catch (const std::exception& e) {
        std::cerr << std::format("error: {}\n", e.what());
        return 1;
    }
}
```

**C 版本 ~60 行，C++ 版本 ~30 行**。沒有任何手動 free。

### 為什麼這麼短？

- `std::ifstream` RAII 管 FILE
- `std::vector<LogEntry>` RAII 管 array + 每個 LogEntry
- 每個 `LogEntry::ip` / `url` 是 `std::string`，RAII 管字串記憶體
- Exception 把 error path 折疊成一個 try/catch

**成員解構**遞歸：vector 解構 → 每個 LogEntry 解構 → 每個 string 解構 → 字串記憶體釋放。全自動。
</details>

## 進階：用 `std::expected`（C++23）

```cpp
std::expected<std::vector<LogEntry>, std::string> parse_file(const std::string& path) {
    std::ifstream f{path};
    if (!f) return std::unexpected{std::format("cannot open {}", path)};

    std::vector<LogEntry> entries;
    std::string line;
    while (std::getline(f, line)) {
        std::istringstream iss{line};
        LogEntry e;
        if (iss >> e.ip >> e.status >> e.url) entries.push_back(std::move(e));
    }
    return entries;
}

int main(int argc, char* argv[]) {
    if (argc != 2) { /* ... */ }

    auto result = parse_file(argv[1]);
    if (!result) {
        std::cerr << "error: " << result.error() << '\n';
        return 1;
    }
    for (const auto& e : *result) {
        std::cout << std::format("{} {} {}\n", e.ip, e.status, e.url);
    }
}
```

## 編譯測試

```bash
# C 版本
gcc -Wall log_parser.c -o c_version

# C++ 版本
g++ -std=c++20 -Wall -Wextra -fsanitize=address,undefined log_parser.cpp -o cpp_version

# 測試
echo "1.2.3.4 200 /index.html" > test.log
echo "5.6.7.8 404 /missing" >> test.log
./cpp_version test.log
```

## 學習重點

完成這題後你該領會：

1. **`std::vector` + `std::string` 組成就是 RAII 容器**，不用自己寫 class
2. **Exception 把錯誤 path 折疊**——原本 C 每個失敗都要手動清，現在 stack unwinding 全做
3. **沒有 pointer 也能寫流暢程式**——value semantics + move 就夠
4. **程式碼變短 = bug 機會變少**——90% 的 C 爛 code 因為 resource management 雜訊

## 常見陷阱

- ❌ 自己寫 `class LogEntries { LogEntry* data_; ~LogEntries(){delete[] data_;} };` — 重複發明 `std::vector`
- ❌ `char*` 當 struct 欄位 — 用 `std::string`
- ❌ 「我要回傳 pointer 給 caller delete」— 用 by-value 回傳（move 自動優化）
- ❌ 用 `char buf[1024]` 當 buffer — `std::string` / `std::getline`

## 本練習重點
- 體會 RAII 如何讓 C 的痛點消失
- `std::vector<T>` 本身就是完整的資源管理器
- Exception + RAII 折疊錯誤處理
- 程式碼行數是現代 C++ 的副產品
