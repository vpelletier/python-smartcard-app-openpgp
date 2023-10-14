OpenPGP smartcard application implementation.

It implements parts of the OpenPGP specification 3.4.1 .

Warning
-------

**THIS IS A WORK IN PROGRESS.**

- it may not be fully functional
- future upgrades may bring changes incompatible with previous version's stored
  data
- despite best attention, it may contain security holes:

  - it may allow access to unexpected pieces of data
  - cryptographic functions may contain bugs making decryption either
    impossible to one in posession of the decryption key, or trivial to one not
    in posession of the decryption key.

- it may support weak cryptographic algorithms (weak hashes, weak elliptic
  curves, ...)

Fee free to play with it, review it and contribute. But **DO NOT USE IT ON
SENSIBLE OR VALUABLE DATA**, and **DO NOT IMPORT VALUABLE KEYS IN IT**.

This code is in dire need of reviews and tests.

Installation
------------

No extra hardware requirements
++++++++++++++++++++++++++++++

To get a standard card, with an executable setting up a gadget.

.. code:: shell

  pip install smartcard-app-openpgp[ccid]

Then, you may set it up to automatically start on boot (assuming ``pip`` comes
fom a virtualenv at ``/opt/smartcard-openpgp``):

- create a systemd service:

  .. code:: ini

    [Unit]
    Description=Behave like a CCID + smartcard combo USB device

    [Service]
    ExecStart=/opt/smartcard-openpgp/bin/smartcard-openpgp-simple \
      --user smartcard-openpgp \
      --filestorage /srv/smartcard-openpgp/card.fs \
      --serial "%m"
    KillMode=mixed

    [Install]
    WantedBy=usb-gadget.target

- create a system user, enable the systemd service, and start it:

  .. code:: shell

    adduser --system --home /srv/smartcard-openpgp smartcard-openpgp
    chmod go= /srv/smartcard-openpgp
    systemctl enable smartcard-gadget.service
    systemctl start smartcard-gadget.service

USB-device-capable Raspberry Pi with IL3895/SSD1780-based ePaper displays
+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

Tested on the `raspi Debian port`_ .

Expected screen resolution: 250x122, such as the `WaveShare 2.13 inches e-Paper
display V1`_, or the black-and-white `Pimoroni Inky pHAT`_.

This extra hardware enables the use of a random PW1 PIN.

The e-Paper display presents a grid of random values. One cell (A1 by default)
contains the valid PIN.

The cell containing the valid PIN can be changed by requesting a PW1 change, and
providing a specially-formatted new password.
For example: ``C30000`` references cell ``C3``, ``2b0000`` references cell
``B2``. Trailing zeroes are ignored, but may be necessary to get the host's
software to actually send the pin to the device.

The grid changes periodically, when the card is accessed but at most once every
30 seconds or when a ``verify`` command is run (whichever comes first), and both
the currently-displayed (at the time the "verify" command runs ont the card) and
the previous PIN are accepted as a correct PIN.

Key Derivation Function (KDF-DO) is not available in this mode.

usage is similar to the `No extra hardware requirements`_ variant, but starts
with:

.. code:: shell

  pip install smartcard-app-openpgp[ccid,randpin]

The executable is then called ``smartcard-openpgp-randpin-epaper`` rather than
``smartcard-openpgp-simple``.

Screen layout
*************

|smartcard-openpgp-randpin-epaper screenshot|

(support for data URLs is needed to see an image here - unfortunately github and
pypi seem hostile to data URLs)

- Top left corner: number of PW1 tries left. ○ are for tries left, ⨯ for tries
  used. Here, there are 2 tries left out of 3.

- Left and top borders, white text on black background: row and column titles.

- Main area: PIN grid. If this card uses the default pin cell, PW1 is
  ``291413``.

- Top right corner (not present here): Battery level (from ■■■■ for a >90%
  charged battery to □□□□ for a <10% discharged battery). A lightning bolt logo
  is superimposed to the battery level when the battery is being charged.

External requirements
*********************

Beyond the installation/build requirements, the code expects the Noto Mono
font to be located at ``/usr/share/fonts/truetype/noto/NotoMono-Regular.ttf``:

  .. code:: shell

    apt-get install fonts-noto-mono

Limitations
***********

The Raspberry Pi Zero has the USB Vbus pins bridged to the 5v power rail, which
prevents the UDC from detecting bus disconnection. As a result, the display does
not change when the Pi is disconnected from the host, and refreshes twice when
reconnected.

Getting access to the screen
****************************

