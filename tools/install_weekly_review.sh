#!/bin/zsh
# Install (or reinstall) the weekly progress review as a launchd job: Sundays at 20:00.
# If the Mac is asleep then, launchd runs it when the Mac wakes.
#   tools/install_weekly_review.sh            install
#   tools/install_weekly_review.sh remove     uninstall
set -e
LABEL=com.gymplanner.weekly-review
PLIST=$HOME/Library/LaunchAgents/$LABEL.plist
REPO=${0:A:h:h}
launchctl bootout gui/$(id -u)/$LABEL 2>/dev/null || true
if [[ $1 == remove ]]; then rm -f $PLIST; echo "removed"; exit 0; fi
cat > $PLIST <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$REPO/.venv/bin/python</string><string>$REPO/db/weekly_review.py</string></array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin</string></dict>
  <key>StartCalendarInterval</key><dict><key>Weekday</key><integer>0</integer><key>Hour</key><integer>20</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/gym-weekly-review.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/gym-weekly-review.log</string>
</dict></plist>
PL
launchctl bootstrap gui/$(id -u) $PLIST
echo "installed: Sundays 20:00 (log: ~/Library/Logs/gym-weekly-review.log)"
