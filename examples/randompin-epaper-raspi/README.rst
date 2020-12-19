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

.. _usb-f-ccid: https://github.com/vpelletier/python-usb-f-ccid
.. _smartcard-app-openpgp: https://github.com/vpelletier/python-smartcard-app-openpgp
.. _WaveShare 2.13 inches e-Paper display: https://www.waveshare.com/wiki/2.13inch_e-Paper_HAT