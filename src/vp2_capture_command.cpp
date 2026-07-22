#include "maya_mcp/vp2_capture_command.h"

#define MNoPluginEntry
#define MNoVersionString
#include <maya/MFnPlugin.h>
#undef MNoVersionString
#undef MNoPluginEntry

#include <maya/M3dView.h>
#include <maya/MArgDatabase.h>
#include <maya/MArgList.h>
#include <maya/MDrawContext.h>
#include <maya/MGlobal.h>
#include <maya/MPxCommand.h>
#include <maya/MRenderTargetManager.h>
#include <maya/MSyntax.h>
#include <maya/MViewport2Renderer.h>

#include <nlohmann/json.hpp>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <exception>
#include <limits>
#include <memory>
#include <new>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace maya_mcp {
namespace {

using Json = nlohmann::json;

constexpr const char* kCommandName = "mayaMcpVp2Capture";
constexpr const char* kRequestFlag = "-r";
constexpr const char* kRequestFlagLong = "-request";
constexpr unsigned int kDefaultMaxDimension = 512;
constexpr unsigned int kHardMaxDimension = 1024;
constexpr std::size_t kBase64Budget = 4U * 1024U * 1024U;

std::atomic<std::uint64_t> gCaptureSequence{0};
std::atomic<unsigned int> gCaptureExecutionDepth{0};

struct Request {
    bool depth = true;
    bool color = false;
    bool objectId = false;
    unsigned int maxDimension = kDefaultMaxDimension;
};

struct FormatInfo {
    const char* name = "unknown";
    unsigned int bytesPerPixel = 0;
    const char* layout = "unknown";
};

struct SampledPass {
    Json value;
    std::size_t base64Chars = 0;
};

struct CaptureState {
    Request request;
    std::string targetPrefix;
    std::size_t base64BudgetPerPass = 0;
    bool callbackRan = false;
    bool failed = false;
    std::string errorCode = "CAPTURE_FAILED";
    std::string errorMessage = "unknown native capture failure";
    SampledPass depth;
    SampledPass color;
};

struct PendingNotification {
    MString name;
    MString semantic;
    std::unique_ptr<CaptureState> capture;
    bool installed = false;
};

std::unique_ptr<PendingNotification> gPendingNotification;

class CaptureExecutionGuard final {
public:
    CaptureExecutionGuard()
        : exclusive_(gCaptureExecutionDepth.fetch_add(1) == 0) {}
    ~CaptureExecutionGuard() { gCaptureExecutionDepth.fetch_sub(1); }

