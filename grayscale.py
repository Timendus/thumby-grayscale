import thumby
from framebuf import FrameBuffer, MONO_VLSB
from time import sleep_us, ticks_us
import _thread
import ujson
import os

# This is the "display" object that we give to Thumby's GraphicsClass. It
# doesn't interact with the display though, instead it just holds a buffer for
# the Grayscale class below to pull the data from.
class GsBuffer:
    def __init__(self, width, height):
        self.buffer = bytearray(int(width / 8 * height))

    def show(self):
        pass

    def contrast(self, value):
        thumby.display.display.contrast(value)

# This class is a thin wrapper around two instances of Thumby's Sprite class.
class Sprite:
    def __init__(self, width, height, layer1Data, layer2Data, x=0, y=0, key=-1, mirrorX=False, mirrorY=False):
        self.layer1 = thumby.Sprite(width, height, layer1Data, x, y, key, mirrorX, mirrorY)
        self.layer2 = thumby.Sprite(width, height, layer2Data, x, y, key, mirrorX, mirrorY)

    @property
    def x(self):
        return self.layer1.x

    @property
    def y(self):
        return self.layer1.y

    @property
    def key(self):
        return self.layer1.key

    @property
    def mirrorX(self):
        return self.layer1.mirrorX

    @property
    def mirrorY(self):
        return self.layer1.mirrorY

    @x.setter
    def x(self, v):
        self.layer1.x = v
        self.layer2.x = v

    @y.setter
    def y(self, v):
        self.layer1.y = v
        self.layer2.y = v

    @key.setter
    def key(self, v):
        self.layer1.key = v
        self.layer2.key = v

    @mirrorX.setter
    def mirrorX(self, v):
        self.layer1.mirrorX = v
        self.layer2.mirrorX = v

    @mirrorY.setter
    def mirrorY(self, v):
        self.layer1.mirrorY = v
        self.layer2.mirrorY = v

