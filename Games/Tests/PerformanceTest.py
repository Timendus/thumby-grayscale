
from utime import ticks_us
from machine import freq
freq(125000000)


# Results (example full run):
'''
core0 GPUv2.1
Draw Angled Line Test: 443639
Draw Straight Line Test: 669122
Hello World Chart Test: 1543745
Rectangle Chart Test: 1876413
Filled Rectangle Chart Test: 880328
Hello World Test: 670881
Cat Sprite Test (nofill): 1724246
White Fill Test: 1104001
Light Gray Fill Test: 1103964

core0 GPUv2.0
Draw Angled Line Test: 446384
Draw Straight Line Test: 340052
Hello World Chart Test: 2338940
Rectangle Chart Test: 1149257
Filled Rectangle Chart Test: 907525
Hello World Test: 1421068
Cat Sprite Test (nofill): 1633822
White Fill Test: 1137705
Light Gray Fill Test: 1137248
'''

# TODO Draw line test

for i in range(2):
    if i==0:
        import thumbyGrayscale as grayscale
        gs = grayscale.display
        gs.startGPU()
        cat = grayscale.ShadedSprite(
            12, 9,         # Dimensions
            bytearray([    # Layer 2 data
                175,7,169,254,237,255,191,157,190,233,255,175,
                1,1,0,1,1,1,1,1,1,1,1,1
            ]),
            bytearray([    # Layer 1 data
                80,248,254,249,238,252,188,222,189,238,248,80,
                0,0,1,1,1,1,1,1,1,1,0,0
            ]),
            30, 15         # Position
        )
        print("\ncore0 GPUv2.1")
    else:
        gs.stopGPU()
        import grayscale as grayscale
        gs = grayscale.Grayscale()
        cat = grayscale.Sprite(
            12, 9,         # Dimensions
            bytearray([    # Layer 2 data
                255,255,87,7,3,3,3,67,3,7,7,255,
                1,1,1,0,0,0,0,0,0,0,1,1
            ]),
            bytearray([    # Layer 1 data
                175,7,169,254,237,255,191,157,190,233,255,175,
                1,1,0,1,1,1,1,1,1,1,1,1
            ]),
            30, 15         # Position
        )
        print("\ncore0 GPUv2.0")

    # Draw Angled Line Test
    t = ticks_us()
    for i in range(1000):
        gs.drawLine(2, 2, 70, 38, gs.WHITE)
        gs.drawLine(70, 2, 2, 38, gs.DARKGRAY)
    print("Draw Angled Line Test:", ticks_us() - t)

    # Draw Straight Line Test
    t = ticks_us()
    for i in range(1000):
        gs.drawLine(2, 2, 2, 38, gs.WHITE)
        gs.drawLine(2, 2, 2, 38, gs.DARKGRAY)
        gs.drawLine(2, 2, 70, 2, gs.WHITE)
        gs.drawLine(2, 2, 70, 2, gs.DARKGRAY)
    print("Draw Straight Line Test:", ticks_us() - t)

    # Hello World Chart Test
    t = ticks_us()
    for i in range(1000):
        gs.drawFilledRectangle(0, 0, 72, 40, gs.WHITE)
        gs.drawFilledRectangle(0, 0, 62, 30, gs.LIGHTGRAY)
        gs.drawFilledRectangle(0, 0, 52, 20, gs.DARKGRAY)
        gs.drawFilledRectangle(0, 0, 42, 10, gs.BLACK)
        gs.drawText("Hello", 2, 31, gs.LIGHTGRAY)
        gs.drawText("world!", 37, 31, gs.DARKGRAY)
    print("Hello World Chart Test:", ticks_us() - t)

    # Rectangle Chart Test
    t = ticks_us()
    for i in range(1000):
        gs.drawRectangle(0, 0, 72, 40, gs.WHITE)
        gs.drawRectangle(0, 0, 62, 30, gs.LIGHTGRAY)
        gs.drawRectangle(0, 0, 52, 20, gs.DARKGRAY)
        gs.drawRectangle(0, 0, 42, 10, gs.BLACK)
    print("Rectangle Chart Test:", ticks_us() - t)

    # Filled Rectangle Chart Test
    t = ticks_us()
    for i in range(1000):
        gs.drawFilledRectangle(0, 0, 72, 40, gs.WHITE)
        gs.drawFilledRectangle(0, 0, 62, 30, gs.LIGHTGRAY)
        gs.drawFilledRectangle(0, 0, 52, 20, gs.DARKGRAY)
        gs.drawFilledRectangle(0, 0, 42, 10, gs.BLACK)
    print("Filled Rectangle Chart Test:", ticks_us() - t)
    
    # Hello World Test
    t = ticks_us()
    for i in range(1000):
        gs.drawText("Hello", 2, 31, gs.LIGHTGRAY)
        gs.drawText("world!", 37, 31, gs.DARKGRAY)
    print("Hello World Test:", ticks_us() - t)
    
    # Cat Sprite Test
    t = ticks_us()
    dx = dy = 1
    for i in range(5000):
        gs.drawSprite(cat)
        cat.x += dx
        cat.y += dy
        if cat.x == 0 or cat.x == 60:
            dx = -1 * dx
        if cat.y == 0 or cat.y == 31:
            dy = -1 * dy
    print("Cat Sprite Test (nofill):", ticks_us() - t)
    
    # White Fill Test
    t = ticks_us()
    for i in range(10000):
        gs.fill(gs.WHITE)
    print("White Fill Test:", ticks_us() - t)
    
    # Light Gray Fill Test
    t = ticks_us()
    for i in range(10000):
        gs.fill(gs.LIGHTGRAY)
    print("Light Gray Fill Test:", ticks_us() - t)
