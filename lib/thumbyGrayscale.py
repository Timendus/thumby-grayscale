from array import array
from machine import Pin, SPI, idle
from os import stat
from utime import sleep_ms, ticks_diff, ticks_ms, ticks_us, sleep_us
import _thread
from ssd1306 import SSD1306_SPI
from thumbyButton import buttonA, buttonB, buttonU, buttonD, buttonL, buttonR
from thumbySprite import Sprite as _Sprite

# The times below are calculated using phase 1 and phase 2 pre-charge
# periods of 1 clock.
# Note that although the SSD1306 datasheet doesn't state it, the 50
# clocks period per row _is_ a constant (datasheets for similar
# controllers from the same manufacturer state this).
# 530kHz is taken to be the highest nominal clock frequency. The
# calculations shown provide the value in seconds, which can be
# multiplied by 1e6 to provide a microsecond value.
# Both values are in microseconds.
_PRE_FRAME_TIME = const(785) # 8 rows: (8*(1+1+50)) / 530e3 seconds
_FRAME_TIME = const(4709) # 48 rows: (49*(1+1+50)) / 530e3 seconds

# Thread state variables for managing the Grayscale Thread.
_THREAD_STOPPED = const(0)
_THREAD_RUNNING = const(1)
_THREAD_STOPPING = const(2)

# Indexes into the multipurpose state array, accessing a particular status.
_ST_THREAD = const(0)
_ST_COPY_BUFFS = const(1)
_ST_PENDING_CMD = const(2)
_ST_CONTRAST = const(3)