# This is the main Grayscale class that does the heavy lifting. If quacks like
# Thumby's GraphicsClass (`thumby.display`) but it proxies most of that logic to
# two underlying GraphicsClass instances. And it manages the thread on the
# second CPU core that uses the buffers in the "display" objects (above) of both
# GraphicsClass instances to show one grayscale image.
class Grayscale:
    STOPPED  = const(0)
    RUNNING  = const(1)
    STOPPING = const(2)

    def __init__(self):
        self.BLACK     = 0
        self.LIGHTGRAY = 1
        self.DARKGRAY  = 2
        self.WHITE     = 3

        self.state = Grayscale.RUNNING
        self.width = thumby.display.width
        self.height = thumby.display.height

        self.gsBuffer1 = GsBuffer(self.width, self.height)
        self.gsBuffer2 = GsBuffer(self.width, self.height)
        self.gsBuffer3 = GsBuffer(self.width, self.height)
        self.gsGraphics1 = thumby.GraphicsClass(self.gsBuffer1, self.width, self.height)
        self.gsGraphics2 = thumby.GraphicsClass(self.gsBuffer2, self.width, self.height)

        try:
            self.config = ConfigFile.load()
        except:
            self.config = {
                "displayRefreshTime": 27400
            }
            self.saveConfig()

        _thread.start_new_thread(self._gsThread, ())

    def stop(self):
        self.state = Grayscale.STOPPING
        while self.state != Grayscale.STOPPED:
            sleep_us(1000)

    def saveConfig(self):
        ConfigFile.save(self.config)

    ### Be a proxy to GraphicsClass

    @micropython.native
    def setFont(self, fontFile, width, height, space):
        self.gsGraphics1.setFont(fontFile, width, height, space)
        self.gsGraphics2.setFont(fontFile, width, height, space)

    @micropython.native
    def setFPS(self):
        pass

    @micropython.native
    def update(self):
        pass

    @micropython.native
    def brightness(self, setting):
        self.gsGraphics1.brightness(setting)

    @micropython.viper
    def fill(self, color:int):
        self.gsGraphics1.fill(1 if color & 1 else 0)
        self.gsGraphics2.fill(1 if color & 2 else 0)
        self._joinBuffers()

    @micropython.viper
    def setPixel(self, x:int, y:int, color:int):
        self.gsGraphics1.setPixel(x, y, 1 if color & 1 else 0)
        self.gsGraphics2.setPixel(x, y, 1 if color & 2 else 0)
        self._joinBuffers()

    @micropython.viper
    def getPixel(self, x:int, y:int) -> int:
        layer1 = int(self.gsGraphics1.getPixel(x, y))
        layer2 = int(self.gsGraphics2.getPixel(x, y))
        return layer1 | (layer2 << 1)

    @micropython.viper
    def drawLine(self, x1:int, y1:int, x2:int, y2:int, color:int):
        self.gsGraphics1.drawLine(x1, y1, x2, y2, 1 if color & 1 else 0)
        self.gsGraphics2.drawLine(x1, y1, x2, y2, 1 if color & 2 else 0)
        self._joinBuffers()

    @micropython.viper
    def drawRectangle(self, x:int, y:int, width:int, height:int, color:int):
        self.gsGraphics1.drawRectangle(x, y, width, height, 1 if color & 1 else 0)
        self.gsGraphics2.drawRectangle(x, y, width, height, 1 if color & 2 else 0)
        self._joinBuffers()

    @micropython.viper
    def drawFilledRectangle(self, x:int, y:int, width:int, height:int, color:int):
        self.gsGraphics1.drawFilledRectangle(x, y, width, height, 1 if color & 1 else 0)
        self.gsGraphics2.drawFilledRectangle(x, y, width, height, 1 if color & 2 else 0)
        self._joinBuffers()

    @micropython.viper
    def drawText(self, stringToPrint:ptr8, x:int, y:int, color:int):
        self.gsGraphics1.drawText(stringToPrint, x, y, 1 if color & 1 else 0)
        self.gsGraphics2.drawText(stringToPrint, x, y, 1 if color & 2 else 0)
        self._joinBuffers()

    @micropython.native
    def drawSprite(self, sprite):
        self.gsGraphics1.drawSprite(sprite.layer1)
        self.gsGraphics2.drawSprite(sprite.layer2)
        self._joinBuffers()

    @micropython.native
    def drawSpriteWithMask(self, s, m):
        self.gsGraphics1.drawSpriteWithMask(sprite.layer1, m.layer1)
        self.gsGraphics2.drawSpriteWithMask(sprite.layer2, m.layer2)
        self._joinBuffers()

    ### Internal methods

    @micropython.viper
    def _joinBuffers(self):
        gs1 = ptr8(self.gsBuffer1.buffer)
        gs2 = ptr8(self.gsBuffer2.buffer)
        gs3 = ptr8(self.gsBuffer3.buffer)
        size = int(self.width) * int(self.height) >> 3
        for i in range(size):
            gs3[i] = gs1[i] & gs2[i]

    @micropython.viper
    def _gsThread(self):
        disp = thumby.display.display
        buf1 = self.gsBuffer1.buffer
        buf2 = self.gsBuffer2.buffer
        buf3 = self.gsBuffer3.buffer

        while self.state == Grayscale.RUNNING:
            startTime = ticks_us()
            refreshTime = int(self.config["displayRefreshTime"])

            # Show first buffer (dark gray & black)
            disp.cs(1)
            disp.dc(1)
            disp.cs(0)
            disp.spi.write(buf1)
            disp.cs(1)

            # Wait until half of displayRefreshTime has passed
            halfTime = refreshTime // 2
            while int(ticks_us() - startTime) < halfTime:
                sleep_us(10)

            # Show second buffer (light gray & black)
            disp.cs(1)
            disp.dc(1)
            disp.cs(0)
            disp.spi.write(buf2)
            disp.cs(1)

            # Wait until three quarters of displayRefreshTime has passed
            threeQuartersTime = 3 * refreshTime//4
            while int(ticks_us() - startTime) < threeQuartersTime:
                sleep_us(10)

            # Show third buffer (light gray, dark gray & black)
            disp.cs(1)
            disp.dc(1)
            disp.cs(0)
            disp.spi.write(buf3)
            disp.cs(1)

            # Wait until all of displayRefreshTime has passed
            while int(ticks_us() - startTime) < refreshTime:
                sleep_us(10)

        self.state = Grayscale.STOPPED

# Abstraction of the JSON config file
class ConfigFile:
    FILENAME = 'grayscale.conf.json'

    @classmethod
    def load(cls):
        if not cls.FILENAME in os.listdir():
            raise Exception("Config file not found")

        file = open(cls.FILENAME, 'r')
        config = False
        try:
            config = ujson.load(file)
        except:
            raise Exception("Could not read or parse config file")
        finally:
            file.close()
        return config

    @classmethod
    def save(cls, config):
        file = open(cls.FILENAME, 'w')
        try:
            file.write(ujson.dumps(config))
        except:
            return False
        finally:
            file.close()
        return True

