# Grayscale library brightness test
# https://github.com/Timendus/thumby-grayscale
#
# B: cycling.
# A: switch between brightness levels.
# Down: B/W
# Up: Grayscale
# Left: B/W
# Right: Quit

from machine import freq
freq(200000000)

# Fix import path so it finds the grayscale library
import sys

# Import dependencies
from machine import reset
import thumby
from time import ticks_ms, sleep_ms
from utime import ticks_us, sleep_us, ticks_diff
import thumbyGrayscale as grayscale

# Initialization
gs = grayscale.display
gs.startGPU()
gs.setFPS(60)

# Display color chart
gs.drawFilledRectangle(0, 0, 72, 40, gs.WHITE)
gs.drawFilledRectangle(0, 0, 62, 30, gs.LIGHTGRAY)
gs.drawFilledRectangle(0, 0, 52, 20, gs.DARKGRAY)
gs.drawFilledRectangle(0, 0, 42, 10, gs.BLACK)
gs.drawText("Hello", 2, 31, gs.LIGHTGRAY)
gs.drawText("world!", 37, 31, gs.DARKGRAY)
gs.update()

t = 0
contrastCycle = -1
contrast = 127
while(thumby.buttonR.pressed() == False):
    if thumby.buttonL.justPressed():
        gs.stopGPU()
    if thumby.buttonU.justPressed():
        gs.stopGPU()
    if thumby.buttonD.justPressed():
        gs.startGPU()
    if thumby.buttonA.justPressed():
        contrastCycle = -1
        contrast = 0 if contrast==127 else 28 if contrast==0 else 127
        gs.brightness(contrast)
    if thumby.buttonB.justPressed():
        contrastCycle = 127
    if contrastCycle >= 0 and t%2:
        contrastCycle = (contrastCycle+1)%256
        gs.brightness(contrastCycle if contrastCycle<128 else 256-contrastCycle)
    gs.update()
    t += 1

gs.stopGPU()
freq(48000000)
reset