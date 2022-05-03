#!/bin/bash

# This sends the library and the test program to your Thumby, and launches it.
# Works on Mac, depends on Ampy being installed.

export AMPY_PORT=`ls /dev/tty.usbmodem*`

ampy put grayscale.py /Games/GrayscaleTest/grayscale.py
ampy put GrayscaleTest.py /Games/GrayscaleTest/GrayscaleTest.py
ampy run launch.py