To configure the 40-pins connector correctly, you need to apply the following
devicetree overlay::

    // Enable SPI0 interface (board pins 19, 21, 23) and its chip-enable lines
    //   (board pins 24, 26)
    // setup GPIO 25 as output (data/command, board pin 22)
    // setup GPIO 17 as output (rst, board pin 11)
    // setup GPIO 24 as input (busy, board pin 18)
    /dts-v1/;
    /plugin/;

    &gpio { // @7e200000
        #gpio-cells = <2>;
        alt0 {
            brcm,pins = <4 5>; // removed 7, 8, 9, 10, 11
        };
        spi0_cs_pins: spi0_cs_pins {
            brcm,function = <1>; // out
            brcm,pins = <7 8>;
        };
        spi0_pins: spi0_pins {
            brcm,function = <4>; // alt0
            brcm,pins = <9 10 11>;
        };
        epaper_pins {
            brcm,function = <1 0 1>; // out in out
            brcm,pins = <17 24 25>;
            brcm,pull = <0 2 0>; // none pull-up none
        };
    };

    &spi { // @7e204000
        #address-cells = <1>;
        #size-cells = <0>;
        cs-gpios = <&gpio 8 0x01>, <&gpio 7 0x01>; // CE0 is gpio 8, CE1 is gpio 7, both active low
        status = "okay";
        pinctrl-0 = <&spi0_cs_pins &spi0_pins>;
        pinctrl-names = "default";
        spidev@0 {
            #address-cells = <1>;
            #size-cells = <0>;
            // "waveshare,epaper-display-v1": because that's what it really is.
            // "rohm,dh2228fv": hack to get a spidev to this device.
            compatible = "waveshare,epaper-display-v1", "rohm,dh2228fv";
            reg = <0>; // uses CS0
            spi-max-frequency = <4000000>; // 4MHz: tcycle >= 250ns
        };
    };

- Compile it with the ``dtc`` command, which may be available from the
  ``device-tree-compiler`` package:

  .. code:: shell

    ${KERNEL_SOURCE}/scripts/dtc/dtc -I dts -O dtb -o epaper2.13in.dtbo epaper2.13in.dts

- (optional) check that the overlay is consistent with kernel's dtb using
  fdtoverlay from the ``device-tree-compiler`` package:

  .. code:: shell

    fdtoverlay -i /boot/firmware/bcm2835-rpi-zero-w.dtb -o /dev/null epaper2.13in.dtbo

  If this emits any error, then you pi may not boot with this overlay. If this
  happens, plug the micro-sd card on a computer and comment-out the correspondig
  ``dtoverlay`` line in ``config.txt``.

- install the devicetree overlay (as root):

  .. code:: shell

    mkdir -p /boot/firmware/overlays/
    cp epaper2.13in.dtbo /boot/firmware/overlays/

- tell the raspberry pi stage 2 bootloader about both files, by adding to
  ``/etc/default/raspi-firmware-custom``::

    dtoverlay=epaper2.13in.dtbo

Battery (UPS-Lite)
++++++++++++++++++

Tested on the `raspi Debian port`_ .

If you have a screen, then there is also optional support for a `UPS-Lite`_
battery.

Getting access to the battery
*****************************

To configure the 40-pins connector correctly, you need to apply the following
devicetree overlay::

    // setup i2c1 dev 0x36 for use with max17040 kernel driver
    // setup GPIO 4 as input (power source detect, board pin 7)
    /dts-v1/;
    /plugin/;

    &gpio { // @7e200000
        #gpio-cells = <2>;
        alt0 {
            brcm,pins = <5>; // removed 4, 7, 8, 9, 10, 11
        };
        external_power {
            brcm,function = <0>; // in
            brcm,pins = <4>;
            brcm,pull = <0>; // no bias
        };
    };

    &i2c1 { // @7e804000
        #address-cells = <1>;
        #size-cells = <0>;
        battery@36 {
            compatible = "maxim,max17040";
            reg = <0x36>;
        };
    };

- Compile it with the ``dtc`` command, which may be available from the
  ``device-tree-compiler`` package:

  .. code:: shell

    ${KERNEL_SOURCE}/scripts/dtc/dtc -I dts -O dtb -o zero_ups_lite.dtbo zero_ups_lite.dts

- (optional) check that the overlay is consistent with kernel's dtb using
  fdtoverlay from the ``device-tree-compiler`` package:

  .. code:: shell

    fdtoverlay -i /boot/firmware/bcm2835-rpi-zero-w.dtb -o /dev/null zero_ups_lite.dtbo

  If this emits any error, then you pi may not boot with this overlay. If this
  happens, plug the micro-sd card on a computer and comment-out the correspondig
  ``dtoverlay`` line in ``config.txt``.

- install the devicetree overlay (as root):

  .. code:: shell

    mkdir -p /boot/firmware/overlays/
    cp zero_ups_lite.dtbo /boot/firmware/overlays/

- tell the raspberry pi stage 2 bootloader about both files, by adding to
  ``/etc/default/raspi-firmware-custom``::

    dtoverlay=zero_ups_lite.dtbo

