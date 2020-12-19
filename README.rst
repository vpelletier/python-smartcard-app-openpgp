OpenPGP smartcard application implementation.

It implements parts of the OpenPGP specification 3.4.1 .

Warning
-------

**THIS IS A WORK IN PROGRESS.**

- it may not be fully functional
- future upgrades may bring changes incompatible with previous version's stored
  data
- despite best attention, it may contain security holes:

  - it may allow access to unecpected pieces of data
  - cryptographic functions may contain bugs making decryption either
    impossible or trivial to an attacker

- it may support weak cryptographic algorithms (weak hashes, ...)

Fee free to play with it, review it and contribute. But **DO NOT USE IT ON
SENSIBLE OR VALUABLE DATA**, and **DO NOT IMPORT VALUABLE KEYS IN IT**.

This code is in dire need for reviewing and testing.

Features
--------

Implemented: Supposed to work, may fail nevertheless.

Missing: Known to exist, not implemented (yet ?). Contribute or express
interest.

Unlisted: Not known to exist. Contribute or report existence (with links to
spec, existing implementations, ...).

================ ====================== =======
Category         Implemented            Missing
================ ====================== =======
high level features
-----------------------------------------------
passcodes        PW1, PW3, RC
passcode format  UTF-8, KDF             PIN block format 2
cryptography     RSA: 2048, 3072, 4096  3DES, Elgamal, RSA <=1024, cast5, idea,
                                        blowfish, twofish, camellia
                 ECDH: SECP256R1,
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

                 EDDSA: ED25519
operations       key generation, key    encryption (AES), get challenge,
                 import, signature,     attestation
                 decryption,
                 authentication,
                 key role swapping
hash support     MD5, SHA1, SHA224,     RipeMD160
                 SHA256, SHA384, SHA512
I/O                                     display, biometric, button, keypad, LED
                                        loudspeaker, microphone, touchscreen
private DOs      0101, 0102, 0103, 0104
low level features
-----------------------------------------------
serial number    random in unmanaged
                 space
lifecycle        blank-on-terminate
protocol         plain                  Secure Messaging
file selection   full DF, partial DF,   short file identifier
                 path, file identifier,
                 record identifier
role selection   simple format          extended format
================ ====================== =======

Usage information
-----------------

Initial PIN values:

- PW1 (aka user PIN): `123456`
- PW3 (aka admin PIN): `12345678`
- Reset Code: (not set)

Initial key format:

- sign, authenticate: ED25519
- decrypt: X25519