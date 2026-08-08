# LINE APK (Morphe Patches) - Auto Builder

[![Build Status](https://github.com/monsivamon/line-apk/actions/workflows/build.yaml/badge.svg)](https://github.com/monsivamon/line-apk/actions)
[![Latest Release](https://img.shields.io/github/v/release/monsivamon/line-apk)](https://github.com/monsivamon/line-apk/releases/latest)

Automated build system for applying [Andrew's Morphe Patches](https://github.com/andrewliang25/morphe-patches) to the LINE app.
**This builder is scheduled to run daily**, automatically checking for updates and compiling new releases whenever a new patch or base app version is available.

## ⚠️ Disclaimer
**App stability is NOT guaranteed.** This build system is configured to dynamically extract patch metadata directly from the Morphe CLI and forcefully apply **all compatible patches** to the LINE app, but unexpected bugs, crashes, or layout issues may still occur. Use at your own risk.

## ✨ Key Features & Improvements

### 1. Dual-Track Release System (Stable & Pre-release)
The builder independently monitors the upstream Morphe repository for both **Stable** and **Pre-release** updates, automatically triggering daily builds and appropriately tagging them so you always have access to the latest development channels.

### 2. Dynamic Patch Extraction via Morphe CLI
Instead of relying on fragile upstream JSON files, this pipeline parses patch metadata (including version compatibility) dynamically and directly from the **Morphe CLI** text output. This ensures the build process remains robust even if the upstream repository structure changes.

## 📥 Download

Get the latest pre-built LINE APKs from the **[Releases Page](https://github.com/monsivamon/line-apk/releases)**.

## 📖 Usage & Patch Details

For instructions on how to use the patched app, configure features, or see exactly what each patch does, please refer to the official patch repository:
👉 **[Andrew's Morphe Patches Documentation](https://github.com/andrewliang25/morphe-patches)**

## Credits

* [andrewliang25/morphe-patches](https://github.com/andrewliang25/morphe-patches) - The patch source.
* [MorpheApp/morphe-cli](https://github.com/MorpheApp/morphe-cli) - Morphe CLI patcher.
* [REAndroid/APKEditor](https://github.com/REAndroid/APKEditor) - Used for merging split APKs.