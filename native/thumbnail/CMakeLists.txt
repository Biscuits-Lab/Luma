cmake_minimum_required(VERSION 3.20)
project(LumaThumbnail LANGUAGES CXX)

if(NOT WIN32 OR NOT MSVC)
    message(FATAL_ERROR "Build with MSVC on Windows.")
endif()

if(NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
    message(FATAL_ERROR "LumaThumbnail requires a Windows x64 build.")
endif()

add_library(LumaThumbnail SHARED thumbnail.cpp exports.def)
target_compile_features(LumaThumbnail PRIVATE cxx_std_17)
target_compile_definitions(LumaThumbnail PRIVATE
    UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX
    WINVER=0x0A00 _WIN32_WINNT=0x0A00
)
target_compile_options(LumaThumbnail PRIVATE
    /W4 /permissive- /EHsc /sdl /guard:cf
)
target_link_options(LumaThumbnail PRIVATE
    /DYNAMICBASE /NXCOMPAT /guard:cf
)
target_link_libraries(LumaThumbnail PRIVATE
    ole32 shell32 uuid gdi32 bcrypt
)
set_property(TARGET LumaThumbnail PROPERTY
    MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>"
)

include(CTest)
if(BUILD_TESTING)
    add_executable(thumbnail_tests thumbnail_tests.cpp)
    target_compile_features(thumbnail_tests PRIVATE cxx_std_17)
    target_compile_definitions(thumbnail_tests PRIVATE
        UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX
        WINVER=0x0A00 _WIN32_WINNT=0x0A00
    )
    target_compile_options(thumbnail_tests PRIVATE
        /W4 /permissive- /EHsc /sdl /guard:cf
    )
    target_link_options(thumbnail_tests PRIVATE
        /DYNAMICBASE /NXCOMPAT /guard:cf
    )
    target_link_libraries(thumbnail_tests PRIVATE
        ole32 uuid gdi32 shlwapi shell32
    )
    set_property(TARGET thumbnail_tests PROPERTY
        MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>"
    )

    set(NO_HELPER_DIR
        "${CMAKE_CURRENT_BINARY_DIR}/fixtures/$<CONFIG>/no-helper")
    set(HELPER_DIR
        "${CMAKE_CURRENT_BINARY_DIR}/fixtures/$<CONFIG>/helper")

    # This synthetic helper is never installed or packaged with the app.
    add_executable(thumbnail_fixture_helper thumbnail_fixture_helper.cpp)
    target_compile_features(thumbnail_fixture_helper PRIVATE cxx_std_17)
    target_compile_definitions(thumbnail_fixture_helper PRIVATE
        UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX
        WINVER=0x0A00 _WIN32_WINNT=0x0A00
    )
    target_compile_options(thumbnail_fixture_helper PRIVATE
        /W4 /permissive- /EHsc /sdl /guard:cf
    )
    target_link_options(thumbnail_fixture_helper PRIVATE
        /DYNAMICBASE /NXCOMPAT /guard:cf
    )
    set_target_properties(thumbnail_fixture_helper PROPERTIES
        OUTPUT_NAME Luma
        RUNTIME_OUTPUT_DIRECTORY "${HELPER_DIR}"
        MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>"
    )

    # Re-stage on every test build, including when only fixture wiring changed.
    add_custom_target(thumbnail_test_fixtures
        COMMAND "${CMAKE_COMMAND}" -E make_directory
            "${NO_HELPER_DIR}" "${HELPER_DIR}"
        COMMAND "${CMAKE_COMMAND}" -E copy_if_different
            "$<TARGET_FILE:LumaThumbnail>"
            "${NO_HELPER_DIR}/LumaThumbnail.dll"
        COMMAND "${CMAKE_COMMAND}" -E copy_if_different
            "$<TARGET_FILE:LumaThumbnail>"
            "${HELPER_DIR}/LumaThumbnail.dll"
        DEPENDS LumaThumbnail
        VERBATIM
    )
    add_dependencies(thumbnail_tests
        thumbnail_test_fixtures thumbnail_fixture_helper)

    add_test(NAME thumbnail_native
        COMMAND thumbnail_tests "${NO_HELPER_DIR}/LumaThumbnail.dll")
    add_test(NAME thumbnail_protocol
        COMMAND thumbnail_tests "${HELPER_DIR}/LumaThumbnail.dll"
            --fixture-protocol)
    add_test(NAME thumbnail_timeout
        COMMAND thumbnail_tests "${HELPER_DIR}/LumaThumbnail.dll"
            --fixture-timeout)
    set_tests_properties(thumbnail_native thumbnail_protocol
        PROPERTIES TIMEOUT 60)
    set_tests_properties(thumbnail_timeout PROPERTIES TIMEOUT 35)
endif()

# Install next to the one-directory Luma.exe, not inside _internal.
install(TARGETS LumaThumbnail RUNTIME DESTINATION .)
