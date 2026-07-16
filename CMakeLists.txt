cmake_minimum_required(VERSION 3.25)
project(maya_mcp VERSION 0.2.0 LANGUAGES CXX)

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

set(MAYA_DEVKIT_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/vendor/devkitBase" CACHE PATH "Maya devkitBase directory")
set(MAYA_LOCATION "C:/Program Files/Autodesk/Maya2027" CACHE PATH "Maya installation")
if(NOT EXISTS "${MAYA_DEVKIT_ROOT}/include/maya/MFnPlugin.h")
    message(FATAL_ERROR "Maya headers not found at ${MAYA_DEVKIT_ROOT}")
endif()
if(NOT EXISTS "${MAYA_DEVKIT_ROOT}/lib/OpenMaya.lib")
    message(FATAL_ERROR "Maya libraries not found at ${MAYA_DEVKIT_ROOT}/lib")
endif()

add_library(maya_mcp MODULE
    src/main_thread_dispatcher.cpp
    src/mcp_server.cpp
    src/plugin.cpp
    src/python_bridge.cpp
)
target_compile_features(maya_mcp PRIVATE cxx_std_17)
target_compile_definitions(maya_mcp PRIVATE NT_PLUGIN REQUIRE_IOSTREAM _BOOL _CRT_SECURE_NO_WARNINGS _HAS_ITERATOR_DEBUGGING=0)
target_compile_definitions(maya_mcp PRIVATE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
    MAYA_MCP_VERSION="${PROJECT_VERSION}"
)
target_include_directories(maya_mcp
    PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/include"
    SYSTEM PRIVATE "${MAYA_DEVKIT_ROOT}/include"
)
target_link_directories(maya_mcp PRIVATE "${MAYA_DEVKIT_ROOT}/lib")
target_link_libraries(maya_mcp PRIVATE
    OpenMaya
    Foundation
    bcrypt
    ws2_32
    httplib::httplib
    nlohmann_json::nlohmann_json
)
if(MSVC)
    target_compile_options(maya_mcp PRIVATE /W4 /permissive- /Zc:__cplusplus /wd4100 /wd4996)
    set_property(TARGET maya_mcp PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
endif()

set_target_properties(maya_mcp PROPERTIES PREFIX "" SUFFIX ".mll" OUTPUT_NAME "maya_mcp"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/package/maya-mcp/plug-ins"
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/package/maya-mcp/plug-ins")
foreach(config Debug Release RelWithDebInfo MinSizeRel)
    string(TOUPPER "${config}" config_upper)
    set_target_properties(maya_mcp PROPERTIES
        "RUNTIME_OUTPUT_DIRECTORY_${config_upper}" "${CMAKE_BINARY_DIR}/package/maya-mcp/plug-ins"
        "LIBRARY_OUTPUT_DIRECTORY_${config_upper}" "${CMAKE_BINARY_DIR}/package/maya-mcp/plug-ins")
endforeach()

configure_file(cmake/maya-mcp.mod.in "${CMAKE_BINARY_DIR}/package/maya-mcp.mod" @ONLY)
add_custom_command(TARGET maya_mcp POST_BUILD
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${CMAKE_BINARY_DIR}/package/maya-mcp/scripts"
    COMMAND "${CMAKE_COMMAND}" -E copy_directory
        "${CMAKE_CURRENT_SOURCE_DIR}/python/maya_mcp_runtime"
        "${CMAKE_BINARY_DIR}/package/maya-mcp/scripts/maya_mcp_runtime"
    COMMENT "Packaging Maya MCP Python runtime"
)
install(TARGETS maya_mcp RUNTIME DESTINATION maya-mcp/plug-ins LIBRARY DESTINATION maya-mcp/plug-ins)
install(DIRECTORY python/maya_mcp_runtime DESTINATION maya-mcp/scripts)
install(FILES "${CMAKE_BINARY_DIR}/package/maya-mcp.mod" DESTINATION .)
message(STATUS "Maya devkit: ${MAYA_DEVKIT_ROOT}")
message(STATUS "Maya install: ${MAYA_LOCATION}")
