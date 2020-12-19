Simple use of openpgp smartcard.

Requirements
------------

- A linux-capable device capable of acting as an USB device, like a Raspberry Pi zero, an Intel Edison...
- python3 pakages `usb-f-ccid`_ and `smartcard-app-openpgp`_ (and their dependencies)

Usage
-----

.. code:: shell

  $ sudo openpgp-simple.py --user "$USER" --filestorage "$PWD/my_test_card.fs"

.. _usb-f-ccid: https://github.com/vpelletier/python-usb-f-ccid
.. _smartcard-app-openpgp: https://github.com/vpelletier/python-smartcard-app-openpgp
