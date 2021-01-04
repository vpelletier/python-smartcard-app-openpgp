# Copyright (C) 2020  Vincent Pelletier <plr.vincent@gmail.com>
#
# This file is part of python-smartcard-app-openpgp.
# python-smartcard-app-openpgp is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# python-smartcard-app-openpgp is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with python-smartcard-app-openpgp.  If not, see <http://www.gnu.org/licenses/>.

import functools
import itertools
import struct
from cryptography.hazmat.primitives.asymmetric.rsa import (
    RSAPrivateNumbers,
    RSAPublicNumbers,
    rsa_crt_iqmp,
    rsa_crt_dmp1,
    rsa_crt_dmq1,
    generate_private_key as generate_private_rsa_key,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.ec import (
    derive_private_key,
    generate_private_key as generate_private_ec_key,
    SECP256R1,
    SECP384R1,
    SECP521R1,
    BrainpoolP256R1,
    BrainpoolP384R1,
    BrainpoolP512R1,
    EllipticCurve,
    EllipticCurveOID,
)
from smartcard.asn1 import (
    CLASS_APPLICATION,
    CLASS_CONTEXT,
    CLASS_PRIVATE,
    CodecBER,
    Integer,
    IntegerBase,
    Null,
    ObjectIdentifier,
    OctetString,
    OctetStringBase,
    Sequence,
    TypeBase,
    TypeListBase,
)
from smartcard.tag import (
    SignatureCounter as GenericSignatureCounter,
)
from smartcard.utils import (
    NamedSingleton,
)

class Private1(OctetString):
    identifier = 0x101

class Private2(OctetString):
    identifier = 0x102

class Private3(OctetString):
    identifier = 0x103

class Private4(OctetString):
    identifier = 0x104

class _ApplicationBase(TypeBase): #pylint: disable=abstract-method
    klass = CLASS_APPLICATION

class _ApplicationSimpleBase(_ApplicationBase): #pylint: disable=abstract-method
    is_composite = False

class _ApplicationCompositeBase(TypeListBase): #pylint: disable=abstract-method
    klass = CLASS_APPLICATION

class _ApplicationOctetStringBase(_ApplicationBase, OctetStringBase):
    klass = CLASS_APPLICATION

class _ContextBase(TypeBase): #pylint: disable=abstract-method
    klass = CLASS_CONTEXT

class _ContextSimpleBase(_ContextBase): #pylint: disable=abstract-method
    is_composite = False

class _ContextCompositeBase(TypeListBase): #pylint: disable=abstract-method
    klass = CLASS_CONTEXT

class _ContextIntegerBase(_ContextBase, IntegerBase):
    klass = CLASS_CONTEXT

class _ContextOctetStringBase(_ContextBase, OctetStringBase):
    klass = CLASS_CONTEXT

class _PrivateBase(TypeBase): #pylint: disable=abstract-method
    klass = CLASS_PRIVATE

class _PrivateSimpleBase(_PrivateBase): #pylint: disable=abstract-method
    is_composite = False

class _PrivateCompositeBase(TypeListBase): #pylint: disable=abstract-method
    klass = CLASS_PRIVATE

class _PrivateOctetStringBase(_PrivateBase, OctetStringBase):
    klass = CLASS_PRIVATE

class _FixedWidthOctetString(_PrivateSimpleBase):
    length = None

    @classmethod
    def encode(cls, value, codec):
        if len(value) != cls.length:
            raise ValueError
        return super().encode(value, codec)

    @classmethod
    def decode(cls, value, codec):
        result = super().decode(value, codec)
        if len(result) != cls.length:
            raise ValueError
        return result

class _FixedWidthOctetStringList(_PrivateSimpleBase):
    item_length = None

    @classmethod
    def encode(cls, value, codec):
        if not all(len(x) == cls.item_length for x in value):
            raise ValueError
        return b''.join(value)

    @classmethod
    def decode(cls, value, codec):
        result = []
        item_length = cls.item_length
        while value:
            item = value[:item_length]
            if len(item) != item_length:
                raise ValueError
            result.append(item)
            value = value[item_length:]
        return result

class _NotImplementedBase: #pylint: disable=abstract-method
    @classmethod
    def encode(cls, value, codec):
        raise NotImplementedError

    @classmethod
    def decode(cls, value, codec):
        raise NotImplementedError

class LoginData(_ApplicationOctetStringBase):
    identifier = 0x1e

class LanguagePreference(_ApplicationOctetStringBase):
    identifier = 0x2d

class SignatureCounter(GenericSignatureCounter):
    __max = 2**24 - 1

    @classmethod
    def encode(cls, value, codec):
        return min(cls.__max, value).to_bytes(3, 'big')

    @classmethod
    def decode(cls, value, codec):
        return int.from_bytes(value, 'big')

class Sex(_ApplicationSimpleBase):
    identifier = 0x35

    UNKNOWN = b'\x30'
    MALE = b'\x31'
    FEMALE = b'\x32'
    NOT_ANNOUNCED = b'\x39'
    __VALUE_SET = (UNKNOWN, MALE, FEMALE, NOT_ANNOUNCED)

    @classmethod
    def encode(cls, value, codec):
        if value not in cls.__VALUE_SET:
            raise ValueError
        return value

    @classmethod
    def decode(cls, value, codec):
        if value not in cls.__VALUE_SET:
            raise ValueError
        return value

class CardholderPrivateKey(_ApplicationOctetStringBase):
    identifier = 0x48

class CardholderCertificate(_NotImplementedBase, _ApplicationCompositeBase): #pylint: disable=abstract-method
    identifier = 0x21

class CardholderPrivateKeyTemplate(_ApplicationBase):
    """
    This type is unusual: it uses Data Object headers to define the length of
    fields whose content is sent in a separate DO (application, elementary,
    0x48). So contained DOs have no content, and their class remain abstract
    (basically just container for their klass, is_composite, identifier, and
    just follow codec rules for head serialisation).
    """
    identifier = 0x48
    is_composite = True

    class PublicExponent(_ContextSimpleBase): #pylint: disable=abstract-method
        identifier = 0x11

    class Prime1(_ContextSimpleBase): #pylint: disable=abstract-method
        identifier = 0x12

    CurvePrivateKey = Prime1

    class Prime2(_ContextSimpleBase): #pylint: disable=abstract-method
        identifier = 0x13

    class PQ(_ContextSimpleBase): #pylint: disable=abstract-method
        identifier = 0x14

    class DP1(_ContextSimpleBase): #pylint: disable=abstract-method
        identifier = 0x15

    class DQ1(_ContextSimpleBase): #pylint: disable=abstract-method
        identifier = 0x16

    class Modulus(_ContextSimpleBase): #pylint: disable=abstract-method
        identifier = 0x17

    class CurvePublicKey(_ContextSimpleBase): #pylint: disable=abstract-method
        identifier = 0x19

    __schema = {
        x.asTagTuple(): x
        for x in (
            PublicExponent,
            Prime1,
            Prime2,
            PQ,
            DP1,
            DQ1,
            Modulus,
            CurvePublicKey,
        )
    }

    @classmethod
    def iterItemSchema(cls):
        return itertools.cycle([cls.__schema])

    @classmethod
    def encode(cls, value, codec):
        public_exponent_len = value['public_exponent_len']
        prime1_len = value['prime1_len']
        prime2_len = value['prime2_len']
        pq_len = value.get('pq_len')
        dp1_len = value.get('dp1_len')
        dq1_len = value.get('dq1_len')
        modulus_len = value.get('modulus_len')
        if 0 in (public_exponent_len, prime1_len, prime2_len):
            raise ValueError
        result = [
            (cls.PublicExponent, public_exponent_len),
            (cls.Prime1, prime1_len),
            (cls.Prime2, prime2_len),
        ]
        if pq_len:
            result.append((cls.PQ, pq_len))
        if dp1_len:
            result.append((cls.DP1, dp1_len))
        if dq1_len:
            result.append((cls.DQ1, dq1_len))
        if modulus_len:
            result.append((cls.Modulus, modulus_len))
        return b''.join(
            codec.encodeTagLength(tag, length)
            for tag, length in result
        )

    @classmethod
    def decode(cls, value, codec):
        return list(codec.iterDecodeTagLength(
            value,
            schema=cls.__schema,
        ))

class CardholderPrivateKeyTemplateExtendedHeader(TypeListBase):
    # Note: no identifier. This is not to be decoded as part of a Data Object,
    # but a helper to decode the payload of an Extended Header List.
    min_length = 1

    @classmethod
    def iterItemSchema(cls):
        return [
            {
                x.asTagTuple(): x
                for x in (
                    ControlReferenceTemplateAuthentication,
                    ControlReferenceTemplateSignature,
                    ControlReferenceTemplateDecryption,
                )
            },
            {
                CardholderPrivateKeyTemplate.asTagTuple(): CardholderPrivateKeyTemplate,
            },
            {
                CardholderPrivateKey.asTagTuple(): CardholderPrivateKey,
            },
        ]

class PublicKeyComponents(_ApplicationCompositeBase):
    identifier = 0x49

    class RSAModulus(_ContextIntegerBase):
        identifier = 0x1

    class RSAPublicExponent(_ContextIntegerBase):
        identifier = 0x2

    class ECPublic(_ContextOctetStringBase):
        identifier = 0x6

    __schema = {
        x.asTagTuple(): x
        for x in (
            RSAModulus,
            RSAPublicExponent,
            ECPublic,
        )
    }

    @classmethod
    def iterItemSchema(cls):
        return itertools.cycle([cls.__schema])

class ExtendedLengthInformation(_ApplicationCompositeBase):
    identifier = 0x66
    min_length = 2
    max_length = 2

    class TwoBytesInteger(Integer):
        @classmethod
        def encode(cls, value, codec):
            return value.to_bytes(2, 'big')

        @classmethod
        def decode(cls, value, codec):
            if len(value) != 2:
                raise ValueError
            return int.from_bytes(value, 'big')

    @classmethod
    def iterItemSchema(cls):
        return [{
            cls.TwoBytesInteger.asTagTuple(): cls.TwoBytesInteger,
        }] * 2

    @classmethod
    def encode(cls, value, codec):
        return super().encode(
            [
                (
                    ExtendedLengthInformation.TwoBytesInteger,
                    value['max_request_length'],
                ),
                (
                    ExtendedLengthInformation.TwoBytesInteger,
                    value['max_response_length'],
                ),
            ],
            codec=codec,
        )

    @classmethod
    def decode(cls, value, codec):
        ( # pylint: disable=unbalanced-tuple-unpacking
            (_, max_request_length),
            (_, max_response_length),
        ) = super().decode(value, codec)
        return {
            'max_request_length': max_request_length,
            'max_response_length': max_response_length,
        }

#TAG_GENERAL_FEATURE_MANAGEMENT          = (CLASS_APPLICATION, BERTLV_ENCODING_BERTLV, 0x74)

class ControlReferenceTemplateBase(_ContextCompositeBase):
    min_length = 0
    max_length = 1

    class ControlReferenceItem(_NotImplementedBase, _ContextSimpleBase): #pylint: disable=abstract-method
        # Probably an integer, maybe the key index in the card ?
        identifier = 0x04

    @classmethod
    def iterItemSchema(cls):
        return [{
            cls.ControlReferenceItem.asTagTuple(): cls.ControlReferenceItem,
        }]

class ControlReferenceTemplateAuthentication(ControlReferenceTemplateBase):
    identifier = 0x04

class ControlReferenceTemplateSignature(ControlReferenceTemplateBase):
    identifier = 0x16

class ControlReferenceTemplateDecryption(ControlReferenceTemplateBase):
    identifier = 0x18

CONTROL_REFERENCE_SCHEMA = {
    x.asTagTuple(): x
    for x in (
        ControlReferenceTemplateAuthentication,
        ControlReferenceTemplateSignature,
        ControlReferenceTemplateDecryption,
    )
}

class ExtendedCapabilities(_PrivateSimpleBase):
    identifier = 0x00

    __SECURE_MESSAGING = 0x80
    __GET_CHALLENGE = 0x40
    __KEY_IMPORT = 0x20
    __MODIFY_PASSWORD_STATUS = 0x10
    __PRIVATE_DATA_OBJECTS = 0x08
    __CHANGE_ALGORITHM_ATTRIBUTES = 0x04
    __AES = 0x02
    __KEY_DERIVED_FUNCTION = 0x01

    SECURE_MESSAGING_ALGORITHM_NONE = NamedSingleton('SECURE_MESSAGING_ALGORITHM_NONE')
    SECURE_MESSAGING_ALGORITHM_AES128 = NamedSingleton('SECURE_MESSAGING_ALGORITHM_AES128')
    SECURE_MESSAGING_ALGORITHM_AES256 = NamedSingleton('SECURE_MESSAGING_ALGORITHM_AES256')
    SECURE_MESSAGING_ALGORITHM_SCP11B = NamedSingleton('SECURE_MESSAGING_ALGORITHM_SCP11B')

    __SECURE_MESSAGING_ALGORIMTH_DICT = {
        SECURE_MESSAGING_ALGORITHM_NONE:   0,
        SECURE_MESSAGING_ALGORITHM_AES128: 1,
        SECURE_MESSAGING_ALGORITHM_AES256: 2,
        SECURE_MESSAGING_ALGORITHM_SCP11B: 3,
    }
    __SECURE_MESSAGING_ALGORIMTH_REVERSE_DICT = {
        value: key
        for key, value in __SECURE_MESSAGING_ALGORIMTH_DICT.items()
    }

    @classmethod
    def encode(cls, value, codec):
        return struct.pack(
            '>BBHHHBB',
            (
                (0 if (
                    value['secure_messaging_algorithm'] is
                    cls.SECURE_MESSAGING_ALGORITHM_NONE
                ) else cls.__SECURE_MESSAGING) |
                (cls.__GET_CHALLENGE if value['challenge_max_length'] else 0) |
                (cls.__KEY_IMPORT if value['has_key_import'] else 0) |
                (cls.__MODIFY_PASSWORD_STATUS if value['has_editable_password_status'] else 0) |
                (cls.__PRIVATE_DATA_OBJECTS if value['has_private_data_objects'] else 0) |
                (cls.__CHANGE_ALGORITHM_ATTRIBUTES if value['has_editable_algorithm_attributes'] else 0) |
                (cls.__AES if value['has_aes'] else 0) |
                (cls.__KEY_DERIVED_FUNCTION if value['has_key_derived_function'] else 0)
            ),
            cls.__SECURE_MESSAGING_ALGORIMTH_DICT[value['secure_messaging_algorithm']],
            value['challenge_max_length'],
            value['certificate_max_length'],
            value['special_data_object_max_length'],
            bool(value['has_pin_block2_format']),
            bool(value['can_swap_auth_dec_key_role']),
        )

    @classmethod
    def decode(cls, value, codec):
        (
            head,
            secure_messaging_algorithm,
            challenge_max_length,
            certificate_max_length,
            special_data_object_max_length,
            has_pin_block2_format,
            can_swap_auth_dec_key_role
        ) = struct.unpack('>BBHHHBB', value)
        return {
            'has_key_import':                    bool(head & cls.__KEY_IMPORT),
            'has_editable_password_status':      bool(head & cls.__MODIFY_PASSWORD_STATUS),
            'has_private_data_objects':          bool(head & cls.__PRIVATE_DATA_OBJECTS),
            'has_editable_algorithm_attributes': bool(head & cls.__CHANGE_ALGORITHM_ATTRIBUTES),
            'has_aes':                           bool(head & cls.__AES),
            'has_key_derived_function':          bool(head & cls.__KEY_DERIVED_FUNCTION),
            'secure_messaging_algorithm':        cls.__SECURE_MESSAGING_ALGORIMTH_REVERSE_DICT(secure_messaging_algorithm),
            'challenge_max_length':              challenge_max_length,
            'certificate_max_length':            certificate_max_length,
            'special_data_object_max_length':    special_data_object_max_length,
            'has_pin_block2_format':             has_pin_block2_format,
            'can_swap_auth_dec_key_role':        can_swap_auth_dec_key_role,
        }

OID_SECP256R1      = ObjectIdentifier.encode(EllipticCurveOID.SECP256R1.dotted_string      , codec=None)
OID_SECP384R1      = ObjectIdentifier.encode(EllipticCurveOID.SECP384R1.dotted_string      , codec=None)
OID_SECP521R1      = ObjectIdentifier.encode(EllipticCurveOID.SECP521R1.dotted_string      , codec=None)
OID_BRAINPOOL256R1 = ObjectIdentifier.encode(EllipticCurveOID.BRAINPOOLP256R1.dotted_string, codec=None)
OID_BRAINPOOL384R1 = ObjectIdentifier.encode(EllipticCurveOID.BRAINPOOLP384R1.dotted_string, codec=None)
OID_BRAINPOOL512R1 = ObjectIdentifier.encode(EllipticCurveOID.BRAINPOOLP512R1.dotted_string, codec=None)
OID_X25519         = ObjectIdentifier.encode('1.3.6.1.4.1.3029.1.5.1'                      , codec=None)
OID_ED25519        = ObjectIdentifier.encode('1.3.6.1.4.1.11591.15.1'                      , codec=None)

class AlgorithmAttributesBase(_PrivateSimpleBase):
    @functools.total_ordering
    class AlgorithmBase:
        @classmethod
        def encode(cls, value):
            raise NotImplementedError

        @classmethod
        def decode(cls, value):
            raise NotImplementedError

        @classmethod
        def getCombinationArgumentDict(cls):
            raise NotImplementedError

        def __init__(self, value):
            self._format_dict = self.decode(value)

        def importKey(self, component_dict):
            raise NotImplementedError

        def newKey(self):
            raise NotImplementedError

        def __eq__(self, other):
            return (
                type(self) == type(other) and
                self._format_dict == other._format_dict
            )

        def __lt__(self, other):
            return NotImplemented

    class RSA(AlgorithmBase):
        ID = 0x01
        IMPORT_FORMAT_STANDARD = NamedSingleton('RSA_IMPORT_FORMAT_STANDARD')
        IMPORT_FORMAT_STANDARD_WITH_MODULUS = NamedSingleton('RSA_IMPORT_FORMAT_STANDARD_WITH_MODULUS')
        IMPORT_FORMAT_CRT = NamedSingleton('RSA_IMPORT_FORMAT_CRT')
        IMPORT_FORMAT_CRT_WITH_MODULUS = NamedSingleton('RSA_IMPORT_FORMAT_CRT_WITH_MODULUS')
        __IMPORT_FORMAT_DICT = {
            IMPORT_FORMAT_STANDARD: 0x00,
            IMPORT_FORMAT_STANDARD_WITH_MODULUS: 0x01,
            IMPORT_FORMAT_CRT: 0x02,
            IMPORT_FORMAT_CRT_WITH_MODULUS: 0x03,
        }
        __IMPORT_FORMAT_REVERSE_DICT = {
            value: key
            for key, value in __IMPORT_FORMAT_DICT.items()
        }

        @classmethod
        def encode(cls, value):
            return struct.pack(
                '>HHB',
                value['modulus_bit_length'],
                value['public_exponent_bit_length'],
                cls.__IMPORT_FORMAT_DICT[value['import_format']],
            )

        @classmethod
        def decode(cls, value):
            (
                modulus_bit_length,
                public_exponent_bit_length,
                import_format,
            ) = struct.unpack(
                '>HHB',
                value,
            )
            return {
                'modulus_bit_length': modulus_bit_length,
                'public_exponent_bit_length': public_exponent_bit_length,
                'import_format': cls.__IMPORT_FORMAT_REVERSE_DICT[import_format],
            }

        @classmethod
        def getCombinationArgumentDict(cls):
            return {
                'modulus_bit_length': (
                    # XXX: how to know which are actually supported ?
                    2048,
                    3072,
                    4096,
                ),
                'public_exponent_bit_length': (
                    # XXX: 0x010001 is actually a 17 bits number, but doc
                    # suggest 32bits... Po2 bit counts only ?
                    32,
                ),
                'import_format': cls.__IMPORT_FORMAT_DICT.keys(),
            }

        @staticmethod
        def __modularInverse(e, m):
            a = 0
            b = m
            u = 1
            while e > 0:
                q, next_e = divmod(b, e)
                b = e
                a, u = u, a - q * u
                e = next_e
            if b == 1:
                return a % m
            raise ValueError('E and M are not coprimes')

        def importKey(self, component_dict):
            e = int.from_bytes(component_dict[CardholderPrivateKeyTemplate.PublicExponent], 'big')
            # XXX: check public exponent length/value ?
            p = int.from_bytes(component_dict[CardholderPrivateKeyTemplate.Prime1], 'big')
            q = int.from_bytes(component_dict[CardholderPrivateKeyTemplate.Prime2], 'big')
            try:
                n = int.from_bytes(component_dict[CardholderPrivateKeyTemplate.Modulus], 'big')
            except KeyError:
                n = p * q
            # modulus is over 10 bits shorter than expected, reject the key.
            # XXX: I think this is gnupg's threshold...
            if n.bit_length() < self._format_dict['modulus_bit_length'] - 10:
                raise ValueError
            d = self.__modularInverse(e, (p - 1) * (q - 1))
            try:
                dmp1 = int.from_bytes(component_dict[CardholderPrivateKeyTemplate.DP1], 'big')
            except KeyError:
                dmp1 = rsa_crt_dmp1(private_exponent=d, p=p)
            try:
                dmq1 = int.from_bytes(component_dict[CardholderPrivateKeyTemplate.DQ1], 'big')
            except KeyError:
                dmq1 = rsa_crt_dmq1(private_exponent=d, q=q)
            try:
                iqmp = int.from_bytes(component_dict[CardholderPrivateKeyTemplate.PQ], 'big')
            except KeyError:
                iqmp = rsa_crt_iqmp(p=p, q=q)
            return RSAPrivateNumbers(
                p=p,
                q=q,
                d=d,
                dmp1=dmp1,
                dmq1=dmq1,
                iqmp=iqmp,
                public_numbers=RSAPublicNumbers(n=n, e=e),
            ).private_key()

        def newKey(self):
            public_exponent = 0x10001
            assert self._format_dict['public_exponent_bit_length'] >= public_exponent.bit_length()
            return generate_private_rsa_key(
                public_exponent=public_exponent,
                key_size=self._format_dict['modulus_bit_length'],
            )

    class ECBase(AlgorithmBase):
        _OID_TO_CURVE_DICT = None

        @classmethod
        def encode(cls, value):
            assert cls._OID_TO_CURVE_DICT
            oid = None
            for oid, oid_algo in cls._OID_TO_CURVE_DICT.items():
                if value['algo'] is oid_algo:
                    break
            if oid is None:
                raise ValueError
            return oid + (
                b'\xff' if value['with_public_key'] else b''
            )

        @classmethod
        def decode(cls, value):
            if value in cls._OID_TO_CURVE_DICT:
                algo = cls._OID_TO_CURVE_DICT[value]
                with_public_key = False
            elif value[-1] == 0xff and value[:-1] in cls._OID_TO_CURVE_DICT:
                algo = cls._OID_TO_CURVE_DICT[value[:-1]]
                with_public_key = True
            else:
                raise ValueError
            return {
                'algo': algo,
                'with_public_key': with_public_key,
            }

        @classmethod
        def getCombinationArgumentDict(cls):
            return {
                'algo': tuple(cls._OID_TO_CURVE_DICT.values()),
                'with_public_key': (False, True),
            }

        def importKey(self, component_dict):
            curve = self._format_dict['algo']
            data = component_dict[
                CardholderPrivateKeyTemplate.CurvePrivateKey
            ]
            if issubclass(curve, EllipticCurve):
                result = derive_private_key(
                    private_value=int.from_bytes(data, 'big'),
                    curve=curve(),
                )
            elif issubclass(
                curve,
                (
                    Ed25519PrivateKey,
                    X25519PrivateKey,
                ),
            ):
                result = curve.from_private_bytes(data=data)
            else:
                raise TypeError(repr(curve))
            return result

        def newKey(self):
            curve = self._format_dict['algo']
            if issubclass(curve, EllipticCurve):
                result = generate_private_ec_key(
                    curve=curve(),
                )
            elif issubclass(
                curve,
                (
                    Ed25519PrivateKey,
                    X25519PrivateKey,
                ),
            ):
                result = curve.generate()
            else:
                raise TypeError(repr(curve))
            return result

    class ECDH(ECBase):
        ID = 0x12
        _OID_TO_CURVE_DICT = {
            OID_SECP256R1     : SECP256R1, # ansix9p256r1
            OID_SECP384R1     : SECP384R1, # ansix9p384r1
            OID_SECP521R1     : SECP521R1, # ansix9p521r1
            OID_BRAINPOOL256R1: BrainpoolP256R1, # brainpoolP256r1
            OID_BRAINPOOL384R1: BrainpoolP384R1, # brainpoolP384r1
            OID_BRAINPOOL512R1: BrainpoolP512R1, # brainpoolP512r1
            OID_X25519        : X25519PrivateKey, # cv25519
        }

    class ECDSA(ECBase):
        ID = 0x13
        _OID_TO_CURVE_DICT = {
            OID_SECP256R1     : SECP256R1, # ansix9p256r1
            OID_SECP384R1     : SECP384R1, # ansix9p384r1
            OID_SECP521R1     : SECP521R1, # ansix9p521r1
            OID_BRAINPOOL256R1: BrainpoolP256R1, # brainpoolP256r1
            OID_BRAINPOOL384R1: BrainpoolP384R1, # brainpoolP384r1
            OID_BRAINPOOL512R1: BrainpoolP512R1, # brainpoolP512r1
        }

    class EDDSA(ECBase):
        ID = 0x16
        _OID_TO_CURVE_DICT = {
            OID_ED25519: Ed25519PrivateKey,
        }

        @classmethod
        def getCombinationArgumentDict(cls):
            return {
                'algo': tuple(cls._OID_TO_CURVE_DICT.values()),
                # No support for an embedded public key
                'with_public_key': (False, ),
            }

    _ATTRIBUTE_DICT = None

    @classmethod
    def encode(cls, value, codec):
        algorithm = value['algorithm']
        if algorithm.ID not in cls._ATTRIBUTE_DICT:
            raise ValueError
        return algorithm.ID.to_bytes(1, 'big') + algorithm.encode(
            value=value['parameter_dict']
        )

    @classmethod
    def decode(cls, value, codec):
        algorithm = cls._ATTRIBUTE_DICT[value[0]]
        return {
            'algorithm': algorithm,
            'parameter_dict': algorithm.decode(value[1:]),
        }

    @classmethod
    def getAlgorithmObject(cls, value, codec):
        _ = codec
        return cls._ATTRIBUTE_DICT[value[0]](value[1:])

    @classmethod
    def getSupportedAttributes(cls):
        # TODO: move computation to metaclass, so it is computed once per subclass
        result = []
        for algorithm in cls._ATTRIBUTE_DICT.values():
            # Convert {'foo': (1, 2), 'bar': (3, 4, 5)} into
            # {'foo': 1, 'bar': 3}, {'foo': 1, 'bar': 4}, ... {'foo': 2, 'bar': 3}, ...
            property_name_list, property_value_list_list = zip(*(
                x for x in algorithm.getCombinationArgumentDict().items()
            ))
            for property_value_list in itertools.product(
                *property_value_list_list
            ):
                result.append(cls.encode(
                    value={
                        'algorithm': algorithm,
                        'parameter_dict': dict(zip(
                            property_name_list,
                            property_value_list,
                        ))
                    },
                    codec=CodecBER,
                ))
        return result

class AlgorithmAttributesSignature(AlgorithmAttributesBase):
    identifier = 0x01
    _ATTRIBUTE_DICT = {
        x.ID: x
        for x in (
            AlgorithmAttributesBase.RSA,
            AlgorithmAttributesBase.ECDSA,
            AlgorithmAttributesBase.EDDSA,
        )
    }

class AlgorithmAttributesDecryption(AlgorithmAttributesBase):
    identifier = 0x02
    _ATTRIBUTE_DICT = {
        x.ID: x
        for x in (
            AlgorithmAttributesBase.RSA,
            AlgorithmAttributesBase.ECDH,
        )
    }

class AlgorithmAttributesAuthentication(AlgorithmAttributesBase):
    identifier = 0x03
    _ATTRIBUTE_DICT = {
        x.ID: x
        for x in (
            AlgorithmAttributesBase.RSA,
            AlgorithmAttributesBase.ECDSA,
            AlgorithmAttributesBase.EDDSA,
        )
    }

class PasswordStatusBytes(_PrivateSimpleBase):
    identifier = 0x04

    @classmethod
    def encode(cls, value, codec):
        return struct.pack(
            'BBBBBBB',
            value['is_pw1_valid_for_multiple_signatures'],
            value['pw1_max_length'],
            value['rc_max_length'],
            value['pw3_max_length'],
            value['pw1_remaining_tries_count'],
            value['rc_remaining_tries_count'],
            value['pw3_remaining_tries_count'],
        )

    @classmethod
    def decode(cls, value, codec):
        (
            is_pw1_valid_for_multiple_signatures,
            pw1_max_length,
            rc_max_length,
            pw3_max_length,
            pw1_remaining_tries_count,
            rc_remaining_tries_count,
            pw3_remaining_tries_count,
        ) = struct.unpack(
            'BBBBBBB',
            value,
        )
        return {
            'is_pw1_valid_for_multiple_signatures': is_pw1_valid_for_multiple_signatures,
            'pw1_max_length': pw1_max_length,
            'rc_max_length': rc_max_length,
            'pw3_max_length': pw3_max_length,
            'pw1_remaining_tries_count': pw1_remaining_tries_count,
            'rc_remaining_tries_count': rc_remaining_tries_count,
            'pw3_remaining_tries_count': pw3_remaining_tries_count,
        }

class Fingerprints(_FixedWidthOctetStringList):
    identifier = 0x05
    item_length = 40

class CAFingerprints(_FixedWidthOctetStringList):
    identifier = 0x06
    item_length = 40

class SignatureKeyFingerprint(_FixedWidthOctetString):
    identifier = 0x07
    length = 40

class DecryptionKeyFingerprint(_FixedWidthOctetString):
    identifier = 0x08
    length = 40

class AuthenticationKeyFingerprint(_FixedWidthOctetString):
    identifier = 0x09
    length = 40

class CAFingerprint1(_FixedWidthOctetString):
    identifier = 0x0a
    length = 40

class CAFingerprint2(_FixedWidthOctetString):
    identifier = 0x0b
    length = 40

class CAFingerprint3(_FixedWidthOctetString):
    identifier = 0x0c
    length = 40

class KeyTimestamps(_FixedWidthOctetStringList):
    identifier = 0x0d
    item_length = 4

class SignatureKeyTimestamp(_FixedWidthOctetString):
    identifier = 0x0e
    length = 4

class DecryptionKeyTimestamp(_FixedWidthOctetString):
    identifier = 0x0f
    length = 4

class AuthenticationKeyTimestamp(_FixedWidthOctetString):
    identifier = 0x10
    length = 4

#TAG_SECURE_MESSAGING_KEY_ENC            = (CLASS_PRIVATE, BERTLV_ENCODING_PLAIN, 0x11)
#TAG_SECURE_MESSAGING_KEY_MAC            = (CLASS_PRIVATE, BERTLV_ENCODING_PLAIN, 0x12)

class ResettingCode(_PrivateOctetStringBase):
    identifier = 0x13

class AESKey(_PrivateOctetStringBase):
    identifier = 0x15

#TAG_USER_INTERACTION_SIGNATURE          = (CLASS_PRIVATE, BERTLV_ENCODING_PLAIN, 0x16)
#TAG_USER_INTERACTION_DECRYPTION         = (CLASS_PRIVATE, BERTLV_ENCODING_PLAIN, 0x17)
#TAG_USER_INTERACTION_AUTHENTICATION     = (CLASS_PRIVATE, BERTLV_ENCODING_PLAIN, 0x18)

class KeyInformation(_PrivateSimpleBase):
    identifier = 0x1e

    @classmethod
    def encode(cls, value, codec):
        return b''.join(
            struct.pack('BB', index, status)
            for index, status in value.items()
        )

    @classmethod
    def decode(cls, value, codec):
        result = {}
        while value:
            index, status = struct.unpack('BB', value[:2])
            result[index] = status
            value = value[2:]
        return result

class Cipher(_ContextCompositeBase):
    identifier = 0x06
    min_length = 1

    @classmethod
    def iterItemSchema(cls):
        return [{
            PublicKeyComponents.asTagTuple(): PublicKeyComponents,
        }]

#TAG_SECURE_MESSAGING_KEY_CONTAINER      = (CLASS_PRIVATE, BERTLV_ENCODING_BERTLV, 0x14)

class KeyDerivedFunction(_PrivateCompositeBase):
    identifier = 0x19

    class Algorithm(_ContextSimpleBase):
        identifier = 0x01
        NONE = NamedSingleton('NONE')
        ITERSALTED_S2K = NamedSingleton('ITERSALTED_S2K')
        __DICT = {
            NONE: b'\x00',
            ITERSALTED_S2K: b'\x03',
        }
        __REVERSE_DICT = {
            value: key
            for key, value in __DICT.items()
        }

        @classmethod
        def encode(cls, value, codec):
            return cls.__DICT[value]

        @classmethod
        def decode(cls, value, codec):
            return cls.__REVERSE_DICT[value]

    class HashAlgorithm(_ContextSimpleBase):
        identifier = 0x02
        SHA256 = NamedSingleton('SHA256')
        SHA512 = NamedSingleton('SHA512')
        __DICT = {
             SHA256: b'\x08',
             SHA512: b'\x0a',
        }
        __REVERSE_DICT = {
            value: key
            for key, value in __DICT.items()
        }

        @classmethod
        def encode(cls, value, codec):
            return cls.__DICT[value]

        @classmethod
        def decode(cls, value, codec):
            return cls.__REVERSE_DICT[value]

    class IterationCount(_ContextSimpleBase):
        identifier = 0x03

        @classmethod
        def encode(cls, value, codec):
            return value.to_bytes(4, 'big')

        @classmethod
        def decode(cls, value, codec):
            return int.from_bytes(value, 'big')

    class SaltBytesPW1(_ContextOctetStringBase):
        identifier = 0x04

    class SaltBytesRC(_ContextOctetStringBase):
        identifier = 0x05

    class SaltBytesPW3(_ContextOctetStringBase):
        identifier = 0x06

    class InitialPasswordHashPW1(_ContextOctetStringBase):
        identifier = 0x07

    class InitialPasswordHashPW3(_ContextOctetStringBase):
        identifier = 0x08

    @classmethod
    def iterItemSchema(cls):
        return itertools.chain([{
            x.asTagTuple(): x
            for x in (
                cls.Algorithm,
                cls.HashAlgorithm,
                cls.IterationCount,
                cls.SaltBytesPW1,
                cls.SaltBytesRC,
                cls.SaltBytesPW3,
                cls.InitialPasswordHashPW1,
                cls.InitialPasswordHashPW3,
            )
        }])

class AlgorithmInformation(_PrivateCompositeBase):
    identifier = 0x1a

    @classmethod
    def iterItemSchema(cls):
        return itertools.chain([{
            x.asTagTuple(): x
            for x in (
                AlgorithmAttributesSignature,
                AlgorithmAttributesDecryption,
                AlgorithmAttributesAuthentication,
            )
        }])

#TAG_SECURE_MESSAGING_CERTIFICATE        = (CLASS_PRIVATE, BERTLV_ENCODING_BERTLV, 0x1b)
#TAG_ATTESTATION_CERTIFICATE             = (CLASS_PRIVATE, BERTLV_ENCODING_BERTLV, 0x1c)

class RSADigestInfo(Sequence):
    min_length = 2

    class HashOID(Sequence):
        min_length = 2

        @classmethod
        def iterItemSchema(cls):
            return [
                {
                    ObjectIdentifier.asTagTuple(): ObjectIdentifier,
                },
                {
                    Null.asTagTuple(): Null,
                },
            ]

    @classmethod
    def iterItemSchema(cls):
        return [
            {
                cls.HashOID.asTagTuple(): cls.HashOID,
            },
            {
                OctetString.asTagTuple(): OctetString,
            },
        ]

    @classmethod
    def encode(cls, value, codec):
        return super().encode(
            [
                (cls.HashOID, [
                    (ObjectIdentifier, value['oid']),
                    (Null, None),
                ]),
                (OctetString, value['condensate']),
            ],
            codec=codec,
        )

    @classmethod
    def decode(cls, value, codec):
        ( # pylint: disable=unbalanced-tuple-unpacking
            (_, (
                (_, oid),
                (_, _),
            )),
            (_, condensate),
        ) = super().decode(value, codec=codec)
        return {
            'oid': oid,
            'condensate': condensate,
        }
