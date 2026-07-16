#include "maya_mcp/main_thread_dispatcher.h"
#include "maya_mcp/mcp_server.h"
#include "maya_mcp/python_bridge.h"

#include <maya/MArgList.h>
#include <maya/MFnPlugin.h>
#include <maya/MGlobal.h>
#include <maya/MPxCommand.h>
#include <maya/MStatus.h>
#include <maya/MString.h>

#include <memory>
#include <string>

namespace {

constexpr const char* kStatusCommand = "mayaMcpStatus";
constexpr const char* kStartCommand = "mayaMcpStart";
constexpr const char* kStopCommand = "mayaMcpStop";
constexpr const char* kPumpCommand = "mayaMcpPump";

std::unique_ptr<maya_mcp::MainThreadDispatcher> gDispatcher;
std::unique_ptr<maya_mcp::PythonBridge> gPythonBridge;
std::unique_ptr<maya_mcp::McpServer> gServer;

class MayaMcpStatusCommand final : public MPxCommand {
public:
    static void* creator() { return new MayaMcpStatusCommand(); }

    MStatus doIt(const MArgList&) override {
        if (gServer == nullptr) {
            return MS::kFailure;
        }
        setResult(MString(gServer->status().dump().c_str()));
        return MS::kSuccess;
    }
};

class MayaMcpStartCommand final : public MPxCommand {
public:
    static void* creator() { return new MayaMcpStartCommand(); }

    MStatus doIt(const MArgList&) override {
        if (gServer == nullptr) {
            return MS::kFailure;
        }
        if (gDispatcher != nullptr && gDispatcher->executing()) {
            MGlobal::displayError(
                "maya-mcp: cannot start the server from inside an MCP tool call");
            return MS::kFailure;
        }
        std::string error;
        if (!gServer->start(error)) {
            MGlobal::displayError(MString("maya-mcp: ") + error.c_str());
            return MS::kFailure;
        }
        setResult(MString(gServer->status().dump().c_str()));
        return MS::kSuccess;
    }
};

class MayaMcpStopCommand final : public MPxCommand {
public:
    static void* creator() { return new MayaMcpStopCommand(); }

    MStatus doIt(const MArgList&) override {
        if (gDispatcher != nullptr && gDispatcher->executing()) {
            MGlobal::displayError(
                "maya-mcp: cannot stop the server from inside an MCP tool call");
            return MS::kFailure;
        }
        if (gServer != nullptr) {
            gServer->stop();
        }
        setResult("stopped");
        return MS::kSuccess;
    }
};

class MayaMcpPumpCommand final : public MPxCommand {
public:
    static void* creator() { return new MayaMcpPumpCommand(); }

    MStatus doIt(const MArgList&) override {
        if (gDispatcher == nullptr) {
            return MS::kFailure;
        }
        if (gDispatcher->executing()) {
            MGlobal::displayError(
                "maya-mcp: nested main-thread pumping is not allowed");
            return MS::kFailure;
        }
        setResult(static_cast<int>(gDispatcher->pump(128)));
        return MS::kSuccess;
    }
};

bool destroyState() {
    if (gServer != nullptr) {
        gServer->stop();
    }
    if (gPythonBridge != nullptr) {
        gPythonBridge->shutdown();
    }
    if (gDispatcher != nullptr) {
        std::string error;
        if (!gDispatcher->uninstall(error)) {
            MGlobal::displayError(
                MString("maya-mcp could not remove its Maya callback: ") +
                error.c_str());
            return false;
        }
    }
    gServer.reset();
    gPythonBridge.reset();
    gDispatcher.reset();
    return true;
}

}  // namespace

MStatus initializePlugin(MObject object) {
    MFnPlugin plugin(object, "Austin Crane", MAYA_MCP_VERSION, "2027");

    gDispatcher = std::make_unique<maya_mcp::MainThreadDispatcher>();
    std::string error;
    if (!gDispatcher->install(error)) {
        MGlobal::displayError(MString("maya-mcp dispatcher: ") + error.c_str());
        destroyState();
        return MS::kFailure;
    }

    gPythonBridge = std::make_unique<maya_mcp::PythonBridge>();
    if (!gPythonBridge->initialize(error)) {
        MGlobal::displayError(MString("maya-mcp Python runtime: ") + error.c_str());
        destroyState();
        return MS::kFailure;
    }

    gServer = std::make_unique<maya_mcp::McpServer>(
        *gDispatcher, *gPythonBridge);

    MStatus status =
        plugin.registerCommand(kStatusCommand, MayaMcpStatusCommand::creator);
    if (!status) {
        destroyState();
        return status;
    }
    status = plugin.registerCommand(kStartCommand, MayaMcpStartCommand::creator);
    if (!status) {
        plugin.deregisterCommand(kStatusCommand);
        destroyState();
        return status;
    }
    status = plugin.registerCommand(kStopCommand, MayaMcpStopCommand::creator);
    if (!status) {
        plugin.deregisterCommand(kStartCommand);
        plugin.deregisterCommand(kStatusCommand);
        destroyState();
        return status;
    }
    status = plugin.registerCommand(kPumpCommand, MayaMcpPumpCommand::creator);
    if (!status) {
        plugin.deregisterCommand(kStopCommand);
        plugin.deregisterCommand(kStartCommand);
        plugin.deregisterCommand(kStatusCommand);
        destroyState();
        return status;
    }

    if (!gServer->start(error)) {
        MGlobal::displayError(MString("maya-mcp server: ") + error.c_str());
        plugin.deregisterCommand(kPumpCommand);
        plugin.deregisterCommand(kStopCommand);
        plugin.deregisterCommand(kStartCommand);
        plugin.deregisterCommand(kStatusCommand);
        destroyState();
        return MS::kFailure;
    }

    const auto statusJson = gServer->status();
    MGlobal::displayInfo(
        MString("maya-mcp ") + MAYA_MCP_VERSION + " listening at " +
        statusJson.value("endpoint", "").c_str());
    return MS::kSuccess;
}

MStatus uninitializePlugin(MObject object) {
    if (gDispatcher != nullptr && gDispatcher->executing()) {
        MGlobal::displayError(
            "maya-mcp: cannot unload the plug-in from inside an MCP tool call");
        return MS::kFailure;
    }
    if (!destroyState()) {
        return MS::kFailure;
    }

    MFnPlugin plugin(object);
    MStatus firstFailure = MS::kSuccess;
    for (const char* command :
         {kPumpCommand, kStopCommand, kStartCommand, kStatusCommand}) {
        const MStatus status = plugin.deregisterCommand(command);
        if (!status && firstFailure) {
            firstFailure = status;
        }
    }
    return firstFailure;
}
