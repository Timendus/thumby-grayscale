import micropython
import utime
from machine import Pin, SPI, freq, idle
import _thread
import os
import gc
from array import array
try:
    from thumbyButton import buttonA, buttonB, buttonU, buttonD, buttonL, buttonR
except:
    # this will fail on Thumbys that have not been updated, but that's ok as we
    # won't run anyway.
    pass


# When the Thumby boots up, it runs at 48MHz. main.py will switch this to 125MHz
# before starting a game, but anything run in the code editor will be running at
# the slower frequency. We want a bit of grunt for the GPU loop so we'll raise it
# to at least 125MHz here.
# We'll assume that if this code has been imported, then the greyscale display
# code is going to be used and go ahead and change the frequency now. The
# alternative is to wait until object creation time, but prior to that other
# objects could have been instantiated that rely on knowing what the runtime CPU
# frequency will be.
if freq() < 125000000:
    freq(125000000)


def check_upython_version(major, minor, release):
    up_ver = [int(s) for s in os.uname().release.split('.')]
    if up_ver[0] > major:
        return True
    if up_ver[0] == major:
        if up_ver[1] > minor:
            return True
        if up_ver[1] == minor:
            if up_ver[2] >= release:
                return True
    return False


class Sprite:
    @micropython.native
    def __init__(self, width, height, bitmapData1, bitmapData2, x = 0, y=0, key=-1, mirrorX=False, mirrorY=False):
        self.width = width
        self.height = height
        self.bitmapSource1 = bitmapData1
        self.bitmapSource2 = bitmapData2
        self.bitmapByteCount = width*(height//8)
        if(height%8):
            self.bitmapByteCount+=width
        self.frameCount = 1
        self.currentFrame = 0
        if type(self.bitmapSource1)==str:
            self.bitmap = bytearray(self.bitmapByteCount1)
            self.file1 = open(self.bitmapSource1,'rb')
            self.file1.readinto(self.bitmap1)
            self.frameCount = os.stat(self.bitmapSource1)[6] // self.bitmapByteCount
        elif type(self.bitmapSource1)==bytearray:
            self.bitmap1 = memoryview(self.bitmapSource1)[0:self.bitmapByteCount]
            self.frameCount = len(self.bitmapSource1) // self.bitmapByteCount
        if type(self.bitmapSource2)==str:
            self.bitmap2 = bytearray(self.bitmapByteCount)
            self.file2 = open(self.bitmapSource2,'rb')
            self.file2.readinto(self.bitmap2)
            assert(self.frameCount == os.stat(self.bitmapSource2)[6] // self.bitmapByteCount)
        elif type(self.bitmapSource2)==bytearray:
            self.bitmap2 = memoryview(self.bitmapSource2)[0:self.bitmapByteCount]
            assert(self.frameCount == len(self.bitmapSource2) // self.bitmapByteCount)
        self.x = x
        self.y = y
        self.key = key
        self.mirrorX = mirrorX
        self.mirrorY = mirrorY

    @micropython.native
    def getFrame(self):
        return self.currentFrame

    @micropython.native
    def setFrame(self, frame):
        if(frame >= 0 and (self.currentFrame is not frame % (self.frameCount))):
            self.currentFrame = frame % (self.frameCount)
            offset=self.bitmapByteCount*self.currentFrame
            if type(self.bitmapSource1)==str:
                self.file1.seek(offset)
                self.file1.readinto(self.bitmap1)
                #f.close()
            elif type(self.bitmapSource1)==bytearray:
                self.bitmap1 = memoryview(self.bitmapSource1)[offset:offset+self.bitmapByteCount]
            if type(self.bitmapSource2)==str:
                self.file2.seek(offset)
                self.file2.readinto(self.bitmap2)
                #f.close()
            elif type(self.bitmapSource2)==bytearray:
                self.bitmap2 = memoryview(self.bitmapSource2)[offset:offset+self.bitmapByteCount]


# The times below are calculated using phase 1 and phase 2 pre-charge
# periods of 1 clock.
# Note that although the SSD1306 datasheet doesn't state it, the 50
# clocks period per row _is_ a constant (datasheets for similar
# controllers from the same manufacturer state this).
# 530kHz is taken to be the highest nominal clock frequency. The
# calculations shown provide the value in seconds, which can be
# multiplied by 1e6 to provide a microsecond value.
_PRE_FRAME_TIME_US    = const( 785)     # 8 rows: ( 8*(1+1+50)) / 530e3 seconds
_FRAME_TIME_US        = const(4709)     # 48 rows: (49*(1+1+50)) / 530e3 seconds

# Thread state variables for managing the Grayscale Thread
_THREAD_STARTING   = const(0)
_THREAD_STOPPED    = const(1)
_THREAD_RUNNING    = const(2)
_THREAD_STOPPING   = const(3)

# Indexes into the multipurpose state array, accessing a particular status
_ST_THREAD       = const(0)
_ST_COPY_BUFFS   = const(1)
_ST_PENDING_CMD  = const(2)
_ST_CONTRAST     = const(3)


class Grayscale:

    BLACK     = 0
    DARKGRAY  = 1
    LIGHTGRAY = 2
    WHITE     = 3

    def __init__(self):
        if not check_upython_version(1, 19, 1):
            raise NotImplementedError('Greyscale support requires at least Micropython v1.19.1. Please update via the Thumby code editor')

        self._spi = SPI(0, sck=Pin(18), mosi=Pin(19))
        self._dc = Pin(17)
        self._cs = Pin(16)
        self._res = Pin(20)

        self._spi.init(baudrate=100 * 1000 * 1000, polarity=0, phase=0)
        self._res.init(Pin.OUT, value=1)
        self._dc.init(Pin.OUT, value=0)
        self._cs.init(Pin.OUT, value=1)

        self.width = 72
        self.height = 40
        self.max_x = 72 - 1
        self.max_y = 40 - 1

        self.pages = self.height // 8
        self.buffer_size = self.pages * self.width
        self.buffer1 = bytearray(self.buffer_size)
        self.buffer2 = bytearray(self.buffer_size)
        self._buffer1 = bytearray(self.buffer_size)
        self._buffer2 = bytearray(self.buffer_size)
        self._buffer3 = bytearray(self.buffer_size)

        # The method used to create reduced flicker greyscale using the SSD1306
        # uses certain assumptions about the internal behaviour of the
        # controller. Even though the behaviour seems to back up those
        # assumptions, it is possible that the assumptions are incorrect but the
        # desired result is achieved anyway. To simplify things, the following
        # comments are written as if the assumptions _are_ correct.

        # We will keep the display synchronised by resetting the row counter
        # before each frame and then outputting a frame of 57 rows. This is 17
        # rows past the 40 of the actual display.

        # Prior to loading in the frame we park the row counter at row 0 and
        # wait for the nominal time for 8 rows to be output. This (hopefully)
        # provides enough time for the row counter to reach row 0 before it
        # sticks there. (Note: recent test indicate that perhaps the current row
        # actually jumps before parking)
        # The 'parking' is done by setting the number of rows (aka 'multiplex
        # ratio') to 1 row. This is an invalid setting according to the datasheet
        # but seems to still have the desired effect.
        # 0xa8,0    Set multiplex ratio to 1
        # 0xd3,52   Set display offset to 52
        self._preFrameCmds = bytearray([0xa8,0, 0xd3,52])
        # Once the frame has been loaded into the display controller's GDRAM, we
        # set the controller to output 57 rows, and then delay for the nominal
        # time for 48 rows to be output.
        # Considering the 17 row 'buffer space' after the real 40 rows, that puts
        # us around halfway between the end of the display, and the row at which
        # it would wrap around.
        # By having 8.5 rows either side of the nominal timing, we can absorb any
        # variation in the frequency of the display controller's RC oscillator as
        # well as any timing offsets introduced by the Python code.
        # 0xd3,x    Set display offset. Since rows are scanned in reverse, the
        #           calculation must work backwards from the last controller row.
        # 0xa8,57-1 Set multiplex ratio to 57
        self._postFrameCmds = bytearray([0xd3,40+(64-57), 0xa8,57-1])

        # We enhance the greys by modulating the contrast.
        # Use setting from thumby.cfg
        brightnessSetting=2
        try:
            with open("thumby.cfg", "r") as fh:
                conf = fh.read().split(',')
            for k in range(len(conf)):
                if(conf[k] == "brightness"):
                    brightnessSetting = int(conf[k+1])
        except OSError:
            pass
        # but with a set of contrast values spanning the entire range provided by the controller
        brightnessVals = [0,56,127]
        brightnessVal = brightnessVals[brightnessSetting]
        # 0x81,<val>        Set Bank0 contrast value to <val>
        self._postFrameAdj = [bytearray([0x81,brightnessVal>>5]), bytearray([0x81,brightnessVal]), bytearray([0x81,(brightnessVal << 1) + 1])]

        # It's better to avoid using regular variables for thread sychronisation.
        # Instead, elements of an array/bytearray should be used.
        # We're using a uint32 array here, as that should hopefully further ensure
        # the atomicity of any element accesses.
        self._state = array('I', [0,0,0,0xff])

        self._pendingCmds = bytearray([0] * 8)

        self.setFont('lib/font5x7.bin', 5, 7, 1)
        #self.setFont('lib/font8x8.bin', 8, 8, 0)

        self.lastUpdateEnd = 0
        self.frameRate = 0
        self.frameTimeMs = 0

        self.fill(Grayscale.BLACK)
        self._copy_buffers()
        self.init_display()
        self.state = _THREAD_STARTING
        _thread.stack_size(2048)        # minimum stack size for RP2040 micropython port
        _thread.start_new_thread(self._display_thread, ())

        # Wait for the thread to successfully settle into a running state
        while self._state[_ST_THREAD] != _THREAD_RUNNING:
            idle()


    # allow use of 'with'
    def __enter__(self):
        return self
    def __exit__(self, type, value, traceback):
        self.stop()


    def reset(self):
        self._res(1)
        utime.sleep_ms(1)
        self._res(0)
        utime.sleep_ms(10)
        self._res(1)
        utime.sleep_ms(10)


    def init_display(self):
        self.reset()
        self._cs(0)
        self._dc(0)
        # initialise as usual, except with shortest pre-charge periods and highest clock frequency
        # 0xae          Display Off
        # 0x20,0x00     Set horizontal addressing mode
        # 0x40          Set display start line to 0
        # 0xa1          Set segment remap mode 1
        # 0xa8,63       Set multiplex ratio to 64 (will be changed later)
        # 0xc8          Set COM output scan direction 1
        # 0xd3,54       Set display offset to 0 (will be changed later)
        # 0xda,0x12     Set COM pins hardware configuration: alternative config,
        #               disable left/right remap
        # 0xd5,0xf0     Set clk div ratio = 1, and osc freq = ~370kHz
        # 0xd9,0x11     Set pre-charge periods: phase 1 = 1 , phase 2 = 1
        # 0xdb,0x20     Set Vcomh deselect level = 0.77 x Vcc
        # 0x81,0x7f     Set Bank0 contrast to 127 (will be changed later)
        # 0xa4          Do not enable entire display (i.e. use GDRAM)
        # 0xa6          Normal (not inverse) display
        # 0x8d,0x14     Charge bump setting: enable charge pump during display on
        # 0xad,0x30     Select internal 30uA Iref (max Iseg=240uA) during display on
        # 0xf           Set display on
        self._spi.write(bytearray([
            0xae, 0x20,0x00, 0x40, 0xa1, 0xa8,63, 0xc8, 0xd3,0, 0xda,0x12, 0xd5,0xf0, 0xd9,0x11, 0xdb,0x20, 0x81,0x7f,
            0xa4, 0xa6, 0x8d,0x14, 0xad,0x30, 0xaf]))
        self._dc(1)
        # clear the entire GDRAM
        zero32 = bytearray([0] * 32)
        for _ in range(32):
            self._spi.write(zero32)
        self._dc(0)
        # set the GDRAM window
        # 0x21,28,99    Set column start (28) and end (99) addresses
        # 0x22,0,4      Set page start (0) and end (4) addresses0
        self._spi.write(bytearray([0x21,28,99, 0x22,0,4]))


    def stop(self):
        if self._state[_ST_THREAD] == _THREAD_RUNNING:
            self._state[_ST_THREAD] = _THREAD_STOPPING
            while self._state[_ST_THREAD] != _THREAD_STOPPED:
                idle()
        self._cs(1)
        self.reset()
        self._cs(0)
        self._dc(0)
        # reinitialise to the normal configuration. Copied from ssd1306.py
        self._spi.write(bytearray([
            0xae, 0x20,0x00, 0x40, 0xa1, 0xa8,self.height-1, 0xc8, 0xd3,0, 0xda,0x12, 0xd5,0x80,
            0xd9,0xf1, 0xdb,0x20, 0x81,0x7f,
            0xa4, 0xa6, 0x8d,0x14, 0xad,0x30, 0xaf,
            0x21,28,99, 0x22,0,4]))
        self._cs(1)

    @micropython.native
    def write_cmd(self, cmd):
        if cmd is list:
            cmd = bytearray(cmd)
        elif not cmd is bytearray:
            cmd = bytearray([cmd])
        if self._state[_ST_THREAD] == _THREAD_RUNNING:
            pendingCmds = self._pendingCmds
            if len(cmd) > len(pendingCmds):
                # We can't just break up the longer list of commands automatically, as we
                # might end up separating a command and its parameter(s).
                raise ValueError('Cannot send more than %u bytes using write_cmd()' % len(pendingCmds))
            i = 0
            while i < len(cmd):
                pendingCmds[i] = cmd[i]
                i += 1
            # Fill the rest of the bytearray with display controller NOPs
            # This is probably better than having to create slice or a memoryview in the GPU thread
            while i < len(pendingCmds):
                pendingCmds[i] = 0x3e
                i += 1
            self._state[_ST_PENDING_CMD] = 1
            while self._state[_ST_PENDING_CMD]:
                idle()
        else:
            self._dc(0)
            self._spi.write(cmd)

    def poweroff(self):
        self.write_cmd(0xae)
    def poweron(self):
        self.write_cmd(0xaf)

    @micropython.viper
    def show(self):
        state:ptr32 = ptr32(self._state)
        state[_ST_COPY_BUFFS] = 1
        if state[_ST_THREAD] != _THREAD_RUNNING:
            return
        while state[_ST_COPY_BUFFS] != 0:
            idle()

    @micropython.native
    def show_async(self):
        self._state[_ST_COPY_BUFFS] = 1


    @micropython.native
    def setFPS(self, newFrameRate):
        self.frameRate = newFrameRate
        if newFrameRate != 0:
            self.frameTimeMs = 1000 // newFrameRate

    @micropython.native
    def update(self):
        self.show()
        if self.frameRate > 0:
            frameTimeMs = self.frameTimeMs
            lastUpdateEnd = self.lastUpdateEnd
            frameTimeRemaining = frameTimeMs - utime.ticks_diff(utime.ticks_ms(), lastUpdateEnd)
            while frameTimeRemaining > 1:
                buttonA.update()
                buttonB.update()
                buttonU.update()
                buttonD.update()
                buttonL.update()
                buttonR.update()
                utime.sleep_ms(1)
                frameTimeRemaining = frameTimeMs - utime.ticks_diff(utime.ticks_ms(), lastUpdateEnd)
            while frameTimeRemaining > 0:
                frameTimeRemaining = frameTimeMs - utime.ticks_diff(utime.ticks_ms(), lastUpdateEnd)
        self.lastUpdateEnd = utime.ticks_ms()


    def brightness(self, c):
        if c < 0:
            c = 0
        elif c > 127:
            c = 127
        self._state[_ST_CONTRAST] = c

    def brightness_sync(self, c):
        if c < 0:
            c = 0
        elif c > 127:
            c = 127
        self._state[_ST_CONTRAST] = c
        if self._state[_ST_THREAD] != _THREAD_RUNNING:
            return
        while self._state[_ST_CONTRAST] != 0xff:
            idle()

    @micropython.viper
    def _copy_buffers(self):
        b1:ptr32 = ptr32(self.buffer1) ; b2:ptr32 = ptr32(self.buffer2)
        _b1:ptr32 = ptr32(self._buffer1) ; _b2:ptr32 = ptr32(self._buffer2) ; _b3:ptr32 = ptr32(self._buffer3)
        i:int = 0
        while i < 90:
            v1:int = b1[i]
            v2:int = b2[i]
            _b1[i] = v1 | v2
            _b2[i] = v2
            _b3[i] = v1 & v2
            i += 1
        self._state[_ST_COPY_BUFFS] = 0


    # GPU (Gray Processing Unit) thread function
    @micropython.viper
    def _display_thread(self):
        # local object arrays for display framebuffers and post-frame commands
        buffers = array('O', [self._buffer1, self._buffer2, self._buffer3])
        postFrameAdj = array('O', [self._postFrameAdj[0], self._postFrameAdj[1], self._postFrameAdj[2]])
        # cache various instance variables, buffers, and functions/methods
        state:ptr32 = ptr32(self._state)
        spi_write = self._spi.write
        dc = self._dc
        preFrameCmds:ptr = self._preFrameCmds
        postFrameCmds:ptr = self._postFrameCmds
        ticks_us = utime.ticks_us
        ticks_diff = utime.ticks_diff
        sleep_ms = utime.sleep_ms
        sleep_us = utime.sleep_us

        # we want ptr32 vars for fast buffer copying
        b1:ptr32 = ptr32(self.buffer1) ; b2:ptr32 = ptr32(self.buffer2)
        _b1:ptr32 = ptr32(self._buffer1) ; _b2:ptr32 = ptr32(self._buffer2) ; _b3:ptr32 = ptr32(self._buffer3)

        # the viper compiler doesn't need variables predeclared with the type
        # decoration like this, but I think it's a bit cleaner
        fn:int ; i:int ; t0:int
        v1:int ; v2:int ; contrast:int

        state[_ST_THREAD] = _THREAD_RUNNING
        while True:
            while state[_ST_THREAD] == _THREAD_RUNNING:
                # this is the main GPU loop. We cycle through each of the 3 display
                # framebuffers, sending the framebuffer data and various commands.
                fn = 0
                while fn < 3:
                    t0 = ticks_us()
                    # the 'dc' output is used to switch the controller to receive
                    # commands (0) or frame data (1)
                    dc(0)
                    # send the pre-frame commands to 'park' the row counter
                    spi_write(preFrameCmds)
                    dc(1)
                    # and then send the frame
                    spi_write(buffers[fn])
                    dc(0)
                    # send the first instance of the contrast adjust command
                    spi_write(postFrameAdj[fn])
                    # wait for the pre-frame time to complete
                    sleep_us(_PRE_FRAME_TIME_US - int(ticks_diff(ticks_us(), t0)))
                    t0 = ticks_us()
                    # now send the post-frame commands to display the frame
                    spi_write(postFrameCmds)
                    # and adjust the contrast for the specific frame number again.
                    # If we do not do this twice, the screen can glitch.
                    spi_write(postFrameAdj[fn])
                    # check if there's a pending frame copy required
                    # we only copy the paint framebuffers to the display framebuffers on
                    # the last frame to avoid screen-tearing artefacts
                    if (fn == 2) and (state[_ST_COPY_BUFFS] != 0):
                        i = 0
                        # fast copy loop. By using using ptr32 vars we copy 3 bytes at a time.
                        while i < 90:
                            v1 = b1[i]
                            v2 = b2[i]
                            # this isn't a straight copy. Instead we are mapping:
                            # in        out
                            # 0 (0b00)  0 (0b000)
                            # 1 (0b01)  1 (0b001)
                            # 2 (0b10)  3 (0b011)
                            # 3 (0b11)  7 (0b111)
                            _b1[i] = v1 | v2
                            _b2[i] = v2
                            _b3[i] = v1 & v2
                            i += 1
                        state[_ST_COPY_BUFFS] = 0
                    # check if there's a pending contrast/brightness value change
                    # again, we only adjust this after the last frame in the cycle
                    elif (fn == 2) and (state[_ST_CONTRAST] != 0xffff):
                        contrast = state[_ST_CONTRAST]
                        state[_ST_CONTRAST] = 0xffff
                        # shift the value to provide 3 different levels
                        postFrameAdj[0][1] = contrast >> 5
                        postFrameAdj[1][1] = contrast >> 1
                        postFrameAdj[2][1] = (contrast << 1) + 1
                    # check if there are pending commands
                    elif state[_ST_PENDING_CMD]:
                        # and send them
                        spi_write(pending_cmds)
                        state[_ST_PENDING_CMD] = 0
                    # two stage wait for frame time to complete
                    # we use sleep_ms() first to allow idle loop usage, with >>10 for a fast
                    # /1000 approximation
                    sleep_ms((_FRAME_TIME_US - int(ticks_diff(ticks_us(), t0))) >> 10)
                    # and finish with a sleep_us() to spin for the correct duration
                    sleep_us(_FRAME_TIME_US - int(ticks_diff(ticks_us(), t0)))
                    fn += 1
            # if the state has changed to 'stopping'
            if state[_ST_THREAD] == _THREAD_STOPPING:
                i = 0
                # blank out framebuffer 1
                while i < 90:
                    _b1[i] = 0
                    i += 1
                dc(1)
                # and send it to clear the screen
                spi_write(buffers[0])
                # and mark that we've stopped
                state[_ST_THREAD] = _THREAD_STOPPED
                # the thread can now exit
                return


    @micropython.viper
    def fill(self, colour:int):
        buffer1:ptr32 = ptr32(self.buffer1)
        buffer2:ptr32 = ptr32(self.buffer2)
        f1:int = -1 if colour & 1 else 0
        f2:int = -1 if colour & 2 else 0
        i:int = 0
        while i < 90:
            buffer1[i] = f1
            buffer2[i] = f2
            i += 1

    @micropython.viper
    def drawFilledRectangle(self, x:int, y:int, width:int, height:int, colour:int):
        if x > 71: return
        if y > 39: return
        if width <= 0: return
        if height <= 0: return
        if x < 0:
            width += x
            x = 0
        if y < 0:
            height += y
            y = 0
        x2:int = x + width
        y2:int = y + height
        if x2 > 72:
            x2 = 72
            width = 72 - x
        if y2 > 40:
            y2 = 40
            height = 40 - y

        buffer1 = ptr8(self.buffer1)
        buffer2 = ptr8(self.buffer2)

        o:int = (y >> 3) * 72
        oe:int = o + x2
        o += x
        strd:int = 72 - width

        v1:int = 0xff if colour & 1 else 0
        v2:int = 0xff if colour & 2 else 0

        yb:int = y & 7
        ybh:int = 8 - yb
        if height <= ybh:
            m:int = ((1 << height) - 1) << yb
        else:
            m:int = 0xff << yb
        im:int = 255-m
        while o < oe:
            if colour & 1:
                buffer1[o] |= m
            else:
                buffer1[o] &= im
            if colour & 2:
                buffer2[o] |= m
            else:
                buffer2[o] &= im
            o += 1
        height -= ybh
        while height >= 8:
            o += strd
            oe += 72
            while o < oe:
                buffer1[o] = v1
                buffer2[o] = v2
                o += 1
            height -= 8
        if height > 0:
            o += strd
            oe += 72
            m:int = (1 << height) - 1
            im:int = 255-m
            while o < oe:
                if colour & 1:
                    buffer1[o] |= m
                else:
                    buffer1[o] &= im
                if colour & 2:
                    buffer2[o] |= m
                else:
                    buffer2[o] &= im
                o += 1



    @micropython.viper
    def drawHLine(self, x:int, y:int, width:int, colour:int):
        if y < 0 or y >= 40: return
        if x >= 72: return
        if width <= 0: return
        if x < 0:
            width += x
            x = 0
        x2:int = x + width
        if x2 > 72:
            x2 = 72
        o:int = (y >> 3) * 72
        oe:int = o + x2
        o += x
        m:int = 1 << (y & 7)
        im:int = 255-m
        buffer1 = ptr8(self.buffer1)
        buffer2 = ptr8(self.buffer2)
        if colour == 0:
            while o < oe:
                buffer1[o] &= im
                buffer2[o] &= im
                o += 1
        elif colour == 1:
            while o < oe:
                buffer1[o] |= m
                buffer2[o] &= im
                o += 1
        elif colour == 2:
            while o < oe:
                buffer1[o] &= im
                buffer2[o] |= m
                o += 1
        elif colour == 3:
            while o < oe:
                buffer1[o] |= m
                buffer2[o] |= m
                o += 1


    @micropython.viper
    def drawVLine(self, x:int, y:int, height:int, colour:int):
        if x < 0 or x >= 72: return
        if y >= 40: return
        if height <= 0: return
        if y < 0:
            height += y
            y = 0
        if (y + height) > 40:
            height = 40 - y

        buffer1 = ptr8(self.buffer1)
        buffer2 = ptr8(self.buffer2)

        o:int = (y >> 3) * 72 + x

        v1:int = 0xff if colour & 1 else 0
        v2:int = 0xff if colour & 2 else 0

        yb:int = y & 7
        ybh:int = 8 - yb
        if height <= ybh:
            m:int = ((1 << height) - 1) << yb
        else:
            m:int = 0xff << yb
        im:int = 255-m
        if colour & 1:
            buffer1[o] |= m
        else:
            buffer1[o] &= im
        if colour & 2:
            buffer2[o] |= m
        else:
            buffer2[o] &= im
        height -= ybh
        while height >= 8:
            o += 72
            buffer1[o] = v1
            buffer2[o] = v2
            height -= 8
        if height > 0:
            o += 72
            m:int = (1 << height) - 1
            im:int = 255-m
            if colour & 1:
                buffer1[o] |= m
            else:
                buffer1[o] &= im
            if colour & 2:
                buffer2[o] |= m
            else:
                buffer2[o] &= im


    @micropython.viper
    def drawRectangle(self, x:int, y:int, width:int, height:int, colour:int):
        self.drawHLine(x, y, width, colour)
        self.drawHLine(x, y+height-1, width, colour)
        self.drawVLine(x, y, height, colour)
        self.drawVLine(x+width-1, y, height, colour)


    @micropython.viper
    def setPixel(self, x:int, y:int, colour:int):
        if x < 0 or x >= 72 or y < 0 or y >= 40:
            return
        o:int = (y >> 3) * 72 + x
        m:int = 1 << (y & 7)
        im:int = 255-m
        buffer1 = ptr8(self.buffer1)
        buffer2 = ptr8(self.buffer2)
        if colour & 1:
            buffer1[o] |= m
        else:
            buffer1[o] &= im
        if colour & 2:
            buffer2[o] |= m
        else:
            buffer2[o] &= im

    @micropython.viper
    def getPixel(self, x:int, y:int) -> int:
        if x < 0 or x >= 72 or y < 0 or y >= 40:
            return 0
        o:int = (y >> 3) * 72 + x
        m:int = 1 << (y & 7)
        buffer1 = ptr8(self.buffer1)
        buffer2 = ptr8(self.buffer2)
        colour:int = 0
        if buffer1[o] & m:
            colour = 1
        if buffer2[o] & m:
            colour |= 2
        return colour

    @micropython.viper
    def drawLine(self, x0:int, y0:int, x1:int, y1:int, colour:int):
        if x0 == x1:
            if y0 == y1:
                self.setPixel(x0, y0, colour)
            else:
                self.drawHLine(x0, y0, x1-x0, colour)
            return
        if y0 == y1:
            self.drawVLine(x0, y0, y1-y0, colour)
            return
        dx:int = x1 - x0
        dy:int = y1 - y0
        sx:int = 1
        # y increment is always 1
        if dy < 0:
            x0,x1 = x1,x0
            y0,y1 = y1,y0
            dy = 0 - dy
            dx = 0 - dx
        if dx < 0:
            dx = 0 - dx
            sx = -1
        x:int = x0
        y:int = y0
        buffer1:ptr8 = ptr8(self.buffer1)
        buffer2:ptr8 = ptr8(self.buffer2)
        cx:int ; o:int

        o:int = (y >> 3) * 72 + x
        m:int = 1 << (y & 7)
        im:int = 255-m

        if dx > dy:
            err:int = dx >> 1
            while x != x1:
                if 0 <= x < 72 and 0 <= y < 40:
                    if colour & 1:
                        buffer1[o] |= m
                    else:
                        buffer1[o] &= im
                    if colour & 2:
                        buffer2[o] |= m
                    else:
                        buffer2[o] &= im
                err -= dy
                if err < 0:
                    y += 1
                    m <<= 1
                    if m & 0x100:
                        o += 72
                        m = 1
                        im = 0xfe
                    else:
                        im = 255-m
                    err += dx
                x += sx
                o += sx
        else:
            err:int = dy >> 1
            while y != y1:
                if 0 <= x < 72 and 0 <= y < 40:
                    if colour & 1:
                        buffer1[o] |= m
                    else:
                        buffer1[o] &= im
                    if colour & 2:
                        buffer2[o] |= m
                    else:
                        buffer2[o] &= im
                err -= dx
                if err < 0:
                    x += sx
                    o += sx
                    err += dy
                y += 1
                m <<= 1
                if m & 0x100:
                    o += 72
                    m = 1
                    im = 0xfe
                else:
                    im = 255-m
        if 0 <= x < 72 and 0 <= y < 40:
            if colour & 1:
                buffer1[o] |= m
            else:
                buffer1[o] &= im
            if colour & 2:
                buffer2[o] |= m
            else:
                buffer2[o] &= im



    def setFont(self, fontFile, width, height, space):
        sz = os.stat(fontFile)[6]
        self.font_bmap = bytearray(sz)
        with open(fontFile, 'rb') as fh:
            fh.readinto(self.font_bmap)
        self.font_width = width
        self.font_height = height
        self.font_space = space
        self.font_glyphcnt = sz // width


    @micropython.viper
    def drawText(self, txt, x:int, y:int, colour:int):
        buffer1:ptr8 = ptr8(self.buffer1)
        buffer2:ptr8 = ptr8(self.buffer2)
        font_bmap:ptr8 = ptr8(self.font_bmap)
        font_width:int = int(self.font_width)
        font_space:int = int(self.font_space)
        font_glyphcnt:int = int(self.font_glyphcnt)
        sm1o:int = 0xff if colour & 1 else 0
        sm1a:int = 255 - sm1o
        sm2o:int = 0xff if colour & 2 else 0
        sm2a:int = 255 - sm2o
        ou:int = (y >> 3) * 72 + x
        ol:int = ou + 72
        shu:int = y & 7
        shl:int = 8 - shu
        for c in txt:
            if isinstance(c, str):
                co:int = int(ord(c)) - 0x20
            else:
                co:int = int(c) - 0x20
            if co < font_glyphcnt:
                gi:int = co * font_width
                gx:int = 0
                while gx < font_width:
                    if 0 <= x < 72:
                        gb:int = font_bmap[gi + gx]
                        gbu:int = gb << shu
                        gbl:int = gb >> shl
                        if 0 <= ou < 360:
                            # paint upper byte
                            buffer1[ou] = (buffer1[ou] | (gbu & sm1o)) & 255-(gbu & sm1a)
                            buffer2[ou] = (buffer2[ou] | (gbu & sm2o)) & 255-(gbu & sm2a)
                        if (shl != 8) and (0 <= ol < 360):
                            # paint lower byte
                            buffer1[ol] = (buffer1[ol] | (gbl & sm1o)) & 255-(gbl & sm1a)
                            buffer2[ol] = (buffer2[ol] | (gbl & sm2o)) & 255-(gbl & sm2a)
                    ou += 1
                    ol += 1
                    x += 1
                    gx += 1
            ou += font_space
            ol += font_space
            x += font_space


    @micropython.viper
    def blit(self, src1:ptr8, src2:ptr8, x:int, y:int, width:int, height:int, key:int, mirrorX:int, mirrorY:int):
        if x+width < 0 or x >= 72:
            return
        if y+height < 0 or y >= 40:
            return
        buffer1:ptr8 = ptr8(self.buffer1)
        buffer2:ptr8 = ptr8(self.buffer2)

        stride:int = width

        srcx:int = 0 ; srcy:int = 0
        dstx:int = x ; dsty:int = y
        sdx:int = 1
        if mirrorX:
            sdx = -1
            srcx += width - 1
            if dstx < 0:
                srcx += dstx
                width += dstx
                dstx = 0
        else:
            if dstx < 0:
                srcx = 0 - dstx
                width += dstx
                dstx = 0
        if dstx+width > 72:
            width = 72 - dstx
        if mirrorY:
            srcy = height - 1
            if dsty < 0:
                srcy += dsty
                height += dsty
                dsty = 0
        else:
            if dsty < 0:
                srcy = 0 - dsty
                height += dsty
                dsty = 0
        if dsty+height > 40:
            height = 40 - dsty

        srco:int = (srcy >> 3) * stride + srcx
        srcm:int = 1 << (srcy & 7)

        dsto:int = (dsty >> 3) * 72 + dstx
        dstm:int = 1 << (dsty & 7)
        dstim:int = 255 - dstm

        while height != 0:
            srcco:int = srco
            dstco:int = dsto
            i:int = width
            while i != 0:
                v:int = 0
                if src1[srcco] & srcm:
                    v = 1
                if src2[srcco] & srcm:
                    v |= 2
                if (key == -1) or (v != key):
                    if v & 1:
                        buffer1[dstco] |= dstm
                    else:
                        buffer1[dstco] &= dstim
                    if v & 2:
                        buffer2[dstco] |= dstm
                    else:
                        buffer2[dstco] &= dstim
                srcco += sdx
                dstco += 1
                i -= 1
            dstm <<= 1
            if dstm & 0x100:
                dsto += 72
                dstm = 1
                dstim = 0xfe
            else:
                dstim = 255 - dstm
            if mirrorY:
                srcm >>= 1
                if srcm == 0:
                    srco -= stride
                    srcm = 0x80
            else:
                srcm <<= 1
                if srcm & 0x100:
                    srco += stride
                    srcm = 1
            height -= 1

    @micropython.native
    def drawSprite(self, s):
        self.blit(s.bitmap1, s.bitmap2, s.x, s.y, s.width, s.height, s.key, s.mirrorX, s.mirrorY)

    @micropython.viper
    def blitWithMask(self, src1:ptr8, src2:ptr8, x:int, y:int, width:int, height:int, key:int, mirrorX:int, mirrorY:int, mask:ptr8):
        if x+width < 0 or x >= 72:
            return
        if y+height < 0 or y >= 40:
            return
        buffer1:ptr8 = ptr8(self.buffer1)
        buffer2:ptr8 = ptr8(self.buffer2)

        stride:int = width

        srcx:int = 0 ; srcy:int = 0
        dstx:int = x ; dsty:int = y
        sdx:int = 1
        if mirrorX:
            sdx = -1
            srcx += width - 1
            if dstx < 0:
                srcx += dstx
                width += dstx
                dstx = 0
        else:
            if dstx < 0:
                srcx = 0 - dstx
                width += dstx
                dstx = 0
        if dstx+width > 72:
            width = 72 - dstx
        if mirrorY:
            srcy = height - 1
            if dsty < 0:
                srcy += dsty
                height += dsty
                dsty = 0
        else:
            if dsty < 0:
                srcy = 0 - dsty
                height += dsty
                dsty = 0
        if dsty+height > 40:
            height = 40 - dsty

        srco:int = (srcy >> 3) * stride + srcx
        srcm:int = 1 << (srcy & 7)

        dsto:int = (dsty >> 3) * 72 + dstx
        dstm:int = 1 << (dsty & 7)
        dstim:int = 255 - dstm

        while height != 0:
            srcco:int = srco
            dstco:int = dsto
            i:int = width
            while i != 0:
                if (mask[srcco] & srcm) == 0:
                    if src1[srcco] & srcm:
                        buffer1[dstco] |= dstm
                    else:
                        buffer1[dstco] &= dstim
                    if src2[srcco] & srcm:
                        buffer2[dstco] |= dstm
                    else:
                        buffer2[dstco] &= dstim
                srcco += sdx
                dstco += 1
                i -= 1
            dstm <<= 1
            if dstm & 0x100:
                dsto += 72
                dstm = 1
                dstim = 0xfe
            else:
                dstim = 255 - dstm
            if mirrorY:
                srcm >>= 1
                if srcm == 0:
                    srco -= stride
                    srcm = 0x80
            else:
                srcm <<= 1
                if srcm & 0x100:
                    srco += stride
                    srcm = 1
            height -= 1

    @micropython.native
    def drawSpriteWithMask(self, s, m):
        self.blit(s.bitmap1, s.bitmap2, s.x, s.y, s.width, s.height, s.key, s.mirrorX, s.mirrorY, m.bitmap1)
