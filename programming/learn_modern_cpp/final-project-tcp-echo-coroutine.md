# Final Project: Coroutine-based TCP Echo Server

**目標**：用 C++20 coroutines 寫一個支援多連線的 TCP echo server。
**用到**：幾乎所有章節——RAII、smart pointers、templates、concepts、ranges、coroutines、concurrency。

## 為什麼這題

- 現實世界的 async I/O 範例
- 練到 coroutine 的**實用面**（不只是 generator 玩具）
- 整合 RAII + 非同步 + error handling 各種現代 C++

## 規格

1. Server 綁定 TCP port（例如 12345）
2. 每個 client 連線進來，server 讀取任何資料，原樣回送
3. Server 支援多 client **併發**
4. Client 斷線時 server 乾淨處理
5. Ctrl-C 時 server 乾淨關閉

## 限制

- C++20 coroutines 做連線處理（不是 thread-per-connection）
- 底層 I/O 用 Linux `epoll` 或 Boost.Asio
- RAII 管 socket FD
- 至少一個 `concept` 約束
- 程式碼 < 400 行（挑戰）

## 方案 A：Boost.Asio + coroutine（推薦）

Boost.Asio 早就整合 C++20 coroutines，省掉自己寫 I/O layer 的痛。

### 安裝
```bash
# MSYS2
pacman -S mingw-w64-ucrt-x86_64-boost

# Ubuntu
sudo apt install libboost-all-dev
```

### 程式

```cpp
#include <boost/asio.hpp>
#include <boost/asio/co_spawn.hpp>
#include <boost/asio/detached.hpp>
#include <boost/asio/signal_set.hpp>

#include <format>
#include <iostream>
#include <string>

namespace asio = boost::asio;
using asio::ip::tcp;
using asio::awaitable;
using asio::co_spawn;
using asio::detached;
using asio::use_awaitable;

awaitable<void> handle_client(tcp::socket sock) {
    try {
        auto endpoint = sock.remote_endpoint();
        std::cout << std::format("client connected: {}:{}\n",
            endpoint.address().to_string(), endpoint.port());

        char buf[1024];
        for (;;) {
            std::size_t n = co_await sock.async_read_some(
                asio::buffer(buf), use_awaitable);
            co_await asio::async_write(sock,
                asio::buffer(buf, n), use_awaitable);
        }
    } catch (const std::exception& e) {
        std::cout << std::format("client disconnected: {}\n", e.what());
    }
    // sock 解構：RAII 關 socket
}

awaitable<void> listener(unsigned short port) {
    auto exec = co_await asio::this_coro::executor;
    tcp::acceptor acceptor{exec, tcp::endpoint(tcp::v4(), port)};

    std::cout << std::format("listening on :{}\n", port);

    for (;;) {
        tcp::socket sock = co_await acceptor.async_accept(use_awaitable);
        co_spawn(exec, handle_client(std::move(sock)), detached);
    }
}

int main(int argc, char* argv[]) {
    unsigned short port = argc > 1 ? std::stoi(argv[1]) : 12345;

    try {
        asio::io_context io;

        // Ctrl-C 乾淨關閉
        asio::signal_set signals{io, SIGINT, SIGTERM};
        signals.async_wait([&](auto, auto) { io.stop(); });

        co_spawn(io, listener(port), detached);
        io.run();
    } catch (const std::exception& e) {
        std::cerr << std::format("fatal: {}\n", e.what());
        return 1;
    }
}
```

### 編譯
```bash
g++ -std=c++20 -fcoroutines -Wall -O2 \
    echo.cpp -o echo \
    -lboost_system -pthread
```

### 測試
```bash
./echo 12345 &

# 另一個 terminal
echo "hello" | nc localhost 12345
# 應該印 "hello"

telnet localhost 12345
# 打字按 enter，會被 echo 回來
```

### 並發測試
```bash
for i in {1..10}; do
    (echo "client$i" | nc localhost 12345) &
done
wait
```

10 個 client 應該都得到自己的回聲，不互相卡。

## 方案 B：從零用 epoll + DIY coroutine Task（進階）

如果你想不靠 Asio，純手搓：

### 骨架（結構）

```cpp
// 你需要：
// 1. Task<T> 類別（promise_type）
// 2. awaitable wrappers: async_read, async_write, async_accept
// 3. IOReactor class（拿 epoll_wait 做主迴圈）
// 4. Socket RAII wrapper
```