- check that you have the driver for the ``max17040_battery``:

  .. code:: shell

    grep CONFIG_BATTERY_MAX17040 "/boot/config-$(uname -r)"

  If you do not have this module, you can build it off-tree with ``dkms`` and a
  recent copy of the kernel source:

  .. code:: shell

    mkdir /usr/src/max17040-0.1/
    echo 'obj-m := max17040_battery.o' > /usr/src/max17040-0.1/Makefile
    cat > /usr/src/max17040-0.1/dkms.conf <<EOF
    PACKAGE_NAME="max17040"
    PACKAGE_VERSION="0.1"
    BUILT_MODULE_NAME[0]="max17040_battery"
    MAKE[0]="make -C ${kernel_source_dir} M=${dkms_tree}/${PACKAGE_NAME}/${PACKAGE_VERSION}/build"
    CLEAN="make -C ${kernel_source_dir} M=${dkms_tree}/${PACKAGE_NAME}/${PACKAGE_VERSION}/build clean"
    DEST_MODULE_LOCATION[0]="/kernel/drivers/power/supply"
    REMAKE_INITRD=no
    AUTOINSTALL=yes
    EOF
    cp "${KERNEL_SOURCE}/drivers/power/supply/max17040_battery.c" /usr/src/max17040-0.1/
    dkms install max17040/0.1

For use as a module
+++++++++++++++++++

Without optional dependencies (to use as a python module in your own projects,
for example to assemble more complex gadgets).

.. code:: shell

  pip install smartcard-app-openpgp

Usage
-----

Initial PIN values:

- PW1 (aka user PIN): ``123456``
- PW3 (aka admin PIN): ``12345678``
- Reset Code: (not set)

Initial key format:

- sign, authenticate: RSA2048
- decrypt: X25519

Threat model
------------

In a nutshell:

- the system administrator of the device running this code is considered to be
  benevolent and competent
- the host accessing this device through the smartcard API (typically, via
  USB) is considered hostile
- the close-range physical world surrounding the device is considered to be
  under control of the device owner

In more details:

This code is intended to be used on general-purpose computing modules, unlike
traditional smartcard implementations. They cannot be assumed to have any
hardening against physical access to their persistent (or even volatile)
memory:

- it is trivially easy to pull the micro SD card from a Raspberry Pi Zero {,W}
- it is easy to solder wires on test-points between the CPU and the micros
  card on a Raspberry Pi Zero {,W} and capture traffic
- on an Intel Edison u-boot may be configured with DFU enabled, which, once
  triggered, allows convenient read access to the content of any partition
  it is configured to access
- electronic noise (including actual noise: coil whine) will leak information
  about what the CPU is doing
- they have communication channels dedicated smartcard hardware does not have:
  WiFi, Bluetooth, TTY on serial (possibly via USB), JTAG...

So if an attacker gets physical access to them, their secrets should be
considered fully compromised.

Further, some of these interfaces allow wide-range networking, which further
opens the device to remote attackers.

**The system configuration of the device on which this code runs is outside of
the area of responsibility of this project.**

Just like any general-purpose computer on which you would store PGP/GPG keys.

Origin story
------------

To do my daily job I rely on the same cryptographic operations as any other
sysadmin: ssh key-based authentication, mail signature and decryption. When
faced with the perspective of having to use a machine I do not trust enough
to give it access to the machines my ssh key has access to, nor to give it
access to the private key associated with my email address, I started looking
for alternatives.

So suddenly I needed another computer I trusted to hold those secrets, and go
through it from the machine I was told to use. Which is cumbersome, both in
volume (who wants to carry around two laptops ?) and in usage (one extra hop
for all accesses). All the while potentially leaking some credentials to the
untrusted machine (the credentials I need to present to the trusted machine to
get into my account and unlock my keys).

So I went looking for:

- A widely-compatible private key store protocol (so I do not have to start all
  over again the next time the policy changes).

  A smartcard and a smartcard reader seem a sensible choice: there are
  widespread standards describing their protocol and they have been around for
  long enough in professional settings to have reasonable level of support in
  a lot of operating systems.

- Is easy to carry around.

  In my view, this eliminates card readers with a built-in PIN pad, which means
  the PIN must be input through the keyboard of the untrusted computer, which
  leads me to the next point.

