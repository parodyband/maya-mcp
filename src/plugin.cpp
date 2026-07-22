#include "maya_mcp/main_thread_dispatcher.h"
#include "maya_mcp/mcp_server.h"
#include "maya_mcp/python_bridge.h"
#include "maya_mcp/vp2_capture_command.h"

#include <maya/MArgList.h>
#include <maya/MFnPlugin.h>
#include <maya/MGlobal.h>
#include <maya/MPxCommand.h>
#include <maya/MStatus.h>
#include <maya/MString.h>

#include <array>
#include <cstddef>
#include <exception>
#include <memory>
#include <new>
#include <string>

namespace {

constexpr const char* kStatusCommand = "mayaMcpStatus";
constexpr const char* kStartCommand = "mayaMcpStart";
constexpr const char* kStopCommand = "mayaMcpStop";
constexpr const char* kPumpCommand = "mayaMcpPump";

std::unique_ptr<maya_mcp::MainThreadDispatcher> gDispatcher;
std::unique_ptr<maya_mcp::PythonBridge> gPythonBridge;
std::unique_ptr<maya_mcp::McpServer> gServer;

enum CommandIndex : std::size_t {
    kStatusIndex = 0,
    kStartIndex,
    kStopIndex,
    kPumpIndex,
    kVp2CaptureIndex,
    kCommandCount,
};

constexpr std::array<const char*, kCommandCount> kCommandNames{
    kStatusCommand,
    kStartCommand,
    kStopCommand,
    kPumpCommand,
    "mayaMcpVp2Capture",
};
std::array<bool, kCommandCount> gCommandRegistered{};

template <typename Function>
MStatus commandBoundary(const char* name, Function&& function) noexcept {
    try {
        return function();
    } catch (const std::exception& exception) {
        try {
            MGlobal::displayError(
                MString("maya-mcp command ") + name +
                " aborted after an exception: " + exception.what());
        } catch (...) {
        }
    } catch (...) {
        try {
            MGlobal::displayError(
                MString("maya-mcp command ") + name +
                " aborted after an unknown exception");
        } catch (...) {
        }
    }
    return MS::kFailure;
}

class MayaMcpStatusCommand final : public MPxCommand {
public:
    static void* creator() {
        return new (std::nothrow) MayaMcpStatusCommand();
    }

    MStatus doIt(const MArgList&) override {
        return commandBoundary(kStatusCommand, [this]() {
            if (gServer == nullptr) {
                return MStatus(MS::kFailure);
            }
            setResult(MString(gServer->status().dump().c_str()));
            return MStatus(MS::kSuccess);
        });
    }
};

class MayaMcpStartCommand final : public MPxCommand {
public:
    static void* creator() {
        return new (std::nothrow) MayaMcpStartCommand();
    }

    MStatus doIt(const MArgList&) override {
        return commandBoundary(kStartCommand, [this]() {
            if (gServer == nullptr) {
                return MStatus(MS::kFailure);
            }
            if (gDispatcher != nullptr && gDispatcher->executing()) {
                MGlobal::displayError(
                    "maya-mcp: cannot start the server from inside an MCP tool call");
                return MStatus(MS::kFailure);
            }
            std::string error;
            if (!gServer->start(error)) {
                MGlobal::displayError(MString("maya-mcp: ") + error.c_str());
                return MStatus(MS::kFailure);
            }
            setResult(MString(gServer->status().dump().c_str()));
            return MStatus(MS::kSuccess);
        });
    }
};

class MayaMcpStopCommand final : public MPxCommand {
public:
    static void* creator() {
        return new (std::nothrow) MayaMcpStopCommand();
    }

    MStatus doIt(const MArgList&) override {
        return commandBoundary(kStopCommand, [this]() {
            if (gDispatcher != nullptr && gDispatcher->executing()) {
                MGlobal::displayError(
                    "maya-mcp: cannot stop the server from inside an MCP tool call");
                return MStatus(MS::kFailure);
            }
            if (gServer != nullptr) {
                gServer->stop();
            }
            setResult("stopped");
            return MStatus(MS::kSuccess);
        });
    }
};

class MayaMcpPumpCommand final : public MPxCommand {
public:
    static void* creator() {
        return new (std::nothrow) MayaMcpPumpCommand();
    }