這大概 300-500 行 code，是很好的學習題，但超出本練習預設範圍。有興趣的可以參考：
- [YACLib](https://github.com/YACLib/YACLib)
- [C++ coroutine tutorial by Lewis Baker](https://lewissbaker.github.io/)
- cppcoro library

## Step by Step 擴充

### 擴充 1：行為超過 echo
- 把輸入轉大寫後回傳
- 實作簡單 protocol：`GET <key>` / `SET <key> <value>`（迷你 redis）

### 擴充 2：超時
Client 30 秒沒動就踢掉：
```cpp
asio::steady_timer timer{exec, std::chrono::seconds{30}};
// 用 awaitable_operators 的 || 做「read 或 timer 先到」
```

### 擴充 3：Graceful shutdown
收到 Ctrl-C 時：
1. 拒絕新連線
2. 等所有現有 client 完成當前操作
3. 關閉 server

### 擴充 4：指標監控
統計：
- 當前連線數（atomic counter）
- 累計處理 bytes
- 連線平均時長

用 `std::atomic` 累加，另一個 coroutine 每 10 秒印統計。

### 擴充 5：logging with std::format
```cpp
auto log = [](std::string_view level, auto&&... args) {
    auto now = std::chrono::system_clock::now();
    std::cout << std::format("[{:%H:%M:%S} {}] ", now, level);
    std::cout << std::format(args...);
    std::cout << '\n';
};

log("INFO", "client {} connected", addr);
```

### 擴充 6：加 concept
```cpp
template <typename Handler>
concept ClientHandler = requires(Handler h, tcp::socket s) {
    { h(std::move(s)) } -> std::same_as<awaitable<void>>;
};

// 然後 listener 可以泛化接收 handler
template <ClientHandler Handler>
awaitable<void> listener(unsigned short port, Handler handler);
```

## 關鍵學習點

### 1. Coroutine 取代 callback hell
Asio 傳統 API 是 callback 嵌套：
```cpp
acceptor.async_accept([](auto ec, auto sock) {
    sock.async_read_some(buf, [](auto ec, auto n) {
        sock.async_write(buf, n, [](auto ec, auto n) {
            // ... 更深巢狀
        });
    });
});
```

Coroutine 版本是平坦的同步式 code。

### 2. 沒有 thread-per-connection
整個 server 是**單 thread**（io_context），靠 coroutine 切換處理多 client。不是 blocking。

可以加 worker thread pool：
```cpp
asio::thread_pool pool{4};
co_spawn(pool, handle_client(std::move(sock)), detached);
```

### 3. RAII 處理 socket
`tcp::socket` 解構自動關閉。Exception 從 `co_await` 丟出時，stack unwinding 照常，RAII 照常。

### 4. Exception 跨 coroutine
Client handler throw exception 時，不會影響其他 client 或 server。因為 coroutine 是獨立的，用 `detached` spawn 讓它自生自滅。

## Checklist（交作業前檢查）

- [ ] `-Wall -Wextra -Werror` 無警告通過
- [ ] `-fsanitize=address,undefined` 跑測試無錯
- [ ] `valgrind ./echo 12345` 無 leak（關閉後）
- [ ] 多 client 併發測試無 race
- [ ] Ctrl-C 能乾淨退出
- [ ] Client 異常斷線不會讓 server crash
- [ ] RAII 管 socket 和其他 resource
- [ ] 用了 `std::format`、`concept`（至少一處）
- [ ] 有 meaningful logging

## 測試腳本

```bash
#!/bin/bash
# test.sh
./echo 12345 &
SERVER_PID=$!
sleep 0.5

PASS=0; FAIL=0
test_case() {
    local input="$1" expected="$2"
    local got=$(echo "$input" | nc -q1 localhost 12345)
    if [ "$got" = "$expected" ]; then
        echo "PASS: $input"
        ((PASS++))
    else
        echo "FAIL: $input -> $got (expected $expected)"
        ((FAIL++))
    fi
}

test_case "hello" "hello"
test_case "world" "world"
test_case "" ""

echo "Concurrent test..."
for i in {1..20}; do
    (echo "conn$i" | nc -q1 localhost 12345 > /tmp/out$i) &
done
wait
for i in {1..20}; do
    if [ "$(cat /tmp/out$i)" != "conn$i" ]; then
        echo "concurrent fail: $i"
        ((FAIL++))
    fi
done

kill $SERVER_PID
wait 2>/dev/null

echo "Pass: $PASS, Fail: $FAIL"
exit $FAIL
```

## 本 Final Project 重點

- Coroutine 做 async I/O 比 callback 乾淨
- 單 thread 能支援千級 concurrent connections
- RAII 在非同步上下文仍然正確
- 結合了本課程幾乎所有章節的技巧
- **實際專案長這樣**：server code 只要幾百行就能做到高並發
