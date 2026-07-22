cmake_minimum_required(VERSION 3.25)
project(maya_mcp VERSION 0.5.3 LANGUAGES CXX)

include(FetchContent)

set(HTTPLIB_INSTALL OFF CACHE BOOL Disabled FORCE)
set(HTTPLIB_TEST OFF CACHE BOOL Disabled FORCE)
FetchContent_Declare(
    cpp_httplib
    GIT_REPOSITORY https://github.com/yhirose/cpp-httplib.git
    # v0.50.1
    GIT_TAG 62d899feac3cf9215a55f2b43da250fdd98d2156
    GIT_SHALLOW TRUE
)

set(JSON_BuildTests OFF CACHE INTERNAL Disabled)
set(JSON_Install OFF CACHE INTERNAL Disabled)
FetchContent_Declare(
    nlohmann_json
    GIT_REPOSITORY https://github.com/nlohmann/json.git
    # v3.12.0
    GIT_TAG 55f93686c01528224f448c19128836e7df245f72
    GIT_SHALLOW TRUE
)

FetchContent_MakeAvailable(cpp_httplib nlohmann_json)

set(MAYA_TARGET "2027" CACHE STRING "Maya release target (2026.3 or 2027)")
set_property(CACHE MAYA_TARGET PROPERTY STRINGS "2026.3" "2027")
if(MAYA_TARGET STREQUAL "2026.3")
    set(MAYA_MAJOR_VERSION "2026")
elseif(MAYA_TARGET STREQUAL "2027")
    set(MAYA_MAJOR_VERSION "2027")
else()
    message(FATAL_ERROR "MAYA_TARGET must be 2026.3 or 2027, got: ${MAYA_TARGET}")
endif()

if(DEFINED ENV{LOCALAPPDATA})
    set(_default_devkit "$ENV{LOCALAPPDATA}/MayaMCP/devkits/maya${MAYA_TARGET}/devkitBase")
else()
    set(_default_devkit "${CMAKE_CURRENT_SOURCE_DIR}/vendor/maya${MAYA_TARGET}/devkitBase")
endif()
set(MAYA_DEVKIT_ROOT "${_default_devkit}" CACHE PATH "Maya devkitBase directory")
set(MAYA_LOCATION "C:/Program Files/Autodesk/Maya${MAYA_MAJOR_VERSION}" CACHE PATH "Maya installation")
if(NOT EXISTS "${MAYA_DEVKIT_ROOT}/include/maya/MFnPlugin.h")
    message(FATAL_ERROR "Maya headers not found at ${MAYA_DEVKIT_ROOT}")
endif()
if(NOT EXISTS "${MAYA_DEVKIT_ROOT}/lib/OpenMaya.lib")
    message(FATAL_ERROR "Maya libraries not found at ${MAYA_DEVKIT_ROOT}/lib")
endif()
file(STRINGS "${MAYA_DEVKIT_ROOT}/include/maya/MTypes.h" _maya_api_line
    REGEX "^#define MAYA_API_VERSION [0-9]+")
string(REGEX MATCH "[0-9]+" MAYA_API_VERSION "${_maya_api_line}")
if(NOT MAYA_API_VERSION)
    message(FATAL_ERROR "Could not read MAYA_API_VERSION from ${MAYA_DEVKIT_ROOT}/include/maya/MTypes.h")
endif()

add_library(maya_mcp MODULE
    src/main_thread_dispatcher.cpp
    src/mcp_server.cpp
    src/plugin.cpp
    src/python_bridge.cpp
    src/vp2_capture_command.cpp
)

