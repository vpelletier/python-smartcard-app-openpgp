# -*- coding: utf-8 -*-
# Copyright (C) 2018-2020  Vincent Pelletier <plr.vincent@gmail.com>
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

from collections import defaultdict
import hmac
import logging
import random
import struct
import threading
import time
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.hashes import (
    MD5,
    SHA1,
    SHA224,
    SHA256,
    SHA384,
    SHA512,
)
from cryptography.hazmat.primitives.asymmetric.padding import (
    PKCS1v15,
)
from cryptography.hazmat.primitives.asymmetric.utils import (
    Prehashed,
    decode_dss_signature,
)
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.ec import (
    EllipticCurvePrivateKey,
    EllipticCurvePublicKey,
    ECDSA,
    ECDH,
)
import persistent
import ZODB.Connection
from smartcard.asn1 import (
    CodecCompact,
    CodecBER,
)
from smartcard import (
    ApplicationFile,
    HISTORICAL_BYTES_CATEGORY_STATUS_RAW,
)
from smartcard.status import (
    SUCCESS,
    APDUException,
    AuthMethodBlocked,
    RecordNotFound,
    ReferenceDataNotFound,
    ReferenceDataNotUsable,
    SecurityNotSatisfied,
    WarnPersistentChanged,
    WrongParameterInCommandData,
    WrongParametersP1P2,
)
from smartcard.tag import (
    ApplicationIdentifier,
    ApplicationLabel,
    ApplicationRelatedData,
    CardCapabilities,
    CardholderData,
    CardServiceData,
    DiscretionaryTemplate,
    encodeSecurityConditionByte,
    ExtendedHeaderList,
    FileIdentifier,
    FileSecurityCompactFormat,
    getDataCodingByte,
    HistoricalData,
    LifecycleBase,
    Name,
    SecuritySupportTemplate,
    SECURITY_CONDITION_ALLOW,
    URL,
    WRITE_FUNCTION_ONE_TIME,
)
from smartcard.utils import (
    PersistentWithVolatileSurvivor,
    transaction_manager,
    NamedSingleton,
)
from .tag import (
    AlgorithmAttributesAuthentication,
    AlgorithmAttributesDecryption,
    AlgorithmAttributesSignature,
    AlgorithmInformation,
    AuthenticationKeyFingerprint,
    AuthenticationKeyTimestamp,
    CAFingerprint1,
    CAFingerprint2,
    CAFingerprint3,
    CAFingerprints,
    CardholderCertificate,
    CardholderPrivateKey,
    CardholderPrivateKeyTemplate,
    CardholderPrivateKeyTemplateExtendedHeader,
    Cipher,
    CONTROL_REFERENCE_SCHEMA,
    ControlReferenceTemplateAuthentication,
    ControlReferenceTemplateDecryption,
    ControlReferenceTemplateSignature,
    DecryptionKeyFingerprint,
    DecryptionKeyTimestamp,
    ExtendedCapabilities,
    ExtendedLengthInformation,
    Fingerprints,
    KeyDerivedFunction,
    KeyInformation,
    KeyTimestamps,
    LanguagePreference,
    LoginData,
    PasswordStatusBytes,
    Private1,
    Private2,
    Private3,
    Private4,
    PublicKeyComponents,
    ResettingCode,
    RSADigestInfo,
    Sex,
    SignatureCounter,
    SignatureKeyFingerprint,
    SignatureKeyTimestamp,
    OID_X25519,
)

logger = logging.getLogger(__name__)

FSFE_RID = b'\xd2\x76\x00\x01\x24'
FSFE_OPENPGP_PIX_APPLICATION = b'\x01'
FSFE_OPENPGP_PIX_VERSION = b'\x03\x41' # Written with spec 3.4.1

PERFORM_SECURITY_OPERATION_CLEARTEXT = 0x80
PERFORM_SECURITY_OPERATION_CIPHERTEXT = 0x86
PERFORM_SECURITY_OPERATION_CONDENSATE = 0x9a
PERFORM_SECURITY_OPERATION_SIGNATURE = 0x9e

KEY_INFORMATION_NOT_PRESENT = 0
KEY_INFORMATION_GENERATED_ON_CARD = 1
KEY_INFORMATION_IMPORTED_TO_CARD = 2

HASH_LENGTH_TO_HASH_DICT = {
    x.digest_size: x
    for x in (
        MD5,
        SHA1,
        SHA224,
        SHA256,
        SHA384,
        SHA512,
    )
}

HASH_OID_DICT = {
    '1.3.36.3.2.1': MD5,
    '1.3.14.3.2.26': SHA1,
    '2.16.840.1.101.3.4.2.4': SHA224,
    '2.16.840.1.101.3.4.2.1': SHA256,
    '2.16.840.1.101.3.4.2.2': SHA384,
    '2.16.840.1.101.3.4.2.3': SHA512,
}

KEY_ROLE_SIGN = NamedSingleton('sign')
KEY_ROLE_DECRYPT = NamedSingleton('decrypt')
KEY_ROLE_AUTHENTICATE = NamedSingleton('authenticate')
KEY_TAG_TO_ROLE_DICT = {
    ControlReferenceTemplateSignature: KEY_ROLE_SIGN,
    ControlReferenceTemplateDecryption: KEY_ROLE_DECRYPT,
    ControlReferenceTemplateAuthentication: KEY_ROLE_AUTHENTICATE,
}
KEY_INDEX_SIGN = 0
KEY_INDEX_DECRYPT = 1
KEY_INDEX_AUTHENTICATE = 2
KEY_ROLE_TO_INDEX_DICT = {
    KEY_ROLE_SIGN: KEY_INDEX_SIGN,
    KEY_ROLE_DECRYPT: KEY_INDEX_DECRYPT,
    KEY_ROLE_AUTHENTICATE: KEY_INDEX_AUTHENTICATE,
}
KEY_ROLE_TO_ATTRIBUTE_TAG_DICT = {
    KEY_ROLE_SIGN: AlgorithmAttributesSignature,
    KEY_ROLE_DECRYPT: AlgorithmAttributesDecryption,
    KEY_ROLE_AUTHENTICATE: AlgorithmAttributesAuthentication,
}
KEY_INDEX_TO_ATTRIBUTE_TAG_DICT = {
    KEY_ROLE_TO_INDEX_DICT[role]: algorithm
    for role, algorithm in KEY_ROLE_TO_ATTRIBUTE_TAG_DICT.items()
}
ROLE_TO_SUPPORTED_ALGORITHM_INFORMATION_LIST_DICT = {
    role: algorithm_attributes_class.getSupportedAttributes()
    for role, algorithm_attributes_class in KEY_ROLE_TO_ATTRIBUTE_TAG_DICT.items()
}

DEFAULT_ALGORITHM_ATTRIBUTES_SIGNATURE = AlgorithmAttributesSignature.encode(
    value={
        'algorithm': AlgorithmAttributesSignature.RSA,
        'parameter_dict': {
            'public_exponent_bit_length': 32,
            'modulus_bit_length': 2048,
            'import_format': AlgorithmAttributesSignature.RSA.IMPORT_FORMAT_STANDARD,
        },
    },
    codec=CodecBER,
)
DEFAULT_ALGORITHM_ATTRIBUTES_DECRYPTION = AlgorithmAttributesDecryption.encode(
    value={
        'algorithm': AlgorithmAttributesDecryption.ECDH,
        'parameter_dict': {
            'algo': OID_X25519,
            'with_public_key': False,
        },
    },
    codec=CodecBER,
)
DEFAULT_ALGORITHM_ATTRIBUTES_AUTHENTICATION = AlgorithmAttributesAuthentication.encode(
    value={
        'algorithm': AlgorithmAttributesAuthentication.RSA,
        'parameter_dict': {
            'public_exponent_bit_length': 32,
            'modulus_bit_length': 2048,
            'import_format': AlgorithmAttributesAuthentication.RSA.IMPORT_FORMAT_STANDARD,
        },
    },
    codec=CodecBER,
)

