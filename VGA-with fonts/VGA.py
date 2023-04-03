from machine import Pin
from rp2 import PIO, StateMachine, asm_pio
from micropython import const
from array import array
from uctypes import addressof
from gc import mem_free,collect
from math import cos,sin,pi,log

# 640*480 resolution
# Scanline part    Pixels    Time [µs]    32bits-Words
# Visible area      640       25.4220        100
# Front porch       16       0.6355          2,5
# Sync pulse        96       3.8133          15
# Back porch        48       1.9066          7,5
# Whole line        800      31.7775         125


# Possibility to change the system clock freq if needed - 125MHz (default/False) or 250MHz (True)
OVCLK=True     

# Routine to boost system clock
@micropython.viper
def set_freq(fclock:int)->int:
    #clock frequency to run the pico default 125MHz. Allow 100-250
    if (fclock<100000000 or fclock>250000000):
        print("invalid clock speed",fclock)
        print("Clock speed must be set between 100MHz and 250MHz")
        return
    if fclock<=130000000:
        FBDIV=fclock//1000000
        POSTDIV1=6  #default 6
        POSTDIV2=2  #default 2
    else: 
        FBDIV=fclock//2000000
        POSTDIV1=3  #default 6
        POSTDIV2=2  #default 2
    ptr32(0x4002800c)[0] = (POSTDIV1<<16)|(POSTDIV2<<12)
    ptr32(0x40028008)[0] = FBDIV
    cs=FBDIV*12//(POSTDIV1*POSTDIV2)
    print('clock speed',cs,'MHz')

# VGA parameters for 640x480 using 3b per pixel
H_res=const(640)             # Horizontal resolution in pixels
V_res=const(480)             # Vertical resolution in pixels
bit_per_pix=const(3)         # Bits per pixel
pixel_bitmask=const(0b111)   # Corresponding bitmask (used for replacing one 3bit pixel in a 32b word)
usable_bits=const(30)        # Numbers of bits that will be used in each 32b word
pix_per_words=const(10)     # Number of 3b pixel per 32b word

# Initiate cursor position (for character drawing only)
x_cursor = 0
y_cursor = 0

# Choose frequency parameters
if OVCLK:
    set_freq(250000000)# To use this Freq you'll have to modify the RGB PIO SM -> see comments in SM2 RGB PIO script
    SM0_FREQ=12587500  # Horizontal sync SM - For use with overclocked 250 MHz system clock
    SM1_FREQ=125000000 # Vertical sync SM - Max freq (driven by SM0 IRQ)
    SM2_FREQ=113287500 # RGB signal output - For use with overclocked 250 MHz system clock
else:
    SM0_FREQ=25175000 # Horizontal sync SM - For use with standard 125 MHz system clock
    SM1_FREQ=125000000 # Vertical sync SM - Max freq (driven by SM0 IRQ)
    SM2_FREQ=100700000 # Horizontal sync SM - For use with standard 125 MHz system clock


#statemachine configuration
#sm0 is used for H sync signal
@asm_pio(set_init=PIO.OUT_HIGH, autopull=True, pull_thresh=32)
def paral_Hsync():
    wrap_target()
    # ACTIVE + FRONTPORCH
    mov(x, osr)               # Copy value from OSR to x scratch register
    label("activeporch")
    jmp(x_dec,"activeporch")  # Remain high in active mode and front porch
    # SYNC PULSE
    set(pins, 0) [31]    # Low for hsync pulse (32 cycles)
    set(pins, 0) [31]    # Low for hsync pulse (32 cycles)
    set(pins, 0) [31]    # Low for hsync pulse (32 cycles)
    # BACKPORCH
    set(pins, 1) [31]    # High for back porch (32 cycles)
    set(pins, 1) [13]    # High for back porch (32 cycles)
    irq(0)               # Set IRQ to signal end of line (47 cycles)
    wrap()
