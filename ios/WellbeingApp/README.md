# iOS SwiftUI Client (Code Drop)

This folder contains a complete SwiftUI project structure.

## Quick Start (Git Clone Ready)

This repository uses **XcodeGen** to manage the project file. This ensures the project is always clean and merge-conflict free.

1. **Install XcodeGen**:
   ```bash
   brew install xcodegen
   ```
2. **Generate Project**:
   ```bash
   cd ios/WellbeingApp
   xcodegen generate
   ```
3. **Open**:
   ```bash
   open WellbeingApp.xcodeproj
   ```

## Requirements

- **Xcode 14+**
- **iOS 16+** (for advanced sleep stages and HealthKit features)
- **Physical Device**: Health, Motion, and Background Location updates require a real iPhone.

## Files

- Sources/: All Swift logic (Health, Location, Motion, Noise, Sync)
- Resources/: Info.plist, Entitlements, and Setup notes
- project.yml: The project definition file used by XcodeGen

## Per-developer setup (one-time)

This project uses a committed `Configs/Project.xcconfig` which includes a gitignored `Configs/Local.xcconfig`. Each developer should copy the example and set their local overrides (e.g. `USER_ID`, `TEAM_ID`).

```bash
cd ios/WellbeingApp
cp Configs/Local.example.xcconfig Configs/Local.xcconfig
# Edit Configs/Local.xcconfig and set USER_ID and optional TEAM_ID
open Configs/Local.xcconfig
```

After creating `Local.xcconfig`, generate the Xcode project as usual:

```bash
xcodegen generate
open WellbeingApp.xcodeproj
```

CI: ensure `Configs/Local.xcconfig` is provided during CI runs (or set the `USER_ID` env var before running `xcodegen`).

Runtime identity note: the canonical backend `user_id` is generated automatically by the app and stored in Keychain so it is more likely to survive reinstalls than a `UserDefaults`-backed identifier. Phone and watch samples keep distinct `device_id` values, and the phone uploader preserves watch-originated `device_id` values on individual readings.
