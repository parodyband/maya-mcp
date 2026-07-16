#pragma once

#include <maya/MMessage.h>
#include <nlohmann/json.hpp>

#include <cstddef>
#include <atomic>
#include <deque>
#include <future>
#include <functional>
#include <memory>
#include <mutex>
#include <string>

namespace maya_mcp {

class MainThreadDispatcher final {
public:
    using Json = nlohmann::json;
    using Task = std::function<Json()>;

    MainThreadDispatcher() = default;
    ~MainThreadDispatcher();

    MainThreadDispatcher(const MainThreadDispatcher&) = delete;
    MainThreadDispatcher& operator=(const MainThreadDispatcher&) = delete;

    bool install(std::string& error);
    bool uninstall(std::string& error);
    void resume();
    void pause(const std::string& reason);

    std::future<Json> submit(Task task);
    std::size_t pump(std::size_t maxItems = 32);
    [[nodiscard]] std::size_t queued() const;
    [[nodiscard]] bool executing() const noexcept {
        return executionDepth_.load() != 0;
    }

private:
    struct WorkItem {
        Task task;
        std::promise<Json> promise;
    };

    static void timerCallback(float elapsedTime, float lastTime, void* clientData);
    void cancelPending(const std::string& reason);

    mutable std::mutex mutex_;
    std::deque<std::shared_ptr<WorkItem>> queue_;
    MCallbackId callbackId_{0};
    bool installed_{false};
    bool accepting_{false};
    std::atomic<unsigned int> executionDepth_{0};
};

}  // namespace maya_mcp
