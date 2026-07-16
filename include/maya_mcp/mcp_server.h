#pragma once

#include <nlohmann/json.hpp>

#include <memory>
#include <string>

namespace maya_mcp {

class MainThreadDispatcher;
class PythonBridge;

class McpServer final {
public:
    using Json = nlohmann::json;

    McpServer(MainThreadDispatcher& dispatcher, PythonBridge& bridge);
    ~McpServer();

    McpServer(const McpServer&) = delete;
    McpServer& operator=(const McpServer&) = delete;

    bool start(std::string& error);
    void stop();

    [[nodiscard]] bool running() const;
    [[nodiscard]] Json status() const;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace maya_mcp
