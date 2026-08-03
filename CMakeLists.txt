cmake_minimum_required(VERSION 3.25)

project(Lumina VERSION 0.1.0 LANGUAGES CXX)
if(POLICY CMP0144)
    cmake_policy(SET CMP0144 NEW)
endif()

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
find_package(LibRaw QUIET)

add_executable(Lumina WIN32
    src/main.cpp
    src/app/Application.cpp
    src/app/ViewerController.cpp
    src/image/DecoderRegistry.cpp
    src/image/FolderImageIndex.cpp
    src/image/ImageAnalysis.cpp
    src/image/PlaceholderDecoder.cpp
    src/image/WicDecoder.cpp
    $<$<BOOL:${LibRaw_FOUND}>:src/image/LibRawDecoder.cpp>
    src/platform/WindowsStrings.cpp
)

target_include_directories(Lumina PRIVATE
    src
)

target_link_libraries(Lumina PRIVATE
    CoreUI::CoreUI
    Shell32
    Ole32
    Windowscodecs
)

target_compile_definitions(Lumina PRIVATE
    UNICODE
    _UNICODE
    NOMINMAX
    WIN32_LEAN_AND_MEAN
)

if(MSVC)
    target_compile_options(Lumina PRIVATE /W4 /permissive- /utf-8)
else()
    target_compile_options(Lumina PRIVATE -Wall -Wextra -Wpedantic)
endif()

add_custom_command(TARGET Lumina POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E make_directory "$<TARGET_FILE_DIR:Lumina>/ui"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${CMAKE_CURRENT_SOURCE_DIR}/ui/main.uix"
        "$<TARGET_FILE_DIR:Lumina>/ui/main.uix"
)


if(LibRaw_FOUND)
    target_link_libraries(Lumina PRIVATE LibRaw::LibRaw)
    target_compile_definitions(Lumina PRIVATE LUMINA_ENABLE_LIBRAW=1)
    if(LibRaw_DLL)
        add_custom_command(TARGET Lumina POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "${LibRaw_DLL}"
                "$<TARGET_FILE_DIR:Lumina>"
        )
    endif()
else()
    message(STATUS "LibRaw not found; RAW formats will use placeholder decoder")
endif()
if(CoreUI_DLL)
    add_custom_command(TARGET Lumina POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${CoreUI_DLL}"
            "$<TARGET_FILE_DIR:Lumina>/core-ui.dll"
    )
endif()










