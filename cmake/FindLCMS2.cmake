set(_lcms_roots
    "${LCMS2_ROOT}"
    "$ENV{LCMS2_ROOT}"
    "${CMAKE_CURRENT_LIST_DIR}/../third_party/lcms2-sdk"
)

find_path(LCMS2_INCLUDE_DIR
    NAMES lcms2.h
    PATHS ${_lcms_roots}
    PATH_SUFFIXES include
    NO_DEFAULT_PATH
)

find_library(LCMS2_LIBRARY
    NAMES lcms2 lcms2_static liblcms2
    PATHS ${_lcms_roots}
    PATH_SUFFIXES lib lib64
    NO_DEFAULT_PATH
)

find_file(LCMS2_DLL
    NAMES lcms2.dll liblcms2.dll
    PATHS ${_lcms_roots}
    PATH_SUFFIXES bin lib lib64
    NO_DEFAULT_PATH
)

include(FindPackageHandleStandardArgs)
find_package_handle_standard_args(LCMS2 REQUIRED_VARS LCMS2_INCLUDE_DIR LCMS2_LIBRARY)

if(LCMS2_FOUND AND NOT TARGET LCMS2::LCMS2)
    add_library(LCMS2::LCMS2 UNKNOWN IMPORTED)
    set_target_properties(LCMS2::LCMS2 PROPERTIES
        IMPORTED_LOCATION "${LCMS2_LIBRARY}"
        INTERFACE_INCLUDE_DIRECTORIES "${LCMS2_INCLUDE_DIR}"
    )
endif()

mark_as_advanced(LCMS2_INCLUDE_DIR LCMS2_LIBRARY LCMS2_DLL)