assert DEFAULT_ALGORITHM_ATTRIBUTES_SIGNATURE == b'\x01\x08\x00\x00\x20\x00', DEFAULT_ALGORITHM_ATTRIBUTES_SIGNATURE.hex()
assert DEFAULT_ALGORITHM_ATTRIBUTES_DECRYPTION == b'\x12\x2b\x06\x01\x04\x01\x97\x55\x01\x05\x01', DEFAULT_ALGORITHM_ATTRIBUTES_DECRYPTION.hex()
assert DEFAULT_ALGORITHM_ATTRIBUTES_AUTHENTICATION == b'\x01\x08\x00\x00\x20\x00', DEFAULT_ALGORITHM_ATTRIBUTES_AUTHENTICATION.hex()
assert DEFAULT_ALGORITHM_ATTRIBUTES_SIGNATURE in ROLE_TO_SUPPORTED_ALGORITHM_INFORMATION_LIST_DICT[KEY_ROLE_SIGN]
assert DEFAULT_ALGORITHM_ATTRIBUTES_DECRYPTION in ROLE_TO_SUPPORTED_ALGORITHM_INFORMATION_LIST_DICT[KEY_ROLE_DECRYPT]
assert DEFAULT_ALGORITHM_ATTRIBUTES_AUTHENTICATION in ROLE_TO_SUPPORTED_ALGORITHM_INFORMATION_LIST_DICT[KEY_ROLE_AUTHENTICATE]

# Value received in verify & change reference data
LEVEL_PW1_SIGN = 1
LEVEL_PW1_DECRYPT = 2
LEVEL_PW3 = 3

REFERENCE_DATA_LENGTH_FORMAT_PIN_BLOCK_2_MASK = 0x80

PW1_INDEX = 0
PW3_INDEX = 1
RESET_CODE_INDEX = 2
REFERENCE_DATA_LEVEL_TO_LIST_OFFSET_DICT = {
    LEVEL_PW1_SIGN: PW1_INDEX, # PW1 is PW1
    LEVEL_PW1_DECRYPT: PW1_INDEX, # PW2 is PW1
    LEVEL_PW3: PW3_INDEX, # PW3 is PW3
}
VERIFICATION_DATA_VALIDITY = 3 # 3 tries

DEFAULT_PW1 = '123456'.encode('utf-8')
DEFAULT_PW3 = '12345678'.encode('utf-8')
DEFAULT_RC = None

SECURITY_CONDITION_PW1_DECRYPT = encodeSecurityConditionByte(
    user_authentication=True,
    # XXX: security ID field of simple security is not supposed to be
    # used for password-level matching...
    security_environment_id=LEVEL_PW1_DECRYPT,
)
SECURITY_CONDITION_PW3 = encodeSecurityConditionByte(
    user_authentication=True,
    # XXX: security ID field of simple security is not supposed to be
    # used for password-level matching...
    security_environment_id=LEVEL_PW3,
)
DATA_OBJECT_GET_ALWAYS_PUT_PW3 = ((FileSecurityCompactFormat, {
    FileSecurityCompactFormat.GET: SECURITY_CONDITION_ALLOW,
    FileSecurityCompactFormat.PUT: SECURITY_CONDITION_PW3,
}), )
DATA_OBJECT_GET_NEVER_PUT_PW3 = ((FileSecurityCompactFormat, {
    FileSecurityCompactFormat.PUT: SECURITY_CONDITION_PW3,
}), )
DATA_OBJECT_GET_ALWAYS_PUT_NEVER = ((FileSecurityCompactFormat, {
    FileSecurityCompactFormat.GET: SECURITY_CONDITION_ALLOW,
}), )

EMPTY_FINGERPRINT = b'\x00' * 20
EMPTY_TIMESTAMP = b'\x00' * 4
ALGORITHM_ATTRIBUTES_TO_FINGERPRINT_AND_CA_AND_TIMESTAMP = {
    AlgorithmAttributesSignature: (
        SignatureKeyFingerprint,
        CAFingerprint1,
        SignatureKeyTimestamp,
    ),
    AlgorithmAttributesDecryption: (
        DecryptionKeyFingerprint,
        CAFingerprint2,
        DecryptionKeyTimestamp,
    ),
    AlgorithmAttributesAuthentication: (
        AuthenticationKeyFingerprint,
        CAFingerprint3,
        AuthenticationKeyTimestamp,
    ),
}

