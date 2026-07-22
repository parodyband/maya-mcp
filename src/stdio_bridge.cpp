#include <httplib.h>
#include <nlohmann/json.hpp>

#include <windows.h>

#include <fcntl.h>
#include <io.h>

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <optional>
#include <regex>
#include <stdexcept>
#include <string>
#include <system_error>
#include <utility>
#include <vector>

namespace {

using Json = nlohmann::json;

constexpr std::size_t kMaximumMessageBytes = 8U * 1024U * 1024U;
constexpr std::size_t kMaximumDiscoveryBytes = 64U * 1024U;

struct Discovery {
    DWORD pid{0};
    std::string host;
    int port{0};
    std::string path;
    std::string token;
    std::string protocolVersion;
    std::filesystem::path source;
};

struct Options {
    std::optional<std::filesystem::path> discoveryFile;
};

std::string environmentValue(const char* name) {
    const DWORD required = GetEnvironmentVariableA(name, nullptr, 0);
    if (required == 0) {
        return {};
    }
    std::string value(required, '\0');
    const DWORD written = GetEnvironmentVariableA(name, value.data(), required);
    if (written == 0 || written >= required) {
        return {};
    }
    value.resize(written);
    return value;
}

std::filesystem::path discoveryDirectory() {
    const std::string localAppData = environmentValue("LOCALAPPDATA");
    if (!localAppData.empty()) {
        return std::filesystem::path(localAppData) / "MayaMCP";
    }
    return std::filesystem::temp_directory_path() / "MayaMCP";
}

bool processIsAlive(const DWORD pid) {
    if (pid == 0) {
        return false;
    }
    HANDLE process = OpenProcess(SYNCHRONIZE, FALSE, pid);
    if (process == nullptr) {
        return false;
    }
    const DWORD waitResult = WaitForSingleObject(process, 0);
    CloseHandle(process);
    return waitResult == WAIT_TIMEOUT;
}

bool validHeaderValue(const std::string& value) {
    return !value.empty() && value.size() <= 1024U &&
        std::none_of(value.begin(), value.end(), [](const unsigned char character) {
            return character == '\r' || character == '\n' || character == '\0';
        });
}

Discovery parseDiscovery(
    const std::filesystem::path& path,
    const bool requireLiveProcess) {
    std::error_code error;
    const std::uintmax_t size = std::filesystem::file_size(path, error);
    if (error || size == 0 || size > kMaximumDiscoveryBytes) {
        throw std::runtime_error("Discovery file has an invalid size");
    }

    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        throw std::runtime_error("Could not open discovery file");
    }
    const Json data = Json::parse(stream);
    if (!data.is_object() || data.value("schemaVersion", 0) != 1) {
        throw std::runtime_error("Discovery file schema is not supported");
    }

    const auto pidValue = data.value("pid", 0ULL);
    if (pidValue == 0 || pidValue > static_cast<unsigned long long>(MAXDWORD)) {
        throw std::runtime_error("Discovery file process id is invalid");
    }
    const DWORD pid = static_cast<DWORD>(pidValue);
    if (requireLiveProcess && !processIsAlive(pid)) {
        throw std::runtime_error("Discovery file belongs to a Maya process that is no longer running");
    }

    const std::string url = data.value("url", "");
    static const std::regex endpointPattern(
        R"(^http://127\.0\.0\.1:([0-9]{1,5})(/mcp)$)",
        std::regex::ECMAScript);
    std::smatch endpointMatch;
    if (!std::regex_match(url, endpointMatch, endpointPattern)) {
        throw std::runtime_error("Discovery endpoint is not an approved loopback MCP URL");
    }
    const int port = std::stoi(endpointMatch[1].str());
    if (port < 1 || port > 65535) {
        throw std::runtime_error("Discovery endpoint port is invalid");
    }

