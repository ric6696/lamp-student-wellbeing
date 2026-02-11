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