#     
paral_write_Hsync = StateMachine(0, paral_Hsync,freq=SM0_FREQ, set_base=Pin(4))
# #
# #sm1 is used for V sync signal
@asm_pio(sideset_init=(PIO.OUT_HIGH,) * 1, autopull=True, pull_thresh=32)
def paral_Vsync():
    pull(block)                  # Pull from FIFO to OSR (only once)
    wrap_target()
    # ACTIVE
    mov(x, osr)                       # Copy value from OSR to x scratch register
    label("active")
    wait(1,irq,0)                     # Wait for hsync to go high
    irq(1)                             # Signal that we're in active mode
    jmp(x_dec,"active")                # Remain in active mode, decrementing counter
    # FRONTPORCH
    set(y, 9)                         # Use y scratch register as counter
    label("frontporch")
    wait(1,irq,0)                    # Wait for hsync to go high
    jmp(y_dec,"frontporch")            # Remain in frontporch, decrementing counter
    # SYNC PULSE
    wait(1,irq,0)              .side(0)# Wait for hsync to go high and Set pin low
    wait(1,irq,0)                      # Wait for hsync to go high (V sync pulse is 2 lines in 640*480 resolution)
    # BACKPORCH
    set(y, 31)                         # First part of back porch into y scratch register (and delays a cycle)
    label("backporch")
    wait(1,irq,0)              .side(1) # Wait for hsync to go high - SIDESET REPLACEMENT HERE
    jmp(y_dec,"backporch")             # Remain in backporch, decrementing counter
    wait(1,irq,0)
    wrap()
# 
paral_write_Vsync = StateMachine(1, paral_Vsync,freq=SM1_FREQ, sideset_base=Pin(5))

#sm4 is used for RGB signal
@asm_pio(out_init=(PIO.OUT_LOW,) * 3, out_shiftdir=PIO.SHIFT_RIGHT, sideset_init=(PIO.OUT_LOW,) * 3, autopull=True, pull_thresh=usable_bits)
def paral_RGB():
    pull(block)                  # Pull from FIFO to OSR (only once)
    mov(y, osr)                  # Copy value from OSR to y scratch register
    wrap_target()
    mov(x, y)                  .side(0) # Initialize counter variable + set colour pins to zero
    wait(1,irq,1)              # Wait for vsync active mode (starts 5 cycles after execution)
    label("colorout")
    out(pins,3)                # Push out to pins (one pixel)
    nop()                      [1]   
    nop()                            #
    nop()                      [1]   #  Uncomment these 3 lines if using 250MHz system clock
    nop()                      [1]   #
    jmp(x_dec,"colorout")       # Stay here thru horizontal active mode
    wrap()                   
    
paral_write_RGB = StateMachine(2, paral_RGB,freq=SM2_FREQ, out_base=Pin(0),sideset_base=Pin(0))