class OpenPGP(PersistentWithVolatileSurvivor, ApplicationFile):
    # XXX: is min length constraint even a thing ? Or is it the spec telling
    # implementors that their maximum password length must be at least this
    # much to quality as an OpenPGP card ?
    __reference_min_length_list = (
        6, # PW1
        8, # PW3
        8, # RESET CODE
    )
    __reference_max_length_list = (
        # 8th bit is PIN BLOCK 2 format marker.
        127, # PW1
        127, # PW3
        127, # RESET CODE
    )
    _v_key_list = None
    __pw1_valid_multiple_signatures = None
    __signature_counter = None
    _has_key_derived_function = True

    def __init__(self, manufacturer=None, serial=None, **kw):
        if manufacturer is None or serial is None:
            if manufacturer is not None or serial is not None:
                raise ValueError(
                    'Either both manufacturer and serial must be provided or none.',
                )
            # Pick manufacturer and serial number in the random range
            # ff00..fffe, as ffff has a different meaning
            manufacturer = b'\xff' + random.randint(0, 0xfe).to_bytes(1, 'big')
            serial = random.getrandbits(32).to_bytes(4, 'big')
        # XXX: application name cannot change once set, as it's referenced at
        # card level for selection.
        # XXX: need a way to upgrade the application without losing data
        super().__init__(
            name=b''.join((
                FSFE_RID,
                FSFE_OPENPGP_PIX_APPLICATION,
                FSFE_OPENPGP_PIX_VERSION,
                manufacturer,
                serial,
                b'\x00\x00',
            )),
            **kw
        )
        self._setupEarlyVolatileSurvivors()
        self.__blank()
        self.setupVolatileSurvivors()

    def blank(self):
        super().blank()
        self.__blank()

    def __blank(self):
        self.__reference_data_list = persistent.list.PersistentList([
            DEFAULT_PW1, # PW1
            DEFAULT_PW3, # PW3 (!)
            DEFAULT_RC,  # PW1 reset code
        ])
        self.__reference_data_counter_list = persistent.list.PersistentList([
            (
                0
                if x is None else
                VERIFICATION_DATA_VALIDITY
            )
            for x in self.__reference_data_list
        ])
        self.__key_list = persistent.list.PersistentList([None] * 3)
        self._v_key_list = [None] * 3
        self.__key_information_list = [
            KEY_INFORMATION_NOT_PRESENT
        ] * 3
        self.setStandardCompactSecurity(
            activate=SECURITY_CONDITION_ALLOW,
            # Termination security is handled in terminate().
            terminate=SECURITY_CONDITION_ALLOW,
        )
        self._setSex(Sex.NOT_ANNOUNCED)
        self.putData(
            tag=AlgorithmAttributesSignature,
            value=DEFAULT_ALGORITHM_ATTRIBUTES_SIGNATURE,
            encode=False,
        )
        self.putData(
            tag=AlgorithmAttributesDecryption,
            value=DEFAULT_ALGORITHM_ATTRIBUTES_DECRYPTION,
            encode=False,
        )
        self.putData(
            tag=AlgorithmAttributesAuthentication,
            value=DEFAULT_ALGORITHM_ATTRIBUTES_AUTHENTICATION,
            encode=False,
        )
        if self._has_key_derived_function:
            self.putData(
                tag=KeyDerivedFunction,
                value=[(
                    KeyDerivedFunction.Algorithm,
                    KeyDerivedFunction.Algorithm.NONE,
                )],
                encode=True,
            )
        for private in (Private1, Private2, Private3, Private4):
            self.putData(
                tag=private,
                value=b'',
                encode=True,
            )
        self.__pw1_valid_multiple_signatures = False
        self.__signature_counter = 0

    def setupVolatileSurvivors(self):
        # Raspberry pi zero can be slow enough at generating keys that it
        # exceeds gnupg's default 5s timeout, making key generation fail.
        # So, spawn a thread whose only job is to top up a list of candidate
        # key pairs.
        self._setupEarlyVolatileSurvivors()
        algorithm_attributes_list = self._v_s_algorithm_attributes_list
        for index, tag in KEY_INDEX_TO_ATTRIBUTE_TAG_DICT.items():
            if algorithm_attributes_list[index] is None:
                algorithm_attributes_list[index] = tag.getAlgorithmObject(
                    self.getData(tag, decode=False),
                    codec=CodecBER,
                )
        self._v_s_keygen_thread = threading.Thread(
            target=self._maintainKeyQueue,
            name='keygen',
            daemon=True,
        )
        self._v_s_keygen_thread.start()

    def _setupEarlyVolatileSurvivors(self):
        # Attributes needed by initial __blank call, in which case this is
        # called by __init__.
        # These attributes may hence be already set when called from
        # setupVolatileSurvivors, do not overwrite them.
        try:
            self._v_s_algorithm_attributes_list
        except AttributeError:
            self._v_s_algorithm_attributes_list = [None] * 3
        try:
            self._v_s_keygen_semaphore
        except AttributeError:
            self._v_s_keygen_semaphore = threading.Semaphore(1)
        try:
            self._v_s_keygen_key_list
        except AttributeError:
            self._v_s_keygen_key_list = [None] * 3

    def __setstate__(self, state):
        super().__setstate__(state)
        self._v_key_list = [
            (
                None
                if private_key is None else
                serialization.load_pem_private_key(
                    private_key,
                    password=None,
                )
            )
            for private_key in self.__key_list
        ]

    def _getExtendedLengthInformation(self):
        return (ExtendedLengthInformation.encode(
            value={
                'max_request_length': 0xffff,
                'max_response_length': 0xffff,
            },
            codec=CodecBER,
        ), )

    def _getExtendedCapabilities(self):
        return (ExtendedCapabilities.encode(
            value={
                'secure_messaging_algorithm': ExtendedCapabilities.SECURE_MESSAGING_ALGORITHM_NONE, # TODO
                'challenge_max_length': 0xffff,
                'has_key_import': True,
                'has_editable_password_status': True,
                'has_private_data_objects': True,
                'has_editable_algorithm_attributes': True,
                'has_aes': False, # TODO
                'has_key_derived_function': self._has_key_derived_function,
                'certificate_max_length': 0xffff,
                'special_data_object_max_length': 0xffff,
                'has_pin_block2_format': False,
                'can_swap_auth_dec_key_role': True,
            },
            codec=CodecBER,
        ), )

    def _getPasswordStatusBytes(self):
        return (
            struct.pack(
                'BBBBBBB',
                self.__pw1_valid_multiple_signatures,
                self.__reference_max_length_list[PW1_INDEX],
                self.__reference_max_length_list[RESET_CODE_INDEX],
                self.__reference_max_length_list[PW3_INDEX],
                self.__reference_data_counter_list[PW1_INDEX],
                self.__reference_data_counter_list[RESET_CODE_INDEX],
                self.__reference_data_counter_list[PW3_INDEX],
            ),
        )

    def _getSecuritySupportTemplate(self):
        return (
            CodecBER.encode(
                tag=SignatureCounter,
                value=self.__signature_counter,
            ),
        )

    def _getSignatureCounter(self):
        return (
            SignatureCounter.encode(
                value=self.__signature_counter,
                codec=CodecBER,
            ),
        )

    def _getApplicationLabel(self):
        return (b'OPENPGP', )

    def _getApplicationRelatedData(self):
        result = []
        for tag in (
            ApplicationIdentifier,
            HistoricalData,
            ExtendedLengthInformation,
            #TAG_GENERAL_FEATURE_MANAGEMENT, # n/a
            ExtendedCapabilities,
            AlgorithmAttributesSignature,
            AlgorithmAttributesDecryption,
            AlgorithmAttributesAuthentication,
            PasswordStatusBytes,
            Fingerprints,
            CAFingerprints,
            KeyTimestamps,
            KeyInformation,
            #TAG_USER_INTERACTION_SIGNATURE, # n/a
            #TAG_USER_INTERACTION_DECRYPTION, # n/a
            #TAG_USER_INTERACTION_AUTHENTICATION, # n/a
        ):
            value = self.getData(tag=tag, decode=False)
            if value is not None:
                result.append(CodecBER.wrapValue(tag, value))
        return (
            CodecBER.wrapValue(
                tag=DiscretionaryTemplate,
                encoded=b''.join(result),
            ),
        )

    def _getCardholderData(self):
        result = []
        for tag in (
            Name,
            LanguagePreference,
            Sex,
        ):
            value = self.getData(tag=tag, decode=False)
            if value is None:
                value = b''
            result.append(CodecBER.wrapValue(tag, value))
        return (b''.join(result), )

    def _getHistoricalData(self):
        return (bytes((
            HISTORICAL_BYTES_CATEGORY_STATUS_RAW,
        )) + CodecCompact.encode(
            tag=CardServiceData,
            value={
                'can_select_full_df_name': True,
                'can_select_partial_df_name': True,
                'ef_dir_is_bertlv': True,
                'ef_atr_is_bertlv': False,
                'ef_dir_ef_atr_access_mode': CardServiceData.EF_DIR_EF_ATR_ACCESS_MODE_GET_DATA,
                'has_master_file': True,
            },
        ) + CodecCompact.encode(
            tag=CardCapabilities,
            value={
                'can_select_full_df_name': True,
                'can_select_partial_df_name': True,
                'can_select_path': True,
                'can_select_file_identifier': True,
                'has_implicit_df_selection': True,
                'supports_short_ef_identifier': False,
                'supports_record_number': True,
                'supports_record_identifier': True,
                'data_coding_byte': getDataCodingByte(
                    supports_ef_with_tlv_content=False,
                    write_function_behaviour=WRITE_FUNCTION_ONE_TIME,
                    supports_ff_tag=True,
                    size_unit=2, # 1 byte unit
                ),
                'supports_command_chaining': True,
                'supports_extended_lenghts': True,
                'extended_lengths_ef_atr': True,
                'channel_assignment_by_card': True,
                'channel_assignment_by_host': True,
                'channel_count': 8,
            },
        ) + bytes(
            (
                # Note: OpenGPG cards uses this byte not as an indication of
                # the current application lifecycle, but as an indication of
                # the capabilities of the card:
                # LifecycleBase.NO_INFO: no life cycle management
                #   (=card cannot be reset to default values)
                # LifecycleBase.INITIALISATION: this application can be reset to
                #   default values
                # LifecycleBase.ACTIVATED: the card supports
                #   INSTRUCTION_TERMINATE_DEDICATED_FILE and
                #   INSTRUCTION_ACTIVATE_FILE.
                # XXX: should probably be an option, if some hardened
                # hardware implementation want to remove theft incentive.
                LifecycleBase.ACTIVATED,
            ),
        ) + bytes(SUCCESS), )

    def _getFingerprints(self):
        return (
            b''.join(
                self.getData(tag=tag, decode=False) or EMPTY_FINGERPRINT
                for tag in (
                    SignatureKeyFingerprint,
                    DecryptionKeyFingerprint,
                    AuthenticationKeyFingerprint,
                )
            ),
        )

    def _getCAFingerprints(self):
        return (
            b''.join(
                self.getData(tag=tag, decode=False) or EMPTY_FINGERPRINT
                for tag in (
                    CAFingerprint1,
                    CAFingerprint2,
                    CAFingerprint3,
                )
            ),
        )

    def _getKeyTimestamps(self):
        return (
            b''.join(
                self.getData(tag=tag, decode=False) or EMPTY_TIMESTAMP
                for tag in (
                    SignatureKeyTimestamp,
                    DecryptionKeyTimestamp,
                    AuthenticationKeyTimestamp,
                )
            ),
        )

    def _getKeyInformation(self):
        return (
            b''.join(
                index.to_bytes(1, 'big') + status.to_bytes(1, 'big')
                for index, status in enumerate(self.__key_information_list)
            ),
        )

    def _getAlgorithmInformation(self):
        return (b''.join(
            KEY_ROLE_TO_ATTRIBUTE_TAG_DICT[role].encode(
                algorithm_information,
                codec=CodecBER,
            )
            for role in (
                KEY_ROLE_SIGN, # signature key first
                KEY_ROLE_DECRYPT,
                KEY_ROLE_AUTHENTICATE,
            )
            for algorithm_information in ROLE_TO_SUPPORTED_ALGORITHM_INFORMATION_LIST_DICT[role]
        ), )

    @property
    def _dynamicGetDataObjectDict(self):
        result = super()._dynamicGetDataObjectDict
        result[ExtendedLengthInformation] = '_getExtendedLengthInformation'
        result[ExtendedCapabilities] = '_getExtendedCapabilities'
        result[PasswordStatusBytes] = '_getPasswordStatusBytes'
        result[SecuritySupportTemplate] = '_getSecuritySupportTemplate'
        result[SignatureCounter] = '_getSignatureCounter'
        result[ApplicationLabel] = '_getApplicationLabel'
        result[ApplicationRelatedData] = '_getApplicationRelatedData'
        result[HistoricalData] = '_getHistoricalData'
        result[CardholderData] = '_getCardholderData'
        result[Fingerprints] = '_getFingerprints'
        result[CAFingerprints] = '_getCAFingerprints'
        # No button, keypad, ..., so TAG_GENERAL_FEATURE_MANAGEMENT can be
        # ommitted. And likewise for TAG_USER_INTERACTION_*.
        #result[TAG_GENERAL_FEATURE_MANAGEMENT] =
        #result[TAG_USER_INTERACTION_SIGNATURE] =
        #result[TAG_USER_INTERACTION_DECRYPTION] =
        #result[TAG_USER_INTERACTION_AUTHENTICATION] =
        result[KeyTimestamps] = '_getKeyTimestamps'
        result[KeyInformation] = '_getKeyInformation'
        result[AlgorithmInformation] = '_getAlgorithmInformation'
        return result

    def _setExtendedHeaderList(self, value, index=None):
        _ = index # Silence pylint.
        private_key_template = CardholderPrivateKeyTemplateExtendedHeader.decode(
            value=value,
            codec=CodecBER,
        )
        # TODO: add support for extended format
        key_role_tag, _ = private_key_template[0]
        try:
            role = KEY_TAG_TO_ROLE_DICT[key_role_tag]
        except KeyError:
            raise WrongParameterInCommandData from None
        if len(private_key_template) == 1:
            # key removal
            private_key = None
        else:
            (
                (_, key_headers_value),
                (_, key_data_value),
            ) = private_key_template[1:]
            component_dict = {}
            for component_tag, component_length in key_headers_value:
                component_data = key_data_value[:component_length]
                if len(component_data) != component_length:
                    raise WrongParameterInCommandData
                if component_tag in component_dict:
                    raise WrongParameterInCommandData(
                        'multiple occurrences of key component %r' % (
                            component_tag,
                        ),
                    )
                component_dict[component_tag] = component_data
                key_data_value = key_data_value[component_length:]
            if key_data_value:
                raise WrongParameterInCommandData
            # XXX: could be simpler...
            key_attribute_tag = KEY_ROLE_TO_ATTRIBUTE_TAG_DICT[role]
            private_key = key_attribute_tag.getAlgorithmObject(
                value=self.getData(
                    key_attribute_tag,
                    decode=False,
                ),
                codec=CodecBER,
            ).importKey(
                component_dict=component_dict,
            )
        self._storePrivateKey(
            role=role,
            key=private_key,
            information=KEY_INFORMATION_IMPORTED_TO_CARD,
        )

    def _storePrivateKey(self, role, key, information):
        index = KEY_ROLE_TO_INDEX_DICT[role]
        self._v_key_list[index] = key
        if key is None:
            key_pem = None
        else:
            key_pem = key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        self.__key_list[index] = key_pem
        self.__key_information_list[index] = information
        if role is KEY_ROLE_SIGN:
            self.__signature_counter = 0

    def _setAlgoAttributes(self, tag, value, role, index):
        current_value = self.getData(tag=tag, index=index, decode=False)
        if current_value == value:
            return
        #raise WrongParameterInCommandData
        self._putData(tag=tag, value=value, index=index)
        key_index = KEY_ROLE_TO_INDEX_DICT[role]
        self._storePrivateKey(
            role=role,
            key=None,
            information=KEY_INFORMATION_NOT_PRESENT,
        )
        self._v_s_keygen_key_list[key_index] = None
        self._v_s_algorithm_attributes_list[key_index] = KEY_ROLE_TO_ATTRIBUTE_TAG_DICT[role].getAlgorithmObject(
            value,
            codec=CodecBER,
        )
        self._v_s_keygen_semaphore.release()

    def _setAlgoAttributesSignature(self, value, index=None):
        _ = index # Silence pylint.
        self._setAlgoAttributes(
            tag=AlgorithmAttributesSignature,
            value=value,
            role=KEY_ROLE_SIGN,
            index=index,
        )

    def _setAlgoAttributesDecryption(self, value, index=None):
        _ = index # Silence pylint.
        self._setAlgoAttributes(
            tag=AlgorithmAttributesDecryption,
            value=value,
            role=KEY_ROLE_DECRYPT,
            index=index,
        )

    def _setAlgoAttributesAuthentication(self, value, index=None):
        _ = index # Silence pylint.
        self._setAlgoAttributes(
            tag=AlgorithmAttributesAuthentication,
            value=value,
            role=KEY_ROLE_AUTHENTICATE,
            index=index,
        )

    def _setAlgoAttributesList(self, value, index=None):
        value_dict = {}
        for tag, algo_attributes in AlgorithmInformation.decode(
            value,
            codec=CodecBER,
        ):
            if tag in value_dict:
                raise WrongParameterInCommandData('Duplicate tag %r' % (tag, ))
            value_dict[tag] = algo_attributes
        for tag, algo_attributes in value_dict.items():
            self.putData(tag, algo_attributes, index=index, encode=True)

    def _setPasswordStatusBytes(self, value, index=None):
        _ = index # Silence pylint.
        # Note: if PIN length modification is implemented, it must only work
        # during personalisation stage.
        if len(value) >= 1:
            self.__pw1_valid_multiple_signatures = value == b'\x01'

    def _setSex(self, value, index=None):
        if value:
            try:
                Sex.decode(value, codec=CodecBER)
            except ValueError:
                raise WrongParameterInCommandData from None
        self._putData(tag=Sex, value=value, index=index)

    def _setFingerprint(self, tag, value, index=None):
        if len(value) not in (0, 20):
            raise WrongParameterInCommandData(len(value))
        self._putData(tag=tag, value=value, index=index)

    def _setSignatureFingerprint(self, value, index=None):
        self._setFingerprint(tag=SignatureKeyFingerprint, value=value, index=index)

    def _setDecryptionFingerprint(self, value, index=None):
        self._setFingerprint(tag=DecryptionKeyFingerprint, value=value, index=index)

    def _setAuthenticationFingerprint(self, value, index=None):
        self._setFingerprint(tag=AuthenticationKeyFingerprint, value=value, index=index)

    def _setFingerprints(self, value, index=None):
        # Start with the last one, to detect bad length before any change.
        self._setAuthenticationFingerprint(value[40:], index=index)
        self._setDecryptionFingerprint(value[20:40], index=index)
        self._setSignatureFingerprint(value[:20], index=index)

    def _setCAFingerprint1(self, value, index=None):
        self._setFingerprint(tag=CAFingerprint1, value=value, index=index)

    def _setCAFingerprint2(self, value, index=None):
        self._setFingerprint(tag=CAFingerprint2, value=value, index=index)

    def _setCAFingerprint3(self, value, index=None):
        self._setFingerprint(tag=CAFingerprint3, value=value, index=index)

    def _setCAFingerprints(self, value, index=None):
        # Start with the last one, to detect bad length before any change.
        self._setCAFingerprint3(value[40:], index=index)
        self._setCAFingerprint2(value[20:40], index=index)
        self._setCAFingerprint1(value[:20], index=index)

    def _setKeyTimestamp(self, tag, value, index=None):
        if len(value) not in (0, 4):
            raise WrongParameterInCommandData(len(value))
        self._putData(tag=tag, value=value, index=index)

    def _setSignatureKeyTimestamp(self, value, index=None):
        self._setKeyTimestamp(tag=SignatureKeyTimestamp, value=value, index=index)

    def _setDecryptionKeyTimestamp(self, value, index=None):
        self._setKeyTimestamp(tag=DecryptionKeyTimestamp, value=value, index=index)

    def _setAuthenticationKeyTimestamp(self, value, index=None):
        self._setKeyTimestamp(tag=AuthenticationKeyTimestamp, value=value, index=index)

    def _setTimestamps(self, value, index=None):
        # Start with the last one, to detect bad length before any change.
        self._setAuthenticationKeyTimestamp(value[8:], index=index)
        self._setDecryptionKeyTimestamp(value[4:8], index=index)
        self._setSignatureKeyTimestamp(value[:4], index=index)

    def _setCardholderData(self, value, index=None):
        for item_tag, item_value in CardholderData.decode(value, codec=CodecBER):
            if item_tag not in (
                Name,
                LanguagePreference,
                Sex,
            ):
                raise WrongParameterInCommandData
            self.setData(item_tag, item_value, index=index)

    def _setResettingCode(self, value, index=None):
        _ = index # Silence pylint.
        self._changeReferenceData(
            new_reference=self._decodeReferenceData(
                index=RESET_CODE_INDEX,
                command_data=value,
            ),
            index=RESET_CODE_INDEX,
        )

    @property
    def _dynamicSetDataObjectDict(self):
        result = super()._dynamicSetDataObjectDict
        result[PasswordStatusBytes] = '_setPasswordStatusBytes'
        result[ExtendedHeaderList] = '_setExtendedHeaderList'
        result[AlgorithmAttributesSignature] = '_setAlgoAttributesSignature'
        result[AlgorithmAttributesDecryption] = '_setAlgoAttributesDecryption'
        result[AlgorithmAttributesAuthentication] = '_setAlgoAttributesAuthentication'
        result[AlgorithmInformation] = '_setAlgoAttributesList'
        # For value validation
        #result[LanguagePreference] = XXX
        result[Sex] = '_setSex'
        result[CardholderData] = '_setCardholderData'
        # XXX: unsure if actually intended in the spec (no listed in PU-able DOs, but listed in access conditions)
        result[Fingerprints] = '_setFingerprints'
        result[SignatureKeyFingerprint] = '_setSignatureFingerprint'
        result[DecryptionKeyFingerprint] = '_setDecryptionFingerprint'
        result[AuthenticationKeyFingerprint] = '_setAuthenticationFingerprint'
        # XXX: unsure if actually intended in the spec (no listed in PU-able DOs, but listed in access conditions)
        result[CAFingerprints] = '_setCAFingerprints'
        result[CAFingerprint1] = '_setCAFingerprint1'
        result[CAFingerprint2] = '_setCAFingerprint2'
        result[CAFingerprint3] = '_setCAFingerprint3'
        result[KeyTimestamps] = '_setTimestamps'
        result[SignatureKeyTimestamp] = '_setSignatureKeyTimestamp'
        result[DecryptionKeyTimestamp] = '_setDecryptionKeyTimestamp'
        result[AuthenticationKeyTimestamp] = '_setAuthenticationKeyTimestamp'
        result[ResettingCode] = '_setResettingCode'
        return result

    def activateSelf(self):
        if self.lifecycle in (
            LifecycleBase.CREATION,
            LifecycleBase.INITIALISATION,
            LifecycleBase.TERMINATED,
        ):
            self.setDataObjectSecurityDict({
                # Not explicitly mentionned in spec:
                ApplicationRelatedData:            DATA_OBJECT_GET_ALWAYS_PUT_NEVER,
                ApplicationLabel:                  DATA_OBJECT_GET_ALWAYS_PUT_NEVER,
                SecuritySupportTemplate:           DATA_OBJECT_GET_ALWAYS_PUT_NEVER,
                ExtendedHeaderList:                DATA_OBJECT_GET_NEVER_PUT_PW3, # Key import
                #DiscretionaryTemplate: ???
                #TAG_CARD_HOLDER_PRIVATE_KEY_TEMPLATE: ??? (only used for encapsulation type and not as DO ?)
                Private1: ((FileSecurityCompactFormat, {
                    FileSecurityCompactFormat.GET: SECURITY_CONDITION_ALLOW,
                    FileSecurityCompactFormat.PUT: SECURITY_CONDITION_PW1_DECRYPT,
                }), ),
                Private2: ((FileSecurityCompactFormat, {
                    FileSecurityCompactFormat.GET: SECURITY_CONDITION_ALLOW,
                    FileSecurityCompactFormat.PUT: SECURITY_CONDITION_PW3,
                }), ),
                Private3: ((FileSecurityCompactFormat, {
                    FileSecurityCompactFormat.GET: SECURITY_CONDITION_PW1_DECRYPT,
                    FileSecurityCompactFormat.PUT: SECURITY_CONDITION_PW1_DECRYPT,
                }), ),
                Private4: ((FileSecurityCompactFormat, {
                    FileSecurityCompactFormat.GET: SECURITY_CONDITION_PW3,
                    FileSecurityCompactFormat.PUT: SECURITY_CONDITION_PW3,
                }), ),
                ApplicationIdentifier:             DATA_OBJECT_GET_ALWAYS_PUT_NEVER,
                Name:                              DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                LoginData:                         DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                LanguagePreference:                DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                Sex:                               DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                CardholderPrivateKeyTemplate:      DATA_OBJECT_GET_NEVER_PUT_PW3,
                URL:                               DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                HistoricalData:                    DATA_OBJECT_GET_ALWAYS_PUT_NEVER,
                CardholderData:                    DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                #TAG_SECURITY_SUPPORT_TEMPLATE:     DATA_OBJECT_GET_ALWAYS_PUT_NEVER, # TODO
                CardholderCertificate:             DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                ExtendedLengthInformation:         DATA_OBJECT_GET_ALWAYS_PUT_NEVER,
                #TAG_GENERAL_FEATURE_MANAGEMENT:    DATA_OBJECT_GET_ALWAYS_PUT_NEVER,
                SignatureCounter:                  DATA_OBJECT_GET_ALWAYS_PUT_NEVER,
                ExtendedCapabilities:              DATA_OBJECT_GET_ALWAYS_PUT_NEVER,
                AlgorithmAttributesSignature:      DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                AlgorithmAttributesDecryption:     DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                AlgorithmAttributesAuthentication: DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                AlgorithmInformation:              DATA_OBJECT_GET_ALWAYS_PUT_PW3, # XXX: Not in spec
                #TAG_ALGORITHM_ATTRIBUTES_ATTESTATION:   DATA_OBJECT_GET_ALWAYS_PUT_PW3, # TODO ? (yubico)
                PasswordStatusBytes:              DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                Fingerprints:                     DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                SignatureKeyFingerprint:          DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                DecryptionKeyFingerprint:         DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                AuthenticationKeyFingerprint:     DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                #TAG_ATTESTATION_KEY_FINGERPRINT:  DATA_OBJECT_GET_ALWAYS_PUT_PW3, # TODO ? (yubico)
                CAFingerprints:                   DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                CAFingerprint1:                   DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                CAFingerprint2:                   DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                CAFingerprint3:                   DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                #TAG_CA_FINGERPRINT_ATTESTATION:   DATA_OBJECT_GET_ALWAYS_PUT_PW3, # TODO ? (yubico)
                KeyTimestamps:                    DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                SignatureKeyTimestamp:            DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                DecryptionKeyTimestamp:           DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                AuthenticationKeyTimestamp:       DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                #TAG_ATTESTATION_KEY_TIMESTAMP:    DATA_OBJECT_GET_ALWAYS_PUT_PW3, # TODO ? (yubico)
                #TAG_SECURE_MESSAGING_KEY_ENC:     DATA_OBJECT_GET_NEVER_PUT_PW3, # TODO
                #TAG_SECURE_MESSAGING_KEY_MAC:     DATA_OBJECT_GET_NEVER_PUT_PW3, # TODO
                ResettingCode:                    DATA_OBJECT_GET_NEVER_PUT_PW3,
                #TAG_AES_ENC_DEC_KEY:                    DATA_OBJECT_GET_NEVER_PUT_PW3, # TODO
                #TAG_USER_INTERACTION_SIGNATURE:         DATA_OBJECT_GET_ALWAYS_PUT_PW3, # TODO ?
                #TAG_USER_INTERACTION_DECRYPTION:        DATA_OBJECT_GET_ALWAYS_PUT_PW3, # TODO ?
                #TAG_USER_INTERACTION_AUTHENTICATION:    DATA_OBJECT_GET_ALWAYS_PUT_PW3, # TODO ?
                #TAG_USER_INTERACTION_ATTESTATION:       DATA_OBJECT_GET_ALWAYS_PUT_PW3, # TODO ? (yubico)
                KeyInformation:                   DATA_OBJECT_GET_ALWAYS_PUT_NEVER,
                #TAG_SECURE_MESSAGING_KEY_CONTAINER:     DATA_OBJECT_GET_NEVER_PUT_PW3, # TODO
                KeyDerivedFunction:               DATA_OBJECT_GET_ALWAYS_PUT_PW3,
                #TAG_SECURE_MESSAGING_CERTIFICATE:       DATA_OBJECT_GET_ALWAYS_PUT_PW3, # TODO
                #TAG_ATTESTATION_CERTIFICATE:            DATA_OBJECT_GET_ALWAYS_PUT_NEVER, # TODO ? (yubico)
            })
        super().activateSelf()

    def terminate(self, channel):
        if (
            # Is PW3-level authenticated ?
            not channel.isUserAuthenticated(level=LEVEL_PW3) and
            # Is PW3 still usable ?
            self.__reference_data_counter_list[
                REFERENCE_DATA_LEVEL_TO_LIST_OFFSET_DICT[LEVEL_PW3]
            ] > 0
        ):
            raise SecurityNotSatisfied
        super().terminate(channel)

    def terminateSelf(self):
        self.blank()

    def deactivateSelf(self):
        # There is no security condition allowing access to this method through
        # normal card use. Set a trap to detect calls.
        raise NotImplementedError

    def getSignatureKeyIndex(self, channel):
        _ = channel # Silence pylint.
        # Cannot be overridden.
        return 0

    def getDecryptionKeyIndex(self, channel):
        return channel.getPrivate().get('decryption_key_index', 1)

    def getAuthenticationKeyIndex(self, channel):
        return channel.getPrivate().get('authentication_key_index', 2)

    def _getKeyMapping(self, channel):
        private_dict = channel.getPrivate()
        try:
            index_dict = private_dict['key_mapping']
        except KeyError:
            index_dict = private_dict['key_mapping'] = KEY_ROLE_TO_INDEX_DICT.copy()
        return index_dict

    def getPrivateKey(self, channel, role):
        return self._v_key_list[self._getKeyMapping(channel=channel)[role]]

    def getPrivateKeyTypeProperties(self, channel, role):
        return self.getData(
            tag=KEY_INDEX_TO_ATTRIBUTE_TAG_DICT[
                self._getKeyMapping(channel=channel)[role]
            ],
            decode=True,
        )

    def remapKey(self, channel, role, key_index):
        index_dict = self._getKeyMapping(channel=channel)
        if role not in index_dict:
            raise ValueError
        index_dict[role] = key_index

    def _getReferenceDataSet(self, index):
        secret = self.__reference_data_list[index]
        if secret is None:
            return ()
        return (secret, )

    def _setReferenceData(self, index, value):
        self.__reference_data_list[index] = self._encodeReferenceData(
            index=index,
            reference_data=value,
        )

    def _getReferenceDataTriesLeft(self, index):
        return self.__reference_data_counter_list[index]

    def _verify(self, index, reference_data, truncate=False):
        """
        Compare reference_data to stored secret at given index,
        optionally truncating reference_data to match stored secret.
        On success, return the length (in chars) of the stored secret.
        Otherwise, raises.
        """
        # XXX: store __reference_data_counter_list outside of ZODB ? this may
        # get a lot of history...
        if self.__reference_data_counter_list[index] == 0:
            raise AuthMethodBlocked
        secret_set = self._getReferenceDataSet(index=index)
        if not secret_set:
            raise ReferenceDataNotUsable
        self.__reference_data_counter_list[index] -= 1

        transaction_manager.commit()
        transaction_manager.begin()

        command_data = self._encodeReferenceData(
            index=index,
            reference_data=reference_data,
        )
        for secret in secret_set:
            if hmac.compare_digest(
                secret,
                bytes(
                    # XXX: does this allow a timing attack to guess secret's
                    # length ?
                    command_data[:len(secret)]
                    if truncate else
                    command_data
                ),
            ):
                break
        else:
            raise SecurityNotSatisfied
        self.__reference_data_counter_list[index] = VERIFICATION_DATA_VALIDITY

        transaction_manager.commit()
        transaction_manager.begin()

        if truncate:
            # Now that the password is verified, it should be fine to do
            # secret-length-dependent operations.
            return len(self._decodeReferenceData(
                index=index,
                command_data=secret,
            ))
        return None

    def _changeReferenceData(self, index, new_reference):
        new_reference_len = len(new_reference)
        if (
            (
                # Tolerate setting an empty reference for the reset code
                new_reference or
                index != RESET_CODE_INDEX
            ) and
            new_reference_len < self.__reference_min_length_list[index]
        ):
            raise WrongParameterInCommandData('Too short')
        if new_reference_len > 0x7f:
            raise WrongParameterInCommandData('Too long')
        self._setReferenceData(index=index, value=bytes(new_reference))
        self.__reference_data_counter_list[index] = (
            VERIFICATION_DATA_VALIDITY
            if new_reference else
            0
        )

    def verify(self, channel, level, command_data):
        try:
            index = REFERENCE_DATA_LEVEL_TO_LIST_OFFSET_DICT[level]
        except KeyError:
            raise WrongParametersP1P2 from None
        if command_data:
            try:
                self._verify(
                    index=index,
                    reference_data=self._decodeReferenceData(
                        index=index,
                        command_data=command_data,
                    ),
                )
            except APDUException:
                channel.clearUserAuthentication(level)
                raise
            else:
                channel.setUserAuthentication(level)
        else:
            if not channel.isUserAuthenticated(level=level):
                raise WarnPersistentChanged(
                    remaining=self.__reference_data_counter_list[index],
                ) from None

    def logout(self, channel, level, command_data):
        _ = command_data # Silence pylint.
        channel.clearUserAuthentication(level=level)

    def _decodeReferenceData(self, index, command_data):
        """
        Decode reference data for input validation and truncation.
        """
        if (
            self.__reference_max_length_list[index] &
            REFERENCE_DATA_LENGTH_FORMAT_PIN_BLOCK_2_MASK
        ):
            raise NotImplementedError
        else:
            return command_data

    def _encodeReferenceData(self, index, reference_data):
        """
        Encode reference data for internal storage.
        Must match the format reference data is received in when verifying, to
        avoid leaking timing information about secret length when verifying.
        """
        if (
            self.__reference_max_length_list[index] &
            REFERENCE_DATA_LENGTH_FORMAT_PIN_BLOCK_2_MASK
        ):
            raise NotImplementedError
        else:
            return reference_data

    def changeReferenceData(self, channel, new_only, level, command_data):
        _ = channel # Silence pylint.
        if new_only:
            raise WrongParametersP1P2
        try:
            index = REFERENCE_DATA_LEVEL_TO_LIST_OFFSET_DICT[level]
        except KeyError:
            raise WrongParametersP1P2('Unknown level') from None
        reference_data = self._decodeReferenceData(
            index=index,
            command_data=command_data,
        )
        new_reference = reference_data[self._verify(
            index=index,
            reference_data=reference_data,
            truncate=True,
        ):]
        self._changeReferenceData(
            index=index,
            new_reference=new_reference,
        )

    def _sign(self, channel, condensate, role):
        private_key = self.getPrivateKey(channel=channel, role=role)
        if isinstance(private_key, RSAPrivateKey):
            _, digest_info_dict, remainder = CodecBER.decode(
                value=condensate,
                schema={
                    RSADigestInfo.asTagTuple(): RSADigestInfo,
                },
            )
            if remainder:
                # XXX: dead code ? RSADigestInfo is a list type, and it would
                # complain.
                raise WrongParameterInCommandData('too many bytes')
            raw_condensate = digest_info_dict['condensate']
            # condensate must not be longer than 40% of the key modulus
            if len(raw_condensate) * 8 > private_key.key_size * .4:
                raise WrongParameterInCommandData(len(raw_condensate))
            oid = digest_info_dict['oid']
            if oid not in HASH_OID_DICT:
                raise WrongParameterInCommandData(oid.bytes())
            signature = private_key.sign(
                data=bytes(raw_condensate),
                padding=PKCS1v15(),
                algorithm=Prehashed(algorithm=HASH_OID_DICT[oid]()),
            )
        elif isinstance(private_key, EllipticCurvePrivateKey):
            r, s = decode_dss_signature(private_key.sign(
                data=bytes(condensate),
                signature_algorithm=ECDSA(Prehashed(
                    algorithm=HASH_LENGTH_TO_HASH_DICT[len(condensate)]()),
                ),
            ))
            field_axis_size = (private_key.curve.key_size + 7) // 8
            signature = (
                r.to_bytes(field_axis_size, 'big') +
                s.to_bytes(field_axis_size, 'big')
            )
        elif isinstance(private_key, Ed25519PrivateKey):
            # EDDSA support requires prehash (Ed25519ph) support, which is
            # missing in pyca.cryptography, and in turn is missing in openssl.
            # So even if below code "works", it does not produce usable
            # output.
            raise NotImplementedError
            signature = private_key.sign(
                data=bytes(condensate),
            )
        else:
            raise RecordNotFound
        return signature

