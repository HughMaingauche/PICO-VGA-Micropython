# PICO-VGA-Micropython
A simple 640x480 60Hz VGA display driver to use with Pico and Micropython

Hello All,

I came out with this simple driver, allowing to use a VGA screen with a 640x480 resolution and 3 bits per pixel (8 colors).
My purpose was just to learn how to use PIO programming and synchronization with DMA, but I might as well shere the result of this with the community.

Since the pico does not have a tremendous amount of memory for this kind of usage, I saved space by using 32 bits words (and only using 30 bits out of them).

In the end the buffer for the whole frame takes 120k of RAM, wich leaves the PICO with approx 50k to do some stuff with micropython

Synchonisation between PIO and DMA is achived using PIO Irqs.

My references are :
- This project (for the pico, written in c) very well documented by V. Hunter Adams (vha3@cornell.edu) https://vanhunteradams.com/Pico/VGA/VGA.html#Code-organization
- The explanations provided on this forum by Roberthh and the tools he posted on github https://github.com/robert-hh/RP2040-Examples/tree/master/rp2_util

I added the possibility to overclock the pico to 250MHz, but in the end it does not really impact the picture quality. I coded a few routines to draw rectangles/lines/circle, for testing purpose.

I tried to document the code as much as I could. Since I am still a beginner, I have no doubt that this can be optimized quite a lot.
If any of you is interested in using or improving it, i'll be happy to provide further explanations.
![20220306_174803](https://user-images.githubusercontent.com/47264131/156934327-0852540c-f7ba-4f09-91b1-b13c856d4752.jpg)