@micropython.viper
def configure_DMAs(nword:int, H_buffer_line_add:ptr32):
    # RGB DMAs
    # Using chan0 as "configure" DMA and chan1 as "Data transfer" DMA
    # Parameters common to the 2 DMA channels
    IRQ_QUIET = 0  # Do not generate an interrupt
    RING_SEL = 0   # No wrapping
    RING_SIZE = 0  # No wrapping
    HIGH_PRIORITY = 1
    INCR_WRITE = 0  # Non increment while writing

    #Setting up the "data" DMA channel 1
    TREQ_SEL = 2    #  num of rhe RGB statemachine -> at the pace of the PIO
    INCR_READ = 1   # 1 increment while reading
    DATA_SIZE = 2   # 32 bit transfer
    CHAIN_TO = 0    # Chain to configure channel DMA 0 so it starts again
    EN = 1          # Channel is enabled by the configure DMA chan0
    DMA_control_word = ((IRQ_QUIET << 21) | (TREQ_SEL << 15) | (CHAIN_TO  << 11) | (RING_SEL << 10) |
                        (RING_SIZE << 9) | (INCR_WRITE << 5) | (INCR_READ << 4) | (DATA_SIZE << 2) |
                        (HIGH_PRIORITY << 1) | (EN << 0))
    ptr32(0x50000040)[0] = 0                        # DMA Channel 1 Read Address pointer <- not important because reset by DMA0 "configure" channel
    ptr32(0x50000044)[0] = uint(0x50200018)         # DMA Channel 1 Write Address pointer -> PIO TX FIFO 2 (sm2) adress
    ptr32(0x50000048)[0] = nword                    # DMA Channel 1 Transfer Count <- length of the Data array buffer
    ptr32(0x50000060)[0] = DMA_control_word         # DMA Channel 1 Control and Status (using alias to not start immediatly - will be started by DMA chanel 0)

    #Setting up the "control" DMA channel 0 - to run the Channel 1 - Vertical Visible Area lines 
    TREQ_SEL = 0x3f # Max speed, however synchronization is achieved via the PIO irq 1
    INCR_READ = 0   # No increment while reading
    CHAIN_TO = 0    # chain to itself (no chaining)
    EN = 1          # Start channel upon setting the trigger register
    DMA_control_word = ((IRQ_QUIET << 21) | (TREQ_SEL << 15) | (CHAIN_TO  << 11) | (RING_SEL << 10) |
                        (RING_SIZE << 9) | (INCR_WRITE << 5) | (INCR_READ << 4) | (DATA_SIZE << 2) |
                        (HIGH_PRIORITY << 1) | (EN << 0))
    ptr32(0x50000000)[0] = uint(H_buffer_line_add)       # DMA Channel 0 Read Address pointer <- data array to reconfigure DMA1
    ptr32(0x50000004)[0] = uint(0x5000007c)              # DMA Channel 0 Write Address pointer -> DMA1 read_adress alias register 3 (CH1_AL3_READ_ADDR_TRIG ) - trigger the DMA1 start
    ptr32(0x50000008)[0] = 1                             # DMA Channel 0 Transfer Count <- Just one data (long) array to transfer continuously
    ptr32(0x50000010)[0] = DMA_control_word              # DMA Channel 0 Control and Status (using alias to not start immediatly - will be started by DMA trigger register)

@micropython.viper
def startsync():
    V=int(ptr16(V_res))
    H=int(ptr16(H_res))
    paral_write_Hsync.put(655)       # H Visible areas + H Front porch loop
    paral_write_Vsync.put(int(V-1))  # V Visible area
    paral_write_RGB.put(int(H-1))    # RGB loop
    ptr32(0x50000430)[0] |= 0b00001  #triggers DMA chan0
    ptr32(0x50200000)[0] |= 0b111    # Enable PIO0 SM 0, 1, and 2

    
#     
@micropython.viper
def stopsync():
    ptr32(0x50000444)[0] |= 0b000011         # Aborts DMA chan0 and 1
    ptr32(0x50200000)[0] &= 0b111111111000   # Disable PIO0 SM 0, 1 and2
    