    const std::string token = data.value("token", "");
    const std::string protocolVersion = data.value("protocolVersion", "");
    if (token.size() < 32U || !validHeaderValue(token)) {
        throw std::runtime_error("Discovery bearer token is invalid");
    }
    if (!validHeaderValue(protocolVersion)) {
        throw std::runtime_error("Discovery protocol version is invalid");
    }

    return Discovery{
        pid,
        "127.0.0.1",
        port,
        endpointMatch[2].str(),
        token,
        protocolVersion,
        path,
    };
}

std::optional<Discovery> tryDiscovery(
    const std::filesystem::path& path,
    const bool requireLiveProcess) {
    try {
        return parseDiscovery(path, requireLiveProcess);
    } catch (const std::exception&) {
        return std::nullopt;
    }
}

Discovery findDiscovery(const Options& options) {
    if (options.discoveryFile.has_value()) {
        return parseDiscovery(*options.discoveryFile, false);
    }

    const std::filesystem::path directory = discoveryDirectory();
    const std::filesystem::path current = directory / "current.json";
    if (const auto discovery = tryDiscovery(current, false)) {
        return *discovery;
    }

    std::vector<std::pair<std::filesystem::file_time_type, Discovery>> candidates;
    std::error_code error;
    for (std::filesystem::directory_iterator iterator(directory, error), end;
         !error && iterator != end;
         iterator.increment(error)) {
        if (!iterator->is_regular_file(error)) {
            error.clear();
            continue;
        }
        const std::string name = iterator->path().filename().string();
        if (name.rfind("server-", 0) != 0 || iterator->path().extension() != ".json") {
            continue;
        }
        if (const auto discovery = tryDiscovery(iterator->path(), true)) {
            const auto modified = iterator->last_write_time(error);
            if (!error) {
                candidates.emplace_back(modified, *discovery);
            }
            error.clear();
        }
    }
    if (candidates.empty()) {
        throw std::runtime_error(
            "No running Maya MCP server was found. Open Maya and load maya_mcp first.");
    }
    return std::max_element(
        candidates.begin(),
        candidates.end(),
        [](const auto& left, const auto& right) {
            return left.first < right.first;
        })->second;
}

Json requestId(const Json& message) {
    if (message.is_object() && message.contains("id") &&
        (message["id"].is_string() || message["id"].is_number())) {
        return message["id"];
    }
    return nullptr;
}

void writeProtocolError(const Json& id, const std::string& message) {
    Json error{
        {"jsonrpc", "2.0"},
        {"id", id},
        {"error", {{"code", -32000}, {"message", message}}},
    };
    std::cout << error.dump() << '\n';
    std::cout.flush();
}

class Bridge {
public:
    explicit Bridge(Options options) : options_(std::move(options)) {}
    ~Bridge() { closeSession(); }

    void handle(const std::string& line) {
        Json message;
        try {
            message = Json::parse(line);
        } catch (const std::exception&) {
            writeProtocolError(nullptr, "The stdio request is not valid JSON.");
            return;
        }

        const Json id = requestId(message);
        const bool notification = !message.is_object() || !message.contains("id");
        try {
            if (!message.is_object() || message.value("jsonrpc", "") != "2.0" ||
                !message.contains("method") || !message["method"].is_string()) {
                throw std::runtime_error("The stdio request is not a valid JSON-RPC 2.0 message.");
            }
            const std::string method = message["method"].get<std::string>();
            if (method == "initialize" || !discovery_.has_value()) {
                closeSession();
                discovery_ = findDiscovery(options_);
            }
            forward(line, method, notification);
        } catch (const std::exception& exception) {
            std::cerr << "maya-mcp-bridge: " << exception.what() << '\n';
            if (!notification) {
                writeProtocolError(id, exception.what());
            }
        }
    }

private:
    void closeSession() noexcept {
        if (!discovery_.has_value() || sessionId_.empty()) {
            return;
        }
        try {
            httplib::Client client(discovery_->host, discovery_->port);
            client.set_connection_timeout(1, 0);
            client.set_read_timeout(1, 0);
            httplib::Headers headers{
                {"Authorization", "Bearer " + discovery_->token},
                {"MCP-Protocol-Version", discovery_->protocolVersion},
                {"MCP-Session-Id", sessionId_},
            };
            (void)client.Delete(discovery_->path, headers);
        } catch (...) {
        }
        sessionId_.clear();
    }

