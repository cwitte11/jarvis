#!/bin/sh
# Rebuild ~/jarvis/bin/jarvis-launch. Rebuilding changes its signature, so macOS will ask for
# Calendar and Reminders permission again the next time a background job uses it.
set -e
cd "$(dirname "$0")"
swiftc -O launcher.swift -o ../bin/jarvis-launch -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker Info.plist
codesign --force --sign - --identifier com.carson.jarvis-launch ../bin/jarvis-launch