@micropython.viper
def draw_pix(x:int,y:int,col:int):
    Data=ptr32(H_buffer_line)
    n=int((y)*(int(H_res)*int(bit_per_pix))+ (x)*int(bit_per_pix))
    k=(n//int(usable_bits)-1) if (n//int(usable_bits)>0)  else (int(len(H_buffer_line))-1)
    p=n%int(usable_bits)
    mask= ((int(pixel_bitmask) << p)^0x3FFFFFFF)
    Data[k]=(Data[k] & mask) | (col << p)

@micropython.viper
def fill_screen(col:int):
    Data=ptr32(H_buffer_line)
    mask=0
    for i in range(0,int(pix_per_words)):
        mask|=col<<(int(bit_per_pix)*i)
    i=0
    while i < int(len(H_buffer_line)):
        Data[i]=mask
        i+=1
    

@micropython.viper
def draw_fastHline(x1:int,x2:int,y:int,col:int):
    if (x1<0):x1=0
    if (x1>(int(H_res)-1)):x1=(int(H_res)-1)
    if (x2<0):x2=0
    if (x2>(int(H_res)-1)):x2=(int(H_res)-1)
    if (y<0):y=0
    if (y>(int(V_res)-1)):y=(int(V_res)-1)
    if (x2<x1):
        temp = x1
        x1 = x2
        x2 = temp
    Data=ptr32(H_buffer_line)
    n1=int((y)*(int(H_res)*int(bit_per_pix))+ (x1)*int(bit_per_pix))
    n2=int((y)*(int(H_res)*int(bit_per_pix))+ (x2)*int(bit_per_pix))
    k1=(n1//int(usable_bits)-1) if (n1//int(usable_bits)>0)  else (int(len(H_buffer_line))-1)
    k2=(n2//int(usable_bits)-1) if (n2//int(usable_bits)>0)  else (int(len(H_buffer_line))-1)
    if (k2==k1):
        for i in range(x1,x2):
            draw_pix(i,y,col)
        return
    p1=n1%int(usable_bits)
    p2=n2%int(usable_bits)
    mask1off=0
    mask1col=0
    mask2off=0
    mask2col=0
    for i in range(p1//int(bit_per_pix),int(pix_per_words)):
        mask1off|=(int(pixel_bitmask))<<(int(bit_per_pix)*i)
        mask1col|=col<<(int(bit_per_pix)*i)
    mask1off^=int(0x3FFFFFFF)
    for i in range(0,p2//int(bit_per_pix)):
        mask2off|=(int(pixel_bitmask))<<(int(bit_per_pix)*i)
        mask2col|=col<<(int(bit_per_pix)*i)
    mask2off^=0x3FFFFFFF
    Data[k1]=(Data[k1] & mask1off) | mask1col
    Data[k2]=(Data[k2] & mask2off) | mask2col
    mask=0
    for i in range(0,int(pix_per_words)):
        mask|=col<<(int(bit_per_pix)*i)
    i=k1+1
    if (i>(int(len(H_buffer_line))-1)):i=0
    while i < k2:
        Data[i]=mask
        i+=1
    
@micropython.viper
def draw_fastVline(x:int,y1:int,y2:int,col:int):
    if (x<0):x=0
    if (x>(int(H_res)-1)):x=(int(H_res)-1)
    if (y1<0):y1=0
    if (y1>(int(V_res)-1)):y1=(int(V_res)-1)
    if (y2<0):y2=0
    if (y2>(int(V_res)-1)):y2=(int(V_res)-1)
    if (y2<y1):
        temp = y1
        y1 = y2
        y2 = temp
    Data=ptr32(H_buffer_line)
    n1=int((y1)*(int(H_res)*int(bit_per_pix))+ (x)*int(bit_per_pix))
    k1=(n1//int(usable_bits)-1) if (n1//int(usable_bits)>0)  else (int(len(H_buffer_line))-1)
    p1=n1%int(usable_bits)
    nword=(int(len(H_buffer_line))//int(V_res))
    mask= ((int(pixel_bitmask) << p1)^0x3FFFFFFF)
    for i in range(y2-y1):
        Data[k1+i*nword]=(Data[k1+i*nword] & mask) | (col << p1)

def draw_line(x1,y1,x2,y2,col):
    if (x1<0):x1=-1
    if (x1>(int(H_res)-1)):x1=(int(H_res))
    if (x2<0):x2=-1
    if (x2>(int(H_res)-1)):x2=(int(H_res))
    if (y1<0):y1=-1
    if (y1>(int(V_res)-1)):y1=(int(V_res))
    if (y2<0):y2=-1
    if (y2>(int(V_res)-1)):y2=(int(V_res))
    if (x2==x1):
        a=0
    else:
        a=(y2-y1)/(x2-x1)
    b=y1-a*x1
    x=x1
    while (x<=x2):
        draw_pix(x,int(x*a+b),col)
        x+=1
        
@micropython.viper
def fill_rect(x1:int,y1:int,x2:int,y2:int,col:int):
    j=int(min(y1,y2))
    while (j<int(max(y1,y2))):
        draw_fastHline(x1,x2,j,col)
        j+=1

@micropython.viper
def draw_rect(x1:int,y1:int,x2:int,y2:int,col:int):
    draw_fastHline(x1,x2,y1,col)
    draw_fastHline(x1,x2,y2,col)
    draw_fastVline(x1,y1,y2,col)
    draw_fastVline(x2,y1,y2,col)

@micropython.viper
def draw_circle(x:int, y:int, r:int , color:int):
    if (x < 0 or y < 0 or x >= int(H_res) or y >= int(V_res)):
        return
    # Bresenham algorithm
    x_pos = 0-r
    y_pos = 0
    err = 2 - 2 * r
    while 1:
        draw_pix(x-x_pos, y+y_pos,color)
        draw_pix(x-x_pos, y-y_pos,color)
        draw_pix(x+x_pos, y+y_pos,color)
        draw_pix(x+x_pos, y-y_pos,color)
        e2 = err
        if (e2 <= y_pos):
            y_pos += 1
            err += y_pos * 2 + 1
            if((0-x_pos) == y_pos and e2 <= x_pos):
                e2 = 0
        if (e2 > x_pos):
            x_pos += 1
            err += x_pos * 2 + 1
        if x_pos > 0:
            break

@micropython.viper
def fill_disk(x:int, y:int, r:int , color:int):
    if (x < 0 or y < 0 or x >= int(H_res) or y >= int(V_res)):
        return
    # Bresenham algorithm
    x_pos = 0-r
    y_pos = 0
    err = 2 - 2 * r
    while 1:
        draw_fastHline(x-x_pos,x+x_pos,y+y_pos,color)
        draw_fastHline(x-x_pos,x+x_pos,y-y_pos,color)
        e2 = err
        if (e2 <= y_pos):
            y_pos += 1
            err += y_pos * 2 + 1
            if((0-x_pos) == y_pos and e2 <= x_pos):
                e2 = 0
        if (e2 > x_pos):
            x_pos += 1
            err += x_pos * 2 + 1
        if x_pos > 0:
            break

def setfont(i):
    global Glyphs,fontbitmaps,Char_height,Char_width,Line_Spacing
    if i==1:
        import FreeMono9pt7b
        #FreeMono9pt7bBitmaps
        fontbitmaps= FreeMono9pt7b.FreeMono9pt7bBitmaps
        Glyphs = FreeMono9pt7b.FreeMono9pt7bGlyphs
        Char_height=13
        Char_width=13
        Line_Spacing=2
    elif i==2:
        #FreeSansSerif7pt7b
        import FreeSansSerif7pt7b
        fontbitmaps= FreeSansSerif7pt7b.FreeSansSerif7pt7bBitmaps
        Glyphs = FreeSansSerif7pt7b.FreeSansSerif7pt7bGlyphs
        Char_height=8
        Char_width=7
        Line_Spacing=2
    elif i==3:
        #FreeMono12pt7b
        import FreeMono12pt7b
        fontbitmaps= FreeMono12pt7b.FreeMono12pt7bBitmaps
        Glyphs = FreeMono12pt7b.FreeMono12pt7bGlyphs
        Char_height=19
        Char_width=19
        Line_Spacing=2
        
def settextcursor(x,y):
    global x_cursor,y_cursor
    x_cursor = x
    y_cursor = y

def settextcolor(color):
    global text_color
    text_color = color

def printh(mess):
    global x_cursor,y_cursor
    for i in mess:
        if i=="\n":
            x_cursor=0
            y_cursor = y_cursor+Char_height+Line_Spacing
        else:
            drawchar(i)
            if x_cursor>(H_res-1):
                x_cursor=0
                y_cursor = y_cursor+Char_height+Line_Spacing


def drawchar(text):
    global x_cursor
    Glyph=Glyphs[ord(text)-0x20]
    index = Glyph[0]
    W = Glyph[1]
    H = Glyph[2]
    xAdv = Glyph[3]
    dX = Glyph[4]
    dY = Glyph[5]
    n_bytes=int(W*H/8)+1
    byte_list=fontbitmaps[index:(index+n_bytes)]
    pos=1
    x=x_cursor+dX
    y=y_cursor+dY
    for a in byte_list:
        for i in range(7,-1,-1):
            if (y-y_cursor-dY)==H:
                break
            if (a & (1<<i)) :
                draw_pix(x,y,text_color)
            pos+=1
            #print("pos=",pos,"\tx=",x,"\ty=",y)        
            x+=1
            if (pos>W):           
                x=x_cursor+dX
                y+=1
                pos=1
    x_cursor+=xAdv

# Builfing the Data array buffer
collect()
a0=mem_free()
# Initiate the buffer - an array of consecutive 32bit words containing ALL the visible pixels
H_buffer_line = array('L')
# Number of required 32bit words
visible_pix=int((H_res)*V_res*bit_per_pix/usable_bits)
# Creating an array with all the 32b words set to zero
for k in range(visible_pix):
    H_buffer_line.append(0)
# We need an array containing the adress of the buffer for the DMA chan0 to read the values
H_buffer_line_address=array('L',[addressof(H_buffer_line)])
# a few information on what we just built
a1=mem_free()
print("mem used by buffer array (kB):\t"+str(round((a0-a1)/1024,3)))
print("Number of 32b words:\t\t"+str(visible_pix))
print("Number of bits (total):\t\t"+str(32*visible_pix))
print("Number of bits (usable):\t"+str(usable_bits*visible_pix))
collect()
a0=mem_free()
print("\nremaining RAM (kB):\t"+str(round(a0/1024,3)))


# 3 bit color names
RED     = 0b001
GREEN   = 0b010
BLUE    = 0b100
YELLOW  = 0b011
BLACK   = 0
WHITE   = 0b111
CYAN    = 0b110
MAGENTA = 0b101

# Configure the DMAs
configure_DMAs(len(H_buffer_line),H_buffer_line_address)
# Start the PIO Statemchines and the DMA Channels
startsync()

# Drawing a simple 8 color checker
# for h in range(8):
#     for i in range(0,60):
#         for k in range(8):
#             col=(h+k)%8
#             draw_fastHline(k*80,k*80+80,h*60+i,col)


# Drawing Various figures
# fill_rect(20,20,150,150,BLACK)
# fill_rect(20,200,150,400,BLUE)
# fill_rect(200,205,205,150,WHITE)
# fill_rect(300,415,350,300,YELLOW)
# fill_rect(550,450,640,150,CYAN)
# draw_circle(100,400,75,YELLOW)
# draw_circle(150,150,98,CYAN)
# fill_disk(320,240,150,BLACK)
# fill_disk(320,240,120,RED)
# fill_disk(320,240,80,GREEN)
# fill_disk(320,240,50,WHITE)
# draw_rect(500,50,620,70,BLACK)
# draw_rect(100,390,600,480,RED)
# fill_rect(400,120,640,360,WHITE)

# setfont(1)
# settextcursor(402,140)
# settextcolor(GREEN)
# printh("Testing font n1")

def plot_graph(valmax,resol,backcol,colgraph1,colgraph2,colgraph3,colgraph4,colaxes,k, offset,resolpol,polcol):
    x=-1*valmax
    fill_screen(backcol)
    setfont(2)
    settextcursor(10,20)
    settextcolor(colgraph1)
    printh("y = x.cos(x)")
    settextcursor(10,40)
    settextcolor(colgraph2)
    printh("y = x.sin(x)")
    settextcursor(10,60)
    settextcolor(polcol)
    printh("r = sin("+str(k)+".theta) + "+str(offset))
    settextcursor(10,80)
    settextcolor(colgraph3)
    printh("y = 1/x^3-1/x^2-1/x+1")
    settextcursor(10,100)
    settextcolor(colgraph4)
    printh("y = 2x.ln(3/x)")

    draw_fastHline(0,640,240,colaxes)
    draw_fastVline(320,0,480,colaxes)

    scale_factor=abs(320/x)
    for i in range(20,640,40):
        draw_fastVline(i,240,245,colaxes)
        settextcursor(i-10,255)
        settextcolor(colaxes)
        printh(str(round((i-320)/scale_factor,2)))
    for i in range(20,480,40):
        draw_fastHline(315,320,i,colaxes)
        settextcursor(290,i+5)
        settextcolor(colaxes)
        printh(str(round((240-i)/scale_factor,2)))

    xc0,yc0 = (int(scale_factor*x),int(scale_factor*x*cos(x)))
    xs0,ys0 = (int(scale_factor*x),int(scale_factor*x*sin(x)))
    
    print("Scale:\t"+str(-1*valmax)+" "+str(valmax)+"\tScale factor:\t"+str(scale_factor)+"\tIncrement:\t"+str(1/scale_factor))

    while x<valmax:
        x+=(1/scale_factor/resol)
        xc1,yc1 = (int(scale_factor*x),int(scale_factor*x*cos(x)))
        xs1,ys1 = (int(scale_factor*x),int(scale_factor*x*sin(x)))
        draw_line(320+xc0,240-yc0,320+xc1,240-yc1,colgraph1)
        draw_line(320+xs0,240-ys0,320+xs1,240-ys1,colgraph2)
        xc0,yc0 = xc1,yc1
        xs0,ys0 = xs1,ys1
 
    x=-1*valmax
    xp0,yp0 = (int(scale_factor*x),int(scale_factor*((1/x/x/x)-(1/x/x)-(1/x)+1)))
    while x<valmax:
        x+=(1/scale_factor/resol)
        xp1,yp1 = (int(scale_factor*x),int(scale_factor*((1/x/x/x)-(1/x/x)-(1/x)+1)))
        draw_line(320+xp0,240-yp0,320+xp1,240-yp1,colgraph3)
        xp0,yp0 = xp1,yp1

    x=0.0001
    xl0,yl0 = (int(scale_factor*x),int(scale_factor*(2*x*log(3/x))))
    while x<valmax:
        x+=(1/scale_factor/resol)
        xp1,yp1 = (int(scale_factor*x),int(scale_factor*(2*x*log(3/x))))
        draw_line(320+xp0,240-yp0,320+xp1,240-yp1,colgraph4)
        xp0,yp0 = xp1,yp1

    theta=0
    r=sin(k*theta)+2
    x0,y0=int(scale_factor*r*cos(theta)),int(scale_factor*r*sin(theta))
    while theta<=2*pi:
        theta+=0.005/resolpol
        r=sin(k*theta)+offset
        x1,y1=int(scale_factor*r*cos(theta)),int(scale_factor*r*sin(theta))
        draw_line(320+x0,240-y0,320+x1,240-y1,polcol)
        x0,y0=x1,y1
    collect()
    print("remaining RAM:\t"+str(mem_free()))
    
plot_graph(9.6,10,BLACK,CYAN,RED,GREEN,YELLOW,WHITE,5,2,2,MAGENTA)

#plot_graph(5,5,BLACK,CYAN,RED,GREEN,YELLOW,WHITE,5,1,2,MAGENTA)
    

# setfont(3)
# settextcursor(402,180)
# settextcolor(RED)
# printh("Testing font n3")