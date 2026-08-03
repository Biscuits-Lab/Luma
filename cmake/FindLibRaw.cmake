set(_libraw_roots
    "${LIBRAW_ROOT}"
    "$ENV{LIBRAW_ROOT}"
    "${CMAKE_CURRENT_LIST_DIR}/../third_party/libraw-sdk"
)

find_path(LibRaw_INCLUDE_DIR
    NAMES libraw/libraw.h
    PATHS ${_libraw_roots}
    PATH_SUFFIXES include
    NO_DEFAULT_PATH
)

find_library(LibRaw_LIBRARY
    NAMES raw raw_r libraw libraw_r
    PATHS ${_libraw_roots}
    PATH_SUFFIXES lib lib64 build out
    NO_DEFAULT_PATH
)

find_file(LibRaw_DLL
    NAMES libraw.dll raw.dll libraw_r.dll raw_r.dll
    PATHS ${_libraw_roots}
    PATH_SUFFIXES bin lib lib64 build out
    NO_DEFAULT_PATH
)

include(FindPackageHandleStandardArgs)
find_package_handle_standard_args(LibRaw
    REQUIRED_VARS LibRaw_INCLUDE_DIR LibRaw_LIBRARY
)

if(LibRaw_FOUND AND NOT TARGET LibRaw::LibRaw)
    add_library(LibRaw::LibRaw UNKNOWN IMPORTED)
    set_target_properties(LibRaw::LibRaw PROPERTIES
        IMPORTED_LOCATION "${LibRaw_LIBRARY}"
        INTERFACE_INCLUDE_DIRECTORIES "${LibRaw_INCLUDE_DIR}"
    )
endif()

mark_as_advanced(LibRaw_INCLUDE_DIR LibRaw_LIBRARY LibRaw_DLL)
