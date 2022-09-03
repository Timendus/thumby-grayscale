import micropython
from utime import sleep_ms, ticks_diff, ticks_ms, sleep_us
from machine import Pin, SPI, freq, idle
import _thread
from os import stat
import gc
from array import array
from thumbyButton import buttonA, buttonB, buttonU, buttonD, buttonL, buttonR


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
            self.frameCount = stat(self.bitmapSource1)[6] // self.bitmapByteCount
        elif type(self.bitmapSource1)==bytearray:
            self.bitmap1 = memoryview(self.bitmapSource1)[0:self.bitmapByteCount]
            self.frameCount = len(self.bitmapSource1) // self.bitmapByteCount
        if type(self.bitmapSource2)==str:
            self.bitmap2 = bytearray(self.bitmapByteCount)
            self.file2 = open(self.bitmapSource2,'rb')
            self.file2.readinto(self.bitmap2)
            assert(self.frameCount == stat(self.bitmapSource2)[6] // self.bitmapByteCount)
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
_THREAD_STOPPED    = const(0)
_THREAD_STARTING   = const(1)
_THREAD_RUNNING    = const(2)
_THREAD_STOPPING   = const(3)

# Indexes into the multipurpose state array, accessing a particular status
_ST_THREAD       = const(0)
_ST_COPY_BUFFS   = const(1)
_ST_PENDING_CMD  = const(2)
_ST_CONTRAST     = const(3)

# Screen display size constants
_WIDTH = const(72)
_HEIGHT = const(40)
_BUFF_SIZE = const((_HEIGHT // 8) * _WIDTH)
_BUFF_INT_SIZE = const(_BUFF_SIZE // 4)


class Grayscale:

    # BLACK and WHITE is 0 and 1 to be compatible with the standard Thumby API
    BLACK     = 0
    WHITE     = 1
    DARKGRAY  = 2
    LIGHTGRAY = 3

    def __init__(self):
        self._spi = SPI(0, sck=Pin(18), mosi=Pin(19))
        self._dc = Pin(17)
        self._cs = Pin(16)
        self._res = Pin(20)

        self._spi.init(baudrate=100 * 1000 * 1000, polarity=0, phase=0)
        self._res.init(Pin.OUT, value=1)
        self._dc.init(Pin.OUT, value=0)
        self._cs.init(Pin.OUT, value=1)

        self._display_initialised = False

        self.display = self     # This acts as both the GraphicsClass and SSD1306

        self.width = _WIDTH
        self.height = _HEIGHT
        self.max_x = _WIDTH - 1
        self.max_y = _HEIGHT - 1

        self.pages = self.height // 8

        # Draw buffers.
        # This comprises of two full buffer lengths.
        # The first section contains black and white compatible
        # with the display buffer from the standard Thumby API,
        # and the second contains the shading to create
        # offwhite (lightgray) or offblack (darkgray).
        self.drawBuffer = bytearray(_BUFF_SIZE*2)
        # The base "buffer" matches compatibility with the std Thumby API.
        self.buffer = memoryview(self.drawBuffer)[:_BUFF_SIZE]
        # The "shading" buffer adds the grayscale
        self.shading = memoryview(self.drawBuffer)[_BUFF_SIZE:]

        self._subframes = array('O', [bytearray(_BUFF_SIZE),
            bytearray(_BUFF_SIZE), bytearray(_BUFF_SIZE)])

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
        self._postFrameCmds = bytearray([0xd3,_HEIGHT+(64-57), 0xa8,57-1])

        # We enhance the greys by modulating the contrast.
        # Use setting from thumby.cfg
        brightnessSetting=2
        try:
            with open("thumby.cfg", "r") as fh:
                conf = fh.read().split(',')
            for k in range(len(conf)):
                if(conf[k] == "brightness"):
                    brightnessSetting = int(conf[k+1])
        except (OSError, ValueError):
            pass
        # but with a set of contrast values spanning the entire range provided by the controller
        brightnessVals = [0,28,127]
        self._brightness = brightnessVals[brightnessSetting]
        # 0x81,<val>        Set Bank0 contrast value to <val>
        self._postFrameAdj = array('O', [bytearray([0x81,0]) for _ in range(3)])
        self._postFrameAdjSrc = bytearray(3)

        # It's better to avoid using regular variables for thread sychronisation.
        # Instead, elements of an array/bytearray should be used.
        # We're using a uint32 array here, as that should hopefully further ensure
        # the atomicity of any element accesses.
        self._state = array('I', [_THREAD_STOPPED,0,0,0])

        self._pendingCmds = bytearray([0] * 8)

        self.setFont('lib/font5x7.bin', 5, 7, 1)

        self.lastUpdateEnd = 0
        self.frameRate = 0
        self.brightness(self._brightness)

        _thread.stack_size(2048)        # minimum stack size for RP2040 micropython port



    # allow use of 'with'
    def __enter__(self):
        self.enableGrayscale()
        return self
    def __exit__(self, type, value, traceback):
        self.disableGrayscale()


    def reset(self):
        self._res(1)
        sleep_ms(1)
        self._res(0)
        sleep_ms(10)
        self._res(1)
        sleep_ms(10)


    def init_display(self):
        self._dc(0)
        if self._display_initialised:
            if self._state[_ST_THREAD] == _THREAD_STOPPED:
                # (Re)Initialise the display for monocrhome timings
                # 0xa8,0        Set multiplex ratio to 0 (pausing updates)
                # 0xd3,52       Set display offset to 52
                self._spi.write(bytearray([0xa8,0, 0xd3,52]))
                sleep_us(_FRAME_TIME_US*3)
                # 0xa8,39       Set multiplex ratio to height (releasing updates)
                # 0xd3,0        Set display offset to 0
                self._spi.write(bytearray([0xa8,_HEIGHT-1,0xd3,0]))
            else:
                # Initialise the display for grayscale timings
                # 0xae          Display Off
                # 0xa8,0        Set multiplex ratio to 0 (will be changed later)
                # 0xd3,0        Set display offset to 0 (will be changed later)
                # 0xaf           Set display on
                self._spi.write(bytearray([0xae, 0xa8,0, 0xd3,0, 0xaf]))
            return

        self.reset()
        self._cs(0)
        # initialise as usual, except with shortest pre-charge
        # periods and highest clock frequency
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
            0xae, 0x20,0x00, 0x40, 0xa1, 0xa8,63, 0xc8, 0xd3,0, 0xda,0x12,
            0xd5,0xf0, 0xd9,0x11, 0xdb,0x20, 0x81,0x7f,
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
        self._display_initialised = True


    def enableGrayscale(self):
        if self._state[_ST_THREAD] == _THREAD_RUNNING:
            return

        self._state[_ST_THREAD] = _THREAD_STARTING
        self.init_display()
        _thread.start_new_thread(self._display_thread, ())

        # Wait for the thread to successfully settle into a running state
        while self._state[_ST_THREAD] != _THREAD_RUNNING:
            idle()


    def disableGrayscale(self):
        if self._state[_ST_THREAD] != _THREAD_RUNNING:
            return
        self._state[_ST_THREAD] = _THREAD_STOPPING
        while self._state[_ST_THREAD] != _THREAD_STOPPED:
            idle()
        # Refresh the image to the B/W form
        self.init_display()
        self.show()
        # Change back to the original (unmodulated) brightness setting
        self.brightness(self._brightness)


    @micropython.native
    def write_cmd(self, cmd):
        if isinstance(cmd, list):
            cmd = bytearray(cmd)
        elif not isinstance(cmd, bytearray):
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
        state = ptr32(self._state)
        if state[_ST_THREAD] == _THREAD_RUNNING:
            state[_ST_COPY_BUFFS] = 1
            while state[_ST_COPY_BUFFS] != 0:
                idle()
        else:
            self._dc(1)
            self._spi.write(self.buffer)

    @micropython.native
    def show_async(self):
        state = ptr32(self._state)
        if state[_ST_THREAD] == _THREAD_RUNNING:
            state[_ST_COPY_BUFFS] = 1
        else:
            self.show()


    @micropython.native
    def setFPS(self, newFrameRate):
        self.frameRate = newFrameRate

    @micropython.native
    def update(self):
        self.show()
        if self.frameRate > 0:
            frameTimeMs = 1000 // self.frameRate
            lastUpdateEnd = self.lastUpdateEnd
            frameTimeRemaining = frameTimeMs - ticks_diff(ticks_ms(), lastUpdateEnd)
            while frameTimeRemaining > 1:
                buttonA.update()
                buttonB.update()
                buttonU.update()
                buttonD.update()
                buttonL.update()
                buttonR.update()
                sleep_ms(1)
                frameTimeRemaining = frameTimeMs - ticks_diff(ticks_ms(), lastUpdateEnd)
            while frameTimeRemaining > 0:
                frameTimeRemaining = frameTimeMs - ticks_diff(ticks_ms(), lastUpdateEnd)
        self.lastUpdateEnd = ticks_ms()


    @micropython.viper
    def brightness(self, c:int):
        if c < 0: c = 0
        if c > 127: c = 127
        state = ptr32(self._state)
        postFrameAdj = self._postFrameAdj
        postFrameAdjSrc = ptr8(self._postFrameAdjSrc)
        # Shift the value to provide 3 different subframe levels for the GPU
        postFrameAdjSrc[0] = c >> 5
        postFrameAdjSrc[1] = c >> 1
        postFrameAdjSrc[2] = (c << 1) + 1
        # Apply to display or GPU
        if state[_ST_THREAD] == _THREAD_RUNNING:
            state[_ST_CONTRAST] = 1
        else:
            # Copy in the new contrast adjustments for when the GPU starts
            postFrameAdj[0][1] = postFrameAdjSrc[0]
            postFrameAdj[1][1] = postFrameAdjSrc[1]
            postFrameAdj[2][1] = postFrameAdjSrc[2]
            # Apply the contrast directly to the display
            self.write_cmd([0x81, c])
        setattr(self, '_brightness', c)


    # GPU (Gray Processing Unit) thread function
    @micropython.viper
    def _display_thread(self):
        # local object arrays for display framebuffers and post-frame commands
        subframes = self._subframes
        postFrameAdj = array('O', [self._postFrameAdj[0], self._postFrameAdj[1], self._postFrameAdj[2]])
        # cache various instance variables, buffers, and functions/methods
        state = ptr32(self._state)
        spi_write = self._spi.write
        dc = self._dc
        preFrameCmds = self._preFrameCmds
        postFrameCmds = self._postFrameCmds

        # we want ptr32 vars for fast buffer copying
        bb = ptr32(self.buffer) ; bs = ptr32(self.shading)
        b1 = ptr32(subframes[0]) ; b2 = ptr32(subframes[1]) ; b3 = ptr32(subframes[2])

        state[_ST_THREAD] = _THREAD_RUNNING
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
                spi_write(subframes[fn])
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
                    while i < _BUFF_INT_SIZE:
                        v1 = bb[i]
                        v2 = bs[i]
                        # this isn't a straight copy. Instead we are mapping:
                        # in        out
                        # 0 (0b00)  0 (0b000)
                        # 1 (0b01)  7 (0b111)
                        # 2 (0b10)  1 (0b001)
                        # 3 (0b11)  3 (0b011)
                        b1[i] = v1 | v2
                        b2[i] = v2
                        b3[i] = v1 & (v1 ^ v2)
                        i += 1
                    state[_ST_COPY_BUFFS] = 0
                # check if there's a pending contrast/brightness value change
                # again, we only adjust this after the last frame in the cycle
                elif (fn == 2) and state[_ST_CONTRAST]:
                    # Copy in the new contrast adjustments
                    ptr8(postFrameAdj[0])[1] = postFrameAdjSrc[0]
                    ptr8(postFrameAdj[1])[1] = postFrameAdjSrc[1]
                    ptr8(postFrameAdj[2])[1] = postFrameAdjSrc[2]
                    state[_ST_CONTRAST] = 0
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

        # mark that we've stopped
        state[_ST_THREAD] = _THREAD_STOPPED


    @micropython.viper
    def fill(self, colour:int):
        buffer = ptr32(self.buffer)
        shading = ptr32(self.shading)
        f1 = -1 if colour & 1 else 0
        f2 = -1 if colour & 2 else 0
        i = 0
        while i < _BUFF_INT_SIZE:
            buffer[i] = f1
            shading[i] = f2
            i += 1

    @micropython.viper
    def drawFilledRectangle(self, x:int, y:int, width:int, height:int, colour:int):
        if x >= _WIDTH: return
        if y >= _HEIGHT: return
        if width <= 0: return
        if height <= 0: return
        if x < 0:
            width += x
            x = 0
        if y < 0:
            height += y
            y = 0
        x2 = x + width
        y2 = y + height
        if x2 > _WIDTH:
            x2 = _WIDTH
            width = _WIDTH - x
        if y2 > _HEIGHT:
            y2 = _HEIGHT
            height = _HEIGHT - y

        buffer = ptr8(self.buffer)
        shading = ptr8(self.shading)

        o = (y >> 3) * _WIDTH
        oe = o + x2
        o += x
        strd = _WIDTH - width

        v1 = 0xff if colour & 1 else 0
        v2 = 0xff if colour & 2 else 0

        yb = y & 7
        ybh = 8 - yb
        if height <= ybh:
            m = ((1 << height) - 1) << yb
        else:
            m = 0xff << yb
        im = 255-m
        while o < oe:
            if colour & 1:
                buffer[o] |= m
            else:
                buffer[o] &= im
            if colour & 2:
                shading[o] |= m
            else:
                shading[o] &= im
            o += 1
        height -= ybh
        while height >= 8:
            o += strd
            oe += _WIDTH
            while o < oe:
                buffer[o] = v1
                shading[o] = v2
                o += 1
            height -= 8
        if height > 0:
            o += strd
            oe += _WIDTH
            m = (1 << height) - 1
            im = 255-m
            while o < oe:
                if colour & 1:
                    buffer[o] |= m
                else:
                    buffer[o] &= im
                if colour & 2:
                    shading[o] |= m
                else:
                    shading[o] &= im
                o += 1



    @micropython.viper
    def drawHLine(self, x:int, y:int, width:int, colour:int):
        if y < 0 or y >= _HEIGHT: return
        if x >= _WIDTH: return
        if width <= 0: return
        if x < 0:
            width += x
            x = 0
        x2 = x + width
        if x2 > _WIDTH:
            x2 = _WIDTH
        o = (y >> 3) * _WIDTH
        oe = o + x2
        o += x
        m = 1 << (y & 7)
        im = 255-m
        buffer = ptr8(self.buffer)
        shading = ptr8(self.shading)
        if colour == 0:
            while o < oe:
                buffer[o] &= im
                shading[o] &= im
                o += 1
        elif colour == 1:
            while o < oe:
                buffer[o] |= m
                shading[o] &= im
                o += 1
        elif colour == 2:
            while o < oe:
                buffer[o] &= im
                shading[o] |= m
                o += 1
        elif colour == 3:
            while o < oe:
                buffer[o] |= m
                shading[o] |= m
                o += 1


    @micropython.viper
    def drawVLine(self, x:int, y:int, height:int, colour:int):
        if x < 0 or x >= _WIDTH: return
        if y >= _HEIGHT: return
        if height <= 0: return
        if y < 0:
            height += y
            y = 0
        if (y + height) > _HEIGHT:
            height = _HEIGHT - y

        buffer = ptr8(self.buffer)
        shading = ptr8(self.shading)

        o = (y >> 3) * _WIDTH + x

        v1 = 0xff if colour & 1 else 0
        v2 = 0xff if colour & 2 else 0

        yb = y & 7
        ybh = 8 - yb
        if height <= ybh:
            m = ((1 << height) - 1) << yb
        else:
            m = 0xff << yb
        im = 255-m
        if colour & 1:
            buffer[o] |= m
        else:
            buffer[o] &= im
        if colour & 2:
            shading[o] |= m
        else:
            shading[o] &= im
        height -= ybh
        while height >= 8:
            o += _WIDTH
            buffer[o] = v1
            shading[o] = v2
            height -= 8
        if height > 0:
            o += _WIDTH
            m = (1 << height) - 1
            im = 255-m
            if colour & 1:
                buffer[o] |= m
            else:
                buffer[o] &= im
            if colour & 2:
                shading[o] |= m
            else:
                shading[o] &= im


    @micropython.viper
    def drawRectangle(self, x:int, y:int, width:int, height:int, colour:int):
        self.drawHLine(x, y, width, colour)
        self.drawHLine(x, y+height-1, width, colour)
        self.drawVLine(x, y, height, colour)
        self.drawVLine(x+width-1, y, height, colour)


    @micropython.viper
    def setPixel(self, x:int, y:int, colour:int):
        if x < 0 or x >= _WIDTH or y < 0 or y >= _HEIGHT:
            return
        o = (y >> 3) * _WIDTH + x
        m = 1 << (y & 7)
        im = 255-m
        buffer = ptr8(self.buffer)
        shading = ptr8(self.shading)
        if colour & 1:
            buffer[o] |= m
        else:
            buffer[o] &= im
        if colour & 2:
            shading[o] |= m
        else:
            shading[o] &= im

    @micropython.viper
    def getPixel(self, x:int, y:int) -> int:
        if x < 0 or x >= _WIDTH or y < 0 or y >= _HEIGHT:
            return 0
        o = (y >> 3) * _WIDTH + x
        m = 1 << (y & 7)
        buffer = ptr8(self.buffer)
        shading = ptr8(self.shading)
        colour = 0
        if buffer[o] & m:
            colour = 1
        if shading[o] & m:
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
        dx = x1 - x0
        dy = y1 - y0
        sx = 1
        # y increment is always 1
        if dy < 0:
            x0,x1 = x1,x0
            y0,y1 = y1,y0
            dy = 0 - dy
            dx = 0 - dx
        if dx < 0:
            dx = 0 - dx
            sx = -1
        x = x0
        y = y0
        buffer = ptr8(self.buffer)
        shading = ptr8(self.shading)

        o = (y >> 3) * _WIDTH + x
        m = 1 << (y & 7)
        im = 255-m

        if dx > dy:
            err = dx >> 1
            while x != x1:
                if 0 <= x < _WIDTH and 0 <= y < _HEIGHT:
                    if colour & 1:
                        buffer[o] |= m
                    else:
                        buffer[o] &= im
                    if colour & 2:
                        shading[o] |= m
                    else:
                        shading[o] &= im
                err -= dy
                if err < 0:
                    y += 1
                    m <<= 1
                    if m & 0x100:
                        o += _WIDTH
                        m = 1
                        im = 0xfe
                    else:
                        im = 255-m
                    err += dx
                x += sx
                o += sx
        else:
            err = dy >> 1
            while y != y1:
                if 0 <= x < _WIDTH and 0 <= y < _HEIGHT:
                    if colour & 1:
                        buffer[o] |= m
                    else:
                        buffer[o] &= im
                    if colour & 2:
                        shading[o] |= m
                    else:
                        shading[o] &= im
                err -= dx
                if err < 0:
                    x += sx
                    o += sx
                    err += dy
                y += 1
                m <<= 1
                if m & 0x100:
                    o += _WIDTH
                    m = 1
                    im = 0xfe
                else:
                    im = 255-m
        if 0 <= x < _WIDTH and 0 <= y < _HEIGHT:
            if colour & 1:
                buffer[o] |= m
            else:
                buffer[o] &= im
            if colour & 2:
                shading[o] |= m
            else:
                shading[o] &= im



    def setFont(self, fontFile, width, height, space):
        sz = stat(fontFile)[6]
        self.font_bmap = bytearray(sz)
        with open(fontFile, 'rb') as fh:
            fh.readinto(self.font_bmap)
        self.font_width = width
        self.font_height = height
        self.font_space = space
        self.font_glyphcnt = sz // width


    @micropython.viper
    def drawText(self, txt, x:int, y:int, colour:int):
        buffer = ptr8(self.buffer)
        shading = ptr8(self.shading)
        font_bmap = ptr8(self.font_bmap)
        font_width = int(self.font_width)
        font_space = int(self.font_space)
        font_glyphcnt = int(self.font_glyphcnt)
        sm1o = 0xff if colour & 1 else 0
        sm1a = 255 - sm1o
        sm2o = 0xff if colour & 2 else 0
        sm2a = 255 - sm2o
        ou = (y >> 3) * _WIDTH + x
        ol = ou + _WIDTH
        shu = y & 7
        shl = 8 - shu
        for c in txt:
            if isinstance(c, str):
                co = int(ord(c)) - 0x20
            else:
                co = int(c) - 0x20
            if co < font_glyphcnt:
                gi = co * font_width
                gx = 0
                while gx < font_width:
                    if 0 <= x < _WIDTH:
                        gb = font_bmap[gi + gx]
                        gbu = gb << shu
                        gbl = gb >> shl
                        if 0 <= ou < 360:
                            # paint upper byte
                            buffer[ou] = (buffer[ou] | (gbu & sm1o)) & 255-(gbu & sm1a)
                            shading[ou] = (shading[ou] | (gbu & sm2o)) & 255-(gbu & sm2a)
                        if (shl != 8) and (0 <= ol < 360):
                            # paint lower byte
                            buffer[ol] = (buffer[ol] | (gbl & sm1o)) & 255-(gbl & sm1a)
                            shading[ol] = (shading[ol] | (gbl & sm2o)) & 255-(gbl & sm2a)
                    ou += 1
                    ol += 1
                    x += 1
                    gx += 1
            ou += font_space
            ol += font_space
            x += font_space


    @micropython.viper
    def blit(self, src1:ptr8, src2:ptr8, x:int, y:int, width:int, height:int, key:int, mirrorX:int, mirrorY:int):
        if x+width < 0 or x >= _WIDTH:
            return
        if y+height < 0 or y >= _HEIGHT:
            return
        buffer = ptr8(self.buffer)
        shading = ptr8(self.shading)

        stride = width

        srcx = 0 ; srcy = 0
        dstx = x ; dsty = y
        sdx = 1
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
        if dstx+width > _WIDTH:
            width = _WIDTH - dstx
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
        if dsty+height > _HEIGHT:
            height = _HEIGHT - dsty

        srco = (srcy >> 3) * stride + srcx
        srcm = 1 << (srcy & 7)

        dsto = (dsty >> 3) * _WIDTH + dstx
        dstm = 1 << (dsty & 7)
        dstim = 255 - dstm

        while height != 0:
            srcco = srco
            dstco = dsto
            i = width
            while i != 0:
                v = 0
                if src1[srcco] & srcm:
                    v = 1
                if src2[srcco] & srcm:
                    v |= 2
                if (key == -1) or (v != key):
                    if v & 1:
                        buffer[dstco] |= dstm
                    else:
                        buffer[dstco] &= dstim
                    if v & 2:
                        shading[dstco] |= dstm
                    else:
                        shading[dstco] &= dstim
                srcco += sdx
                dstco += 1
                i -= 1
            dstm <<= 1
            if dstm & 0x100:
                dsto += _WIDTH
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
        if x+width < 0 or x >= _WIDTH:
            return
        if y+height < 0 or y >= _HEIGHT:
            return
        buffer = ptr8(self.buffer)
        shading = ptr8(self.shading)

        stride = width

        srcx = 0 ; srcy = 0
        dstx = x ; dsty = y
        sdx = 1
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
        if dstx+width > _WIDTH:
            width = _WIDTH - dstx
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
        if dsty+height > _HEIGHT:
            height = _HEIGHT - dsty

        srco = (srcy >> 3) * stride + srcx
        srcm = 1 << (srcy & 7)

        dsto = (dsty >> 3) * _WIDTH + dstx
        dstm = 1 << (dsty & 7)
        dstim = 255 - dstm

        while height != 0:
            srcco = srco
            dstco = dsto
            i = width
            while i != 0:
                if (mask[srcco] & srcm) == 0:
                    if src1[srcco] & srcm:
                        buffer[dstco] |= dstm
                    else:
                        buffer[dstco] &= dstim
                    if src2[srcco] & srcm:
                        shading[dstco] |= dstm
                    else:
                        shading[dstco] &= dstim
                srcco += sdx
                dstco += 1
                i -= 1
            dstm <<= 1
            if dstm & 0x100:
                dsto += _WIDTH
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

display = Grayscale()
display.enableGrayscale()