add_executable(maya_mcp_bridge src/stdio_bridge.cpp)
target_compile_features(maya_mcp_bridge PRIVATE cxx_std_17)
target_compile_definitions(maya_mcp_bridge PRIVATE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
    _CRT_SECURE_NO_WARNINGS
    MAYA_MCP_VERSION="${PROJECT_VERSION}"
)
target_link_libraries(maya_mcp_bridge PRIVATE
    httplib::httplib
    nlohmann_json::nlohmann_json
    ws2_32
)
target_compile_features(maya_mcp PRIVATE cxx_std_17)
target_compile_definitions(maya_mcp PRIVATE NT_PLUGIN REQUIRE_IOSTREAM _BOOL _CRT_SECURE_NO_WARNINGS _HAS_ITERATOR_DEBUGGING=0)
target_compile_definitions(maya_mcp PRIVATE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
    MAYA_MCP_VERSION="${PROJECT_VERSION}"
    MAYA_MCP_MAYA_VERSION="${MAYA_MAJOR_VERSION}"
    MAYA_MCP_MAYA_TARGET="${MAYA_TARGET}"
    MAYA_MCP_MAYA_API_VERSION=${MAYA_API_VERSION}
)
target_include_directories(maya_mcp
    PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/include"
    SYSTEM PRIVATE "${MAYA_DEVKIT_ROOT}/include"
)
target_link_directories(maya_mcp PRIVATE "${MAYA_DEVKIT_ROOT}/lib")
target_link_libraries(maya_mcp PRIVATE
    OpenMaya
    OpenMayaUI
    OpenMayaRender
    Foundation
    bcrypt
    ws2_32
    httplib::httplib
    nlohmann_json::nlohmann_json
)
if(MSVC)
    target_compile_options(maya_mcp PRIVATE /W4 /permissive- /Zc:__cplusplus /wd4100 /wd4996)
    set_property(TARGET maya_mcp PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
    target_compile_options(maya_mcp_bridge PRIVATE /W4 /permissive- /Zc:__cplusplus /wd4100 /wd4996)
    set_property(TARGET maya_mcp_bridge PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded")
endif()

set_target_properties(maya_mcp PROPERTIES PREFIX "" SUFFIX ".mll" OUTPUT_NAME "maya_mcp"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/package/maya-mcp/plug-ins"
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/package/maya-mcp/plug-ins")
set_target_properties(maya_mcp_bridge PROPERTIES OUTPUT_NAME "maya-mcp-bridge"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/package/maya-mcp/bin")
foreach(config Debug Release RelWithDebInfo MinSizeRel)
    string(TOUPPER "${config}" config_upper)
    set_target_properties(maya_mcp PROPERTIES
        "RUNTIME_OUTPUT_DIRECTORY_${config_upper}" "${CMAKE_BINARY_DIR}/package/maya-mcp/plug-ins"
        "LIBRARY_OUTPUT_DIRECTORY_${config_upper}" "${CMAKE_BINARY_DIR}/package/maya-mcp/plug-ins")
    set_target_properties(maya_mcp_bridge PROPERTIES
        "RUNTIME_OUTPUT_DIRECTORY_${config_upper}" "${CMAKE_BINARY_DIR}/package/maya-mcp/bin")
endforeach()

configure_file(cmake/maya-mcp.mod.in "${CMAKE_BINARY_DIR}/package/maya-mcp.mod" @ONLY)
configure_file(cmake/package-manifest.json.in "${CMAKE_BINARY_DIR}/package/package-manifest.json" @ONLY)
file(GLOB_RECURSE MAYA_MCP_RUNTIME_SOURCES CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/python/maya_mcp_runtime/*.py")
add_custom_target(maya_mcp_runtime_package ALL
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${CMAKE_BINARY_DIR}/package/maya-mcp/scripts"
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${CMAKE_BINARY_DIR}/package/maya-mcp/client"
    COMMAND "${CMAKE_COMMAND}" -E copy_directory
        "${CMAKE_CURRENT_SOURCE_DIR}/python/maya_mcp_runtime"
        "${CMAKE_BINARY_DIR}/package/maya-mcp/scripts/maya_mcp_runtime"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different
        "${CMAKE_CURRENT_SOURCE_DIR}/scripts/start-client-bridge.ps1"
        "${CMAKE_BINARY_DIR}/package/maya-mcp/client/Start-MayaMcpBridge.ps1"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different
        "${CMAKE_CURRENT_SOURCE_DIR}/scripts/configure-clients.ps1"
        "${CMAKE_BINARY_DIR}/package/maya-mcp/client/Configure-MayaMcpClients.ps1"
    DEPENDS maya_mcp maya_mcp_bridge ${MAYA_MCP_RUNTIME_SOURCES}
        scripts/start-client-bridge.ps1 scripts/configure-clients.ps1
    COMMENT "Packaging Maya MCP Python runtime"
)
install(TARGETS maya_mcp RUNTIME DESTINATION maya-mcp/plug-ins LIBRARY DESTINATION maya-mcp/plug-ins)
install(TARGETS maya_mcp_bridge RUNTIME DESTINATION maya-mcp/bin)
install(DIRECTORY python/maya_mcp_runtime DESTINATION maya-mcp/scripts)
install(FILES
    scripts/start-client-bridge.ps1
    DESTINATION maya-mcp/client
    RENAME Start-MayaMcpBridge.ps1)
install(FILES
    scripts/configure-clients.ps1
    DESTINATION maya-mcp/client
    RENAME Configure-MayaMcpClients.ps1)
install(FILES
    "${CMAKE_BINARY_DIR}/package/maya-mcp.mod"
    "${CMAKE_BINARY_DIR}/package/package-manifest.json"
    DESTINATION .)
message(STATUS "Maya devkit: ${MAYA_DEVKIT_ROOT}")
message(STATUS "Maya target: ${MAYA_TARGET} (API ${MAYA_API_VERSION})")
message(STATUS "Maya install: ${MAYA_LOCATION}")
