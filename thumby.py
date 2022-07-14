#!/usr/bin/env python3

# This sends everything in ./Games/ to your Thumby, and launches the program. It
# only sends files modified since the last send to your Tumby to save time. If
# you want to, it can also compile some specific files to `.mpy`, and send those
# instead of the `.py` files.

# Tested on Mac, depends on Ampy and mpy_cross being installed.
# See https://github.com/scientifichackers/ampy#installation for instructions
# for Ampy and do something like this for mpy_cross:
# pip3 install mpy-cross-v5

# Available commands:
#  `./thumby.py`       --> Compiles your project, sends it to Thumby and runs it
#  `./thumby.py build` --> Only compiles your project
#  `./thumby.py send`  --> Only sends the files in your project to the Thumby
#  `./thumby.py run`   --> Only runs the project on the Thumby


# Start of config:

# This should be your main file to run on the Thumby:
initFile = '/Games/GrayscaleTest/GrayscaleTest.py'

# This file keeps track of the last time files were sent to the Thumby.
# Add it to your .gitignore file if you use Git.
timeFile = 'send.time'

# These are the files you want to have compiled to `.mpy` files.
# Use an empty list to disable compilation.
filesToCompileToMPY = []


# End of config, start of script

from os import listdir, system
from os.path import isfile, isdir, join, getmtime, splitext, exists
from glob import glob
from textwrap import dedent
from inspect import getsource
from ampy import pyboard, files
from sys import argv

def build(files):
    import mpy_cross
    for file in files:
        name, ext = splitext(file)
        mpyfile = name + '.mpy'
        if not exists(mpyfile) or (exists(mpyfile) and getmtime(mpyfile) < getmtime(file)):
            print("Compiling file", file[1:])
            mpy_cross.run(file)


# Functions to run on the Thumby

def present(file):
    from os import stat
    try:
        stat(file)
        print(True)
    except OSError:
        print(False)

def startProgram(file):
    __import__(file)

# Thumby abstraction to use from the PC side

class Thumby:
    def _thumby(self):
        if not hasattr(self, 'thumby'):
            devices = glob('/dev/tty.usbmodem*')
            port = devices and devices[0]
            if not port:
                print('Could not find your Thumby! Is it plugged in and turned on..?')
                exit()
            try:
                self.thumby = pyboard.Pyboard(port)
            except pyboard.PyboardError as err:
                print('Ampy gave an error opening the device. Is code.thumby.us interfering..?')
                exit()
        return self.thumby

    def _files(self):
        if not hasattr(self, 'files'):
            self.files = files.Files(self._thumby())
        return self.files

    def _hasBeenUpdated(self, file):
        return not isfile(timeFile) or getmtime(file) > getmtime(timeFile)

    def _thumbyCall(self, command, streaming=False):
        self._thumby().enter_raw_repl()
        result = self._thumby().exec(dedent(command), streaming)
        self._thumby().exit_raw_repl()
        return result.decode('utf-8')

    def execute(self, function, args=[], verbose=False):
        code = dedent(getsource(function))
        args = map(lambda v: f'"{v}"' if isinstance(v, str) else f'{v}', args)
        args = ','.join(args)
        code += f'\n{function.__name__}({args})\n'
        if verbose: print("Running on Thumby:\n\n", code)
        return self._thumbyCall(code, verbose)

    def exists(self, file):
        return self.execute(present, [file]).strip() == "True"

    def put(self, localfile, remotefile):
        with open(localfile, "rb") as infile:
            data = infile.read()
            self._files().put(remotefile, data)

    def send(self, path):
        for f in listdir(path):
            file = join(path, f)
            remotefile = file[1:]

            # Skip hidden files, including .DS_Store
            if f[0] == '.':
                continue

            # Send newly updated files to Thumby
            if isfile(file) and self._hasBeenUpdated(file):
                name, ext = splitext(file)
                if ext == '.py' and isfile(name + '.mpy'):
                    if self.exists(remotefile):
                        print('Removing file', remotefile, '(because .mpy version exists now)')
                        self._files().rm(remotefile)
                    else:
                        print('Skipping file', remotefile, '(because there is a .mpy version)')
                else:
                    print("Sending file", remotefile)
                    self.put(file, remotefile)

            # Create directories that don't exist yet
            if isdir(file):
                self._files().mkdir(remotefile, True)
                self.send(file)


# When ran from the command line, execution starts here:

if __name__ == "__main__":
    arg = None if len(argv) < 2 else argv[1]
    thumby = Thumby()
    if (arg == None or arg == 'build') and len(filesToCompileToMPY) > 0:
        build(filesToCompileToMPY)
    if arg == None or arg == 'send':
        thumby.send('./Games/')
        system('touch send.time')
    if arg == None or arg == 'run':
        thumby.execute(startProgram, [initFile], verbose=True)