    MStatus doIt(const MArgList&) override {
        return commandBoundary(kPumpCommand, [this]() {
            if (gDispatcher == nullptr) {
                return MStatus(MS::kFailure);
            }
            if (gDispatcher->executing()) {
                MGlobal::displayError(
                    "maya-mcp: nested main-thread pumping is not allowed");
                return MStatus(MS::kFailure);
            }
            setResult(static_cast<int>(gDispatcher->pump(128)));
            return MStatus(MS::kSuccess);
        });
    }
};

void appendError(std::string& error, const std::string& message) {
    if (!error.empty()) {
        error += "; ";
    }
    error += message;
}

MStatus registerCommandAt(MFnPlugin& plugin, const std::size_t index) {
    switch (index) {
        case kStatusIndex:
            return plugin.registerCommand(kStatusCommand,
                                          MayaMcpStatusCommand::creator);
        case kStartIndex:
            return plugin.registerCommand(kStartCommand,
                                          MayaMcpStartCommand::creator);
        case kStopIndex:
            return plugin.registerCommand(kStopCommand,
                                          MayaMcpStopCommand::creator);
        case kPumpIndex:
            return plugin.registerCommand(kPumpCommand,
                                          MayaMcpPumpCommand::creator);
        case kVp2CaptureIndex:
            return maya_mcp::registerVp2CaptureCommand(plugin);
        default: return MS::kInvalidParameter;
    }
}

MStatus deregisterCommandAt(MFnPlugin& plugin, const std::size_t index) {
    if (index == kVp2CaptureIndex) {
        return maya_mcp::deregisterVp2CaptureCommand(plugin);
    }
    return plugin.deregisterCommand(kCommandNames[index]);
}

bool deregisterCommands(
    MFnPlugin& plugin,
    const std::array<bool, kCommandCount>& desired,
    std::string& error) {
    bool succeeded = true;
    for (std::size_t offset = 0; offset < kCommandCount; ++offset) {
        const std::size_t index = kCommandCount - 1U - offset;
        if (!desired[index] || !gCommandRegistered[index]) {
            continue;
        }
        const MStatus status = deregisterCommandAt(plugin, index);
        if (status) {
            gCommandRegistered[index] = false;
        } else {
            succeeded = false;
            appendError(
                error,
                std::string("could not deregister ") + kCommandNames[index] +
                    ": " + status.errorString().asChar());
        }
    }
    return succeeded;
}

bool restoreCommands(
    MFnPlugin& plugin,
    const std::array<bool, kCommandCount>& desired,
    std::string& error) {
    bool succeeded = true;
    for (std::size_t index = 0; index < kCommandCount; ++index) {
        if (!desired[index] || gCommandRegistered[index]) {
            continue;
        }
        const MStatus status = registerCommandAt(plugin, index);
        if (status) {
            gCommandRegistered[index] = true;
        } else {
            succeeded = false;
            appendError(
                error,
                std::string("could not restore ") + kCommandNames[index] +
                    ": " + status.errorString().asChar());
        }
    }
    return succeeded;
}

bool destroyState(std::string& error) {
    const bool serverWasRunning = gServer != nullptr && gServer->running();
    try {
        if (gServer != nullptr) {
            gServer->stop();
        }
    } catch (const std::exception& exception) {
        error = std::string("could not stop the MCP server: ") + exception.what();
        return false;
    } catch (...) {
        error = "could not stop the MCP server: unknown exception";
        return false;
    }

    if (gDispatcher != nullptr) {
        std::string uninstallError;
        bool uninstalled = false;
        try {
            uninstalled = gDispatcher->uninstall(uninstallError);
        } catch (const std::exception& exception) {
            uninstallError = exception.what();
        } catch (...) {
            uninstallError = "unknown dispatcher teardown exception";
        }
        if (!uninstalled) {
            gDispatcher->resume();
            error = std::string("could not remove the Maya timer callback: ") +
                    uninstallError;
            if (serverWasRunning && gServer != nullptr) {
                std::string restartError;
                try {
                    if (!gServer->start(restartError)) {
                        appendError(error,
                                    std::string("server restart failed: ") +
                                        restartError);
                    }
                } catch (const std::exception& exception) {
                    appendError(
                        error,
                        std::string("server restart threw: ") + exception.what());
                } catch (...) {
                    appendError(error, "server restart threw an unknown exception");
                }
            }
            return false;
        }
    }

    if (gPythonBridge != nullptr) {
        gPythonBridge->shutdown();
    }
    gServer.reset();
    gPythonBridge.reset();
    gDispatcher.reset();
    return true;
}

MStatus failInitialization(MFnPlugin& plugin, const std::string& reason,
                           const MStatus originalStatus = MS::kFailure) {
    MGlobal::displayError(MString("maya-mcp initialization failed: ") +
                          reason.c_str());
    const auto desired = gCommandRegistered;
    std::string rollbackError;
    std::string captureError;
    bool safeToUnload = maya_mcp::cleanupVp2CaptureCallbacks(captureError);
    if (!safeToUnload) {
        appendError(rollbackError,
                    std::string("VP2 callback cleanup failed: ") + captureError);
    }
    if (safeToUnload &&
        !deregisterCommands(plugin, desired, rollbackError)) {
        safeToUnload = false;
    }
    if (safeToUnload) {
        std::string destroyError;
        if (!destroyState(destroyError)) {
            safeToUnload = false;
            appendError(rollbackError, destroyError);
        }
    }
    if (!safeToUnload) {
        std::string restoreError;
        (void)restoreCommands(plugin, desired, restoreError);
        if (!restoreError.empty()) {
            appendError(rollbackError, restoreError);
        }
        MGlobal::displayError(
            MString("maya-mcp kept the DLL loaded because rollback was unsafe: ") +
            rollbackError.c_str());
        return MS::kSuccess;
    }
    return originalStatus;
}

MStatus initializePluginImpl(MObject object) {
    MFnPlugin plugin(
        object, "Austin Crane", MAYA_MCP_VERSION, MAYA_MCP_MAYA_VERSION);

    gDispatcher = std::make_unique<maya_mcp::MainThreadDispatcher>();
    std::string error;
    if (!gDispatcher->install(error)) {
        return failInitialization(
            plugin, std::string("dispatcher setup failed: ") + error);
    }

    gPythonBridge = std::make_unique<maya_mcp::PythonBridge>();
    if (!gPythonBridge->initialize(error)) {
        return failInitialization(
            plugin, std::string("Python runtime setup failed: ") + error);
    }

    gServer = std::make_unique<maya_mcp::McpServer>(
        *gDispatcher, *gPythonBridge);

    for (std::size_t index = 0; index < kCommandCount; ++index) {
        const MStatus status = registerCommandAt(plugin, index);
        if (!status) {
            return failInitialization(
                plugin,
                std::string("could not register ") + kCommandNames[index] +
                    ": " + status.errorString().asChar(),
                status);
        }
        gCommandRegistered[index] = true;
    }

    if (!gServer->start(error)) {
        return failInitialization(
            plugin, std::string("server startup failed: ") + error);
    }

    const auto statusJson = gServer->status();
    MGlobal::displayInfo(
        MString("maya-mcp ") + MAYA_MCP_VERSION + " listening at " +
        statusJson.value("endpoint", "").c_str());
    return MS::kSuccess;
}

MStatus uninitializePluginImpl(MObject object) {
    if (gDispatcher != nullptr && gDispatcher->executing()) {
        MGlobal::displayError(
            "maya-mcp: cannot unload the plug-in from inside an MCP tool call");
        return MS::kFailure;
    }

    std::string captureError;
    if (!maya_mcp::cleanupVp2CaptureCallbacks(captureError)) {
        MGlobal::displayError(
            MString("maya-mcp could not remove its VP2 callback: ") +
            captureError.c_str());
        return MS::kFailure;
    }

    MFnPlugin plugin(object);
    const auto desired = gCommandRegistered;
    std::string error;
    if (!deregisterCommands(plugin, desired, error)) {
        std::string restoreError;
        (void)restoreCommands(plugin, desired, restoreError);
        if (!restoreError.empty()) {
            appendError(error, restoreError);
        }
        MGlobal::displayError(
            MString("maya-mcp command teardown failed; plug-in remains loaded: ") +
            error.c_str());
        return MS::kFailure;
    }

    std::string destroyError;
    if (!destroyState(destroyError)) {
        std::string restoreError;
        (void)restoreCommands(plugin, desired, restoreError);
        if (!restoreError.empty()) {
            appendError(destroyError, restoreError);
        }
        MGlobal::displayError(
            MString("maya-mcp state teardown failed; plug-in remains loaded: ") +
            destroyError.c_str());
        return MS::kFailure;
    }
    return MS::kSuccess;
}

}  // namespace

MStatus initializePlugin(MObject object) {
    try {
        return initializePluginImpl(object);
    } catch (const std::exception& exception) {
        try {
            MFnPlugin plugin(object);
            return failInitialization(
                plugin,
                std::string("unhandled exception: ") + exception.what());
        } catch (...) {
            MGlobal::displayError(
                "maya-mcp initialization threw during emergency rollback; "
                "the DLL is being kept loaded");
            return MS::kSuccess;
        }
    } catch (...) {
        try {
            MFnPlugin plugin(object);
            return failInitialization(plugin, "unknown unhandled exception");
        } catch (...) {
            MGlobal::displayError(
                "maya-mcp initialization threw during emergency rollback; "
                "the DLL is being kept loaded");
            return MS::kSuccess;
        }
    }
}

MStatus uninitializePlugin(MObject object) {
    try {
        return uninitializePluginImpl(object);
    } catch (const std::exception& exception) {
        MGlobal::displayError(
            MString("maya-mcp unload aborted after an exception: ") +
            exception.what());
        return MS::kFailure;
    } catch (...) {
        MGlobal::displayError(
            "maya-mcp unload aborted after an unknown exception");
        return MS::kFailure;
    }
}
