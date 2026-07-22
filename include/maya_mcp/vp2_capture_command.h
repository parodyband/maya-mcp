#pragma once

#include <maya/MStatus.h>

#include <string>

class MFnPlugin;

namespace maya_mcp {

const char* vp2CaptureCommandName();
MStatus registerVp2CaptureCommand(MFnPlugin& plugin);
MStatus deregisterVp2CaptureCommand(MFnPlugin& plugin);
bool cleanupVp2CaptureCallbacks(std::string& error);

}  // namespace maya_mcp