    [[nodiscard]] bool exclusive() const noexcept { return exclusive_; }

private:
    bool exclusive_;
};

bool removePendingNotification(std::unique_ptr<CaptureState>* capture,
                               std::string& error) {
    if (gPendingNotification == nullptr) {
        return true;
    }
    if (gPendingNotification->installed) {
        MHWRender::MRenderer* renderer = MHWRender::MRenderer::theRenderer();
        if (renderer == nullptr) {
            error =
                "Viewport 2.0 renderer is unavailable while removing a pending notification";
            return false;
        }
        const MStatus status = renderer->removeNotification(
            gPendingNotification->name, gPendingNotification->semantic);
        if (!status) {
            error = status.errorString().asChar();
            return false;
        }
        gPendingNotification->installed = false;
    }
    if (capture != nullptr) {
        *capture = std::move(gPendingNotification->capture);
    }
    gPendingNotification.reset();
    return true;
}

bool cleanupPendingNotification(std::string& error) {
    return removePendingNotification(nullptr, error);
}

Json capabilities() {
    return {
        {"depth", {{"supported", true}}},
        {"color", {{"supported", true}, {"default", false}}},
        {"object_id",
         {{"supported", false},
          {"reason",
           "Maya does not expose a stable object-ID render target through "
           "this capture path."}}},
    };
}

Json limits(std::size_t used = 0) {
    return {
        {"default_max_dimension", kDefaultMaxDimension},
        {"hard_max_dimension", kHardMaxDimension},
        {"base64_budget_chars", kBase64Budget},
        {"base64_chars", used},
    };
}

Json errorResult(const char* code, const std::string& message,
                 bool retryable = false) {
    return {
        {"schema_version", 1},
        {"ok", false},
        {"error",
         {{"code", code}, {"message", message}, {"retryable", retryable}}},
        {"capabilities", capabilities()},
        {"limits", limits()},
    };
}

bool getStrictBoolean(const Json& value, const char* key, bool& destination,
                      std::string& error) {
    const auto iterator = value.find(key);
    if (iterator == value.end()) {
        return true;
    }
    if (!iterator->is_boolean()) {
        error = std::string("'") + key + "' must be a boolean";
        return false;
    }
    destination = iterator->get<bool>();
    return true;
}

bool parseRequest(const MString& encoded, Request& request, std::string& error) {
    Json value;
    try {
        value = encoded.length() == 0 ? Json::object()
                                      : Json::parse(encoded.asChar());
    } catch (const Json::exception& exception) {
        error = std::string("request is not valid JSON: ") + exception.what();
        return false;
    }
    if (!value.is_object()) {
        error = "request must be a JSON object";
        return false;
    }

    static const std::vector<std::string> allowed = {
        "depth", "color", "object_id", "max_dimension"};
    for (const auto& item : value.items()) {
        if (std::find(allowed.begin(), allowed.end(), item.key()) ==
            allowed.end()) {
            error = "unknown request field '" + item.key() + "'";
            return false;
        }
    }

    if (!getStrictBoolean(value, "depth", request.depth, error) ||
        !getStrictBoolean(value, "color", request.color, error) ||
        !getStrictBoolean(value, "object_id", request.objectId, error)) {
        return false;
    }

    const auto maxDimension = value.find("max_dimension");
    if (maxDimension != value.end()) {
        if (!maxDimension->is_number_unsigned()) {
            error = "'max_dimension' must be an unsigned integer";
            return false;
        }
        const auto parsed = maxDimension->get<std::uint64_t>();
        if (parsed < 1 || parsed > kHardMaxDimension) {
            error = "'max_dimension' must be between 1 and 1024";
            return false;
        }
        request.maxDimension = static_cast<unsigned int>(parsed);
    }

    if (!request.depth && !request.color && !request.objectId) {
        error = "at least one render pass must be requested";
        return false;
    }
    return true;
}

FormatInfo formatInfo(MHWRender::MRasterFormat format) {
    using namespace MHWRender;
    switch (format) {
        case kD24S8: return {"kD24S8", 4, "depth24_stencil8"};
        case kD24X8: return {"kD24X8", 4, "depth24_unused8"};
        case kD32_FLOAT: return {"kD32_FLOAT", 4, "depth_float32"};
        case kR24G8: return {"kR24G8", 4, "r24_g8"};
        case kR24X8: return {"kR24X8", 4, "r24_unused8"};
        case kDXT1_UNORM: return {"kDXT1_UNORM", 0, "block_compressed"};
        case kDXT1_UNORM_SRGB:
            return {"kDXT1_UNORM_SRGB", 0, "block_compressed"};
        case kDXT2_UNORM: return {"kDXT2_UNORM", 0, "block_compressed"};
        case kDXT2_UNORM_SRGB:
            return {"kDXT2_UNORM_SRGB", 0, "block_compressed"};
        case kDXT2_UNORM_PREALPHA:
            return {"kDXT2_UNORM_PREALPHA", 0, "block_compressed"};
        case kDXT3_UNORM: return {"kDXT3_UNORM", 0, "block_compressed"};
        case kDXT3_UNORM_SRGB:
            return {"kDXT3_UNORM_SRGB", 0, "block_compressed"};
        case kDXT3_UNORM_PREALPHA:
            return {"kDXT3_UNORM_PREALPHA", 0, "block_compressed"};
        case kDXT4_UNORM: return {"kDXT4_UNORM", 0, "block_compressed"};
        case kDXT4_SNORM: return {"kDXT4_SNORM", 0, "block_compressed"};
        case kDXT5_UNORM: return {"kDXT5_UNORM", 0, "block_compressed"};
        case kDXT5_SNORM: return {"kDXT5_SNORM", 0, "block_compressed"};
        case kBC6H_UF16: return {"kBC6H_UF16", 0, "block_compressed"};
        case kBC6H_SF16: return {"kBC6H_SF16", 0, "block_compressed"};
        case kBC7_UNORM: return {"kBC7_UNORM", 0, "block_compressed"};
        case kBC7_UNORM_SRGB:
            return {"kBC7_UNORM_SRGB", 0, "block_compressed"};
        case kR9G9B9E5_FLOAT:
            return {"kR9G9B9E5_FLOAT", 4, "rgb9_shared_exponent5"};
        case kR1_UNORM: return {"kR1_UNORM", 0, "bit_packed"};
        case kA8: return {"kA8", 1, "a8"};
        case kR8_UNORM: return {"kR8_UNORM", 1, "r8_unorm"};
        case kR8_SNORM: return {"kR8_SNORM", 1, "r8_snorm"};
        case kR8_UINT: return {"kR8_UINT", 1, "r8_uint"};
        case kR8_SINT: return {"kR8_SINT", 1, "r8_sint"};
        case kL8: return {"kL8", 1, "l8"};
        case kR16_FLOAT: return {"kR16_FLOAT", 2, "r16_float"};
        case kR16_UNORM: return {"kR16_UNORM", 2, "r16_unorm"};
        case kR16_SNORM: return {"kR16_SNORM", 2, "r16_snorm"};
        case kR16_UINT: return {"kR16_UINT", 2, "r16_uint"};
        case kR16_SINT: return {"kR16_SINT", 2, "r16_sint"};
        case kL16: return {"kL16", 2, "l16"};
        case kR8G8_UNORM: return {"kR8G8_UNORM", 2, "rg8_unorm"};
        case kR8G8_SNORM: return {"kR8G8_SNORM", 2, "rg8_snorm"};
        case kR8G8_UINT: return {"kR8G8_UINT", 2, "rg8_uint"};
        case kR8G8_SINT: return {"kR8G8_SINT", 2, "rg8_sint"};
        case kB5G5R5A1: return {"kB5G5R5A1", 2, "b5g5r5a1"};
        case kB5G6R5: return {"kB5G6R5", 2, "b5g6r5"};
        case kR32_FLOAT: return {"kR32_FLOAT", 4, "r32_float"};
        case kR32_UINT: return {"kR32_UINT", 4, "r32_uint"};
        case kR32_SINT: return {"kR32_SINT", 4, "r32_sint"};
        case kR16G16_FLOAT: return {"kR16G16_FLOAT", 4, "rg16_float"};
        case kR16G16_UNORM: return {"kR16G16_UNORM", 4, "rg16_unorm"};
        case kR16G16_SNORM: return {"kR16G16_SNORM", 4, "rg16_snorm"};
        case kR16G16_UINT: return {"kR16G16_UINT", 4, "rg16_uint"};
        case kR16G16_SINT: return {"kR16G16_SINT", 4, "rg16_sint"};
        case kR8G8B8A8_UNORM:
            return {"kR8G8B8A8_UNORM", 4, "rgba8_unorm"};
        case kR8G8B8A8_SNORM:
            return {"kR8G8B8A8_SNORM", 4, "rgba8_snorm"};
        case kR8G8B8A8_UINT:
            return {"kR8G8B8A8_UINT", 4, "rgba8_uint"};
        case kR8G8B8A8_SINT:
            return {"kR8G8B8A8_SINT", 4, "rgba8_sint"};
        case kR10G10B10A2_UNORM:
            return {"kR10G10B10A2_UNORM", 4, "rgb10a2_unorm"};
        case kR10G10B10A2_UINT:
            return {"kR10G10B10A2_UINT", 4, "rgb10a2_uint"};
        case kB8G8R8A8: return {"kB8G8R8A8", 4, "bgra8"};
        case kB8G8R8X8: return {"kB8G8R8X8", 4, "bgrx8"};
        case kR8G8B8X8: return {"kR8G8B8X8", 4, "rgbx8"};
        case kA8B8G8R8: return {"kA8B8G8R8", 4, "abgr8"};
        case kR32G32_FLOAT: return {"kR32G32_FLOAT", 8, "rg32_float"};
        case kR32G32_UINT: return {"kR32G32_UINT", 8, "rg32_uint"};
        case kR32G32_SINT: return {"kR32G32_SINT", 8, "rg32_sint"};
        case kR16G16B16A16_FLOAT:
            return {"kR16G16B16A16_FLOAT", 8, "rgba16_float"};
        case kR16G16B16A16_UNORM:
            return {"kR16G16B16A16_UNORM", 8, "rgba16_unorm"};
        case kR16G16B16A16_SNORM:
            return {"kR16G16B16A16_SNORM", 8, "rgba16_snorm"};
        case kR16G16B16A16_UINT:
            return {"kR16G16B16A16_UINT", 8, "rgba16_uint"};
        case kR16G16B16A16_SINT:
            return {"kR16G16B16A16_SINT", 8, "rgba16_sint"};
        case kR32G32B32_FLOAT:
            return {"kR32G32B32_FLOAT", 12, "rgb32_float"};
        case kR32G32B32_UINT:
            return {"kR32G32B32_UINT", 12, "rgb32_uint"};
        case kR32G32B32_SINT:
            return {"kR32G32B32_SINT", 12, "rgb32_sint"};
        case kR32G32B32A32_FLOAT:
            return {"kR32G32B32A32_FLOAT", 16, "rgba32_float"};
        case kR32G32B32A32_UINT:
            return {"kR32G32B32A32_UINT", 16, "rgba32_uint"};
        case kR32G32B32A32_SINT:
            return {"kR32G32B32A32_SINT", 16, "rgba32_sint"};
        case kNumberOfRasterFormats:
            return {"kNumberOfRasterFormats", 0, "invalid"};
    }
    return {"unknown", 0, "unknown"};
}

std::size_t encodedLength(std::size_t bytes) {
    if (bytes > (std::numeric_limits<std::size_t>::max() - 2U)) {
        throw std::overflow_error("payload size overflow");
    }
    const std::size_t groups = (bytes + 2U) / 3U;
    if (groups > std::numeric_limits<std::size_t>::max() / 4U) {
        throw std::overflow_error("encoded payload size overflow");
    }
    return 4U * groups;
}

std::string base64Encode(const std::vector<std::uint8_t>& data) {
    static constexpr char alphabet[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string output;
    output.reserve(encodedLength(data.size()));
    for (std::size_t offset = 0; offset < data.size(); offset += 3U) {
        const std::uint32_t first = data[offset];
        const std::uint32_t second =
            offset + 1U < data.size() ? data[offset + 1U] : 0U;
        const std::uint32_t third =
            offset + 2U < data.size() ? data[offset + 2U] : 0U;
        const std::uint32_t value = (first << 16U) | (second << 8U) | third;
        output.push_back(alphabet[(value >> 18U) & 0x3fU]);
        output.push_back(alphabet[(value >> 12U) & 0x3fU]);
        output.push_back(offset + 1U < data.size()
                             ? alphabet[(value >> 6U) & 0x3fU]
                             : '=');
        output.push_back(offset + 2U < data.size() ? alphabet[value & 0x3fU]
                                                   : '=');
    }
    return output;
}

std::pair<unsigned int, unsigned int> sampleDimensions(
    unsigned int sourceWidth, unsigned int sourceHeight,
    unsigned int maxDimension, unsigned int bytesPerPixel,
    std::size_t encodedBudget) {
    if (sourceWidth == 0 || sourceHeight == 0 || bytesPerPixel == 0) {
        throw std::runtime_error("render target has invalid dimensions or format");
    }

    const unsigned int sourceLargest = std::max(sourceWidth, sourceHeight);
    const double dimensionScale =
        sourceLargest > maxDimension
            ? static_cast<double>(maxDimension) /
                  static_cast<double>(sourceLargest)
            : 1.0;
    unsigned int width = std::max(
        1U, static_cast<unsigned int>(std::floor(sourceWidth * dimensionScale)));
    unsigned int height = std::max(
        1U,
        static_cast<unsigned int>(std::floor(sourceHeight * dimensionScale)));

    auto byteCount = [bytesPerPixel](unsigned int w, unsigned int h) {
        const std::uint64_t count = static_cast<std::uint64_t>(w) * h *
                                    static_cast<std::uint64_t>(bytesPerPixel);
        if (count > std::numeric_limits<std::size_t>::max()) {
            throw std::overflow_error("sample size overflow");
        }
        return static_cast<std::size_t>(count);
    };

    const std::size_t currentBytes = byteCount(width, height);
    if (encodedLength(currentBytes) > encodedBudget) {
        const std::size_t maximumRaw = (encodedBudget / 4U) * 3U;
        const double budgetScale = std::sqrt(
            static_cast<double>(maximumRaw) / static_cast<double>(currentBytes));
        width = std::max(
            1U, static_cast<unsigned int>(std::floor(width * budgetScale)));
        height = std::max(
            1U, static_cast<unsigned int>(std::floor(height * budgetScale)));
    }

    while (encodedLength(byteCount(width, height)) > encodedBudget) {
        if (width >= height && width > 1U) {
            --width;
        } else if (height > 1U) {
            --height;
        } else {
            throw std::runtime_error("base64 budget is too small for one pixel");
        }
    }
    return {width, height};
}

SampledPass sampleTarget(const MHWRender::MRenderTarget* target,
                         unsigned int maxDimension,
                         std::size_t encodedBudget) {
    if (target == nullptr) {
        throw std::runtime_error("render target is unavailable");
    }

    MHWRender::MRenderTargetDescription description;
    target->targetDescription(description);
    const FormatInfo format = formatInfo(description.rasterFormat());
    if (format.bytesPerPixel == 0) {
        throw std::runtime_error(std::string("unsupported raster format ") +
                                 format.name);
    }
    if (description.arraySliceCount() != 1U || description.isCubeMap()) {
        throw std::runtime_error(
            "array and cube-map render targets are not supported");
    }

    int rowPitch = 0;
    std::size_t slicePitch = 0;
    void* raw = const_cast<MHWRender::MRenderTarget*>(target)->rawData(
        rowPitch, slicePitch);
    if (raw == nullptr) {
        throw std::runtime_error("MRenderTarget::rawData returned null");
    }
    struct RawDataGuard {
        void* value;
        ~RawDataGuard() { MHWRender::MRenderTarget::freeRawData(value); }
    } rawDataGuard{raw};

    if (rowPitch <= 0) {
        throw std::runtime_error("render target row pitch is not positive");
    }
    const std::uint64_t packedRow =
        static_cast<std::uint64_t>(description.width()) * format.bytesPerPixel;
    if (packedRow > static_cast<std::uint64_t>(rowPitch)) {
        throw std::runtime_error("render target row pitch is smaller than a row");
    }
    const std::uint64_t requiredSlice =
        description.height() == 0
            ? 0
            : static_cast<std::uint64_t>(description.height() - 1U) *
                      static_cast<unsigned int>(rowPitch) +
                  packedRow;
    if (requiredSlice > slicePitch) {
        throw std::runtime_error(
            "render target slice pitch is smaller than its addressable pixels");
    }

    const auto dimensions = sampleDimensions(
        description.width(), description.height(), maxDimension,
        format.bytesPerPixel, encodedBudget);
    const unsigned int sampleWidth = dimensions.first;
    const unsigned int sampleHeight = dimensions.second;
    const std::size_t sampleRow =
        static_cast<std::size_t>(sampleWidth) * format.bytesPerPixel;
    std::vector<std::uint8_t> sampled(sampleRow * sampleHeight);
    const auto* source = static_cast<const std::uint8_t*>(raw);
    for (unsigned int y = 0; y < sampleHeight; ++y) {
        const unsigned int sourceY = static_cast<unsigned int>(
            (static_cast<std::uint64_t>(y) * description.height()) /
            sampleHeight);
        for (unsigned int x = 0; x < sampleWidth; ++x) {
            const unsigned int sourceX = static_cast<unsigned int>(
                (static_cast<std::uint64_t>(x) * description.width()) /
                sampleWidth);
            const std::size_t sourceOffset =
                static_cast<std::size_t>(sourceY) * rowPitch +
                static_cast<std::size_t>(sourceX) * format.bytesPerPixel;
            const std::size_t destinationOffset =
                static_cast<std::size_t>(y) * sampleRow +
                static_cast<std::size_t>(x) * format.bytesPerPixel;
            std::memcpy(sampled.data() + destinationOffset,
                        source + sourceOffset, format.bytesPerPixel);
        }
    }

    std::string encoded = base64Encode(sampled);
    SampledPass pass;
    pass.base64Chars = encoded.size();
    pass.value = {
        {"source",
         {{"width", description.width()},
          {"height", description.height()},
          {"row_pitch_bytes", rowPitch},
          {"slice_pitch_bytes", slicePitch},
          {"sample_count", description.multiSampleCount()},
          {"array_slices", description.arraySliceCount()},
          {"cube_map", description.isCubeMap()},
          {"raster_format",
           {{"name", format.name},
            {"value", static_cast<int>(description.rasterFormat())},
            {"layout", format.layout},
            {"pixel_stride_bytes", format.bytesPerPixel}}},
          {"row_order", "renderer_native"},
          {"byte_order", "native"}}},
        {"sample",
         {{"width", sampleWidth},
          {"height", sampleHeight},
          {"filter", "nearest"},
          {"row_stride_bytes", sampleRow},
          {"pixel_stride_bytes", format.bytesPerPixel},
          {"byte_count", sampled.size()},
          {"source_row_order_preserved", true}}},
        {"payload",
         {{"encoding", "base64"},
          {"media_type", "application/vnd.autodesk.maya.render-target"},
          {"base64_chars", encoded.size()},
          {"data", std::move(encoded)}}},
    };
    return pass;
}

struct TargetGuard {
    const MHWRender::MRenderTargetManager* manager = nullptr;
    const MHWRender::MRenderTarget* target = nullptr;
    ~TargetGuard() {
        if (manager != nullptr && target != nullptr) {
            manager->releaseRenderTarget(target);
        }
    }
};

void captureCallback(MHWRender::MDrawContext& context, void* clientData) {
    auto* state = static_cast<CaptureState*>(clientData);
    if (state == nullptr || state->callbackRan) {
        return;
    }
    state->callbackRan = true;
    try {
        MHWRender::MRenderer* renderer = MHWRender::MRenderer::theRenderer();
        if (renderer == nullptr) {
            throw std::runtime_error("Viewport 2.0 renderer is unavailable");
        }
        const auto* manager = renderer->getRenderTargetManager();
        if (manager == nullptr) {
            throw std::runtime_error("render target manager is unavailable");
        }

        if (state->request.depth) {
            TargetGuard depth{
                manager, context.copyCurrentDepthRenderTarget(
                             MString((state->targetPrefix + "-depth").c_str()))};
            if (depth.target == nullptr) {
                throw std::runtime_error("current depth render target is unavailable");
            }
            state->depth = sampleTarget(depth.target, state->request.maxDimension,
                                        state->base64BudgetPerPass);
        }
        if (state->request.color) {
            TargetGuard color{
                manager, context.copyCurrentColorRenderTarget(
                             MString((state->targetPrefix + "-color").c_str()))};
            if (color.target == nullptr) {
                throw std::runtime_error("current color render target is unavailable");
            }
            state->color = sampleTarget(color.target, state->request.maxDimension,
                                        state->base64BudgetPerPass);
        }
    } catch (const std::exception& exception) {
        state->failed = true;
        try {
            state->errorMessage = exception.what();
        } catch (...) {
            // The preallocated fallback remains valid if reporting allocates.
        }
    } catch (...) {
        state->failed = true;
    }
}

const char* drawApiName(MHWRender::DrawAPI api) {
    switch (api) {
        case MHWRender::kOpenGL: return "opengl";
        case MHWRender::kDirectX11: return "directx11";
        case MHWRender::kOpenGLCoreProfile: return "opengl_core_profile";
        case MHWRender::kNone: return "none";
        case MHWRender::kAllDevices: return "all_devices";
    }
    return "unknown";
}

class Vp2CaptureCommand final : public MPxCommand {
public:
    static void* creator() {
        return new (std::nothrow) Vp2CaptureCommand();
    }

    static MSyntax newSyntax() {
        MSyntax syntax;
        syntax.addFlag(kRequestFlag, kRequestFlagLong, MSyntax::kString);
        syntax.enableQuery(false);
        syntax.enableEdit(false);
        return syntax;
    }

    MStatus doIt(const MArgList& arguments) override {
        bool ownsCaptureExecution = false;
        try {
            CaptureExecutionGuard execution;
            ownsCaptureExecution = execution.exclusive();
            if (!ownsCaptureExecution) {
                setJsonResult(errorResult(
                    "CAPTURE_REENTRANT",
                    "nested VP2 capture is not allowed while a capture is active",
                    true));
                return MS::kSuccess;
            }
            std::string pendingCleanupError;
            if (!cleanupPendingNotification(pendingCleanupError)) {
                setJsonResult(errorResult(
                    "CALLBACK_CLEANUP_PENDING",
                    std::string("A previous VP2 notification is still registered: ") +
                        pendingCleanupError,
                    true));
                return MS::kSuccess;
            }
            MStatus status;
            MArgDatabase database(syntax(), arguments, &status);
            if (!status) {
                setJsonResult(errorResult("INVALID_ARGUMENT",
                                          status.errorString().asChar()));
                return MS::kSuccess;
            }

            MString encodedRequest;
            if (database.isFlagSet(kRequestFlag)) {
                status = database.getFlagArgument(kRequestFlag, 0,
                                                  encodedRequest);
                if (!status) {
                    setJsonResult(errorResult("INVALID_ARGUMENT",
                                              status.errorString().asChar()));
                    return MS::kSuccess;
                }
            }

            Request request;
            std::string parseError;
            if (!parseRequest(encodedRequest, request, parseError)) {
                setJsonResult(errorResult("INVALID_ARGUMENT", parseError));
                return MS::kSuccess;
            }
            if (request.objectId) {
                setJsonResult(errorResult(
                    "UNSUPPORTED_PASS",
                    "object_id capture is not exposed because this VP2 path "
                    "does not provide stable scene-object identifiers"));
                return MS::kSuccess;
            }

            const MGlobal::MMayaState mayaState = MGlobal::mayaState(&status);
            if (!status || mayaState == MGlobal::kBatch ||
                mayaState == MGlobal::kLibraryApp) {
                setJsonResult(errorResult(
                    "VIEWPORT_UNAVAILABLE",
                    "native VP2 capture requires an interactive Maya viewport"));
                return MS::kSuccess;
            }

            MHWRender::MRenderer* renderer = MHWRender::MRenderer::theRenderer();
            if (renderer == nullptr) {
                setJsonResult(errorResult("VIEWPORT_UNAVAILABLE",
                                          "Viewport 2.0 renderer is unavailable",
                                          true));
                return MS::kSuccess;
            }
            M3dView view = M3dView::active3dView(&status);
            if (!status) {
                setJsonResult(errorResult("VIEWPORT_UNAVAILABLE",
                                          "Maya has no active 3D view", true));
                return MS::kSuccess;
            }

            auto pending = std::make_unique<PendingNotification>();
            pending->capture = std::make_unique<CaptureState>();
            CaptureState& pendingCapture = *pending->capture;
            pendingCapture.request = request;
            pendingCapture.targetPrefix =
                "mayaMcpVp2Capture-" +
                std::to_string(gCaptureSequence.fetch_add(1));
            const std::size_t passCount =
                static_cast<std::size_t>(request.depth) +
                static_cast<std::size_t>(request.color);
            pendingCapture.base64BudgetPerPass = kBase64Budget / passCount;
            pending->name = MString(pendingCapture.targetPrefix.c_str());
            pending->semantic = MString(
                MHWRender::MPassContext::kEndRenderSemantic);
            gPendingNotification = std::move(pending);
            status = renderer->addNotification(
                captureCallback, gPendingNotification->name,
                gPendingNotification->semantic,
                gPendingNotification->capture.get());
            if (!status) {
                gPendingNotification.reset();
                setJsonResult(errorResult(
                    "CAPTURE_FAILED",
                    std::string("could not install VP2 render notification: ") +
                        status.errorString().asChar(),
                    true));
                return MS::kSuccess;
            }
            gPendingNotification->installed = true;

            status = view.refresh(false, true);
            std::unique_ptr<CaptureState> capture;
            std::string removeError;
            if (!removePendingNotification(&capture, removeError)) {
                setJsonResult(errorResult(
                    "CAPTURE_FAILED",
                    std::string("could not remove VP2 render notification: ") +
                        removeError,
                    true));
                return MS::kSuccess;
            }
            if (!status) {
                setJsonResult(errorResult(
                    "CAPTURE_FAILED",
                    std::string("active viewport refresh failed: ") +
                        status.errorString().asChar(),
                    true));
                return MS::kSuccess;
            }
            if (capture == nullptr || !capture->callbackRan) {
                setJsonResult(errorResult(
                    "CAPTURE_NOT_COMPLETED",
                    "viewport refresh completed without the VP2 end-render "
                    "notification",
                    true));
                return MS::kSuccess;
            }
            if (capture->failed) {
                setJsonResult(errorResult(capture->errorCode.c_str(),
                                          capture->errorMessage, true));
                return MS::kSuccess;
            }

            const std::size_t base64Chars = capture->depth.base64Chars +
                                             capture->color.base64Chars;
            if (base64Chars > kBase64Budget) {
                setJsonResult(errorResult(
                    "CAPTURE_FAILED",
                    "native capture exceeded the base64 response budget"));
                return MS::kSuccess;
            }

            Json passes = Json::object();
            if (request.depth) {
                passes["depth"] = std::move(capture->depth.value);
            }
            if (request.color) {
                passes["color"] = std::move(capture->color.value);
            }
            MStatus widthStatus;
            const int portWidth = view.portWidth(&widthStatus);
            MStatus heightStatus;
            const int portHeight = view.portHeight(&heightStatus);
            if (!widthStatus || !heightStatus || portWidth < 1 || portHeight < 1) {
                setJsonResult(errorResult(
                    "CAPTURE_FAILED",
                    "could not read valid active viewport dimensions", true));
                return MS::kSuccess;
            }
            const MHWRender::DrawAPI drawApi = renderer->drawAPI();
            Json result = {
                {"schema_version", 1},
                {"ok", true},
                {"request",
                 {{"depth", request.depth},
                  {"color", request.color},
                  {"object_id", false},
                  {"max_dimension", request.maxDimension}}},
                {"source",
                 {{"kind", "active_viewport_2"},
                  {"viewport_width", portWidth},
                  {"viewport_height", portHeight},
                  {"draw_api",
                   {{"name", drawApiName(drawApi)},
                    {"value", static_cast<unsigned int>(drawApi)}}},
                  {"draw_api_version", renderer->drawAPIVersion()}}},
                {"capabilities", capabilities()},
                {"limits", limits(base64Chars)},
                {"passes", std::move(passes)},
            };
            setJsonResult(result);
            return MS::kSuccess;
        } catch (const std::exception& exception) {
            if (ownsCaptureExecution) {
                try {
                    std::string cleanupError;
                    (void)cleanupPendingNotification(cleanupError);
                } catch (...) {
                    // Keep any still-registered notification and its client data alive.
                }
            }
            return setInternalErrorResult(exception.what());
        } catch (...) {
            if (ownsCaptureExecution) {
                try {
                    std::string cleanupError;
                    (void)cleanupPendingNotification(cleanupError);
                } catch (...) {
                    // Keep any still-registered notification and its client data alive.
                }
            }
            return setInternalErrorResult("unknown native command failure");
        }
    }

private:
    void setJsonResult(const Json& value) {
        const std::string serialized = value.dump();
        setResult(MString(serialized.c_str()));
    }

    MStatus setInternalErrorResult(const char* message) noexcept {
        try {
            setJsonResult(errorResult("INTERNAL_ERROR", message));
            return MS::kSuccess;
        } catch (...) {
            try {
                MGlobal::displayError(
                    "maya-mcp VP2 capture could not report an internal error");
            } catch (...) {
            }
            return MS::kFailure;
        }
    }
};

}  // namespace

const char* vp2CaptureCommandName() { return kCommandName; }

MStatus registerVp2CaptureCommand(MFnPlugin& plugin) {
    return plugin.registerCommand(kCommandName, Vp2CaptureCommand::creator,
                                  Vp2CaptureCommand::newSyntax);
}

MStatus deregisterVp2CaptureCommand(MFnPlugin& plugin) {
    return plugin.deregisterCommand(kCommandName);
}

bool cleanupVp2CaptureCallbacks(std::string& error) {
    if (gCaptureExecutionDepth.load() != 0) {
        error = "a VP2 capture command is still executing";
        return false;
    }
    return cleanupPendingNotification(error);
}

}  // namespace maya_mcp
