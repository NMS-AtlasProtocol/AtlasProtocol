# 🔴 ATLAS_PROTOCOL // PROGRESS_MATRIX_v1.1

**Atlas Protocol** is a lightweight, fully automated, standalone Windows utility designed to decrypt, modify, and patch expedition milestone progress data for *No Man's Sky*—with **zero manual copy-pasting required**.

Unlike generic text modifications that require players to manually traverse massive, nested JSON trees inside a raw save file editor, Atlas Protocol interacts directly with local binary files, visualizes progress against real target metrics, and updates player data safely via automated background pipelines.

---

## ⚡ Key Features

* **Native Automated Save Parsing:** Bypasses manual clipboard operations. The program automatically scans local standard Windows directories for Steam/GOG installations, identifies active save profiles, and reads active save files in memory.
* **In-Memory Decompression Bridge:** Uses an embedded `lz4` block stream architecture to handle the game's obfuscated file blocks safely without risking save file corruption.
* **Five-Column Progress Grid:** Displays your current live metrics side-by-side with the engine's internal **Max Goal Values** and real English localization mission descriptors (e.g., `P1: Rocketman`).
* **Dynamic Color-Shift Tracers:** Entry boxes feature an active variable value tracer. The split-second you modify or edit a number, the text color inside that field instantly flashes into milestone amber alert text (`#f2b134`) to show pending changes.
* **One-Click Bulk Max Overdrive:** Instantly completes all 43 milestone registers with a single macro click, automatically scaling high thresholds (like travel distance or units) safely past their activation checkpoints.
* **Automated Safety Backups:** Automatically generates timestamped file backups inside `%APPDATA%\NMSMilestoneEditor\backups\` prior to executing any direct game directory write phase.

---

## 🏃‍♂️ Quick Start (For Users)

1. Close **No Man's Sky** completely (the application layer will block write sequences if `NMS.exe` is actively running).
2. Download the latest pre-compiled standalone binary from the Github repo or nexus link.
3. Launch `AtlasProtocol.exe` (The customized Atlas Glitch icon will pin cleanly to your Windows taskbar).
4. Use the top dropdown menu selector to select your target Save Profile Slot.
5. Customize individual parameters manually using intuitive cell tab navigation, or hit **Bulk Max All Rows**.
6. Click the green **"Save Changes directly to Game"** button.
7. **CRITICAL REWARD STEP:** Boot No Man's Sky and load into that **specific Expedition Save Slot first**. Open your Expedition Log menu and manually click to claim every single milestone reward badge and the final Phase Reward boxes. **You must claim them here first to register the permanent unlock to your account profile!**
8. Switch over to your main normal character save, board the Space Anomaly, speak to the Quicksilver Companion vendor, and claim your permanent account-wide rewards for 0 Quicksilver!

---

## 🛠️ Development & Local Compilation

If you prefer to run the raw, open-source Python script directly or compile the standalone binary executable locally on your own machine, follow these steps:

### Prerequisites
Ensure your local environment has Python 3.12+ installed along with the required graphic layout and binary compression frameworks:
```bash
pip install customtkinter lz4 pyinstaller
```

### Local Build Script Execution
Place your custom `atlas_diamond.ico` application graphic asset inside the root directory and execute the PyInstaller compilation command macro to generate a clean, single-file bundle:
```cmd
pyinstaller --onefile --noconsole --icon="atlas_diamond.ico" --add-data "atlas_diamond.ico;." --name AtlasProtocol --collect-all customtkinter --hidden-import lz4.block nms_milestone_editor.py
```
The compiled standalone binary executable will generate directly inside your local `/dist` directory.

---

## ❤️ Support the Protocol Development

This protocol utility was written completely out of pure passion for the *No Man's Sky* community and is distributed 100% free, ad-free, and open-source. 

If this tool saved you hours of tedious milestone grinding and you would like to support continuous optimization or fund the development of **Version 2.0 (The Live Nanite/Quicksilver Wallet Injector & Inventory Tech Slot Multiplier Tab)**, consider throwing a tip into the project warp drive!

🔴 **[Consider funding future development: Ko-fi Page](https://ko-fi.com/AtlasProtocol)**

---

*Disclaimer: This is an independent third-party save profile modification utility. Always ensure your backup folders are secure before applying directory patches. MIT License © 2026.*
