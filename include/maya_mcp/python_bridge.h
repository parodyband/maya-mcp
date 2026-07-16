#pragma once

#include <nlohmann/json.hpp>

#include <string>

namespace maya_mcp {

class PythonBridge final {
public:
    using Json = nlohmann::json;

    bool initialize(std::string& error);
    void shutdown() noexcept;
    [[nodiscard]] const Json& catalog() const noexcept { return catalog_; }
    [[nodiscard]] Json callTool(const std::string& name, const Json& arguments) const;
    [[nodiscard]] Json readResource(const std::string& uri) const;

private:
    [[nodiscard]] Json callEncoded(const char* functionName, const Json& payload) const;
    Json catalog_ = Json::object();
};

}  // namespace maya_mcp