# TODO: implement and advertise AES support
#    def _encrypt(self, channel, cleartext):

    @staticmethod
    def _getECPeerPublicKey(ciphertext):
        try:
            (
                _, # Cipher
                (
                    (
                        _, # PublicKeyComponents
                        (
                            (
                                public_key_component_tag,
                                peer_public_key,
                            ),
                        ),
                    ),
                ),
                remainder,
            ) = CodecBER.decode(
                value=ciphertext,
                schema={
                    Cipher.asTagTuple(): Cipher,
                },
            )
        except ValueError:
            raise WrongParameterInCommandData('structure error')
        if remainder:
            raise WrongParameterInCommandData('remainder: %r' % (remainder.hex(), ))
        if public_key_component_tag is not PublicKeyComponents.ECPublic:
            raise WrongParameterInCommandData('No ECPublic key provided')
        return bytes(peer_public_key)

    def _decrypt(self, channel, ciphertext):
        private_key = self.getPrivateKey(
            channel=channel,
            role=KEY_ROLE_DECRYPT,
        )
        if isinstance(private_key, RSAPrivateKey):
            if ciphertext[0] != 0: # 0 == RSA
                raise WrongParameterInCommandData(
                    'unexpected padding byte: %02x' % (
                        ciphertext[0],
                    ),
                )
            plaintext = private_key.decrypt(
                ciphertext=bytes(ciphertext[1:]),
                padding=PKCS1v15(),
            )
        elif isinstance(private_key, EllipticCurvePrivateKey):
            plaintext = private_key.exchange(
                algorithm=ECDH(),
                peer_public_key=EllipticCurvePublicKey.from_encoded_point(
                    curve=self.getPrivateKeyTypeProperties(
                        channel=channel,
                        role=KEY_ROLE_DECRYPT,
                    )['parameter_dict']['algo'](),
                    data=self._getECPeerPublicKey(ciphertext=bytes(ciphertext)),
                ),
            )
        elif isinstance(private_key, X25519PrivateKey):
            plaintext = private_key.exchange(
                peer_public_key=X25519PublicKey.from_public_bytes(
                    data=self._getECPeerPublicKey(ciphertext=bytes(ciphertext)),
                ),
            )
        else:
            raise RecordNotFound
        return plaintext

    def performSecurityOperation(
        self,
        channel,
        apdu_head,
        command_data,
        response_len,
    ):
        _ = response_len # Silence pylint.
        to_type = apdu_head.parameter1
        from_type = apdu_head.parameter2
        if (
            from_type == PERFORM_SECURITY_OPERATION_CONDENSATE and
            to_type == PERFORM_SECURITY_OPERATION_SIGNATURE
        ):
            channel.checkUserAuthentication(level=LEVEL_PW1_SIGN)
            result = self._sign(
                channel=channel,
                condensate=command_data,
                role=KEY_ROLE_SIGN,
            )
            self.__signature_counter += 1
            if not self.__pw1_valid_multiple_signatures:
                channel.clearUserAuthentication(level=LEVEL_PW1_SIGN)
        elif (
            from_type == PERFORM_SECURITY_OPERATION_CIPHERTEXT and
            to_type == PERFORM_SECURITY_OPERATION_CLEARTEXT
        ):
            channel.checkUserAuthentication(level=LEVEL_PW1_DECRYPT)
            result = self._decrypt(channel=channel, ciphertext=command_data)
        else:
            raise WrongParametersP1P2
        return result + SUCCESS

    def setSecurityEnvironment(
        self,
        channel,
        secure_messaging_command,
        secure_messaging_response,
        decipher,
        encipher,
        control_reference,
        control_reference_value_list,
    ):
        if (
            secure_messaging_command or
            secure_messaging_response or
            not decipher or
            encipher
        ):
            raise WrongParametersP1P2
        if control_reference == 0xa4:
            role = KEY_INDEX_AUTHENTICATE
        elif control_reference == 0xb8:
            role = KEY_INDEX_DECRYPT
        else:
            raise WrongParametersP1P2
        try:
            (tag, key_index), = control_reference_value_list
        except ValueError:
            raise WrongParameterInCommandData from None
        if tag is not FileIdentifier or key_index not in (
            KEY_INDEX_DECRYPT,
            KEY_INDEX_AUTHENTICATE,
        ):
            raise WrongParameterInCommandData
        self.remapKey(
            channel=channel,
            role=role,
            key_index=key_index,
        )

    def _maintainKeyQueue(self):
        """
        keygen thread main loop
        """
        semaphore = self._v_s_keygen_semaphore
        key_list = self._v_s_keygen_key_list
        key_attributes_list = self._v_s_algorithm_attributes_list
        retry = False
        while True:
            if retry:
                retry = False
            else:
                semaphore.acquire()
            for index, key in enumerate(key_list):
                if key is None:
                    attributes = key_attributes_list[index]
                    try:
                        before = time.time()
                        private_key = attributes.newKey()
                    except Exception: #pylint: disable=broad-except
                        logger.error('Error in keygen thread:', exc_info=1)
                        private_key = False
                    else:
                        logger.debug(
                            'keygen: produced key %i in %.2fs',
                            index,
                            time.time() - before,
                        )
                        if attributes != key_attributes_list[index]:
                            logger.debug(
                                'keygen: ...but parameters changed, discarding',
                            )
                            retry = True
                            continue
                    key_list[index] = private_key

    def generateAsymmetricKeyPair(self, channel, p1, p2, command_data):
        if p2:
            raise WrongParametersP1P2('p2=%02x' % (p2, ))
        try:
            # TODO: add support for extended format
            (tag, _), = list(CodecBER.iterDecode(
                value=command_data,
                schema=CONTROL_REFERENCE_SCHEMA,
            ))
            role = KEY_TAG_TO_ROLE_DICT[tag]
        except (ValueError, KeyError):
            raise WrongParameterInCommandData from None
        index = KEY_ROLE_TO_INDEX_DICT[role]
        if p1 == 0x80:
            channel.checkUserAuthentication(level=LEVEL_PW3)
            private_key = self._v_s_keygen_key_list[index]
            if private_key is None:
                raise ValueError('key not ready yet')
            if private_key is False:
                raise ValueError('key generation failed (unsupported format ?)')
            self._v_s_keygen_key_list[index] = None
            self._v_s_keygen_semaphore.release()
            self._storePrivateKey(
                role=role,
                key=private_key,
                information=KEY_INFORMATION_GENERATED_ON_CARD,
            )
        elif p1 == 0x81:
            private_key = self._v_key_list[index]
        else:
            raise WrongParametersP1P2('p1=%02x' % (p1, ))
        if isinstance(private_key, RSAPrivateKey):
            public_numbers = private_key.public_key().public_numbers()
            component_list = [
                (
                    PublicKeyComponents.RSAModulus,
                    public_numbers.n,
                ),
                (
                    PublicKeyComponents.RSAPublicExponent,
                    public_numbers.e,
                ),
            ]
        elif isinstance(private_key, EllipticCurvePrivateKey):
            component_list = [(
                PublicKeyComponents.ECPublic,
                private_key.public_key().public_bytes(
                    encoding=serialization.Encoding.X962,
                    format=serialization.PublicFormat.UncompressedPoint,
                ),
            )]
        elif isinstance(
            private_key,
            (
                Ed25519PrivateKey,
                X25519PrivateKey,
            ),
        ):
            component_list = [(
                PublicKeyComponents.ECPublic,
                private_key.public_key().public_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PublicFormat.Raw,
                ),
            )]
        else:
            raise ReferenceDataNotFound
        return CodecBER.encode(
            tag=PublicKeyComponents,
            value=component_list,
        ) + SUCCESS

    def internalAuthenticate(self, channel, p1, p2, command_data):
        if p1 or p2:
            raise WrongParametersP1P2('unhandled p1p2')
        if not command_data:
            raise WrongParameterInCommandData('no command_data')
        channel.checkUserAuthentication(level=LEVEL_PW1_DECRYPT)
        return self._sign(
            channel=channel,
            condensate=command_data,
            role=KEY_ROLE_AUTHENTICATE,
        )

    def resetRetryCounter(self, channel, p1, p2, command_data):
        if p2 != 0x81:
            raise WrongParametersP1P2('unhandled p2')
        # XXX: this is an implementation limitation: RC and PW1 should be
        # allowed to be in independent formats.
        assert (
            self.__reference_max_length_list[RESET_CODE_INDEX] &
            REFERENCE_DATA_LENGTH_FORMAT_PIN_BLOCK_2_MASK
        ) == (
            self.__reference_max_length_list[PW1_INDEX] &
            REFERENCE_DATA_LENGTH_FORMAT_PIN_BLOCK_2_MASK
        )
        reference_data = self._decodeReferenceData(
            index=PW1_INDEX,
            command_data=command_data,
        )
        if p1 == 0: # reference_data = Resetting code + new PW1
            new_reference = reference_data[self._verify(
                index=RESET_CODE_INDEX,
                reference_data=reference_data,
                truncate=True,
            ):]
        elif p1 == 2: # reference_data = new PW1
            channel.checkUserAuthentication(level=LEVEL_PW3)
            new_reference = reference_data
        else:
            raise WrongParametersP1P2('unhandled p1')
        self._changeReferenceData(
            index=PW1_INDEX,
            new_reference=new_reference,
        )

