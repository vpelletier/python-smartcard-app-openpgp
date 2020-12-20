Advanced use of openpgp smartcard.

PW1 is not a fixed vale, but a random PIN, presented on an e-Paper display among
other PINs, in a grid. The secret is to know which cell of this grid contains
the correct PIN (by default: A1).

This can be changed by requesting a PW1 change, and formatting it to reference
the desired cell. For example: `C30000` references cell `C3`, `2b0000`
references cell `B2`. Trailing zeroes are ignored.

The grid changes periodically, as the card is used (at most once every
30 seconds), and the last 2 PINs in the correct cell are accepted (the one
currently displayed when the "verify" command runs on the card, and the one
before that).

Key Derivation Function is not available.

Requirements
------------

- A Raspberry Pi zero (or zero W)
- A `WaveShare 2.13 inches e-Paper display`_ V1, with the required pins
  enabled: spi0, GPIO 17, 24 and 25 available (connector pins 11, 18, 22)
- python3 pakages `usb-f-ccid`_ and `smartcard-app-openpgp`_ (and their
  dependencies)

Limitations
-----------

The Raspberry Pi Zero has the USB Vbus pins bridged to the 5v power rail, which
prevents the UDC from detecting bus disconnection. As a result, the display does
not change when the Pi is disconnected from the host, and refreshes twice when
reconnected. There is no workaround known so far.

Usage
-----

.. code:: shell

  $ sudo openpgp-randompin-epaper-raspi.py --user "$USER" --filestorage "$PWD/my_test_card.fs"

Notes for Debian
----------------

Tested on the unofficial (but excellent) `raspi Debian port`_ . You may need to
dist-upgrade to sid to use this.

Sadly, the Debian kernel (as of this writing: 5.9-4) does not seem to support
DeviceTree overlays, so there is some extra work needed:

- fetch the kernel source for your current version (hint: apt-get source
  linux-image-...)
- apply the following trivial patch to the DeviceTree compiler so it includes
  symbols in the generated binary:

  .. code:: diff

    --- a/scripts/Makefile.lib 2020-12-20 00:46:45.488813401 +0000
    +++ b/scripts/Makefile.lib 2020-12-20 00:47:21.808699913 +0000
    @@ -318,6 +318,7 @@
     quiet_cmd_dtc = DTC     $@
     cmd_dtc = $(HOSTCC) -E $(dtc_cpp_flags) -x assembler-with-cpp -o $(dtc-tmp) $< ; \
     	$(DTC) -O $(patsubst .%,%,$(suffix $@)) -o $@ -b 0 \
    +		-@ \
     		$(addprefix -i,$(dir $<) $(DTC_INCLUDE)) $(DTC_FLAGS) \
     		-d $(depfile).dtc.tmp $(dtc-tmp) ; \
     	cat $(depfile).pre.tmp $(depfile).dtc.tmp > $(depfile)

- build the correct DeviceTree binary file for your model (here, the zero-w).
  This can be done on another machine, hence the `ARCH` variable:

  .. code:: shell

    ARCH=arm make bcm2835-rpi-zero-w.dtb

- build the provided overlay (using kernel-provided dtc command, you may also
  install it from the `device-tree-compiler` package):

  .. code:: shell

    $(KERNEL_SOURCE)/scripts/dtc/dtc -I dts -O dtb -@ -o vanilla-enable-spi0.dtbo vanilla-enable-spi0.dts

- (optional) check that the overlay is consistent with kernel's dtb using
  fdtoverlay from the `device-tree-compiler` package:

  .. code:: shell

    fdtoverlay -i $(KERNEL_SOURCE)/arch/arm/boot/dts/bcm2835-rpi-zero-w.dtb -o /dev/null vanilla-enable-spi0.dtbo

  If this emits any error, then you pi may not boot with this overlay.

- install the with-symbols devicetree and the spi overlay (as root):

  .. code:: shell

    cp $(KERNEL_SOURCE)/arch/arm/boot/dts/bcm2835-rpi-zero-w.dtb /boot/firmware/bcm2835-rpi-zero-w_with-symbols.dtb
    mkdir -p /boot/firmware/overlays/
    cp vanilla-enable-spi0.dtbo /boot/firmware/overlays/

- tell the raspberry pi stage 2 bootloader about both files, by editing
  ``/boot/firmware/config.txt``::

    device_tree=bcm2835-rpi-zero-w_with-symbols.dtb
    dtoverlay=vanilla-enable-spi0.dtbo

.. _usb-f-ccid: https://github.com/vpelletier/python-usb-f-ccid
.. _smartcard-app-openpgp: https://github.com/vpelletier/python-smartcard-app-openpgp
.. _WaveShare 2.13 inches e-Paper display: https://www.waveshare.com/wiki/2.13inch_e-Paper_HAT
.. _raspi Debian port: https://raspi.debian.net/
