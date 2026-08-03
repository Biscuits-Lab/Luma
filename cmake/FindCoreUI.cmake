set(_core_ui_roots
    "${CORE_UI_ROOT}"
    "$ENV{CORE_UI_ROOT}"
    "${CMAKE_CURRENT_LIST_DIR}/../third_party/core-ui"
)

find_path(CoreUI_INCLUDE_DIR
    NAMES ui_core.h
    PATHS ${_core_ui_roots}
    PATH_SUFFIXES include
    NO_DEFAULT_PATH
)

find_library(CoreUI_LIBRARY
    NAMES core-ui core_ui ui_core
    PATHS ${_core_ui_roots}
    PATH_SUFFIXES lib build out out/lib
    NO_DEFAULT_PATH
)

find_file(CoreUI_DLL
    NAMES core-ui.dll core_ui.dll ui_core.dll
    PATHS ${_core_ui_roots}
    PATH_SUFFIXES bin lib build out out/bin
    NO_DEFAULT_PATH
)

include(FindPackageHandleStandardArgs)
find_package_handle_standard_args(CoreUI
    REQUIRED_VARS CoreUI_INCLUDE_DIR CoreUI_LIBRARY
)

if(CoreUI_FOUND AND NOT TARGET CoreUI::CoreUI)
    add_library(CoreUI::CoreUI UNKNOWN IMPORTED)
    set_target_properties(CoreUI::CoreUI PROPERTIES
        IMPORTED_LOCATION "${CoreUI_LIBRARY}"
        INTERFACE_INCLUDE_DIRECTORIES "${CoreUI_INCLUDE_DIR}"
    )
endif()

mark_as_advanced(CoreUI_INCLUDE_DIR CoreUI_LIBRARY CoreUI_DLL)