- Which would not rely on nearly-constant credentials, so I can keep the device
  plugged in for extended periods of time without having to worry about the
  untrusted machine using it behind my back.

  Smartcards rely on PINs, which, while they can be changed, I am sure nobody
  change after every single operation, much less from a trusted terminal. So
  once I have input my PIN on the untrusted computer, what's stopping it from
  reusing the PIN for further operations without my consent ?

  So I need some form of TOTP, but smartcards do not have an RTC (...that I
  know of), which means they are not aware of time, so they cannot internally
  produce something which can be both unpredictable to an attacker *and*
  predictable to a TOTP display where the user can tell what the current
  password is. But further than this: I would very much not rely on an RTC at
  all, so be resilient to NTP attacks.

  So I want a device which has a display capable of telling me what the PIN
  I need to use for the next operation is, and change this pin after every
  input. There exist high-end cards with build-in 7-segments displays, some
  even with a tactile pin pad, which leads to the next point.

- Which uses commonly-available hardware.

  I do not want to rely on a specific model, which may or may not remain
  available for the duration of my career.

  Instead, there are now commonly available USB-capable general-purpose
  computers for very affordable prices and with extension capabilities.
  And if a specific model is not available in a few years, then there should
  be another, thank to the maker communities relying on these devices
  (robotics, home automation, ...). I want to use these.

General-purpose devices come with a drawback, of course: they are not
physically hardened (see `Threat model`_). But so would my second laptop, so I
believe this is an improvement overall.

Final refinement: I want some resistance to casual misuse. With large-enough
displays, this is easy: instead of displaying a single random PIN, display an
array of random PINs, of which a single cell contains the correct PIN. The
larger the display and the smaller the font, the better the added security.
But as discussed above, the device should remain small, and this is only aimed
at a casual attacker: anyone motivated and competent enough will find other
ways to access the data.

Implementation principles
-------------------------

- how to manage memory: do not manage memory

  This module is implemented in pure python, to try to achieve a lower
  maintenance burden against buffer overflows that manual memory allocation
  languages are generally more prone to. It does interface (indirectly) with C
  code though, so there is a thin layer at which more care is required.

- how to implement good cryptography: do not implement cryptography

  This module does not implement cryptography itself. It uses the
  `pyca/cryptography`_ module for this, which itself typically relies on
  OpenSSL. Standing on the shoulders of these giants is mandatory.

  There are also places related to security but not related to cryptography
  which needs to be carefully implemented:

  - PIN checking. While this is ultra-low-level cryptography, manipulating PINs
    could leak timing information to the outside world, so it must be (and is)
    carefully done with time-constant functions.
  - random number generation (for GET_CHALLENGE method). The best source of
    system entropy must be used.

Features
--------

Implemented: Supposed to work, may fail nevertheless.

Missing: Known to exist, not implemented (yet ?). Contribute or express
interest.

Unlisted: Not known to exist. Contribute or report existence (with links to
spec, existing implementations, ...).

================== ====================== =======
Category           Implemented            Missing
================== ====================== =======
high level features
-------------------------------------------------
passcodes          PW1, PW3, RC
passcode format    UTF-8, KDF             PIN block format 2
cryptography       RSA: 2048, 3072, 4096  3DES, Elgamal, RSA <=1024, cast5,
                                          idea, blowfish, twofish, camellia,
                   ECDH: SECP256R1,       EDDSA ED25519
                   SECP384R1,
                   SECP512R1,
                   BRAINPOOL256R1,
                   BRAINPOOL384R1,
                   BRAINPOOL512R1,
                   X25519

                   ECDSA: SECP256R1,
                   SECP384R1,
                   SECP512R1,
                   BRAINPOOL256R1,
                   BRAINPOOL384R1,
                   BRAINPOOL512R1
operations         key generation, key    encryption (AES), get challenge,
                   import, signature,     attestation
                   decryption,
                   authentication,
                   key role swapping
hash support       MD5, SHA1, SHA224,     RipeMD160
                   SHA256, SHA384, SHA512
I/O                                       display, biometric, button, keypad,
                                          LED, loudspeaker, microphone,
                                          touchscreen
private DOs        0101, 0102, 0103, 0104
key role selection simple format          extended format
low level features
-------------------------------------------------
serial number      random in unmanaged
                   space
lifecycle          blank-on-terminate
protocol           plain                  Secure Messaging
file selection     full DF, partial DF,   short file identifier
                   path, file identifier,
                   record identifier
================== ====================== =======

.. _WaveShare 2.13 inches e-Paper display V1: https://www.waveshare.com/wiki/2.13inch_e-Paper_HAT
.. _Pimoroni Inky pHAT: https://shop.pimoroni.com/products/inky-phat?variant=12549254938707
.. _UPS-Lite: https://www.tindie.com/products/rachel/ups-lite-for-raspberry-pi-zero/
.. _pyca/cryptography: https://github.com/pyca/cryptography
.. _raspi Debian port: https://raspi.debian.net/
.. |smartcard-openpgp-randpin-epaper screenshot| image:: data:image/jpeg;base64,
    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
    ABtiuAANbhawIYCESEwEAMEA7AARALAMT5A//9k=
