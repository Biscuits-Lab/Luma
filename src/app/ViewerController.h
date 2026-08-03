#pragma once

#include "image/DecoderRegistry.h"
#include "image/FolderImageIndex.h"
#include "image/ImageFrame.h"

#include <filesystem>
#include <string>

#include <ui_core.h>

namespace lumina::app {

class ViewerController {
public:
    explicit ViewerController(image::DecoderRegistry& decoders);
    ~ViewerController();

    ViewerController(const ViewerController&) = delete;
    ViewerController& operator=(const ViewerController&) = delete;

    [[nodiscard]] bool createWindow();
    void show();
    void open(const std::filesystem::path& path);
    void openPrevious();
    void openNext();
    void setZoomPercent(float percent);
    void updateViewport(float zoom);

private:
    static void onKey(UiWindow win, int vkCode, void* userdata);
    static void onResize(UiWindow win, int width, int height, void* userdata);
    static void onPinClicked(UiWidget widget, void* userdata);
    static void onFullscreenClicked(UiWidget widget, void* userdata);
    static void onSliderChanged(UiWidget widget, float value, void* userdata);
    static void onViewportChanged(UiWidget widget, float zoom, float panX, float panY, void* userdata);
    static void onDrop(UiWindow win, const wchar_t* path, void* userdata);

    void buildUi();
    float calculateFitZoom() const;
    void refreshFitZoom(bool preserveZoomPercent);
    float zoomPercentFromImageZoom(float zoom) const;
    float imageZoomFromZoomPercent(float percent) const;
    void toggleAlwaysOnTop();
    void toggleFullscreen();
    void updatePinButton();
    void updateFullscreenButton();
    void updateMetadata(const std::filesystem::path& path, const image::ImageFrame& frame);
    void setStatus(const std::wstring& text);
    void applyFrame(const image::ImageFrame& frame);

    image::DecoderRegistry& decoders_;
    image::FolderImageIndex folderIndex_;

    UiWindow window_ = UI_INVALID;
    UiWidget root_ = UI_INVALID;
    UiWidget imageView_ = UI_INVALID;
    UiWidget titleBar_ = UI_INVALID;
    UiWidget infoLabel_ = UI_INVALID;
    UiWidget zoomSlider_ = UI_INVALID;
    UiWidget zoomLabel_ = UI_INVALID;
    UiWidget pinButton_ = UI_INVALID;
    UiWidget fullscreenButton_ = UI_INVALID;

    std::filesystem::path currentPath_;
    bool updatingSlider_ = false;
    float fitZoom_ = 1.0F;
    bool alwaysOnTop_ = false;
    bool fullscreen_ = false;
    bool hasFullscreenRestoreRect_ = false;
    int fullscreenRestoreLeft_ = 0;
    int fullscreenRestoreTop_ = 0;
    int fullscreenRestoreWidth_ = 0;
    int fullscreenRestoreHeight_ = 0;
};

} // namespace lumina::app