# Built-in display sync calibration tool
class Calibration:
    background = Sprite(72, 40, bytearray([
            132,132,132,255,252,252,252,252,255,132,132,132,4,127,60,60,60,28,31,4,4,4,4,31,28,60,60,60,127,4,132,132,132,255,252,252,252,252,255,132,132,132,132,255,252,252,252,252,31,228,228,228,36,47,44,236,236,236,31,132,132,132,132,255,252,252,252,252,255,132,132,132,
            16,16,16,255,240,240,240,16,7,3,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,3,7,16,240,240,240,255,31,31,31,31,255,240,240,240,240,0,255,255,255,0,0,0,255,255,255,0,31,31,31,31,255,240,240,240,240,255,16,16,16,
            66,66,66,255,195,195,0,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,0,195,195,255,126,126,126,126,255,195,195,195,195,0,255,255,255,0,0,0,255,255,255,0,126,126,126,126,255,195,195,195,195,255,66,66,66,
            8,8,8,255,15,15,15,8,231,223,191,127,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,127,191,223,231,8,15,15,15,255,248,0,252,252,253,253,253,253,253,252,253,253,253,253,253,253,253,253,253,252,252,252,252,252,253,253,253,3,15,255,8,8,8,
            33,33,33,255,63,63,63,63,255,33,33,33,32,254,61,61,61,59,251,35,35,35,35,251,59,61,61,61,254,32,33,33,33,255,63,63,63,63,255,33,32,39,39,247,55,55,55,55,247,39,39,39,39,247,55,55,55,55,247,39,39,39,39,247,55,55,56,63,255,33,33,33
        ]), bytearray([
            132,132,132,255,132,132,132,132,255,252,252,252,124,127,4,4,4,4,31,28,28,220,220,223,196,132,132,132,127,124,252,252,252,255,132,132,132,132,255,252,252,252,252,255,132,132,132,132,31,236,236,236,172,47,164,228,228,228,31,252,252,252,252,255,132,132,132,132,255,132,132,132,
            16,16,16,255,31,31,31,31,7,0,0,0,0,0,0,0,0,0,0,0,0,255,255,255,255,255,255,255,255,255,254,252,248,231,31,31,31,31,255,240,240,240,240,255,31,31,31,31,0,255,255,255,255,0,255,255,255,255,0,240,240,240,240,255,31,31,31,31,255,16,16,16,
            66,66,66,255,126,126,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,255,255,255,255,255,255,255,255,255,255,255,255,255,255,0,126,126,255,195,195,195,195,255,126,126,126,126,0,255,255,255,127,0,127,255,255,255,0,195,195,195,195,255,126,126,126,126,255,66,66,66,
            8,8,8,255,248,248,248,248,224,0,0,0,0,0,0,0,0,0,0,0,0,255,255,255,255,255,255,255,255,255,127,63,31,231,248,248,248,248,255,15,3,253,253,253,252,252,252,252,252,253,253,253,253,253,253,253,253,253,252,253,253,253,253,253,252,252,0,248,255,8,8,8,
            33,33,33,255,33,33,33,33,255,63,63,63,62,254,32,32,32,32,248,56,56,59,59,251,35,33,33,33,254,62,63,63,63,255,33,33,33,33,255,63,56,55,55,247,39,39,39,39,247,55,55,55,55,247,39,39,39,39,247,55,55,55,55,247,39,39,32,33,255,33,33,33
        ])
    )
    handle = Sprite(7, 2, bytearray([1,1,1,1,1,1,1]), bytearray([0,0,0,0,0,0,0]), 50, 0)

    def __init__(self, gs):
        self.gs = gs

    def start(self):
        if self.gs.state != Grayscale.RUNNING:
            return
        while self._anyKeyPressed():
            pass

        while True:
            self._drawScene()
            while not self._anyKeyPressed():
                pass
            if thumby.buttonU.pressed():
                self.gs.config["displayRefreshTime"] += 100
            if thumby.buttonD.pressed():
                self.gs.config["displayRefreshTime"] -= 100
            if thumby.buttonL.pressed():
                self.gs.config["displayRefreshTime"] -= 10
            if thumby.buttonR.pressed():
                self.gs.config["displayRefreshTime"] += 10
            if self.gs.config["displayRefreshTime"] < 0:
                self.gs.config["displayRefreshTime"] = 0
            if self.gs.config["displayRefreshTime"] > 99990:
                self.gs.config["displayRefreshTime"] = 99990
            if thumby.buttonA.pressed() or thumby.buttonB.pressed():
                return

    def _drawScene(self):
        refreshTime = self.gs.config["displayRefreshTime"]
        self.gs.drawSprite(Calibration.background)
        Calibration.handle.y = Math.lerp(22, 6, refreshTime / 100000)
        self.gs.drawSprite(Calibration.handle)
        self.gs.drawText(f'{refreshTime:05d}'[:4], 42, 27, self.gs.BLACK)

    def _anyKeyPressed(self):
        return thumby.buttonU.pressed() or thumby.buttonD.pressed() or thumby.buttonL.pressed() or thumby.buttonR.pressed() or thumby.buttonA.pressed() or thumby.buttonB.pressed()

class Math:
    @classmethod
    def lerp(cls, start, stop, amt):
        return start + (stop-start) * amt
