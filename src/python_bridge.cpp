#include "maya_mcp/python_bridge.h"

#include <maya/MGlobal.h>
#include <maya/MStatus.h>
#include <maya/MString.h>

#include <array>
#include <cstdint>
#include <stdexcept>
#include <string>

namespace maya_mcp {
namespace {

std::string encodeBase64(const std::string& input) {
    constexpr std::array<char, 64> alphabet{
        'A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M',
        'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z',
        'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm',
        'n', 'o', 'p', 'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z',
        '0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '+', '/'};

    std::string output;
    output.reserve(((input.size() + 2U) / 3U) * 4U);
    std::uint32_t buffer = 0;
    int bits = -6;
    for (const unsigned char byte : input) {
        buffer = (buffer << 8U) | byte;
        bits += 8;
        while (bits >= 0) {
            output.push_back(alphabet[(buffer >> bits) & 0x3FU]);
            bits -= 6;
        }
    }
    if (bits > -6) {
        output.push_back(alphabet[(buffer << 8U >> (bits + 8)) & 0x3FU]);
    }
    while ((output.size() % 4U) != 0U) {
        output.push_back('=');
    }
    return output;
}

PythonBridge::Json parsePythonJson(const MString& value, const char* operation) {
    try {
        return PythonBridge::Json::parse(value.asChar());
    } catch (const std::exception& exception) {
        throw std::runtime_error(
            std::string(operation) + " returned invalid JSON: " + exception.what());
    }
}

}  // namespace

bool PythonBridge::initialize(std::string& error) {
    MStatus status;
    const MString result = MGlobal::executePythonCommandStringResult(
        "__import__('maya_mcp_runtime.catalog', "
        "fromlist=['catalog_json']).catalog_json()",
        false,
        false,
        &status);
    if (!status) {
        error = "Could not import maya_mcp_runtime.catalog: ";
        error += status.errorString().asChar();
        return false;
    }

    try {
        catalog_ = parsePythonJson(result, "catalog_json");
        if (!catalog_.is_object() || !catalog_.contains("tools") ||
            !catalog_["tools"].is_array()) {
            error = "The Python runtime returned an invalid Maya MCP catalog";
            catalog_ = Json::object();
            return false;
        }
    } catch (const std::exception& exception) {
        error = exception.what();
        catalog_ = Json::object();
        return false;
    }
    status = MGlobal::executePythonCommand(
        "from maya_mcp_runtime import state as _maya_mcp_state; "
        "_maya_mcp_state.install_callbacks()",
        false,
        false);
    if (!status) {
        error = "Could not install Maya MCP scene callbacks: ";
        error += status.errorString().asChar();
        catalog_ = Json::object();
        return false;
    }
    return true;
}

void PythonBridge::shutdown() noexcept {
    try {
        MGlobal::executePythonCommand(
            "from maya_mcp_runtime import state as _maya_mcp_state; "
            "_maya_mcp_state.shutdown_callbacks()",
            false,
            false);
    } catch (...) {
        // No exception may cross a Maya plug-in teardown boundary.
    }
}

PythonBridge::Json PythonBridge::callTool(
    const std::string& name, const Json& arguments) const {
    return callEncoded(
        "dispatch_base64", Json{{"name", name}, {"arguments", arguments}});
}

PythonBridge::Json PythonBridge::readResource(const std::string& uri) const {
    return callEncoded("read_resource_base64", Json{{"uri", uri}});
}

PythonBridge::Json PythonBridge::callEncoded(
    const char* functionName, const Json& payload) const {
    const std::string encoded = encodeBase64(payload.dump());
    const std::string command =
        "__import__('maya_mcp_runtime.dispatcher', "
        "fromlist=['" +
        std::string(functionName) + "'])." + functionName + "('" + encoded + "')";

    MStatus status;
    const MString result = MGlobal::executePythonCommandStringResult(
        command.c_str(), false, false, &status);
    if (!status) {
        throw std::runtime_error(
            std::string("Maya Python dispatch failed: ") +
            status.errorString().asChar());
    }
    return parsePythonJson(result, functionName);
}

}  // namespace maya_mcp
