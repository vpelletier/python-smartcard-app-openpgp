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
    impossible or trivial to an attacker

- it may support weak cryptographic algorithms (weak hashes, weak elliptic
  curves, ...)

Fee free to play with it, review it and contribute. But **DO NOT USE IT ON
SENSIBLE OR VALUABLE DATA**, and **DO NOT IMPORT VALUABLE KEYS IN IT**.

This code is in dire need for reviewing and testing.

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
                                          idea, blowfish, twofish, camellia
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

Usage information
-----------------

For end-users:

Initial PIN values:

- PW1 (aka user PIN): `123456`
- PW3 (aka admin PIN): `12345678`
- Reset Code: (not set)

Initial key format:

- sign, authenticate: ED25519
- decrypt: X25519

For developers: see examples/* .
