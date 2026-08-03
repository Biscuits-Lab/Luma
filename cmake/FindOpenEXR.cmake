set(_openexr_roots
    "${OPENEXR_ROOT}"
    "$ENV{OPENEXR_ROOT}"
    "${CMAKE_CURRENT_LIST_DIR}/../third_party/openexr-sdk"
)

find_path(OpenEXR_INCLUDE_DIR
    NAMES OpenEXR/ImfRgbaFile.h OpenEXR/ImfInputFile.h
    PATHS ${_openexr_roots}
    PATH_SUFFIXES include
    NO_DEFAULT_PATH
)

find_library(OpenEXR_LIBRARY
    NAMES OpenEXR OpenEXR-3_3 OpenEXR-3_2 OpenEXR-3_1 IlmImf
    PATHS ${_openexr_roots}
    PATH_SUFFIXES lib lib64
    NO_DEFAULT_PATH
)

find_file(OpenEXR_DLL
    NAMES OpenEXR.dll OpenEXR-3_3.dll OpenEXR-3_2.dll OpenEXR-3_1.dll IlmImf.dll
    PATHS ${_openexr_roots}
    PATH_SUFFIXES bin lib lib64
    NO_DEFAULT_PATH
)

include(FindPackageHandleStandardArgs)
find_package_handle_standard_args(OpenEXR REQUIRED_VARS OpenEXR_INCLUDE_DIR OpenEXR_LIBRARY)

if(OpenEXR_FOUND AND NOT TARGET OpenEXR::OpenEXR)
    add_library(OpenEXR::OpenEXR UNKNOWN IMPORTED)
    set_target_properties(OpenEXR::OpenEXR PROPERTIES
        IMPORTED_LOCATION "${OpenEXR_LIBRARY}"
        INTERFACE_INCLUDE_DIRECTORIES "${OpenEXR_INCLUDE_DIR}"
    )
endif()

mark_as_advanced(OpenEXR_INCLUDE_DIR OpenEXR_LIBRARY OpenEXR_DLL)
