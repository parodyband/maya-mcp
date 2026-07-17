#include "maya_mcp/mcp_server.h"

#include "maya_mcp/main_thread_dispatcher.h"
#include "maya_mcp/python_bridge.h"

#include <windows.h>
#include <bcrypt.h>
#include <httplib.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cctype>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <future>
#include <iomanip>
#include <mutex>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <unordered_map>
#include <utility>
#include <vector>

namespace maya_mcp {
namespace {

constexpr const char* kProtocolVersion = "2025-11-25";
constexpr const char* kEndpointPath = "/mcp";

std::string randomHex(const std::size_t byteCount) {
    std::vector<unsigned char> bytes(byteCount);
    const NTSTATUS status = BCryptGenRandom(
        nullptr,
        bytes.data(),
        static_cast<ULONG>(bytes.size()),
        BCRYPT_USE_SYSTEM_PREFERRED_RNG);
    if (status < 0) {
        throw std::runtime_error("BCryptGenRandom failed");
    }

    std::ostringstream stream;
    stream << std::hex << std::setfill('0');
    for (const auto byte : bytes) {
        stream << std::setw(2) << static_cast<unsigned int>(byte);
    }
    return stream.str();
}

std::string environmentValue(const char* name) {
    std::size_t length = 0;
    char* value = nullptr;
    if (_dupenv_s(&value, &length, name) != 0 || value == nullptr) {
        return {};
    }
    std::string result(value);
    std::free(value);
    return result;
}

bool environmentFlag(const char* name) {
    std::string value = environmentValue(name);
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    return value == "1" || value == "true" || value == "yes" || value == "on";
}

int configuredPort() {
    const std::string value = environmentValue("MAYA_MCP_PORT");
    if (value.empty()) {
        return 7001;
    }
    try {
        const int port = std::stoi(value);
        return port > 0 && port <= 65535 ? port : 7001;
    } catch (...) {
        return 7001;
    }
}

bool constantTimeEqual(const std::string& left, const std::string& right) {
    const std::size_t size = std::max(left.size(), right.size());
    std::size_t difference = left.size() ^ right.size();
    for (std::size_t index = 0; index < size; ++index) {
        const unsigned char a =
            index < left.size() ? static_cast<unsigned char>(left[index]) : 0U;
        const unsigned char b =
            index < right.size() ? static_cast<unsigned char>(right[index]) : 0U;
        difference |= static_cast<std::size_t>(a ^ b);
    }
    return difference == 0U;
}

nlohmann::json jsonRpcError(
    const nlohmann::json& id,
    const int code,
    const std::string& message,
    const nlohmann::json& data = nullptr) {
    nlohmann::json error{{"code", code}, {"message", message}};
    if (!data.is_null()) {
        error["data"] = data;
    }
    return {
        {"jsonrpc", "2.0"},
        {"id", id},
        {"error", std::move(error)},
    };
}

nlohmann::json jsonRpcResult(
    const nlohmann::json& id, nlohmann::json result) {
    return {
        {"jsonrpc", "2.0"},
        {"id", id},
        {"result", std::move(result)},
    };
}

nlohmann::json toolFailure(
    const std::string& code,
    const std::string& message,
    const nlohmann::json& details = nlohmann::json::object()) {
    nlohmann::json structured{
        {"schema_version", "1.0"},
        {"ok", false},
        {"request_id", "native-bridge-error"},
        {"scene_epoch", ""},
        {"revisions",
         {
             {"scene_before", 0},
             {"scene_after", 0},
             {"context", 0},
         }},
        {"summary", message},
        {"data", nlohmann::json::object()},
        {"changes", nlohmann::json::array()},
        {"warnings", nlohmann::json::array()},
        {"undo", {{"available", false}, {"label", ""}}},
        {"timing_ms", 0.0},
        {"error", {{"code", code}, {"message", message}, {"details", details}}},
    };
    return {
        {"content", {{{"type", "text"}, {"text", structured.dump()}}}},
        {"structuredContent", structured},
        {"isError", true},
    };
}

constexpr std::size_t kMaxSerializedResultBytes =
    16U * 1024U * 1024U;

bool exceedsResultBudget(const nlohmann::json& value) {
    return value.dump().size() > kMaxSerializedResultBytes;
}

void writeJson(httplib::Response& response, const nlohmann::json& value) {
    response.set_header("Cache-Control", "no-store");
    response.set_content(value.dump(), "application/json");
}

std::string replaceAll(
    std::string value, const std::string& token, const std::string& replacement) {
    std::size_t offset = 0;
    while ((offset = value.find(token, offset)) != std::string::npos) {
        value.replace(offset, token.size(), replacement);
        offset += replacement.size();
    }
    return value;
}

void writeJsonFileAtomically(
    const std::filesystem::path& path, const nlohmann::json& value) {
    std::filesystem::path temporary = path;
    temporary += ".tmp-" + randomHex(4);
    {
        std::ofstream stream(
            temporary, std::ios::binary | std::ios::trunc);
        if (!stream) {
            throw std::runtime_error(
                "Could not create discovery file: " + temporary.string());
        }
        stream << value.dump(2);
        stream.flush();
        if (!stream) {
            throw std::runtime_error(
                "Could not write discovery file: " + temporary.string());
        }
    }
    if (!MoveFileExW(
            temporary.c_str(),
            path.c_str(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        const DWORD windowsError = GetLastError();
        std::error_code ignored;
        std::filesystem::remove(temporary, ignored);
        throw std::runtime_error(
            "Could not publish discovery file (Windows error " +
            std::to_string(windowsError) + ")");
    }
}

}  // namespace

class McpServer::Impl final {
public:
    Impl(MainThreadDispatcher& dispatcher, PythonBridge& bridge)
        : dispatcher_(dispatcher), bridge_(bridge) {}

    ~Impl() {
        try {
            stop();
        } catch (...) {
            // A C++ exception must never escape plug-in teardown.
        }
    }

    bool start(std::string& error) {
        std::unique_lock lifecycleLock(lifecycleMutex_);
        if (running_.load()) {
            return true;
        }

        try {
            // A listener can end on its own and leave a joinable thread behind.
            // Join it before assigning a replacement; assigning over a joinable
            // std::thread would terminate the Maya process.
            if (listener_.joinable()) {
                lifecycleLock.unlock();
                listener_.join();
                lifecycleLock.lock();
            }
            if (http_ != nullptr) {
                http_.reset();
                {
                    std::lock_guard sessionsLock(sessionsMutex_);
                    sessions_.clear();
                }
                cleanupDiscoveryFile();
                endpoint_.clear();
                port_ = 0;
                token_.clear();
            }

            lastError_.clear();
            token_ = environmentValue("MAYA_MCP_TOKEN");
            if (token_.empty()) {
                token_ = randomHex(32);
            }

            const auto configureHttp = [this]() {
                http_ = std::make_unique<httplib::Server>();
                http_->set_socket_options([](socket_t socket) {
                    // cpp-httplib defaults to SO_REUSEADDR. On Windows that can
                    // route two Maya processes on one port to the wrong token.
                    const BOOL enabled = TRUE;
                    ::setsockopt(
                        socket,
                        SOL_SOCKET,
                        SO_EXCLUSIVEADDRUSE,
                        reinterpret_cast<const char*>(&enabled),
                        sizeof(enabled));
                });
                http_->new_task_queue = []() {
                    return new httplib::ThreadPool(4, 4, 64);
                };
                http_->set_payload_max_length(8U * 1024U * 1024U);
                http_->set_read_timeout(10, 0);
                http_->set_write_timeout(300, 0);
                installRoutes();
            };
            configureHttp();

            port_ = configuredPort();
            if (!http_->bind_to_port("127.0.0.1", port_)) {
                // A failed bind decommissions a cpp-httplib Server instance;
                // recreate it before asking the OS for an ephemeral port.
                http_.reset();
                configureHttp();
                port_ = http_->bind_to_any_port("127.0.0.1");
            }
            if (port_ <= 0) {
                error = "Could not bind the Maya MCP server to loopback";
                http_.reset();
                return false;
            }

            endpoint_ =
                "http://127.0.0.1:" + std::to_string(port_) + kEndpointPath;
            dispatcher_.resume();
            running_.store(true);
            writeDiscoveryFile();
            listener_ = std::thread([this]() {
                try {
                    if (http_ != nullptr) {
                        http_->listen_after_bind();
                    }
                    if (running_.exchange(false)) {
                        std::lock_guard lock(lifecycleMutex_);
                        lastError_ = "HTTP listener stopped unexpectedly";
                    }
                } catch (const std::exception& exception) {
                    running_.store(false);
                    std::lock_guard lock(lifecycleMutex_);
                    lastError_ =
                        std::string("HTTP listener failed: ") + exception.what();
                } catch (...) {
                    running_.store(false);
                    std::lock_guard lock(lifecycleMutex_);
                    lastError_ = "HTTP listener failed with an unknown exception";
                }
            });
            return true;
        } catch (const std::exception& exception) {
            error = exception.what();
            running_.store(false);
            http_.reset();
            cleanupDiscoveryFile();
            return false;
        }
    }

    void stop() {
        std::unique_lock lifecycleLock(lifecycleMutex_);
        if (!running_.exchange(false) && http_ == nullptr &&
            !listener_.joinable()) {
            return;
        }

        dispatcher_.pause("Maya MCP server stopped");
        if (http_ != nullptr) {
            http_->stop();
        }
        lifecycleLock.unlock();
        if (listener_.joinable()) {
            listener_.join();
        }
        lifecycleLock.lock();

        http_.reset();
        {
            std::lock_guard sessionsLock(sessionsMutex_);
            sessions_.clear();
        }
        cleanupDiscoveryFile();
        endpoint_.clear();
        port_ = 0;
        token_.clear();
    }

    [[nodiscard]] bool running() const {
        return running_.load();
    }

    [[nodiscard]] Json status() const {
        std::lock_guard lifecycleLock(lifecycleMutex_);
        Json result{
            {"name", "maya-mcp"},
            {"version", MAYA_MCP_VERSION},
            {"protocolVersion", kProtocolVersion},
            {"running", running_.load()},
            {"endpoint", endpoint_},
            {"discoveryFile", discoveryFile_.string()},
            {"pendingMainThreadRequests", dispatcher_.queued()},
            {"scriptExecutionEnabled",
             environmentFlag("MAYA_MCP_ALLOW_UNSAFE_CODE")},
            {"unsafeCodeEnabled", environmentFlag("MAYA_MCP_ALLOW_UNSAFE_CODE")},
            {"lastError", lastError_},
        };
        {
            std::lock_guard lock(sessionsMutex_);
            result["sessions"] = sessions_.size();
        }
        return result;
    }

private:
    struct Session {
        std::string protocolVersion;
        bool initialized{false};
        std::chrono::steady_clock::time_point lastSeen{
            std::chrono::steady_clock::now()};
    };

    void installRoutes() {
        http_->Get("/health", [this](const httplib::Request&, httplib::Response& res) {
            writeJson(
                res,
                {{"status", running_.load() ? "ok" : "stopping"},
                 {"name", "maya-mcp"},
                 {"version", MAYA_MCP_VERSION}});
        });

        http_->Post(kEndpointPath, [this](
            const httplib::Request& request, httplib::Response& response) {
            handlePost(request, response);
        });

        http_->Get(kEndpointPath, [this](
            const httplib::Request& request, httplib::Response& response) {
            if (!authorize(request, response)) {
                return;
            }
            response.status = 405;
            response.set_header("Allow", "POST, DELETE");
        });

        http_->Delete(kEndpointPath, [this](
            const httplib::Request& request, httplib::Response& response) {
            if (!authorize(request, response)) {
                return;
            }
            const std::string sessionId =
                request.get_header_value("MCP-Session-Id");
            if (sessionId.empty()) {
                response.status = 400;
                return;
            }
            std::lock_guard lock(sessionsMutex_);
            if (sessions_.erase(sessionId) == 0U) {
                response.status = 404;
                return;
            }
            response.status = 204;
        });
    }

    bool authorize(
        const httplib::Request& request, httplib::Response& response) const {
        const std::string origin = request.get_header_value("Origin");
        if (!origin.empty()) {
            static const std::regex allowedOrigin(
                R"(^https?://(localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?$)",
                std::regex::icase);
            if (!std::regex_match(origin, allowedOrigin)) {
                response.status = 403;
                writeJson(
                    response,
                    jsonRpcError(nullptr, -32003, "Origin is not allowed"));
                return false;
            }
        }

        const std::string authorization =
            request.get_header_value("Authorization");
        const std::string expected = "Bearer " + token_;
        if (authorization.size() > 1024U ||
            !constantTimeEqual(authorization, expected)) {
            response.status = 401;
            response.set_header("WWW-Authenticate", "Bearer");
            writeJson(
                response,
                jsonRpcError(nullptr, -32001, "Authentication required"));
            return false;
        }
        return true;
    }

    void handlePost(
        const httplib::Request& request, httplib::Response& response) {
        if (!running_.load()) {
            response.status = 503;
            return;
        }
        if (!authorize(request, response)) {
            return;
        }

        std::string contentType = request.get_header_value("Content-Type");
        std::transform(
            contentType.begin(),
            contentType.end(),
            contentType.begin(),
            [](const unsigned char c) {
                return static_cast<char>(std::tolower(c));
            });
        const std::size_t separator = contentType.find(';');
        const std::string mediaType = contentType.substr(0, separator);
        if (mediaType != "application/json") {
            response.status = 415;
            writeJson(
                response,
                jsonRpcError(nullptr, -32600, "Content-Type must be application/json"));
            return;
        }

        Json message;
        try {
            message = Json::parse(request.body);
        } catch (const std::exception& exception) {
            response.status = 400;
            writeJson(
                response,
                jsonRpcError(nullptr, -32700, "Parse error", exception.what()));
            return;
        }

        if (!message.is_object() || message.value("jsonrpc", "") != "2.0" ||
            !message.contains("method") || !message["method"].is_string()) {
            writeJson(
                response,
                jsonRpcError(
                    message.value("id", Json(nullptr)),
                    -32600,
                    "Invalid JSON-RPC request"));
            return;
        }
        if (message.contains("id") &&
            !message["id"].is_string() &&
            !message["id"].is_number()) {
            writeJson(
                response,
                jsonRpcError(nullptr, -32600, "Request id must be a string or number"));
            return;
        }

        const bool notification = !message.contains("id");
        const Json id = notification ? Json(nullptr) : message["id"];
        const std::string method = message["method"].get<std::string>();
        const Json params = message.value("params", Json::object());
        if (!notification && method.rfind("notifications/", 0) == 0) {
            writeJson(
                response,
                jsonRpcError(id, -32600, "Notifications must not include an id"));
            return;
        }

        if (method == "initialize") {
            handleInitialize(id, params, response);
            return;
        }

        const std::string sessionId =
            request.get_header_value("MCP-Session-Id");
        Session session;
        {
            std::lock_guard lock(sessionsMutex_);
            const auto found = sessions_.find(sessionId);
            if (sessionId.empty() || found == sessions_.end()) {
                response.status = sessionId.empty() ? 400 : 404;
                writeJson(
                    response,
                    jsonRpcError(id, -32002, "A valid MCP session is required"));
                return;
            }
            found->second.lastSeen = std::chrono::steady_clock::now();
            session = found->second;
        }

        const std::string protocolHeader =
            request.get_header_value("MCP-Protocol-Version");
        if (!protocolHeader.empty() &&
            protocolHeader != session.protocolVersion) {
            response.status = 400;
            writeJson(
                response,
                jsonRpcError(id, -32600, "MCP protocol version does not match session"));
            return;
        }

        if (method == "notifications/initialized") {
            std::lock_guard lock(sessionsMutex_);
            const auto found = sessions_.find(sessionId);
            if (found != sessions_.end()) {
                found->second.initialized = true;
            }
            response.status = 202;
            return;
        }
        if (method == "notifications/cancelled") {
            response.status = 202;
            return;
        }
        if (!session.initialized) {
            writeJson(
                response,
                jsonRpcError(id, -32002, "Session initialization is incomplete"));
            return;
        }
        if (notification) {
            response.status = 202;
            return;
        }

        if (method == "ping") {
            writeJson(response, jsonRpcResult(id, Json::object()));
        } else if (method == "tools/list") {
            writeJson(
                response,
                jsonRpcResult(id, {{"tools", bridge_.catalog()["tools"]}}));
        } else if (method == "tools/call") {
            handleToolCall(id, params, response);
        } else if (method == "resources/list") {
            writeJson(
                response,
                jsonRpcResult(
                    id,
                    {{"resources", bridge_.catalog().value(
                        "resources", Json::array())}}));
        } else if (method == "resources/read") {
            handleResourceRead(id, params, response);
        } else if (method == "prompts/list") {
            Json prompts = bridge_.catalog().value("prompts", Json::array());
            for (auto& prompt : prompts) {
                prompt.erase("_message");
            }
            writeJson(response, jsonRpcResult(id, {{"prompts", prompts}}));
        } else if (method == "prompts/get") {
            handlePromptGet(id, params, response);
        } else {
            writeJson(
                response,
                jsonRpcError(id, -32601, "Method not found", {{"method", method}}));
        }
    }

    void handleInitialize(
        const Json& id, const Json& params, httplib::Response& response) {
        if (id.is_null() || !params.is_object() ||
            !params.contains("protocolVersion") ||
            !params["protocolVersion"].is_string() ||
            !params.contains("capabilities") ||
            !params["capabilities"].is_object() ||
            !params.contains("clientInfo") ||
            !params["clientInfo"].is_object() ||
            !params["clientInfo"].contains("name") ||
            !params["clientInfo"]["name"].is_string() ||
            !params["clientInfo"].contains("version") ||
            !params["clientInfo"]["version"].is_string()) {
            writeJson(
                response,
                jsonRpcError(id, -32602, "Invalid initialize parameters"));
            return;
        }

        const std::string requested =
            params["protocolVersion"].get<std::string>();
        const std::string negotiated =
            requested == "2025-06-18" || requested == "2025-03-26"
                ? requested
                : kProtocolVersion;
        const std::string sessionId = randomHex(24);
        {
            std::lock_guard lock(sessionsMutex_);
            const auto now = std::chrono::steady_clock::now();
            for (auto iterator = sessions_.begin(); iterator != sessions_.end();) {
                if (now - iterator->second.lastSeen > std::chrono::hours(2)) {
                    iterator = sessions_.erase(iterator);
                } else {
                    ++iterator;
                }
            }
            if (sessions_.size() >= 128U) {
                const auto oldest = std::min_element(
                    sessions_.begin(),
                    sessions_.end(),
                    [](const auto& left, const auto& right) {
                        return left.second.lastSeen < right.second.lastSeen;
                    });
                if (oldest != sessions_.end()) {
                    sessions_.erase(oldest);
                }
            }
            sessions_[sessionId] = Session{negotiated};
        }

        Json result{
            {"protocolVersion", negotiated},
            {"capabilities",
             {
                 {"tools", {{"listChanged", false}}},
                 {"resources", {{"subscribe", false}, {"listChanged", false}}},
                 {"prompts", {{"listChanged", false}}},
             }},
            {"serverInfo",
             {
                 {"name", "maya-mcp"},
                 {"title", "Maya 2027 MCP"},
                 {"version", MAYA_MCP_VERSION},
             }},
            {"instructions",
             bridge_.catalog().value(
                 "instructions",
                 "Inspect before editing. Prefer typed tools and use scripts only "
                 "when no typed operation can express the task.")},
        };
        response.set_header("MCP-Session-Id", sessionId);
        writeJson(response, jsonRpcResult(id, std::move(result)));
    }

    void handleToolCall(
        const Json& id, const Json& params, httplib::Response& response) {
        if (!params.is_object() || !params.contains("name") ||
            !params["name"].is_string()) {
            writeJson(
                response,
                jsonRpcError(id, -32602, "Tool name is required"));
            return;
        }
        const std::string name = params["name"].get<std::string>();
        const Json arguments = params.value("arguments", Json::object());
        if (!arguments.is_object()) {
            writeJson(
                response,
                jsonRpcError(id, -32602, "Tool arguments must be an object"));
            return;
        }

        const auto& tools = bridge_.catalog()["tools"];
        const bool exists = std::any_of(
            tools.begin(), tools.end(), [&name](const Json& tool) {
                return tool.value("name", "") == name;
            });
        if (!exists) {
            writeJson(
                response,
                jsonRpcError(id, -32602, "Unknown tool", {{"name", name}}));
            return;
        }

        try {
            auto future = dispatcher_.submit(
                [this, name, arguments]() {
                    return bridge_.callTool(name, arguments);
                });
            Json toolResult = future.get();
            if (!toolResult.is_object() || !toolResult.contains("content")) {
                toolResult = toolFailure(
                    "INVALID_TOOL_RESULT",
                    "The Maya tool returned an invalid MCP result");
            } else if (exceedsResultBudget(toolResult)) {
                toolResult = toolFailure(
                    "TOOL_RESPONSE_TOO_LARGE",
                    "The Maya tool result exceeded the 16 MiB response budget",
                    {{"maximum_bytes", kMaxSerializedResultBytes}});
            }
            writeJson(response, jsonRpcResult(id, std::move(toolResult)));
        } catch (const std::exception& exception) {
            writeJson(
                response,
                jsonRpcResult(
                    id,
                    toolFailure("MAYA_EXECUTION_ERROR", exception.what())));
        }
    }

    void handleResourceRead(
        const Json& id, const Json& params, httplib::Response& response) {
        if (!params.is_object() || !params.contains("uri") ||
            !params["uri"].is_string()) {
            writeJson(
                response,
                jsonRpcError(id, -32602, "Resource URI is required"));
            return;
        }
        try {
            const std::string uri = params["uri"].get<std::string>();
            auto future = dispatcher_.submit(
                [this, uri]() { return bridge_.readResource(uri); });
            Json resourceResult = future.get();
            if (exceedsResultBudget(resourceResult)) {
                writeJson(
                    response,
                    jsonRpcError(
                        id,
                        -32005,
                        "Resource result exceeded the 16 MiB response budget",
                        {{"maximum_bytes", kMaxSerializedResultBytes}}));
                return;
            }
            writeJson(response, jsonRpcResult(id, std::move(resourceResult)));
        } catch (const std::exception& exception) {
            writeJson(
                response,
                jsonRpcError(id, -32004, "Resource read failed", exception.what()));
        }
    }

    void handlePromptGet(
        const Json& id, const Json& params, httplib::Response& response) const {
        if (!params.is_object() || !params.contains("name") ||
            !params["name"].is_string()) {
            writeJson(
                response,
                jsonRpcError(id, -32602, "Prompt name is required"));
            return;
        }
        const std::string name = params["name"].get<std::string>();
        const Json arguments = params.value("arguments", Json::object());
        const auto prompts = bridge_.catalog().value("prompts", Json::array());
        const auto found = std::find_if(
            prompts.begin(), prompts.end(), [&name](const Json& prompt) {
                return prompt.value("name", "") == name;
            });
        if (found == prompts.end()) {
            writeJson(
                response,
                jsonRpcError(id, -32602, "Unknown prompt", {{"name", name}}));
            return;
        }

        std::string message = found->value("_message", "");
        if (arguments.is_object()) {
            for (const auto& [key, value] : arguments.items()) {
                const std::string replacement =
                    value.is_string() ? value.get<std::string>() : value.dump();
                message = replaceAll(message, "{{" + key + "}}", replacement);
            }
        }
        writeJson(
            response,
            jsonRpcResult(
                id,
                {
                    {"description", found->value("description", "")},
                    {"messages",
                     {{{"role", "user"},
                       {"content", {{"type", "text"}, {"text", message}}}}}},
                }));
    }

    void writeDiscoveryFile() {
        std::filesystem::path base;
        const std::string localAppData = environmentValue("LOCALAPPDATA");
        if (!localAppData.empty()) {
            base = std::filesystem::path(localAppData) / "MayaMCP";
        } else {
            base = std::filesystem::temp_directory_path() / "MayaMCP";
        }
        std::filesystem::create_directories(base);

        const DWORD pid = GetCurrentProcessId();
        discoveryFile_ =
            base / ("server-" + std::to_string(pid) + ".json");
        currentFile_ = base / "current.json";
        const Json discovery{
            {"schemaVersion", 1},
            {"pid", pid},
            {"url", endpoint_},
            {"token", token_},
            {"protocolVersion", kProtocolVersion},
            {"pluginVersion", MAYA_MCP_VERSION},
            {"scriptExecutionEnabled",
             environmentFlag("MAYA_MCP_ALLOW_UNSAFE_CODE")},
            {"unsafeCodeEnabled", environmentFlag("MAYA_MCP_ALLOW_UNSAFE_CODE")},
        };
        writeJsonFileAtomically(discoveryFile_, discovery);
        writeJsonFileAtomically(currentFile_, discovery);
    }

    void cleanupDiscoveryFile() {
        std::error_code error;
        if (!discoveryFile_.empty()) {
            std::filesystem::remove(discoveryFile_, error);
        }
        error.clear();
        if (!currentFile_.empty() &&
            std::filesystem::exists(currentFile_, error)) {
            try {
                std::ifstream stream(currentFile_, std::ios::binary);
                const Json current = Json::parse(stream);
                if (current.value("pid", 0UL) == GetCurrentProcessId()) {
                    std::filesystem::remove(currentFile_, error);
                }
            } catch (...) {
                // A stale discovery file is harmless and may belong to another Maya.
            }
        }
        discoveryFile_.clear();
        currentFile_.clear();
    }

    MainThreadDispatcher& dispatcher_;
    PythonBridge& bridge_;
    mutable std::mutex lifecycleMutex_;
    mutable std::mutex sessionsMutex_;
    std::unordered_map<std::string, Session> sessions_;
    std::unique_ptr<httplib::Server> http_;
    std::thread listener_;
    std::atomic<bool> running_{false};
    int port_{0};
    std::string endpoint_;
    std::string token_;
    std::string lastError_;
    std::filesystem::path discoveryFile_;
    std::filesystem::path currentFile_;
};

McpServer::McpServer(
    MainThreadDispatcher& dispatcher, PythonBridge& bridge)
    : impl_(std::make_unique<Impl>(dispatcher, bridge)) {}

McpServer::~McpServer() = default;

bool McpServer::start(std::string& error) {
    return impl_->start(error);
}

void McpServer::stop() {
    impl_->stop();
}

bool McpServer::running() const {
    return impl_->running();
}

McpServer::Json McpServer::status() const {
    return impl_->status();
}

}  // namespace maya_mcp
