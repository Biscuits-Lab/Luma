cmake_minimum_required(VERSION 3.25)

project(LuminaViewer VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(LUMINA_OUTPUT_DIR "${CMAKE_CURRENT_SOURCE_DIR}/output" CACHE PATH "Directory for packaged preview executable")
foreach(_config IN ITEMS Debug Release RelWithDebInfo MinSizeRel)
    string(TOUPPER "${_config}" _config_upper)
    set(CMAKE_RUNTIME_OUTPUT_DIRECTORY_${_config_upper} "${LUMINA_OUTPUT_DIR}")
endforeach()
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${LUMINA_OUTPUT_DIR}")
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")

find_package(CoreUI REQUIRED)

add_executable(lumina-viewer WIN32
    src/main.cpp
    src/app/Application.cpp
    src/app/ViewerController.cpp
    src/image/DecoderRegistry.cpp
    src/image/FolderImageIndex.cpp
    src/image/PlaceholderDecoder.cpp
    src/image/WicDecoder.cpp
    src/platform/WindowsStrings.cpp
)

target_include_directories(lumina-viewer PRIVATE
    src
)

target_link_libraries(lumina-viewer PRIVATE
    CoreUI::CoreUI
    Shell32
    Ole32
    Windowscodecs
)

target_compile_definitions(lumina-viewer PRIVATE
    UNICODE
    _UNICODE
    NOMINMAX
    WIN32_LEAN_AND_MEAN
)

if(MSVC)
    target_compile_options(lumina-viewer PRIVATE /W4 /permissive- /utf-8)
else()
    target_compile_options(lumina-viewer PRIVATE -Wall -Wextra -Wpedantic)
endif()

add_custom_command(TARGET lumina-viewer POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E make_directory "$<TARGET_FILE_DIR:lumina-viewer>/ui"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${CMAKE_CURRENT_SOURCE_DIR}/ui/main.uix"
        "$<TARGET_FILE_DIR:lumina-viewer>/ui/main.uix"
)

if(CoreUI_DLL)
    add_custom_command(TARGET lumina-viewer POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${CoreUI_DLL}"
            "$<TARGET_FILE_DIR:lumina-viewer>/core-ui.dll"
    )
endif()