    void forward(
        const std::string& body,
        const std::string& method,
        const bool notification) {
        if (!discovery_.has_value()) {
            throw std::runtime_error("No Maya MCP discovery data is available.");
        }
        httplib::Client client(discovery_->host, discovery_->port);
        client.set_connection_timeout(3, 0);
        client.set_read_timeout(600, 0);
        client.set_write_timeout(30, 0);

        httplib::Headers headers{
            {"Authorization", "Bearer " + discovery_->token},
            {"Accept", "application/json, text/event-stream"},
            {"MCP-Protocol-Version", discovery_->protocolVersion},
        };
        if (!sessionId_.empty() && method != "initialize") {
            headers.emplace("MCP-Session-Id", sessionId_);
        }

        const auto response = client.Post(
            discovery_->path,
            headers,
            body,
            "application/json");
        if (!response) {
            throw std::runtime_error(
                "Could not reach Maya MCP. Confirm Maya is open and the plug-in is loaded.");
        }
        if (response->status != 200 && response->status != 202) {
            throw std::runtime_error(
                "Maya MCP rejected the bridge request with HTTP " +
                std::to_string(response->status) + ".");
        }
        if (method == "initialize") {
            sessionId_ = response->get_header_value("MCP-Session-Id");
            if (!validHeaderValue(sessionId_)) {
                throw std::runtime_error("Maya MCP did not return a valid session id.");
            }
        }
        if (notification || response->status == 202) {
            return;
        }
        if (response->body.empty() || response->body.size() > kMaximumMessageBytes) {
            throw std::runtime_error("Maya MCP returned an invalid response size.");
        }
        try {
            (void)Json::parse(response->body);
        } catch (const std::exception&) {
            throw std::runtime_error("Maya MCP returned an invalid JSON response.");
        }
        std::cout << response->body << '\n';
        std::cout.flush();
    }

    Options options_;
    std::optional<Discovery> discovery_;
    std::string sessionId_;
};

Options parseOptions(const int argc, char** argv) {
    Options options;
    const std::string environmentDiscovery = environmentValue("MAYA_MCP_DISCOVERY_FILE");
    if (!environmentDiscovery.empty()) {
        options.discoveryFile = std::filesystem::path(environmentDiscovery);
    }
    for (int index = 1; index < argc; ++index) {
        const std::string argument = argv[index];
        if (argument == "--discovery-file") {
            if (++index >= argc) {
                throw std::runtime_error("--discovery-file requires a path");
            }
            options.discoveryFile = std::filesystem::path(argv[index]);
        } else if (argument == "--version") {
            std::cout << "maya-mcp-bridge " MAYA_MCP_VERSION "\n";
            std::exit(0);
        } else {
            throw std::runtime_error("Unknown argument: " + argument);
        }
    }
    return options;
}

} // namespace

int main(const int argc, char** argv) {
    try {
        _setmode(_fileno(stdin), _O_BINARY);
        _setmode(_fileno(stdout), _O_BINARY);
        Bridge bridge(parseOptions(argc, argv));
        std::string line;
        while (std::getline(std::cin, line)) {
            if (!line.empty() && line.back() == '\r') {
                line.pop_back();
            }
            if (line.empty()) {
                continue;
            }
            if (line.size() > kMaximumMessageBytes) {
                writeProtocolError(nullptr, "The stdio request exceeds the 8 MiB limit.");
                continue;
            }
            bridge.handle(line);
        }
        return 0;
    } catch (const std::exception& exception) {
        std::cerr << "maya-mcp-bridge: " << exception.what() << '\n';
        return 1;
    }
}
