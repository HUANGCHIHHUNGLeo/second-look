#!/bin/sh
# Downloads the sample video used by "sample:pasta" so the screen can replay clips.
# "Cooking with Laura: Homemade Pasta" by Northampton Community Television, CC BY 3.0, via Wikimedia Commons:
# https://commons.wikimedia.org/wiki/File:Cooking_with_Laura_Homemade_Pasta.webm
set -e
cd "$(dirname "$0")/../fixtures/pasta"
curl -L -A "crv-guide-mcp sample fetch" -o source.webm \
  "https://upload.wikimedia.org/wikipedia/commons/d/d7/Cooking_with_Laura_Homemade_Pasta.webm"
echo "Saved fixtures/pasta/source.webm"
