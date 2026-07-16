#include "maya_mcp/main_thread_dispatcher.h"

#include <maya/MStatus.h>
#include <maya/MString.h>
#include <maya/MTimerMessage.h>

#include <chrono>
#include <exception>
#include <stdexcept>
#include <utility>
#include <vector>

namespace maya_mcp {

MainThreadDispatcher::~MainThreadDispatcher() {
    std::string error;
    (void)uninstall(error);
}

bool MainThreadDispatcher::install(std::string& error) {
    if (installed_) {
        resume();
        return true;
    }

    MStatus status;
    callbackId_ = MTimerMessage::addTimerCallback(
        0.01F, &MainThreadDispatcher::timerCallback, this, &status);
    if (!status) {
        error = status.errorString().asChar();
        callbackId_ = 0;
        return false;
    }

    {
        std::lock_guard lock(mutex_);
        installed_ = true;
        accepting_ = true;
    }
    return true;
}

bool MainThreadDispatcher::uninstall(std::string& error) {
    pause("Maya MCP dispatcher is shutting down");
    if (callbackId_ != 0) {
        const MStatus status = MMessage::removeCallback(callbackId_);
        if (!status) {
            error = status.errorString().asChar();
            return false;
        }
        callbackId_ = 0;
    }
    std::lock_guard lock(mutex_);
    installed_ = false;
    return true;
}

void MainThreadDispatcher::resume() {
    std::lock_guard lock(mutex_);
    if (installed_) {
        accepting_ = true;
    }
}

void MainThreadDispatcher::pause(const std::string& reason) {
    {
        std::lock_guard lock(mutex_);
        accepting_ = false;
    }
    cancelPending(reason);
}

std::future<MainThreadDispatcher::Json> MainThreadDispatcher::submit(Task task) {
    auto item = std::make_shared<WorkItem>();
    item->task = std::move(task);
    auto future = item->promise.get_future();

    {
        std::lock_guard lock(mutex_);
        if (!installed_ || !accepting_) {
            item->promise.set_exception(std::make_exception_ptr(
                std::runtime_error("Maya MCP dispatcher is not accepting work")));
            return future;
        }
        if (queue_.size() >= 256) {
            item->promise.set_exception(std::make_exception_ptr(
                std::runtime_error("Maya MCP main-thread queue is full")));
            return future;
        }
        queue_.push_back(std::move(item));
    }
    return future;
}

std::size_t MainThreadDispatcher::pump(const std::size_t maxItems) {
    if (executing()) {
        return 0;
    }
    using Clock = std::chrono::steady_clock;
    const auto deadline = Clock::now() + std::chrono::milliseconds(5);
    std::size_t completed = 0;

    while (completed < maxItems && Clock::now() < deadline) {
        std::shared_ptr<WorkItem> item;
        {
            std::lock_guard lock(mutex_);
            if (queue_.empty()) {
                break;
            }
            item = std::move(queue_.front());
            queue_.pop_front();
        }

        executionDepth_.fetch_add(1);
        try {
            Json value = item->task();
            executionDepth_.fetch_sub(1);
            item->promise.set_value(std::move(value));
        } catch (...) {
            executionDepth_.fetch_sub(1);
            item->promise.set_exception(std::current_exception());
        }
        ++completed;
    }
    return completed;
}

std::size_t MainThreadDispatcher::queued() const {
    std::lock_guard lock(mutex_);
    return queue_.size();
}

void MainThreadDispatcher::timerCallback(
    const float elapsedTime, const float lastTime, void* clientData) {
    (void)elapsedTime;
    (void)lastTime;
    auto* dispatcher = static_cast<MainThreadDispatcher*>(clientData);
    if (dispatcher != nullptr) {
        try {
            dispatcher->pump();
        } catch (...) {
            // Maya callbacks must never allow a C++ exception to cross the ABI.
        }
    }
}

void MainThreadDispatcher::cancelPending(const std::string& reason) {
    std::deque<std::shared_ptr<WorkItem>> pending;
    {
        std::lock_guard lock(mutex_);
        pending.swap(queue_);
    }

    for (const auto& item : pending) {
        item->promise.set_exception(
            std::make_exception_ptr(std::runtime_error(reason)));
    }
}

}  // namespace maya_mcp
