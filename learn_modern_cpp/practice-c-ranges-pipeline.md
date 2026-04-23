# Practice C: Ranges + Concepts 資料 pipeline

**目標**：用 C++20 ranges + concepts 寫一個 CSV 資料處理 pipeline。
**用到**：Ch11-14（algorithms、constexpr、concepts、ranges）

## 背景

你有一份 CSV 檔案，紀錄 HTTP server access log：
```
timestamp,ip,method,path,status,bytes
1713123456,1.2.3.4,GET,/index.html,200,1024
1713123457,5.6.7.8,POST,/api/login,401,256
1713123458,1.2.3.4,GET,/style.css,200,4096
1713123459,5.6.7.8,GET,/api/user,500,512
1713123460,9.1.2.3,GET,/index.html,200,1024
...
```

## 任務

寫一個 C++20 程式，用 ranges + concepts 完成：

1. 讀取 CSV 檔
2. 篩選特定時間範圍
3. 過濾狀態碼 `>= 400` 的錯誤
4. 按 IP 分組統計錯誤數
5. 輸出錯誤數排名前 10 的 IP

## 限制

- 至少 80% 的處理邏輯用 **ranges / views / algorithms**，不要手寫 for-if
- 用 **concepts** 限制至少一個 template 函式
- 輸出用 **`std::format`** 或 C++23 `std::print`
- 程式要能跑在 gcc 13（主體）和選擇性 gcc 14 特性

## Step 1：定義資料結構

```cpp
struct LogEntry {
    long timestamp;
    std::string ip;
    std::string method;
    std::string path;
    int status;
    size_t bytes;

    auto operator<=>(const LogEntry&) const = default;
};
```

用 C++20 spaceship 自動得到所有比較。

## Step 2：parser

CSV 切割可以用 `std::views::split`。注意 gcc 13 的 split view 行為，可能要手搓：

```cpp
#include <charconv>

std::optional<LogEntry> parse_line(std::string_view line) {
    std::array<std::string_view, 6> fields;
    // 切割 line 成 6 欄
    auto it = fields.begin();
    while (!line.empty() && it != fields.end()) {
        auto comma = line.find(',');
        if (comma == std::string_view::npos) {
            *it++ = line;
            break;
        }
        *it++ = line.substr(0, comma);
        line.remove_prefix(comma + 1);
    }
    if (it != fields.end()) return std::nullopt;

    LogEntry e;
    // from_chars 做無分配字串轉數字
    auto [p1, ec1] = std::from_chars(fields[0].data(), fields[0].data() + fields[0].size(), e.timestamp);
    if (ec1 != std::errc{}) return std::nullopt;
    e.ip = fields[1];
    e.method = fields[2];
    e.path = fields[3];
    auto [p2, ec2] = std::from_chars(fields[4].data(), fields[4].data() + fields[4].size(), e.status);
    if (ec2 != std::errc{}) return std::nullopt;
    auto [p3, ec3] = std::from_chars(fields[5].data(), fields[5].data() + fields[5].size(), e.bytes);
    if (ec3 != std::errc{}) return std::nullopt;

    return e;
}
```

## Step 3：讀檔 + parse

```cpp
std::vector<LogEntry> read_log(const std::string& path) {
    std::ifstream f{path};
    if (!f) throw std::runtime_error{"cannot open"};

    std::vector<LogEntry> entries;
    std::string line;
    std::getline(f, line);   // skip header

    while (std::getline(f, line)) {
        if (auto e = parse_line(line)) {
            entries.push_back(std::move(*e));
        }
    }
    return entries;
}
```

## Step 4：用 ranges 做 pipeline

```cpp
#include <ranges>
namespace rg = std::ranges;
namespace vw = std::views;

void analyze(const std::vector<LogEntry>& entries, long start, long end) {
    // 篩時間範圍 + 錯誤
    auto filtered = entries
        | vw::filter([start, end](const LogEntry& e) {
            return e.timestamp >= start && e.timestamp < end;
          })
        | vw::filter([](const LogEntry& e) { return e.status >= 400; });

    // 按 IP 統計（ranges 不直接支援 group by，自己累計）
    std::unordered_map<std::string, int> counts;
    for (const auto& e : filtered) {
        ++counts[e.ip];
    }

    // 轉成 vector，按 count 降序
    std::vector<std::pair<std::string, int>> ranked(counts.begin(), counts.end());
    rg::sort(ranked, {}, [](const auto& p) { return -p.second; });

    // 取前 10
    auto top10 = ranked | vw::take(10);

    std::cout << std::format("Top 10 error-generating IPs (status >= 400):\n");
    for (const auto& [ip, count] : top10) {
        std::cout << std::format("  {:>15}  {:>5}\n", ip, count);
    }
}
```

