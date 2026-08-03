#include "image/LibRawDecoder.h"

#include <libraw/libraw.h>

#include <algorithm>
#include <stdexcept>
#include <string>

namespace lumina::image {

namespace {
void checkLibRaw(int code, const char* operation) {
    if (code != LIBRAW_SUCCESS) {
        std::string message = operation;
        message += " failed: ";
        message += libraw_strerror(code);
        throw std::runtime_error(message);
    }
}
} // namespace

bool LibRawDecoder::canDecodeExtension(std::wstring_view extension) const {
    return std::ranges::find(supportedExtensions_, extension) != supportedExtensions_.end();
}

DecodeResult LibRawDecoder::decode(const std::filesystem::path& path, const DecodeOptions&) {
    LibRaw processor;
    processor.imgdata.params.use_camera_wb = 1;
    processor.imgdata.params.use_auto_wb = 0;
    processor.imgdata.params.output_bps = 8;
    processor.imgdata.params.no_auto_bright = 0;

    checkLibRaw(processor.open_file(path.string().c_str()), "LibRaw::open_file");
    checkLibRaw(processor.unpack(), "LibRaw::unpack");
    checkLibRaw(processor.dcraw_process(), "LibRaw::dcraw_process");

    libraw_processed_image_t* processed = processor.dcraw_make_mem_image(nullptr);
    if (!processed) {
        throw std::runtime_error("LibRaw::dcraw_make_mem_image failed.");
    }

    try {
        if (processed->type != LIBRAW_IMAGE_BITMAP || processed->colors < 3 || processed->bits != 8) {
            LibRaw::dcraw_clear_mem(processed);
            throw std::runtime_error("LibRaw produced an unsupported processed image format.");
        }

        ImageFrame frame;
        frame.width = static_cast<int>(processed->width);
        frame.height = static_cast<int>(processed->height);
        frame.stride = frame.width * 4;
        frame.pixels.resize(static_cast<std::size_t>(frame.stride) * static_cast<std::size_t>(frame.height));

        const auto* source = processed->data;
        for (int y = 0; y < frame.height; ++y) {
            for (int x = 0; x < frame.width; ++x) {
                const auto sourceOffset = static_cast<std::size_t>((y * frame.width + x) * processed->colors);
                const auto targetOffset = static_cast<std::size_t>(y * frame.stride + x * 4);
                frame.pixels[targetOffset + 0] = source[sourceOffset + 2];
                frame.pixels[targetOffset + 1] = source[sourceOffset + 1];
                frame.pixels[targetOffset + 2] = source[sourceOffset + 0];
                frame.pixels[targetOffset + 3] = 255;
            }
        }

        LibRaw::dcraw_clear_mem(processed);
        return DecodeResult{.frame = std::move(frame), .warning = {}};
    } catch (...) {
        LibRaw::dcraw_clear_mem(processed);
        throw;
    }
}

} // namespace lumina::image
