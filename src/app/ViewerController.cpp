#include "app/ViewerController.h"

#include "platform/WindowsStrings.h"

#include <Windows.h>

#include <algorithm>
#include <cstring>
#include <format>
#include <stdexcept>

namespace lumina::app {

namespace {
constexpr float kMinZoomPercent = 10.0F;
constexpr float kMaxZoomPercent = 800.0F;
constexpr float kDefaultZoomPercent = 100.0F;

UiColor rgba(float r, float g, float b, float a) {
    return UiColor{r, g, b, a};
}

std::wstring fileNameForDisplay(const std::filesystem::path& path) {
    if (path.empty()) {
        return L"Lumina Viewer";
    }
    return path.filename().wstring();
}
} // namespace

ViewerController::ViewerController(image::DecoderRegistry& decoders)
    : decoders_(decoders), folderIndex_(decoders) {}

ViewerController::~ViewerController() {
    if (window_ != UI_INVALID) {
        ui_window_destroy(window_);
        window_ = UI_INVALID;
    }
}

bool ViewerController::createWindow() {
    UiWindowConfig config{};
    config.title = L"Lumina Viewer";
    config.width = 1280;
    config.height = 820;
    config.system_frame = 0;
    config.resizable = 1;
    config.accept_files = 1;

    window_ = ui_window_create(&config);
    if (window_ == UI_INVALID) {
        return false;
    }

    buildUi();
    ui_window_on_key(window_, &ViewerController::onKey, this);
    ui_window_on_drop(window_, &ViewerController::onDrop, this);
    return true;
}

void ViewerController::buildUi() {
    root_ = ui_vbox();
    ui_widget_set_bg_color(root_, rgba(0.055F, 0.055F, 0.06F, 1.0F));
    ui_widget_set_gap(root_, 0.0F);

    titleBar_ = ui_titlebar(L"Lumina Viewer");
    ui_titlebar_show_buttons(titleBar_, 1, 1, 1);
    ui_titlebar_show_icon(titleBar_, 0);
    ui_titlebar_set_title_weight(titleBar_, 500);
    ui_titlebar_set_bg_color(titleBar_, rgba(0.07F, 0.07F, 0.078F, 0.94F));
    ui_widget_set_height(titleBar_, 44.0F);

    imageView_ = ui_image_view();
    ui_widget_set_expand(imageView_, 1);
    ui_image_set_zoom_range(imageView_, kMinZoomPercent / 100.0F, kMaxZoomPercent / 100.0F);
    ui_image_set_checkerboard(imageView_, 1);
    ui_image_set_antialias(imageView_, 1);
    ui_image_on_viewport_changed(imageView_, &ViewerController::onViewportChanged, this);

    auto bottomBar = ui_hbox();
    ui_widget_set_height(bottomBar, 54.0F);
    ui_widget_set_padding(bottomBar, 18.0F, 8.0F, 18.0F, 12.0F);
    ui_widget_set_gap(bottomBar, 12.0F);
    ui_widget_set_bg_color(bottomBar, rgba(0.07F, 0.07F, 0.078F, 0.92F));

    infoLabel_ = ui_label(L"Open an image to preview");
    ui_label_set_font_size(infoLabel_, 12.0F);
    ui_label_set_text_color(infoLabel_, rgba(0.86F, 0.86F, 0.89F, 0.82F));
    ui_widget_set_expand(infoLabel_, 1);

    zoomLabel_ = ui_label(L"100%");
    ui_label_set_align(zoomLabel_, 1);
    ui_label_set_font_size(zoomLabel_, 12.0F);
    ui_label_set_text_color(zoomLabel_, rgba(0.86F, 0.86F, 0.89F, 0.82F));
    ui_widget_set_width(zoomLabel_, 56.0F);

    zoomSlider_ = ui_slider(kMinZoomPercent, kMaxZoomPercent, kDefaultZoomPercent);
    ui_widget_set_width(zoomSlider_, 180.0F);
    ui_slider_on_changed(zoomSlider_, &ViewerController::onSliderChanged, this);

    ui_widget_add_child(bottomBar, infoLabel_);
    ui_widget_add_child(bottomBar, zoomLabel_);
    ui_widget_add_child(bottomBar, zoomSlider_);

    ui_widget_add_child(root_, titleBar_);
    ui_widget_add_child(root_, imageView_);
    ui_widget_add_child(root_, bottomBar);
    ui_window_set_root(window_, root_);
}

void ViewerController::show() {
    ui_window_show(window_);
}

void ViewerController::open(const std::filesystem::path& path) {
    try {
        folderIndex_.loadAround(path);
        currentPath_ = path;
        ui_image_set_loading(imageView_, 1);

        const auto result = decoders_.decode(path, image::DecodeOptions{.max_width = 3840, .max_height = 2160});
        applyFrame(result.frame);
        updateMetadata(path, result.frame);
        if (!result.warning.empty()) {
            ui_toast(window_, result.warning.c_str(), 4500);
        }
    } catch (const std::exception& error) {
        ui_image_set_loading(imageView_, 0);
        ui_image_clear(imageView_);
        const auto message = std::wstring(L"Failed to open image: ") + std::wstring(error.what(), error.what() + std::strlen(error.what()));
        setStatus(message);
    }
}

void ViewerController::openPrevious() {
    if (const auto previous = folderIndex_.previous()) {
        open(*previous);
    }
}

void ViewerController::openNext() {
    if (const auto next = folderIndex_.next()) {
        open(*next);
    }
}

void ViewerController::setZoomPercent(float percent) {
    const auto clamped = std::clamp(percent, kMinZoomPercent, kMaxZoomPercent);
    ui_image_set_zoom(imageView_, clamped / 100.0F);
    updateViewport(clamped / 100.0F);
}

void ViewerController::updateViewport(float zoom) {
    const auto percent = std::clamp(zoom * 100.0F, kMinZoomPercent, kMaxZoomPercent);
    ui_label_set_text(zoomLabel_, std::format(L"{:.0f}%", percent).c_str());

    updatingSlider_ = true;
    ui_slider_set_value(zoomSlider_, percent);
    updatingSlider_ = false;
}

void ViewerController::applyFrame(const image::ImageFrame& frame) {
    if (frame.empty()) {
        ui_image_clear(imageView_);
        return;
    }

    ui_image_set_pixels(
        imageView_,
        window_,
        frame.pixels.data(),
        frame.width,
        frame.height,
        frame.stride);
    ui_image_fit(imageView_);
    ui_image_set_loading(imageView_, 0);
}

void ViewerController::updateMetadata(const std::filesystem::path& path, const image::ImageFrame& frame) {
    ui_window_set_title(window_, fileNameForDisplay(path).c_str());
    ui_titlebar_set_title(titleBar_, fileNameForDisplay(path).c_str());

    const auto fileSize = platform::formatFileSize(platform::fileSizeOrZero(path));
    const auto info = std::format(L"{} × {} · {}", frame.width, frame.height, fileSize);
    ui_label_set_text(infoLabel_, info.c_str());
    updateViewport(ui_image_get_zoom(imageView_));
}

void ViewerController::setStatus(const std::wstring& text) {
    ui_label_set_text(infoLabel_, text.c_str());
}

void ViewerController::onKey(UiWindow, int vkCode, void* userdata) {
    auto* self = static_cast<ViewerController*>(userdata);
    if (!self) {
        return;
    }

    if (vkCode == VK_LEFT) {
        self->openPrevious();
    } else if (vkCode == VK_RIGHT) {
        self->openNext();
    }
}

void ViewerController::onSliderChanged(UiWidget, float value, void* userdata) {
    auto* self = static_cast<ViewerController*>(userdata);
    if (!self || self->updatingSlider_) {
        return;
    }
    self->setZoomPercent(value);
}

void ViewerController::onViewportChanged(UiWidget, float zoom, float, float, void* userdata) {
    auto* self = static_cast<ViewerController*>(userdata);
    if (!self) {
        return;
    }
    self->updateViewport(zoom);
}

void ViewerController::onDrop(UiWindow, const wchar_t* path, void* userdata) {
    auto* self = static_cast<ViewerController*>(userdata);
    if (!self || !path) {
        return;
    }
    self->open(std::filesystem::path(path));
}

} // namespace lumina::app