## Step 5：加 concept

寫一個泛型 printer，要求型別是「有 `ip` 和 `count` 的東西」：

```cpp
template <typename T>
concept HasIpAndCount = requires(T t) {
    { t.ip } -> std::convertible_to<std::string>;
    { t.count } -> std::integral;
};

template <HasIpAndCount T>
void print_ranking(const std::vector<T>& items) {
    for (const auto& item : items) {
        std::cout << std::format("{:>15}  {:>5}\n", item.ip, item.count);
    }
}
```

或用現有的 concept：

```cpp
void print_pair_ranking(const rg::range auto& r) {
    for (const auto& [key, val] : r) {
        std::cout << std::format("{:>15}  {:>5}\n", key, val);
    }
}
```

## Step 6：主程式

```cpp
int main(int argc, char* argv[]) {
    if (argc < 2) {
        std::cerr << std::format("usage: {} <log.csv> [start_ts] [end_ts]\n", argv[0]);
        return 1;
    }

    try {
        auto entries = read_log(argv[1]);
        std::cout << std::format("loaded {} entries\n", entries.size());

        long start = argc > 2 ? std::stol(argv[2]) : 0;
        long end = argc > 3 ? std::stol(argv[3]) : std::numeric_limits<long>::max();

        analyze(entries, start, end);
    } catch (const std::exception& e) {
        std::cerr << std::format("error: {}\n", e.what());
        return 1;
    }
}
```

## Step 7：測試資料

生成測試 CSV：
```bash
cat > test.csv << 'EOF'
timestamp,ip,method,path,status,bytes
1713123456,1.2.3.4,GET,/index.html,200,1024
1713123457,5.6.7.8,POST,/api/login,401,256
1713123458,1.2.3.4,GET,/style.css,200,4096
1713123459,5.6.7.8,GET,/api/user,500,512
1713123460,9.1.2.3,GET,/index.html,200,1024
1713123461,5.6.7.8,POST,/api/login,401,256
1713123462,1.2.3.4,GET,/missing,404,0
EOF

g++ -std=c++20 -Wall -Wextra -O2 analyze.cpp -o analyze
./analyze test.csv
```

期望看到：
```
loaded 7 entries
Top 10 error-generating IPs (status >= 400):
      5.6.7.8      3
      1.2.3.4      1
```

## 進階挑戰

### 1. Lazy parsing
目前 parse 完全讀進 vector。試改用 `std::views::istream` 配合你的 parse 函式，讓整個流程都是 lazy，不用實體化整個 vector。

### 2. 按小時桶分
加一個 feature：按小時統計錯誤數（用 `timestamp / 3600`）。

### 3. C++23 的 chunk / slide
`std::views::chunk(n)` 把 range 切成大小 n 的塊。用它做「每 1000 筆算一次平均」。

### 4. 並行版本
用 `std::execution::par` 在 sort 或其他階段並行化（需 `-ltbb`）。

### 5. 使用 `std::expected`（C++23）
改 `parse_line` 回 `std::expected<LogEntry, std::string>`，帶具體錯誤訊息。

## 常見陷阱

### 陷阱 1：filter 對暫時 vector
```cpp
auto r = read_log("x.csv") | vw::filter(...);   // ❌ read_log 的 vector 解構後，view 懸垂
```
要先存到變數再 filter。

### 陷阱 2：view 多次迭代
```cpp
auto v = ... | vw::transform(expensive);
for (auto x : v) { ... }   // expensive 執行一次
for (auto x : v) { ... }   // expensive 又執行一次
```
要多次用：實體化到 vector。

### 陷阱 3：projection 搞混
```cpp
rg::sort(v, {}, [](const auto& p) { return p.second; });
// 這是升序。要降序用 std::greater 或取負。
rg::sort(v, std::greater{}, [](const auto& p) { return p.second; });
```

## 本練習重點
- Ranges pipeline 讓「filter → transform → aggregate」流程可讀
- Ranges **不直接支援 group-by**，要手寫累計
- `std::from_chars` 是零分配字串轉數字
- Concepts 做 template 的型別約束
- gcc 13 的 ranges 支援大多夠用；C++23 到 gcc 14
