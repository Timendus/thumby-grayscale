import thumby
from framebuf import FrameBuffer, MONO_VLSB
from time import sleep_us, ticks_us
import _thread
import ujson

class Grayscale:
    STOPPED  = const(0)
    RUNNING  = const(1)
    STOPPING = const(2)

    CONFIG_FILE = '/grayscale.conf.json'

    def __init__(self):
        self.state = Grayscale.RUNNING
        self.width = thumby.display.width
        self.height = thumby.display.height
        self._loadConfig()

        self.gsArray1 = bytearray(int(self.width * self.height / 8))
        self.gsBuffer1 = FrameBuffer(self.gsArray1, self.width, self.height, MONO_VLSB)
        self.gsArray2 = bytearray(int(self.width * self.height / 8))
        self.gsBuffer2 = FrameBuffer(self.gsArray2, self.width, self.height, MONO_VLSB)
        self.gsArray3 = bytearray(int(self.width * self.height / 8))
        self.gsBuffer3 = FrameBuffer(self.gsArray3, self.width, self.height, MONO_VLSB)

        _thread.start_new_thread(self._gsThread, ())

    def stop(self):
        self.state = Grayscale.STOPPING
        while self.state != Grayscale.STOPPED:
            sleep_us(1000)

    def calibrationTool(self):
        if self.state != Grayscale.RUNNING:
            return
        while self._anyKeyPressed():
            pass
        background_layer1 = thumby.Sprite(72, 40, bytearray([
            132,132,132,255,252,252,252,252,255,132,132,132,4,127,60,60,60,28,31,4,4,4,4,31,28,60,60,60,127,4,132,132,132,255,252,252,252,252,255,132,132,132,132,255,252,252,252,252,31,228,228,228,36,47,44,236,236,236,31,132,132,132,132,255,252,252,252,252,255,132,132,132,
            16,16,16,255,240,240,240,16,7,3,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,3,7,16,240,240,240,255,31,31,31,31,255,240,240,240,240,0,255,255,255,0,0,0,255,255,255,0,31,31,31,31,255,240,240,240,240,255,16,16,16,
            66,66,66,255,195,195,0,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,240,0,195,195,255,126,126,126,126,255,195,195,195,195,0,255,255,255,0,0,0,255,255,255,0,126,126,126,126,255,195,195,195,195,255,66,66,66,
            8,8,8,255,15,15,15,8,231,223,191,127,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,127,191,223,231,8,15,15,15,255,248,0,252,252,253,253,253,253,253,252,253,253,253,253,253,253,253,253,253,252,252,252,252,252,253,253,253,3,15,255,8,8,8,
            33,33,33,255,63,63,63,63,255,33,33,33,32,254,61,61,61,59,251,35,35,35,35,251,59,61,61,61,254,32,33,33,33,255,63,63,63,63,255,33,32,39,39,247,55,55,55,55,247,39,39,39,39,247,55,55,55,55,247,39,39,39,39,247,55,55,56,63,255,33,33,33
        ]))
        background_layer2 = thumby.Sprite(72, 40, bytearray([
            132,132,132,255,132,132,132,132,255,252,252,252,124,127,4,4,4,4,31,28,28,220,220,223,196,132,132,132,127,124,252,252,252,255,132,132,132,132,255,252,252,252,252,255,132,132,132,132,31,236,236,236,172,47,164,228,228,228,31,252,252,252,252,255,132,132,132,132,255,132,132,132,
            16,16,16,255,31,31,31,31,7,0,0,0,0,0,0,0,0,0,0,0,0,255,255,255,255,255,255,255,255,255,254,252,248,231,31,31,31,31,255,240,240,240,240,255,31,31,31,31,0,255,255,255,255,0,255,255,255,255,0,240,240,240,240,255,31,31,31,31,255,16,16,16,
            66,66,66,255,126,126,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,255,255,255,255,255,255,255,255,255,255,255,255,255,255,0,126,126,255,195,195,195,195,255,126,126,126,126,0,255,255,255,127,0,127,255,255,255,0,195,195,195,195,255,126,126,126,126,255,66,66,66,
            8,8,8,255,248,248,248,248,224,0,0,0,0,0,0,0,0,0,0,0,0,255,255,255,255,255,255,255,255,255,127,63,31,231,248,248,248,248,255,15,3,253,253,253,252,252,252,252,252,253,253,253,253,253,253,253,253,253,252,253,253,253,253,253,252,252,0,248,255,8,8,8,
            33,33,33,255,33,33,33,33,255,63,63,63,62,254,32,32,32,32,248,56,56,59,59,251,35,33,33,33,254,62,63,63,63,255,33,33,33,33,255,63,56,55,55,247,39,39,39,39,247,55,55,55,55,247,39,39,39,39,247,55,55,55,55,247,39,39,32,33,255,33,33,33
        ]))
        handle_layer1 = thumby.Sprite(7, 2, bytearray([1,1,1,1,1,1,1]))
        handle_layer2 = thumby.Sprite(7, 2, bytearray([0,0,0,0,0,0,0]))
        while True:
            self.drawSprite(background_layer1, background_layer2)
            handle_layer1.x = handle_layer2.x = 50
            handle_layer1.y = handle_layer2.y = 6 + 16 - (self.config["displayRefreshTime"] / 100000 * (22 - 6)) # Between 6 and 22
            self.drawSprite(handle_layer1, handle_layer2)
            self.gsBuffer1.text(f'{self.config["displayRefreshTime"]:04d}'[:3], 41, 27, 0)
            self.gsBuffer2.text(f'{self.config["displayRefreshTime"]:04d}'[:3], 41, 27, 0)
            self.gsBuffer3.text(f'{self.config["displayRefreshTime"]:04d}'[:3], 41, 27, 0)
            while not self._anyKeyPressed():
                pass
            if thumby.buttonU.pressed():
                self.config["displayRefreshTime"] += 100
            if thumby.buttonD.pressed():
                self.config["displayRefreshTime"] -= 100
            if thumby.buttonL.pressed():
                self.config["displayRefreshTime"] -= 10
            if thumby.buttonR.pressed():
                self.config["displayRefreshTime"] += 10
            if self.config["displayRefreshTime"] < 0:
                self.config["displayRefreshTime"] = 0
            if self.config["displayRefreshTime"] > 99990:
                self.config["displayRefreshTime"] = 99990
            if thumby.buttonA.pressed() or thumby.buttonB.pressed():
                self._saveConfig()
                return

    @micropython.native
    def drawSprite(self, s1, s2):
        bitmap1 = FrameBuffer(s1.bitmap, s1.width, s1.height, MONO_VLSB)
        bitmap2 = FrameBuffer(s2.bitmap, s2.width, s2.height, MONO_VLSB)
        self.gsBuffer1.blit(bitmap1, int(s1.x), int(s1.y), int(s1.width), int(s1.height))
        self.gsBuffer2.blit(bitmap2, int(s2.x), int(s2.y), int(s2.width), int(s2.height))
        self._joinBuffers()

    def _loadConfig(self):
        try:
            with open(Grayscale.CONFIG_FILE, 'r') as file:
                self.config = ujson.load(file)
        except:
            self.config = {
                "displayRefreshTime": 27400
            }
            self._saveConfig()

    def _saveConfig(self):
        try:
            with open(Grayscale.CONFIG_FILE, 'w') as file:
                ujson.dump(self.config, file)
        except Exception as err:
            print("Couldn't write to config file:", err)

    def _anyKeyPressed(self):
        return thumby.buttonU.pressed() or thumby.buttonD.pressed() or thumby.buttonL.pressed() or thumby.buttonR.pressed() or thumby.buttonA.pressed() or thumby.buttonB.pressed()

    @micropython.viper
    def _joinBuffers(self):
        gs1 = ptr8(self.gsBuffer1)
        gs2 = ptr8(self.gsBuffer2)
        gs3 = ptr8(self.gsBuffer3)
        size = int(self.width) * int(self.height) >> 3
        for i in range(size):
            gs3[i] = gs1[i] & gs2[i]

    @micropython.viper
    def _gsThread(self):
        disp = thumby.display.display
        buf1 = self.gsArray1
        buf2 = self.gsArray2
        buf3 = self.gsArray3

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