# Screen display size constants
_WIDTH = const(72)
_HEIGHT = const(40)
_BUFF_SIZE = const((_HEIGHT // 8) * _WIDTH)
_BUFF_INT_SIZE = const(_BUFF_SIZE // 4)

class Grayscale:
    ### Grayscale extension of thumby.display retaining compatbility,
    # (matches thumbyGraphics.GraphicsClass but with shading)
    # Most games can be switched to support grayscale with the following:
    #     import thumbyGrayscale
    #     thumby.display = thumbyGrayscale.display
    #     thumbyGrayscale.display.startGPU()
    ###

    # BLACK and WHITE is 0 and 1 to match compatbility with the std Thumby API.
    BLACK = 0
    WHITE = 1
    DARKGRAY = 2
    LIGHTGRAY = 3

    def __init__(self):
        # Public and compatibility variables
        self.width = _WIDTH
        self.height = _HEIGHT
        self.max_x = _WIDTH - 1
        self.max_y = _HEIGHT - 1
        self.lastUpdateEnd = 0
        self.frameRate = 0
        self.display = self # This acts as both the GraphicsClass and SSD1306
        self.contrast = self.brightness

        # Draw buffers.
        # This comprises of two full buffer lengths.
        # The first section contains black and white compatible
        # with the display buffer from the standard Thumby API,
        # and the second contains the shading to create
        # offwhite (lightgray) or offblack (darkgray).
        self._drawBuffer = bytearray(_BUFF_SIZE*2)
        # The base "buffer" matches compatibility with the std Thumby API.
        self.buffer = memoryview(self._drawBuffer)[:_BUFF_SIZE]
        # The "shading" buffer adds the grayscale
        self.shading = memoryview(self._drawBuffer)[_BUFF_SIZE:]

        # Display device configuration
        self._spi = SPI(0, sck=Pin(18), mosi=Pin(19))
        self._dc = Pin(17)
        self._cs = Pin(16)
        self._res = Pin(20)
        self._res.init(Pin.OUT, value=1)
        self._spi.init(baudrate=100 * 1000 * 1000, polarity=0, phase=0)
        # Black and White direct display for when the GPU is off (also inits)
        self._displayBW = SSD1306_SPI(_WIDTH, _HEIGHT,
            self._spi, dc=self._dc, res=self._res, cs=self._cs)
        # Inject buffer to Black and White display
        self._displayBW.buffer = self.buffer

        # Display driver subframe buffers.
        # These essentially combine into one framebuffer,
        # but are split into three buffers that hit the display
        # at different subframe intervals.
        self._subframes = array('O', [bytearray(_BUFF_SIZE),
            bytearray(_BUFF_SIZE), bytearray(_BUFF_SIZE)])

        # Timing and display synchronisation commands.
        #
        # DISCLAIMER: The following comments relating to the hardware
        # may not be technically correct, and may instead only be
        # effectively correct.
        #
        # We keep the display synchronised by resetting the row counter
        # before each frame and then outputting a frame of 57 rows.
        # This is 17 rows past the 40 of the actual display.
        #
        # Prior to loading in the frame we park the row counter at row 0
        # and wait for the nominal time for 8 rows to be output. This
        # seems to provide enough time for the row counter to reach row 0
        # before it sticks there. Note: the row counter may jump then stick.
        #
        # The 'parking' is done by setting the number of rows (aka
        # 'multiplex ratio') to 1 row. This is an invalid setting
        # according to the datasheet but seems to still have the desired
        # effect.
        # 0xa8,0    Set multiplex ratio to 1
        # 0xd3,52   Set display offset to 52
        self._preFrameCmds = bytearray([0xa8,0, 0xd3,52])
        # Once the frame has been loaded into the display controller's
        # GDRAM, we set the controller to output 57 rows, and then delay
        # for the nominal time for 48 rows to be output.
        # Considering the 17 row 'buffer space' after the real 40 rows,
        # that puts us around halfway between the end of the display, and
        # the row at which it would wrap around.
        # By having 8.5 rows either side of the nominal timing, we can
        # absorb any variation in the frequency of the display controller's
        # RC oscillator as well as any timing offsets introduced by the
        # Python code.
        # 0xd3,x    Set display offset. Since rows are scanned in reverse,
        #           the calculation must work backwards from the last
        #.          controller row.
        # 0xa8,57-1 Set multiplex ratio to 57
        self._postFrameCmds = bytearray([0xd3,40+(64-57), 0xa8,57-1])

        # Brightness modulation.
        # We enhance the greys by modulating the contrast,
        # limited to brightness setting from thumby.cfg
        brightness = 127
        try:
            with open("thumby.cfg", "r") as fh:
                _, _, conf = fh.read().partition("brightness,")
                b = int(conf.split(',')[0])
                # Set to the relevant brightness level
                brightness = 0 if b==0 else 56 if b==1 else 127
        except (OSError, ValueError):
            pass
        # 0x81,<val>    Set Bank0 contrast value to <val>
        self._postFrameAdj = array('O', [
            bytearray([0x81,brightness>>5]),
            bytearray([0x81,brightness]),
            bytearray([0x81,(brightness << 1) + 1])])

        # It's safer to avoid using regular variables for shared thread data.
        # Instead, elements of an array/bytearray should be used.
        # We're also using a uint32 array here, as thsis more likely to ensure
        # the atomicity of any element accesses.
        # [thread_state, buff_copy_gate, pending_cmd_gate, constrast_change]
        self._state = array('I', [_THREAD_STOPPED,0,0,0xff])
        # Buffer to funnel cmds to the thread
        self._pendingCmds = bytearray([0] * 8)
        # Set font, also supports: self.setFont('lib/font8x8.bin', 8, 8, 0)
        self.setFont('lib/font5x7.bin', 5, 7, 1)

    # Allow use of 'with' for manaing the GPU state
    def __enter__(self):
        return self
    def __exit__(self, type, value, traceback):
        self.stopGPU()
    # Garbage collect the GPU thread properly
    def __del__(self):
        self.stopGPU()


    ## Display device functions ##


    def reset(self):
        self._res(1)
        sleep_ms(1)
        self._res(0)
        sleep_ms(10)
        self._res(1)
        sleep_ms(10)

    def init_display(self):
        # Initialise the display for grayscale timings
        self.reset()
        self._cs(0)
        self._dc(0)
        # Usual initialisation, except with shortest pre-charge periods
        # and highest clock frequency:
        # 0xae      Display Off
        # 0x20,0x00 Set horizontal addressing mode
        # 0x40      Set display start line to 0
        # 0xa1      Set segment remap mode 1
        # 0xa8,63   Set multiplex ratio to 64 (will be changed later)
        # 0xc8      Set COM output scan direction 1
        # 0xd3,54   Set display offset to 0 (will be changed later)
        # 0xda,0x12 Set COM pins hw config: alt config, disable left/right remap
        # 0xd5,0xf0 Set clk div ratio = 1, and osc freq = ~370kHz
        # 0xd9,0x11 Set pre-charge periods: phase 1 = 1 , phase 2 = 1
        # 0xdb,0x20 Set Vcomh deselect level = 0.77 x Vcc
        # 0x81,0x7f Set Bank0 contrast to 127 (will be changed later)
        # 0xa4      Do not enable entire display (i.e. use GDRAM)
        # 0xa6      Normal (not inverse) display
        # 0x8d,0x14 Charge bump setting: enable charge pump during display on
        # 0xad,0x30 Select internal 30uA Iref (max Iseg=240uA) during display on
        # 0xf       Set display on
        self._spi.write(bytearray([
            0xae, 0x20,0x00, 0x40, 0xa1, 0xa8,63, 0xc8, 0xd3,0, 0xda,0x12,
            0xd5,0xf0, 0xd9,0x11, 0xdb,0x20, 0x81,0x7f, 0xa4, 0xa6, 0x8d,0x14,
            0xad,0x30, 0xaf]))
        self._dc(1)
        # clear the entire GDRAM
        for _ in range(32):
            self._spi.write(bytearray(32))
        self._dc(0)
        # set the GDRAM window
        # 0x21,28,99    Set column start (28) and end (99) addresses
        # 0x22,0,4      Set page start (0) and end (4) addresses0
        self._spi.write(bytearray([0x21,28,99, 0x22,0,4]))

    @micropython.viper
    def show(self):
        state = ptr32(self._state)
        if state[_ST_THREAD] == _THREAD_RUNNING:
            state[_ST_COPY_BUFFS] = 1
            while state[_ST_COPY_BUFFS] != 0:
                idle()
        else:
            self._displayBW.show()

    @micropython.native
    def show_async(self):
        if state[_ST_THREAD] == _THREAD_RUNNING:
            self._state[_ST_COPY_BUFFS] = 1
        else:
            self._displayBW.show()

    @micropython.native
    def write_cmd(self, cmd):
        ### Send display controller commands ###
        if cmd is list:
            cmd = bytearray(cmd)
        elif not cmd is bytearray:
            cmd = bytearray([cmd])

        # Handle when GPU isn't active
        if self._state[_ST_THREAD] != _THREAD_RUNNING:
            self._dc(0)
            self._spi.write(cmd)
            return

        # GPU is active - ferry the commans to the thread
        pendingCmds = self._pendingCmds
        assert len(cmd) <= len(pendingCmds), "Display commands too long"
        # Fill the rest of the bytearray with display controller NOPs
        cmd += bytearray([0x3e]*(len(pendingCmds)-len(cmd)))
        # Send to GPU
        for i in range(len(cmd)):
            pendingCmds[i] = cmd[i]
        # Notify GPU and wait
        self._state[_ST_PENDING_CMD] = 1
        while self._state[_ST_PENDING_CMD]:
            idle()

    def poweroff(self):
        self.write_cmd(0xae)
    def poweron(self):
        self.write_cmd(0xaf)

    def startGPU(self):
        ### Activate grayscale in the display (Gray Processing Unit).
        # Takes over the second core.
        # When the GPU is not running, the display will only show
        # black and white.
        ###
        if self._state[_ST_THREAD] != _THREAD_STOPPED:
            self.stopGPU()

        # Start the GPU thread
        self.init_display()
        _thread.stack_size(2048) # minimum stack size for RP2040 upython port
        _thread.start_new_thread(self._display_thread, ())

        # Wait for the thread to successfully settle into a running state
        while self._state[_ST_THREAD] != _THREAD_RUNNING:
            idle()

    @micropython.viper
    def _display_thread(self):
        ### GPU (Gray Processing Unit) thread function ###
        # cache various instance variables, buffers, and functions/methods
        buffers = self._subframes
        postFrameAdj = self._postFrameAdj
        state = ptr32(self._state)
        spi_write = self._spi.write
        dc = self._dc
        preFrameCmds = self._preFrameCmds
        postFrameCmds = self._postFrameCmds
        pendingCmds = self._pendingCmds
        dBuf = ptr32(self._drawBuffer)
        b1 = ptr32(buffers[0]); b2 = ptr32(buffers[1]); b3 = ptr32(buffers[2])

        dc(0) # command mode
        state[_ST_THREAD] = _THREAD_RUNNING
        while state[_ST_THREAD] == _THREAD_RUNNING:
            # This is the main GPU loop.
            # We cycle through each of the 3 display subframe buffers,
            # sending the framebuffer data and various commands.
            for fn in range(3):
                # Send fn'th subframe
                t0 = ticks_us()
                # Send the pre-frame commands to 'park' the row counter
                spi_write(preFrameCmds)
                # Now send the (sub) frame data
                dc(1) # frame data mode
                spi_write(buffers[fn])
                dc(0) # back to command mode
                # Send the first instance of the contrast adjust command
                spi_write(postFrameAdj[fn])
                # Wait for the pre-frame time to complete
                sleep_us(_PRE_FRAME_TIME - int(ticks_diff(ticks_us(), t0)))
                t0 = ticks_us()
                # Send the post-frame commands to display the frame
                spi_write(postFrameCmds)
                # Adjust the contrast for the specific frame number again.
                # If we do not do this twice, the screen can glitch.
                spi_write(postFrameAdj[fn])

                # Tasks that only happen after the last subframe
                if (fn == 2):
                    # Check if there's a pending frame copy required.
                    # We only copy the paint framebuffers to the display
                    # framebuffers on the last frame to avoid screen-tearing.
                    if (state[_ST_COPY_BUFFS] != 0):
                        # By using using ptr32 vars we copy 4 bytes at a time
                        for i in range (_BUFF_INT_SIZE):
                            v1 = dBuf[i]
                            v2 = dBuf[i+_BUFF_INT_SIZE]
                            # This remaps to the different buffer format.
                            b1[i] = v1 | v2 # white, lightgray and darkgray
                            b2[i] = v1 # white and lightgray
                            b3[i] = v1 & (v1^v2) # white only
                        state[_ST_COPY_BUFFS] = 0
                    # Check if there's a pending contrast/brightness change
                    elif (state[_ST_CONTRAST] != 0xff):
                        contrast = state[_ST_CONTRAST]
                        state[_ST_CONTRAST] = 0xff
                        # Shift the value to provide 3 different levels
                        postFrameAdj[0][1] = contrast >> 5
                        postFrameAdj[1][1] = contrast >> 1
                        postFrameAdj[2][1] = (contrast << 1) + 1
                    # Check if there are pending display controller commands
                    elif state[_ST_PENDING_CMD]:
                        spi_write(pendingCmds)
                        state[_ST_PENDING_CMD] = 0

                # Precise idle wait for next subframe timing.
                # This is a two stage wait for the frame time to complete.
                # We use sleep_ms first to allow idle loop usage,
                # with >>10 for a fast /1000 approximation.
                # Then we use sleep_us to busy wait for the precise time.
                sleep_ms((_FRAME_TIME - int(ticks_diff(ticks_us(), t0))) >> 10)
                sleep_us(_FRAME_TIME - int(ticks_diff(ticks_us(), t0)))

        # Announce the thread is done
        state[_ST_THREAD] = _THREAD_STOPPED

    def stopGPU(self, modeGPU=0):
        ### Disable grayscale, stopping the running thread.
        # If modeGPU is set to 1, it will not reset the display
        # controller configuration.
        ###
        if self._state[_ST_THREAD] == _THREAD_RUNNING:
            self._state[_ST_THREAD] = _THREAD_STOPPING
            while self._state[_ST_THREAD] != _THREAD_STOPPED:
                idle()
        # Refresh the image to the B/W form
        if not modeGPU:
            self._displayBW.init_display()
        self.show()


    ## GraphicsClass functions ##


    @micropython.native
    def setFPS(self, newFrameRate):
        self.frameRate = newFrameRate

    @micropython.viper
    def fill(self, colour:int):
        dBuf = ptr32(self._drawBuffer)
        f = -1 if colour & 1 else 0
        for i in range(_BUFF_INT_SIZE):
            dBuf[i] = f # Black/White layer
        f = -1 if colour & 2 else 0
        for i in range(_BUFF_INT_SIZE, _BUFF_INT_SIZE*2):
            dBuf[i] = f # Shading layer

    def brightness(self, c):
        if c < 0:
            c = 0
        elif c > 127:
            c = 127
        if state[_ST_THREAD] == _THREAD_RUNNING:
            self._state[_ST_CONTRAST] = c
        else:
            self._displayBW.contrast(c)

    @micropython.native
    def update(self):
        self.show()
        if self.frameRate > 0:
            frameTimeMs = 1000 // self.frameRate
            last = self.lastUpdateEnd
            frameTimeRemaining = frameTimeMs - ticks_diff(ticks_ms(), last)
            while frameTimeRemaining > 1:
                buttonA.update()
                buttonB.update()
                buttonU.update()
                buttonD.update()
                buttonL.update()
                buttonR.update()
                sleep_ms(1)
                frameTimeRemaining = frameTimeMs - ticks_diff(ticks_ms(), last)
            while frameTimeRemaining > 0:
                frameTimeRemaining = frameTimeMs - ticks_diff(ticks_ms(), last)
        self.lastUpdateEnd = ticks_ms()

    @micropython.viper
    def drawFilledRectangle(self, x:int, y:int, width:int, height:int, colour:int):
        if x > 71 or y > 39 or width <= 0 or height <= 0:
            return
        if x < 0:
            width += x
            x = 0
        if y < 0:
            height += y
            y = 0
        x2 = x + width
        y2 = y + height
        if x2 > 72:
            x2 = 72
            width = 72 - x
        if y2 > 40:
            y2 = 40
            height = 40 - y

        dBuff = ptr8(self._drawBuffer)

        o = (y >> 3) * 72
        oe = o + x2
        o += x
        strd = 72 - width

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
                dBuff[o] |= m
            else:
                dBuff[o] &= im
            if colour & 2:
                dBuff[o+_BUFF_SIZE] |= m
            else:
                dBuff[o+_BUFF_SIZE] &= im
            o += 1
        height -= ybh
        while height >= 8:
            o += strd
            oe += 72
            while o < oe:
                dBuff[o] = v1
                dBuff[o+_BUFF_SIZE] = v2
                o += 1
            height -= 8
        if height > 0:
            o += strd
            oe += 72
            m = (1 << height) - 1
            im = 255-m
            while o < oe:
                if colour & 1:
                    dBuff[o] |= m
                else:
                    dBuff[o] &= im
                if colour & 2:
                    dBuff[o+_BUFF_SIZE] |= m
                else:
                    dBuff[o+_BUFF_SIZE] &= im
                o += 1

    @micropython.viper
    def drawRectangle(self, x:int, y:int, width:int, height:int, colour:int):
        x1, y1 = x + width - 1, y + height - 1
        self.drawLine(x, y, x1, y, colour)
        self.drawLine(x, y1, x1, y1, colour)
        self.drawLine(x, y, x, y1, colour)
        self.drawLine(x1, y, x1, y1, colour)

    @micropython.viper
    def setPixel(self, x:int, y:int, colour:int):
        if x < 0 or x >= 72 or y < 0 or y >= 40:
            return
        o = (y >> 3) * 72 + x
        m = 1 << (y & 7)
        im = 255-m
        dBuff = ptr8(self._drawBuffer)
        if colour & 1:
            dBuff[o] |= m
        else:
            dBuff[o] &= im
        if colour & 2:
            dBuff[o+_BUFF_SIZE] |= m
        else:
            dBuff[o+_BUFF_SIZE] &= im

    @micropython.viper
    def getPixel(self, x:int, y:int) -> int:
        if x < 0 or x >= 72 or y < 0 or y >= 40:
            return 0
        o = (y >> 3) * 72 + x
        m = 1 << (y & 7)
        dBuff = ptr8(self._drawBuffer)
        colour = 0
        if dBuff[o] & m:
            colour = 1
        if dBuff[o+_BUFF_SIZE] & m:
            colour |= 2
        return colour

    @micropython.viper
    def drawLine(self, x0:int, y0:int, x1:int, y1:int, colour:int):
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
        dBuff = ptr8(self._drawBuffer)

        o = (y >> 3) * 72 + x
        m = 1 << (y & 7)
        im = 255-m

        if dx > dy:
            err = dx >> 1
            while x != x1+1:
                if 0 <= x < 72 and 0 <= y < 40:
                    if colour & 1:
                        dBuff[o] |= m
                    else:
                        dBuff[o] &= im
                    if colour & 2:
                        dBuff[o+_BUFF_SIZE] |= m
                    else:
                        dBuff[o+_BUFF_SIZE] &= im
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
            err = dy >> 1
            while y != y1+1:
                if 0 <= x < 72 and 0 <= y < 40:
                    if colour & 1:
                        dBuff[o] |= m
                    else:
                        dBuff[o] &= im
                    if colour & 2:
                        dBuff[o+_BUFF_SIZE] |= m
                    else:
                        dBuff[o+_BUFF_SIZE] &= im
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
    def drawText(self, stringToPrint, x:int, y:int, colour:int):
        dBuff = ptr8(self._drawBuffer)
        font_bmap = ptr8(self.font_bmap)
        font_width = int(self.font_width)
        font_space = int(self.font_space)
        font_glyphcnt = int(self.font_glyphcnt)
        sm1o = 0xff if colour & 1 else 0
        sm1a = 255 - sm1o
        sm2o = 0xff if colour & 2 else 0
        sm2a = 255 - sm2o
        ou = (y >> 3) * 72 + x
        ol = ou + 72
        shu = y & 7
        shl = 8 - shu
        for c in stringToPrint:
            if isinstance(c, str):
                co = int(ord(c)) - 0x20
            else:
                co = int(c) - 0x20
            if co < font_glyphcnt:
                gi = co * font_width
                gx = 0
                while gx < font_width:
                    if 0 <= x < 72:
                        gb = font_bmap[gi + gx]
                        gbu = gb << shu
                        gbl = gb >> shl
                        if 0 <= ou < 360:
                            # paint upper byte
                            dBuff[ou] = (dBuff[ou] | (gbu & sm1o)) & 255-(gbu & sm1a)
                            dBuff[ou+_BUFF_SIZE] = (dBuff[ou+_BUFF_SIZE] | (gbu & sm2o)) & 255-(gbu & sm2a)
                        if (shl != 8) and (0 <= ol < 360):
                            # paint lower byte
                            dBuff[ol] = (dBuff[ol] | (gbl & sm1o)) & 255-(gbl & sm1a)
                            dBuff[ol+_BUFF_SIZE] = (dBuff[ol+_BUFF_SIZE] | (gbl & sm2o)) & 255-(gbl & sm2a)
                    ou += 1
                    ol += 1
                    x += 1
                    gx += 1
            ou += font_space
            ol += font_space
            x += font_space

    @micropython.viper
    def blit(self, sprtptr:ptr8, x:int, y:int, width:int, height:int, key:int, mirrorX:int, mirrorY:int):
        self.blitSHD(sprtptr, 0, x, y, width, height, key, mirrorX, mirrorY)

    @micropython.viper
    def blitSHD(self, sprtptr:ptr8, src2:ptr8, x:int, y:int, width:int, height:int, key:int, mirrorX:int, mirrorY:int):
        shading = int(src2)
        if x+width < 0 or x >= 72:
            return
        if y+height < 0 or y >= 40:
            return
        dBuff = ptr8(self._drawBuffer)

        stride = width

        srcx = srcy = 0
        dstx = x; dsty = y
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

        srco = (srcy >> 3) * stride + srcx
        srcm = 1 << (srcy & 7)

        dsto = (dsty >> 3) * 72 + dstx
        dstm = 1 << (dsty & 7)
        dstim = 255 - dstm

        while height != 0:
            srcco = srco
            dstco = dsto
            i = width
            while i != 0:
                v = 1 if sprtptr[srcco] & srcm else 0
                if shading and (src2[srcco] & srcm):
                    v |= 2
                if (key == -1) or (v != key):
                    if v & 1:
                        dBuff[dstco] |= dstm
                    else:
                        dBuff[dstco] &= dstim
                    if v & 2:
                        dBuff[dstco+_BUFF_SIZE] |= dstm
                    else:
                        dBuff[dstco+_BUFF_SIZE] &= dstim
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
        shading = 0
        if type(s) is ShadedSprite:
            shading = s.bitmapSHD
        self.blitSHD(s.bitmap, shading, s.x, s.y, s.width, s.height, s.key, s.mirrorX, s.mirrorY)

    @micropython.viper
    def blitWithMask(self, sprtptr:ptr8, x:int, y:int, width:int, height:int, key:int, mirrorX:int, mirrorY:int, mask:ptr8):
        self.blitSHD(sprtptr, 0, x, y, width, height, key, mirrorX, mirrorY, mask)

    @micropython.viper
    def blitWithMaskSHD(self, sprtptr:ptr8, src2:ptr8, x:int, y:int, width:int, height:int, key:int, mirrorX:int, mirrorY:int, mask:ptr8):
        shading = int(src2)
        if x+width < 0 or x >= 72:
            return
        if y+height < 0 or y >= 40:
            return
        dBuf = ptr8(self._drawBuffer)

        stride = width

        srcx = srcy = 0
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

        srco = (srcy >> 3) * stride + srcx
        srcm = 1 << (srcy & 7)

        dsto = (dsty >> 3) * 72 + dstx
        dstm = 1 << (dsty & 7)
        dstim = 255 - dstm
        if shading:
            src2 = ptr8(shading)

        while height != 0:
            srcco = srco
            dstco = dsto
            i = width
            while i != 0:
                if (mask[srcco] & srcm) == 0:
                    if sprtptr[srcco] & srcm:
                        dBuf[dstco] |= dstm
                    else:
                        dBuf[dstco] &= dstim
                    if shading:
                        if src2[srcco] & srcm:
                            dBuf[dstco+_BUFF_SIZE] |= dstm
                        else:
                            dBuf[dstco+_BUFF_SIZE] &= dstim
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
        shading = 0
        if type(s) is ShadedSprite:
            shading = s.bitmapSHD
        self.blitWithMaskSHD(s.bitmap, shading, s.x, s.y, s.width, s.height, s.key, s.mirrorX, s.mirrorY, m.bitmap)


class ShadedSprite(_Sprite):
    ### Extends the Sprite with grayscale support.
    # Pass in the shadingData to the constructor.
    # 1 sets white to lightgray and black to darkgray.
    ###
    @micropython.native
    def __init__(self, width, height, bitmapData, shadingData, x=0, y=0, key=-1, mirrorX=False, mirrorY=False):
        super().__init__(width, height, bitmapData, x, y, key, mirrorX, mirrorY)
        self.bitmapSourceSHD = shadingData
        if type(self.bitmapSourceSHD)==str:
            self.bitmapSHD = bytearray(self.bitmapByteCount)
            self.fileSHD = open(self.bitmapSourceSHD,'rb')
            self.fileSHD.readinto(self.bitmapSHD)
            assert(self.frameCount == stat(self.bitmapSourceSHD)[6] // self.bitmapByteCount)
        elif type(self.bitmapSourceSHD)==bytearray:
            self.bitmapSHD = memoryview(self.bitmapSourceSHD)[0:self.bitmapByteCount]
            assert(self.frameCount == len(self.bitmapSourceSHD) // self.bitmapByteCount)

    @micropython.native
    def setFrame(self, frame):
        super().setFrame(frame)
        if(frame >= 0 and (self.currentFrame is not frame % (self.frameCount))):
            self.currentFrame = frame % (self.frameCount)
            offset=self.bitmapByteCount*self.currentFrame
            if type(self.bitmapSourceSHD)==str:
                self.fileSHD.seek(offset)
                self.fileSHD.readinto(self.bitmapSHD)
            elif type(self.bitmapSourceSHD)==bytearray:
                self.bitmapSHD = memoryview(self.bitmapSourceSHD)[offset:offset+self.bitmapByteCount]

display = Grayscale()