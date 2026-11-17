#!/bin/zsh
# Install (or reinstall) the hourly Garmin sync as a launchd job for this user. Run after db/garmin_key.py.
#   tools/install_garmin_sync.sh            install + start
#   tools/install_garmin_sync.sh remove     stop + uninstall
set -e
LABEL=com.gymplanner.garmin-sync
PLIST=$HOME/Library/LaunchAgents/$LABEL.plist
REPO=${0:A:h:h}
launchctl bootout gui/$(id -u)/$LABEL 2>/dev/null || true
if [[ $1 == remove ]]; then rm -f $PLIST; echo "removed"; exit 0; fi
cat > $PLIST <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$REPO/.venv/bin/python</string><string>$REPO/db/garmin_sync.py</string></array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/gym-garmin-sync.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/gym-garmin-sync.log</string>
</dict></plist>
PL
launchctl bootstrap gui/$(id -u) $PLIST
echo "installed: runs now and every hour (log: ~/Library/Logs/gym-garmin-sync.log)"