class PINQueueConnection(ZODB.Connection.Connection):
    """
    See OpenPGPRandomPassword.
    """
    def __init__(self, *args, **kw):
        self.__openpgp_kw = kw.pop('openpgp_kw')
        super().__init__(*args, **kw)

    def setstate(self, obj):
        super().setstate(obj)
        if isinstance(obj, OpenPGPRandomPassword):
            obj.setPinQueue(**self.__openpgp_kw)

class OpenPGPRandomPassword(OpenPGP):
    """
    OpenPGP smartcard application which does not use a constant PIN, but
    uses a PIN array, expected to be re-filled with random values anytime,
    of which a single cell contains the accepted PIN.

    This cell defaults to A1, but can be changed by a SET_REFERENCE_DATA call
    with a new pin referencing the desired cell. Ex: B30000 will use cell B3,
    2c0000 will use cell C2. Trailing zeroes are ignored, case is folded up.

    XXX: Makes the card incompatible with Key Derived Function for PW1, but
    there is no apparent way to disable KDF for a single PIN. So KDF is disabled
    entirely.

    When instances of this class are loaded from database, setPinQueue must be
    called with the deque instance to use as PIN source.
    Use PINQueueConnection as a ZODB connection class. Example:

        class DB(ZODB.DB.DB):
            klass = functools.partial(
                PINQueueConnection,
                openpgp_kw={
                    'pin_queue': some_deque,
                    'row_name_set': ('A', 'B', 'C'),
                    'column_name_set': ('1', '2', '3'),
                },
            )
        db = DB(
            storage=ZODB.FileStorage.FileStorage(
                file_name=self.__zodb_path,
            ),
            pool_size=1,
        )
    """

    # XXX: can this be made compatible ? Probably not, as the pi is likely not
    # capable of deriving the key fast enough.
    _has_key_derived_function = False

    def _getKeyDerivedFunction(self):
        raise RecordNotFound

    @property
    def _dynamicGetDataObjectDict(self):
        result = super()._dynamicGetDataObjectDict
        result[KeyDerivedFunction] = '_getKeyDerivedFunction'
        return result

    _v_pin_queue = None
    def setPinQueue(self, pin_queue, row_name_set, column_name_set):
        self._v_pin_queue = pin_queue
        self._v_row_name_set = row_name_set
        self._v_column_name_set = column_name_set

    def _getReferenceDataSet(self, index):
        secret = super()._getReferenceDataSet(index=index)
        if index == PW1_INDEX:
            cell_id, = secret
            cell_id = (
                'A1'
                if cell_id == DEFAULT_PW1 else
                cell_id.decode('utf-8')
            )
            secret = []
            while True:
                try:
                    item = self._v_pin_queue.popleft()
                except IndexError:
                    break
                secret.append(item[cell_id].encode('utf-8'))
        return secret

    def getPIN1TriesLeft(self):
        return self._getReferenceDataTriesLeft(index=PW1_INDEX)

    def _setReferenceData(self, index, value):
        if index == PW1_INDEX:
            try:
                value = value.tobytes().decode('utf-8')
            except UnicodeDecodeError:
                raise WrongParameterInCommandData(repr(value)) from None
            if set(value[2:]) != set(('0', )):
                raise WrongParameterInCommandData(repr(value))
            column = row = None
            for character in value[:2]:
                character = character.upper()
                if character in self._v_row_name_set:
                    row = character
                elif character in self._v_column_name_set:
                    column = character
                else:
                    raise WrongParameterInCommandData(repr(value))
            if None in (column, row):
                raise WrongParameterInCommandData(repr(value))
            value = (row + column).encode('utf-8')
        super()._setReferenceData(index=index, value=value)

    def getChallenge(self, channel, p1, p2, command_data, response_len):
        if p1 or p2 or command_data:
            raise WrongParameterInCommandData(
                'p1=%02x p2=%02x command_data=%s' % (
                    p1,
                    p2,
                    command_data.hex(),
                ),
            )
        return os.urandom(response_len) + SUCCESS

from ._version import get_versions
__version__ = get_versions()['version']
del get_versions
