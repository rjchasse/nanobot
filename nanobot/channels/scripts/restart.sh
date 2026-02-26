#!/bin/bash
# Restart nanobot via launchd.
# Called by the /restart slash command in the Signal channel.

SERVICE="gui/$(id -u)/com.nanobot.gateway"

# Use kickstart -k to forcefully kill and immediately restart the service.
# This is more reliable than "stop" which can be delayed if the process is busy.
launchctl kickstart -k "$SERVICE"
