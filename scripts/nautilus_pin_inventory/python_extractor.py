"""Fail-closed Python literal and governed-comparison pin extraction."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import tokenize
import unicodedata
from dataclasses import dataclass
from io import StringIO
from typing import Iterable

from .model import (
    DynamicGovernedCheck,
    GovernedRelation,
    Observation,
    PythonExtractionResult,
    SourceSpan,
)
from .registry import Registry


_TOKEN_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.+-")
_OBJECTS = frozenset({"policy", "manifest", "closure_manifest", "closure_policy"})
_FIELDS = {
    "engine_version": "engine_version",
    "engine_upstream_commit": "upstream_commit",
    "source_commit": "upstream_commit",
    "profile": "profile",
    "profile_manifest_schema_version": "closure_schema",
    "result_validator_id": "validator",
    "schema_version": "closure_schema",
    "semantic_profile": "semantic_profile",
}

_DOCUMENT_FIELDS = {
    "nautilus_engine_build_policy": _FIELDS,
    "nautilus_runtime_closure_policy": {**_FIELDS, "source_commit": "selected_source"},
    "nautilus_closure_manifest": {**_FIELDS, "source_commit": "selected_source"},
    "nautilus_base_runtime_manifest": _FIELDS,
}
_CONDITIONAL_ROOTS = frozenset({"specification", "expected_identity"})
_GOVERNED_ROOTS = _OBJECTS | _CONDITIONAL_ROOTS


@dataclass(frozen=True)
class _Endpoint:
    path: str
    qualified_scope: str
    root: str
    binding_kind: str
    document_kind: str


_ENDPOINTS = (
    _Endpoint(
        "scripts/materialize_nautilus_runtime_closure.py",
        "_validate_policy_bytes@422",
        "policy",
        "runtime_policy_json_object",
        "nautilus_runtime_closure_policy",
    ),
    _Endpoint(
        "scripts/materialize_nautilus_runtime_closure.py",
        "_validate_policy_bytes@422",
        "specification",
        "profile_specification_lookup",
        "nautilus_runtime_closure_policy",
    ),
    _Endpoint(
        "services/job_worker/nautilus_closure.py",
        "attest_nautilus_backtest_closure@515",
        "closure_manifest",
        "closure_manifest_read_json",
        "nautilus_closure_manifest",
    ),
    _Endpoint(
        "services/job_worker/nautilus_closure.py",
        "attest_nautilus_backtest_closure@515",
        "expected_identity",
        "profile_identity_lookup",
        "nautilus_closure_manifest",
    ),
)

_REVIEWED_NON_GOVERNED_COMPARISONS = frozenset({
    ('scripts/materialize_nautilus_runtime_closure.py', '_build_native_entry_guard@741', '2f520ee5814509ba2edb3584fe54ad78a7ecba06ac419d5ab41a9c6df023537a'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_build_native_entry_guard@741', '36addb363e2a63a83e0e6540007e48cc0ea3a0b1b972409141c996b9d44307eb'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_build_native_entry_guard@741', '48c12f9fc5707506dd2ff0c51d1ca9d9fff062b7d559ad541a236c533b8794a5'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_build_native_entry_guard@741', 'c2f1bf6c73c67bd190af261b41d36421b63130cda00c6e7040be8ea87861258d'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_build_native_entry_guard@741', 'fa43d69ea1c2acbfad1c45ec0fb3b8f211420dc2a52fc2fd5af6b2bc57cfe02c'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_build_output_manifest@1049', '20b303b03cecd5bf06f306a8f310eb67a43f9332830daf72e308e9c99f113be0'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_copy_file@919', 'ea946132a37ddc4e95406ac1d9b1568c945bc701d6a0044ec410543e23af1324'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_load_local_tool@320', '7d97ff53e44aedd52ecc1fa404e49ff1bd926b1b7f889108918dd55d0017f95a'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_load_local_tool@320', 'ed020301bfb7124c5d94c3ce75135caa070449c0e86115fe8f07b6c888a96005'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_publish_noreplace@975', '006317878104aa9d5a324219f6fcd7af422a2bf72e9fad16bf5e3f4df06d32b3'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_publish_noreplace@975', '0fb39a2701a8ddef31a833b518885c9ea45311a55be124fc70aabf15ac605ca8'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_publish_noreplace@975', '86b85d653f1eb3c800392c372c21b53844ceb766d87c05b5a682105e46c926de'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_publish_noreplace@975', 'b0ff6d5ebe3ee5b1e5adff4e962b91acd7f827e2431496dad0fe5ef84bf5afee'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_publish_noreplace@975', 'bcfac9dd4c432ac8b17d3c4d110ba8a78f6b6a6a573fdf07a4d226c61496d950'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_publish_noreplace@975', 'f6d5938c8543ae08dcbbf7f51ac86384231b0ebe172a9ff5db551e0b29e53bb0'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_read_file@200', '44378824943f72df2ec9d73dcea24bebfc4e95e70801b4601cc2ba847420d4d3'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_read_file@200', '86b85d653f1eb3c800392c372c21b53844ceb766d87c05b5a682105e46c926de'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_read_file@200', '956fee3a6cb0f1d6884ae31cfa2ab7d5391ebc543dc2f0ab15cd9575a189fcab'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_read_file@200', 'e0abdf95309f161037a2a373df605bddc0547b61f281e73e176e74283e9634d4'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_read_private_build_output@233', '86b85d653f1eb3c800392c372c21b53844ceb766d87c05b5a682105e46c926de'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_read_private_build_output@233', 'e0abdf95309f161037a2a373df605bddc0547b61f281e73e176e74283e9634d4'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_renameat2_noreplace@939', 'a1302b5eefcb24ec1b9d3cbe132d2a6ab309db0c68d1d1ff9de9338cb50e3a13'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_require_sha256@296', '596f0ef1b49c113317757d5d92291c5970de143e2ff6c57780c3fb7a59402d8c'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_safe_relative@302', '3701b1c0bc46a53020d3d89c3a203eb2711ca73fa4397bc05a9e2f8d6e9c48e3'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_safe_target@311', '68e2602e7f6795ec1b207708fe0d60b96999e7276f1dd6907e35548b9f734b22'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_safe_target@311', 'c2b9c5ed340f716fca4219ef44ae4f491afab24d9017a1bfd19e0a316da9c47d'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_sealed_directory@277', '026f9d53981d5ea04f215018f63bc2c2bd50298ba43086755fea2618585e563f'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_sealed_directory@277', '73587aa887ad42a7d4403b34ab4f9951a04974f3eeaf0d7c645721c1a3a08b43'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_sealed_directory@277', '86b85d653f1eb3c800392c372c21b53844ceb766d87c05b5a682105e46c926de'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_sealed_directory@277', 'c2b9c5ed340f716fca4219ef44ae4f491afab24d9017a1bfd19e0a316da9c47d'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_artifact_bytes@656', '0051bb991e217820b452e2b2d0b59369924b40f03521a79ad39bccad0da90e75'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_artifact_bytes@656', '2fe5ded9f3c8cc5199e4bfb2e8902dfcd57c0a924faf57c8e7484a851a58f0dc'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_artifact_bytes@656', '5926066852740414460ebb6828ea5f5c5bd982951a974c12f5d4de2d50584442'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_artifact_bytes@656', '734e33335d84e5bef1d095503cefc0e96f6127c5048111a114ff2a5c2bea45b1'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_artifact_bytes@656', '8138f71a8051485a2cff2b4b55d9bdc282345cf09b499968014c7e59fad2315e'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_artifact_bytes@656', '86c34738ff1565e3ab45b7267c85f85b5bc7dcfeb631ec0f350d1d89ccb347ad'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_artifact_bytes@656', '8fa9f829787e1236306b5d9a65142c31abb76c28e8d819df57f792bb0a73d8c1'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', '0449ca3d691081894ee1817d47a9f222b2a41779ff86bea9e2ab1b4d9e31f863'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', '9123be4bd64ef6379023e8f68cd9feea469a64344f281aefc360ea7cd20dfe34'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', '959d3c252ab41d619fd249c1b564437d0b6822a605a2777662b6bb1e79459062'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', '95fdf2e9f2d9d49df6846cd89ebed33201ea983e73f2bccbbf2c7ed4f440c9b1'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', 'b100f0c4dd1b9e38adb32f54143739218f80b2b2b5bdd9d1f30fa453b6588273'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', 'b1941501311429ad27a433099e06299d3038c60e43ae46e34ad3daf3a3d149a0'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', 'ccd39daa21fe024ef4fb40c4aba5225c2b829e0f9cf27b58ae9b4589a340bc1f'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', 'd692c2d6cb498b150ea513a5c877a3b5623ab3c17303203d3d4495f7552ae3e8'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', 'dec6d70767283273276e5310880b02e957630a66d093167e078fc870cc08f8d8'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', 'eee5051ac973e53a39201094e3bba6fe3cdb0cac7024d19e4cae6e1a33f29eef'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_path_inventory@546', '72d5a5c96454f541d187a0020dd84ba5532ffccef5d83cde5baf1386bf37b838'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_path_inventory@546', '73587aa887ad42a7d4403b34ab4f9951a04974f3eeaf0d7c645721c1a3a08b43'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', '0ea3a58701b04d536c0f889654e229aae2f769d7aaab147a817195147e8bcb4e'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', '3c58f38e1620cbf3aea560faf530c19c6756caeb577516d0a413ebb094d7a189'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', '6c9bb4f1e6a0d819e7b5b63f34d96d81fec364c3ec70e8791ebd04226336479f'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', '7ef99115026d981e326ccac0c0007e9f5c9433a25c363da517a7b5e75decf182'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', '81bc6acb811d5a3c8b3f39e9ef350f6a487f7521725cfe64935b1db3794470d9'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', '8bb30130aefac63cbedbb8c456cbcb78d29654992017ddbaaeea31ef09324af0'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', '9352b75adad4c1914aef74644a0a594261c1ea80be3e1a6881ecf050bda4d485'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', '9bd4a97301949dedad49d877c7045ffd9ca329b6578a9fd0c83ef96bffb2643a'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', 'be7e9bb41018f019b8c78a242119f222ce1251a570485a7a0005641a02b0c7b2'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', 'd0432f77f4199055731269ddd84efc84531439d65a969bbd0a5967b28be639d1'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', 'f00b0f85b2b06aadff9066558fdb5691b305bba706df92886bfb94615cb4185b'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', 'f0946899eac23d6f8f23d5ece81eb2547a253a490a3e2349cb468324823190a3'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', 'f6812371ddc4c67e78834116baf982919e8dca9a2c7b6fddd1bc6d95ca1abac8'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', '3c3eac977354f4e9bffe6b29296e5531f57ab2c51453ad252bbc9ebf2ebfb567'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', '7d97ff53e44aedd52ecc1fa404e49ff1bd926b1b7f889108918dd55d0017f95a'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', '812ad67cd8775370d897c7c3217b51fdf3b9e856bee2b33a1e0cf214a87f2f74'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', '95a24796bd11135c78d3f755f9c0c6a8dff9ea97250d53aae732d712b2f5e03f'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', 'b146f8e67c8af2fa96b9b38388501e0f8a6daf0e30c5e0b52e250caf93d9907e'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', 'bb793cf8e77ac25901eab29c0c13ef7d1dbd1ff03f6b13c9c131d79bfddcb129'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', 'e7f6508f8aec2df1096d50e86002cdfb630edbe2a5bc15fb96b2bedfa9ad7256'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', 'e8251a809b567e29d43d817d5abac958b556db791406dce9fc1bce0f3a334a18'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', 'e8800568b5d1555918bc8fc6e4bb97cee21638fdc293e33d6cd70bfa1fe11ec3'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_verify_native_guard_toolchains@691', '38c5d1e993fbb1ae314ab86d089cac646f5c868433ae2cbda0a261260ed645df'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_verify_native_guard_toolchains@691', '5f53a4e5eca5da88a0338deb32cb331ee8b74fdb7ba754abf16815a47203b3ad'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '02133d48b4bfc4c5bec829d5aef90969d19c3835f7658eb7ccb7d317c5f455ff'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '026f9d53981d5ea04f215018f63bc2c2bd50298ba43086755fea2618585e563f'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '09c2efba9fb8ecb69642a5fb7d6ae7517a0648a1e2f0c53d923ac28b244630f0'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '1287b2c67610323cb927ac6ca1bf879bfb742ebb2b839b2b8aa4a1333f749880'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '155b8f09e75d50bdb1d24cbbec1c19e0a173ba617d29f17d323e820fa9feeb71'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '223c268cf27bea6058b3e76273c23200dbc2a55e34fc0b1e427b63b51761192f'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '9142ce84282ed6c5910c416e51f32f7582eab94f531a00cbebc3784186695a81'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '99f267d40d713b810256615f3b651dcd4f3c42c9cc1caf60bdf0ecb035464fe4'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '9faab62d932842d3a8b6776f917ff813bc28a4819c519e5acd42b76e92e64144'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', 'a9803a1cc3cb8c0690ddc8f6cbaf84bcc324fc241d922b4d2c5fe324622a1d62'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', 'aa158e60951215881b919c3ab43d646866a34c34d6594428c97a83b12b3665e2'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', 'aad42027975b9583c293b369544d9d19ae67f2a5c089d9ed575796ed5ae6de8c'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', 'b100f0c4dd1b9e38adb32f54143739218f80b2b2b5bdd9d1f30fa453b6588273'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', 'b81bdce2b1c83d4cda3fc819f73a5d02a7b8a35196166e129347886219bc5340'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', 'bb207bb0a63a779af8986cb25e580b8322da385b67e8b1c7ff69eca050e4c1ee'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', 'c2b9c5ed340f716fca4219ef44ae4f491afab24d9017a1bfd19e0a316da9c47d'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', 'cb935e6be2c18a7aaeb38bdee8fc071640f2f7686c1f7f6496325e0fe54a59ef'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', 'ef332cca348f96638a72ff89bb5bdbf538fb482c60d87f92cf73bd418066ba08'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', 'f1b951e6fbee88bd3568fd1d72a653203957ebd09f474b983945af0ad4e9b280'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'read_base_file@523', '2b3eb058820b5ea6f4fcce154ae01eb709ae42ab7a4fe4f8bbc50a363d594369'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'read_base_file@523', 'd53b4514fdc11e3daa5f8839621f7f2bce33792627d9a64c8b0ed301bc6c87f1'),
    ('services/job_worker/nautilus_closure.py', '_closure_digest@343', '21934e4143168d8f61761fc41c8d0b31a2be686e4a14dbcda1e6984e707ae168'),
    ('services/job_worker/nautilus_closure.py', '_closure_digest@343', '7be737951f72bba53accb057bbc0fabb947d760720f3a49a339b0f7565b3a984'),
    ('services/job_worker/nautilus_closure.py', '_closure_digest@343', '8893186d9055df4c799d0b5f0f0f8ad7084dd409f96c85920b84f79d9b128af0'),
    ('services/job_worker/nautilus_closure.py', '_closure_digest@343', 'dae16d3192aaf02dafa422eecc19a4820a230420b8c9c67836dbea60a09f1219'),
    ('services/job_worker/nautilus_closure.py', '_closure_digest@343', 'e367135c245d2c358c6defd3e9590e188e2a6b18497d0a8f5bf7dacfe406cc2a'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', '00e1182367b0eb0e7b3a3b2c05ded910f1adce83e6130bbba3a45e7b01e9f500'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', '166caccff998a8859d2e48d078beb3e33c33508d2fc1c8c1e6eca9fc49662387'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', '1eb459e5f19cbf6e2156474746c95657a21ca50704e0a9e2e87d5d4cbf508bac'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', '1efea1da8a9e284f275b1729431e05f072e58f7074d891af7828d72b8d9c7afe'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', '685689b5d22b0258f1de8d3a76ca3a26e01caf48c1be4ca3ab63d59a0475e5f1'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', 'b33afb5ee407d5b1ce35dcd179da63c62cedf0d5e128d835ac10e87895d24e87'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', 'bbdda72e70327e3dce824ac4fa3d5a43c5a792943d323c48c1ec299667f33124'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', 'c653864794a539fa95fe246210d25d40bfede8e8f8b7df457ebacdfad1b45d13'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', 'c741438db8d3779f6ab8200b7f0d5105bc59a6cb2daeef4e3d67ac16d89b4839'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', 'ce0cd260ae40251512dc3cbe6d269d702f6bf43eb8d1b6bef0b9cfdaf2db1bd0'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', 'ee400e693052efbd51c2be2f7b6a2a1617e0409716eb4552f7e9ff3331bd3f7d'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '0c358f1539e18dd59095701b8bc24053f9363ceb4a211bccd4cba169abf4cf22'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '13eccf2b138ba69788e461c52a1c7a6f778342212a20480f8c6f305d87f4d5b7'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '21934e4143168d8f61761fc41c8d0b31a2be686e4a14dbcda1e6984e707ae168'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '21c069a33fbd5887ecec7b4eb53130972681cef780cb6989e36a3759beb0886c'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '3dcf96b3a4d9ddd0446e55709006579cca9e3a1729883bff3af32c739101e114'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '46f7e948d1a98ac8266c79dfa095e2b40266fd264ccdfb61754d357300f27cc2'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '4c22c5b510cef5141606732cf1e2132d9d1f69e113bcda5b501acecdc5800984'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '4eb07fd822584e11f68a609c2a5435f3ca41cec5d8112bbb249eea3e2e7094be'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '5f86830feee13b483dfee543b00d66a407d2c75a95166d94ddfd101c52a61ba5'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '6ace4240cdfbe3cae395c239178edd73ee6c08f89cf5883b9fc9aa67f29c9b26'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '7e9621cd3e67092e955e6b5baa53277d8df3ba927ebe607f24fa1f19b32ca3d3'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '8831a21e5d99c009076113719c0cc76d665ac47e1c6dac6111de6ec51b5cc350'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '8893186d9055df4c799d0b5f0f0f8ad7084dd409f96c85920b84f79d9b128af0'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '8a7616502f40aea45446215fd8bd7c42f847d740a1d579cf2463edaa7cc63db9'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'b0c0a118df7d2106c2d024bebcc366d8396c0a21559fc63a7a65180dd79f6831'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'b4a3ecfcf186a99be9e9a08687f263213e8d07574e08d7786614187827d5620f'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'b85337e85f009da5c32640d2e4a6f102b34a7409cce94413b0f1d32e04ab534d'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'b9b023ef92aaf21f5b719c6a20cde1bbb427bfd3226a359403bf522e3bf181b6'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'bbda9ba2a4e54c4ee5ebadd03aa1747b3a20716838f20942c181aa3426c0bc01'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'bed58bbab351864b1ade9f3d603372b60104e56146899288224a88f43fc343d7'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'c80cadfda1d2e9456a6112e32b411efb5422ed98864a23a5cbb6c70baf356cc5'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'e367135c245d2c358c6defd3e9590e188e2a6b18497d0a8f5bf7dacfe406cc2a'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'e7e986e1db3e09dc2ba78ea9203d671781318eb400664b947c2d66de48d16c44'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'ef4549fe2f8e1a31bd44e4405a39fa9b8c06da2f7f1918d072e5e0d757b0d626'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'f5fce7c5d93e17178980c80f7a1fbbbb335f544da4d0170d3b286139ef5f9df6'),
    ("scripts/materialize_nautilus_runtime_closure.py", "_validate_policy_bytes@422", "de09224dbc77f3afba6f6e185eb9f91c1db8845d4a84fb16785934798f9c46e4"),
    ("scripts/materialize_nautilus_runtime_closure.py", "_validate_policy_bytes@422", "a3dbd3c7709999775bb4cc8cfadebeaf2937d6c4c4cd80f1152ac61d5a14d877"),
    ("scripts/materialize_nautilus_runtime_closure.py", "_validate_policy_bytes@422", "debed322d69944217766e11fc9f7b4059371773a3bdb4c102e85eddca64355ce"),
    ("scripts/materialize_nautilus_runtime_closure.py", "_validate_policy_bytes@422", "2781413285a4633479d8fcd015188d76d67d243d07bd6579073dd8e390ba530a"),
    ("scripts/materialize_nautilus_runtime_closure.py", "_validate_policy_bytes@422", "6aa07ee3e32b0a940fc2e89015428b2b603e177422f999990f8e6249933c6c34"),
    ("scripts/materialize_nautilus_runtime_closure.py", "_validate_policy_bytes@422", "7cbd66ccdc6e90b9e870d128acd18a77522fee7b62f9cc9521bea446ca50a64f"),
    ("scripts/materialize_nautilus_runtime_closure.py", "_validate_policy_bytes@422", "dd979afa4086730754d40a30fac78845bfb972293922c6f67c3bb6c0c9a61f06"),
    ("scripts/materialize_nautilus_runtime_closure.py", "_validate_policy_bytes@422", "c1e4de7659adb9ef5c59927dbdee07fb0ffa81a74a65ab4270ad392ae611cc12"),
    ("scripts/materialize_nautilus_runtime_closure.py", "_validate_policy_bytes@422", "f3e7cdc9cd0c4e5eb962ef7a8a1fa98cb52385732f30491b78f886ec3e64d59d"),
    ("scripts/materialize_nautilus_runtime_closure.py", "_validate_base_runtime_bytes@566", "4498fe88c5890e81dd3f37d0abf1b0ca515ad1d801d1f57e63d4031735857f3b"),
    ("scripts/materialize_nautilus_runtime_closure.py", "_validate_base_runtime_bytes@566", "4fe719c05253c86235f6e095e9ee6937a54c29dd0e6773098d1d92b5701e4583"),
    ("scripts/materialize_nautilus_runtime_closure.py", "_validate_base_runtime_bytes@566", "3846b906346321fe672c4707c89461b4923b39b2590869fffe1607e91673c092"),
    ("scripts/materialize_nautilus_runtime_closure.py", "_validate_base_runtime_bytes@566", "d57e1c92d58bb5659b8af468cec73b5cc5b3a0a3515ed53b4fa091f7811a2bc6"),
    ("scripts/materialize_nautilus_runtime_closure.py", "_validate_base_runtime_bytes@566", "3b3f2e251e948241835bd3696af60970e5f1ac79041b26140c44948e5fc1b15a"),
    ("scripts/materialize_nautilus_runtime_closure.py", "_validate_base_runtime_bytes@566", "c1632ae04178ceb7c6e89ecf3447956f397890bd0e19a71fddd45fbecf506e44"),
    ("scripts/materialize_nautilus_runtime_closure.py", "_validate_base_runtime_bytes@566", "e18f323fc09afea8edf41064437ba39938d32e608d6f6207a8c5224c5ea268a8"),
    ("scripts/materialize_nautilus_runtime_closure.py", "_validate_artifact_bytes@656", "62dac560edba056fa56365416ce8e011f52a6c3cbe15081b2071c9e35c064d42"),
    ("scripts/materialize_nautilus_runtime_closure.py", "_validate_artifact_bytes@656", "2143bb5cee32d6173acf3b5e27d9a27d475ae4fd17bad355ca6c0c113b5a960a"),
    ("scripts/materialize_nautilus_runtime_closure.py", "_validate_artifact_bytes@656", "dd61e05ee3ce8d204cf020dd83787f7a0121873e8f4a989ca91441ed49ac1941"),
    ("scripts/materialize_nautilus_runtime_closure.py", "materialize_runtime_closure@1084", "1ed850b900245f77c1df99dc3a54c9eaca44cb8a072eba4b968bc35ee028f0ef"),
    ("scripts/materialize_nautilus_runtime_closure.py", "materialize_runtime_closure@1084", "b3da90b0c9e6de912e6b0e0c86083af1bfcb83996e9744f41041ff4bf9b11b67"),
    ("services/job_worker/nautilus_closure.py", "attest_nautilus_backtest_closure@515", "826b5c8c4fb5d42e608a2d7074250683605f3cb4043edf3df9842e12d27b09e2"),
    ("services/job_worker/nautilus_closure.py", "attest_nautilus_backtest_closure@515", "b4475481e8d369e80f82fbc998d1e614f7f1116cacadf9a10da2b4eee4f652eb"),
    ("services/job_worker/nautilus_closure.py", "attest_nautilus_backtest_closure@515", "c38f2a5f5d573abf365fdcb3f1679f586b696e7af12f31f2746fbdb5158fb001"),
    ("services/job_worker/nautilus_closure.py", "attest_nautilus_backtest_closure@515", "32f774098f99ab314b758727d941310179e513ad5c273ec4f19591f6a3e7a2af"),
    ("services/job_worker/nautilus_closure.py", "attest_nautilus_backtest_closure@515", "ee095e7fcfc1fa2a65ae57d2cc7c2cb66885bb14d4c1dea00ab2f57107894e59"),
    ("services/job_worker/nautilus_closure.py", "attest_nautilus_backtest_closure@515", "23c2e05634d0ad9cabf71c03521a11bf65c374b4980d1388439f1159ec401d6e"),
    ("services/job_worker/nautilus_closure.py", "attest_nautilus_backtest_closure@515", "60fd447d4ed4b5265aefad7e896ee3f73d37243d6eeca7bd92c44151e8515a03"),
    ("services/job_worker/nautilus_closure.py", "attest_nautilus_backtest_closure@515", "da0e95125121492c8e09a55b45095e44ce232c127665fbc13261ef2f4028b3db"),
    ("services/job_worker/nautilus_closure.py", "attest_nautilus_backtest_closure@515", "04cc1b8a739ff7ca2dbe0f19cdd96429231de41b52cfab7ce9c3132c2af87cc0"),
    ("services/job_worker/nautilus_closure.py", "attest_nautilus_backtest_closure@515", "e5682c25c0d61bbb80cb661a4241cf062fb51a656163a2581c23d24c11aab673"),
    ("services/job_worker/nautilus_closure.py", "attest_nautilus_backtest_closure@515", "94f7a107f30a44331d8231de8d1eedcfe75bfeff01898e1c30cefb4a83a583ea"),
    ("services/job_worker/nautilus_closure.py", "attest_nautilus_backtest_closure@515", "4a0a635bd02436f1d0dd8cdcaa406343917a647f289ead69118e55ee1641e39d"),
})
_REVIEWED_SAFE_GOVERNED_CALLS = frozenset({
    "3d4ad19082532002014a744ddedddd6cd333c3f5d1987cb0331f58e1435f9514",
    "b475289b7031085c6c7a6e4daa894fc1d1a5b9ac62b4af2bdaec01fe7fefc9f6",
    "923364943e3c73f401260a5ee1c6bb088143a8d26bdee6d3502048f3fa2d598f",
    "8b61eb4c9496de44d19c9a0134f02ec6f3c95d82b6e4b82f1080e2e809bc7d6b",
    "b62aff658e6b5f31494880da9c4168c4e015497422398842bd09a9a1875534f5",
    "db1ffe2b0393bc2f6352bc5772e21c4f887b83dcbb539b87960051ddb5031440",
    "e089721330353b0aad5d0d23e843b2c3ebdc46f0bce2f89fbff627bbbd59a570",
    "0bdde0c0ade606511c4bed54e50a172edd099c3f87f0ae26a1c5a4a30a06d9b9",
    "5d53f222f72ad5c6a17241e5a7127aba102054808b6395ffce56f2be7a0bade9",
    "7d2184f5327f799ee94d388cd563b12c5eb8e090b77a7c3da20806c5c9f17b2c",
})


class PythonExtractionError(ValueError):
    """A governed Python expression cannot be given an exact, safe citation."""


@dataclass(frozen=True)
class _Origin:
    start: int
    end: int


@dataclass(frozen=True)
class _Literal:
    value: str | int | float
    origins: tuple[_Origin, ...]
    dynamic: bool = False


@dataclass(frozen=True)
class _StringToken:
    start: int
    end: int
    literal: _Literal


@dataclass(frozen=True)
class _Binding:
    kind: str
    value: _Literal | str | None


@dataclass(frozen=True)
class _BindingEvent:
    value: ast.AST | None
    top_level_simple: bool


def _invalid() -> PythonExtractionError:
    return PythonExtractionError("invalid governed Python expression")


def _offsets(text: str) -> tuple[int, ...]:
    starts = [0]
    for index, character in enumerate(text):
        if character == "\n":
            starts.append(index + 1)
    return tuple(starts)


def _offset(line_starts: tuple[int, ...], line: int, column: int) -> int:
    return line_starts[line - 1] + column


def _ast_offset(text: str, line_starts: tuple[int, ...], line: int, byte_column: int) -> int:
    """Convert CPython AST UTF-8 byte columns to character offsets."""
    start = line_starts[line - 1]
    end = text.find("\n", start)
    fragment = text[start : len(text) if end < 0 else end]
    consumed = 0
    for index, character in enumerate(fragment):
        if consumed == byte_column:
            return start + index
        consumed += len(character.encode("utf-8"))
    if consumed == byte_column:
        return start + len(fragment)
    raise _invalid()


def _position(text: str, offset: int) -> tuple[int, int]:
    line = text.count("\n", 0, offset) + 1
    line_start = text.rfind("\n", 0, offset) + 1
    return line, offset - line_start + 1


def _decode_escaped(content: str, base: int, *, raw: bool) -> _Literal:
    characters: list[str] = []
    origins: list[_Origin] = []
    index = 0
    while index < len(content):
        if raw or content[index] != "\\":
            characters.append(content[index])
            origins.append(_Origin(base + index, base + index + 1))
            index += 1
            continue
        start = index
        index += 1
        if index == len(content):
            raise _invalid()
        marker = content[index]
        if marker == "\n":
            index += 1
            continue
        if marker == "\r" and index + 1 < len(content) and content[index + 1] == "\n":
            index += 2
            continue
        if marker == "x":
            width = 4
        elif marker == "u":
            width = 6
        elif marker == "U":
            width = 10
        elif marker in "01234567":
            digits = 1
            while digits < 3 and index + digits < len(content) and content[index + digits] in "01234567":
                digits += 1
            width = 1 + digits
        elif marker == "N":
            closing = content.find("}", index + 2)
            if index + 1 >= len(content) or content[index + 1] != "{" or closing < 0:
                raise _invalid()
            width = closing - start + 1
        elif marker in "\\'\"abfnrtv":
            width = 2
        else:
            raise _invalid()
        try:
            if marker == "x":
                decoded = chr(int(content[index + 1 : start + width], 16))
            elif marker == "u" or marker == "U":
                decoded = chr(int(content[index + 1 : start + width], 16))
            elif marker in "01234567":
                decoded = chr(int(content[index : start + width], 8))
            elif marker == "N":
                decoded = unicodedata.lookup(content[index + 2 : start + width - 1])
            else:
                decoded = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}.get(marker, marker)
        except (KeyError, ValueError):
            raise _invalid() from None
        if len(decoded) != 1:
            raise _invalid()
        characters.append(decoded)
        origins.append(_Origin(base + start, base + start + width))
        index = start + width
    return _Literal("".join(characters), tuple(origins))


def _literal_string(token: tokenize.TokenInfo, line_starts: tuple[int, ...]) -> _StringToken:
    source = token.string
    match = re.match(r"(?i)([rubf]*)('''|\"\"\"|'|\")", source)
    if match is None:
        raise _invalid()
    prefix, quote = match.groups()
    if source[-len(quote) :] != quote:
        raise _invalid()
    raw = "r" in prefix.casefold()
    formatted = "f" in prefix.casefold()
    start = _offset(line_starts, token.start[0], token.start[1])
    content_start = start + len(prefix) + len(quote)
    content = source[len(prefix) + len(quote) : -len(quote)]
    if not formatted:
        decoded = _decode_escaped(content, content_start, raw=raw)
        end = _offset(line_starts, token.end[0], token.end[1])
        return _StringToken(start, end, decoded)

    # This bounded scanner intentionally records only literal f-string segments.
    # Any interpolation makes the complete expression unusable as a governed value.
    characters: list[str] = []
    origins: list[_Origin] = []
    dynamic = False
    index = 0
    segment_start = 0
    while index < len(content):
        if content[index] not in "{}":
            index += 1
            continue
        if index + 1 < len(content) and content[index + 1] == content[index]:
            segment = _decode_escaped(content[segment_start:index], content_start + segment_start, raw=raw)
            characters.extend(segment.value)
            origins.extend(segment.origins)
            characters.append(content[index])
            origins.append(_Origin(content_start + index, content_start + index + 2))
            index += 2
            segment_start = index
            continue
        segment = _decode_escaped(content[segment_start:index], content_start + segment_start, raw=raw)
        characters.extend(segment.value)
        origins.extend(segment.origins)
        dynamic = True
        depth = 1
        index += 1
        while index < len(content) and depth:
            if content[index] == "{":
                depth += 1
            elif content[index] == "}":
                depth -= 1
            index += 1
        if depth:
            raise _invalid()
        segment_start = index
    segment = _decode_escaped(content[segment_start:], content_start + segment_start, raw=raw)
    characters.extend(segment.value)
    origins.extend(segment.origins)
    end = _offset(line_starts, token.end[0], token.end[1])
    return _StringToken(start, end, _Literal("".join(characters), tuple(origins), dynamic))


def _string_tokens(text: str) -> tuple[_StringToken, ...]:
    line_starts = _offsets(text)
    try:
        tokens = tokenize.generate_tokens(StringIO(text).readline)
        return tuple(_literal_string(token, line_starts) for token in tokens if token.type == tokenize.STRING)
    except (tokenize.TokenError, IndentationError):
        raise _invalid() from None


def _span(path: str, text: str, origins: Iterable[_Origin]) -> SourceSpan:
    items = tuple(origins)
    if not items:
        raise _invalid()
    start_line, start_column = _position(text, items[0].start)
    end_line, end_column = _position(text, items[-1].end)
    return SourceSpan.content(path, start_line, start_column, end_line, end_column)


def _token_ranges(value: str) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < len(value):
        if value[start] not in _TOKEN_CHARACTERS:
            start += 1
            continue
        end = start + 1
        while end < len(value) and value[end] in _TOKEN_CHARACTERS:
            end += 1
        ranges.append((start, end))
        start = end
    return tuple(ranges)


def _raw_access(node: ast.AST) -> str | None:
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in _OBJECTS:
        if isinstance(node.slice, ast.Constant) and type(node.slice.value) is str:
            return _FIELDS.get(node.slice.value)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in _OBJECTS
        and len(node.args) == 1
        and not node.keywords
        and isinstance(node.args[0], ast.Constant)
        and type(node.args[0].value) is str
    ):
        return _FIELDS.get(node.args[0].value)
    return None


def _governed_like(node: ast.AST) -> bool:
    """Recognize every attempted governed access, including dynamic keys we reject."""
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in _OBJECTS:
        return not (isinstance(node.slice, ast.Constant) and type(node.slice.value) is str and node.slice.value not in _FIELDS)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and isinstance(node.func.value, ast.Name) and node.func.value.id in _OBJECTS:
        return not (node.args and isinstance(node.args[0], ast.Constant) and type(node.args[0].value) is str and node.args[0].value not in _FIELDS)
    return _raw_access(node) is not None


class PythonExtractor:
    """Extract exact string-literal pins and closed governed policy comparisons."""

    __slots__ = ("_registry",)

    def __init__(self, registry: Registry) -> None:
        if type(registry) is not Registry:
            raise ValueError("extractor registry must be a Registry")
        self._registry = registry

    def extract(self, path: str, text: str) -> PythonExtractionResult:
        if type(path) is not str or type(text) is not str:
            raise ValueError("Python extraction path and text must be strings")
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError:
            raise _invalid() from None
        tokens = _string_tokens(text)
        bindings, invalid_names, invalid_governed_names = self._bindings(tree, tokens, text)
        observations = set(self._literal_observations(path, text, tokens, tree))
        dynamic_guards: set[DynamicGovernedCheck] = set()
        governed_relations: set[GovernedRelation] = set()
        parents = {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                evidence = self._governed_evidence(path, text, tree, node, parents)
                if evidence is not None:
                    guard, relation = evidence
                    if guard is not None:
                        dynamic_guards.add(guard)
                    if relation is not None:
                        governed_relations.add(relation)
                    continue
                if self._reviewed_non_governed_comparison(path, tree, node):
                    continue
                observations.update(self._comparison_observations(path, text, node, tokens, bindings, invalid_names, invalid_governed_names))
        return PythonExtractionResult(
            tuple(sorted(observations, key=self._sort_key)),
            tuple(sorted(dynamic_guards, key=self._dynamic_sort_key)),
            tuple(sorted(governed_relations, key=self._relation_sort_key)),
        )

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=False).encode("utf-8")

    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return hashlib.sha256(cls._canonical(value)).hexdigest()

    @staticmethod
    def _scope(tree: ast.Module, node: ast.AST) -> ast.AST:
        candidates = [
            candidate for candidate in ast.walk(tree)
            if isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            and candidate.lineno <= node.lineno <= candidate.end_lineno
        ]
        return max(candidates, key=lambda candidate: candidate.lineno) if candidates else tree

    @staticmethod
    def _scope_bindings(scope: ast.AST) -> dict[str, ast.AST]:
        """Return single-assignment direct bindings in one lexical scope."""
        values: dict[str, list[ast.AST]] = {}
        deleted: set[str] = set()
        for item in ast.walk(scope):
            if item is not scope and isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        values.setdefault(target.id, []).append(item.value)
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and item.value is not None:
                values.setdefault(item.target.id, []).append(item.value)
            elif isinstance(item, ast.Name) and isinstance(item.ctx, ast.Del):
                deleted.add(item.id)
        return {name: entries[0] for name, entries in values.items() if len(entries) == 1 and name not in deleted}

    @staticmethod
    def _binding_kind(root: str, value: ast.AST) -> str | None:
        if (
            root == "policy"
            and isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "_json_object"
            and len(value.args) == 1
            and isinstance(value.args[0], ast.Name)
            and value.args[0].id == "raw"
            and len(value.keywords) == 1
            and value.keywords[0].arg == "label"
            and isinstance(value.keywords[0].value, ast.Constant)
            and value.keywords[0].value.value == "runtime closure policy"
        ):
            return "runtime_policy_json_object"
        if (
            root == "closure_manifest"
            and isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "_read_json"
            and len(value.args) == 2
            and not value.keywords
            and isinstance(value.args[0], ast.BinOp)
            and isinstance(value.args[0].left, ast.Attribute)
            and isinstance(value.args[0].left.value, ast.Name)
            and value.args[0].left.value.id == "config"
            and value.args[0].left.attr == "runtime_root"
            and isinstance(value.args[0].op, ast.Div)
            and isinstance(value.args[0].right, ast.Name)
            and value.args[0].right.id == "_MANIFEST_NAME"
            and isinstance(value.args[1], ast.Constant)
            and value.args[1].value == "closure manifest"
        ):
            return "closure_manifest_read_json"
        if (
            root == "specification"
            and isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and isinstance(value.func.value, ast.Name)
            and value.func.value.id == "_PROFILE_SPECS"
            and value.func.attr == "get"
            and len(value.args) == 1
            and not value.keywords
            and isinstance(value.args[0], ast.Call)
            and isinstance(value.args[0].func, ast.Name)
            and value.args[0].func.id == "str"
            and len(value.args[0].args) == 1
            and isinstance(value.args[0].args[0], ast.Name)
            and value.args[0].args[0].id == "profile"
            and not value.args[0].keywords
        ):
            return "profile_specification_lookup"
        if root == "expected_identity" and isinstance(value, ast.Subscript) and isinstance(value.value, ast.Name) and value.value.id == "_PROFILES" and isinstance(value.slice, ast.Name) and value.slice.id == "profile":
            return "profile_identity_lookup"
        return None

    @staticmethod
    def _direct_access(node: ast.AST) -> tuple[str, str] | None:
        if not isinstance(node, ast.Subscript) or not isinstance(node.value, ast.Name) or node.value.id not in _GOVERNED_ROOTS:
            return None
        if not isinstance(node.slice, ast.Constant) or type(node.slice.value) is not str:
            return None
        return node.value.id, node.slice.value

    @staticmethod
    def _qualified_scope(scope: ast.AST) -> str:
        return f"{getattr(scope, 'name', '<module>')}@{getattr(scope, 'lineno', 1)}"

    @classmethod
    def _reviewed_non_governed_comparison(cls, path: str, tree: ast.Module, node: ast.Compare) -> bool:
        scope = cls._scope(tree, node)
        shape = ast.dump(node, annotate_fields=True, include_attributes=False).encode("utf-8")
        return (path, cls._qualified_scope(scope), hashlib.sha256(shape).hexdigest()) in _REVIEWED_NON_GOVERNED_COMPARISONS

    @staticmethod
    def _target_base(target: ast.AST) -> ast.Name | None:
        while isinstance(target, (ast.Attribute, ast.Subscript)):
            target = target.value
        return target if isinstance(target, ast.Name) else None

    @classmethod
    def _scope_binding_is_proved(cls, tree: ast.Module, scope: ast.AST, root: str, value: ast.AST) -> bool:
        parents = {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

        def exposes_root(node: ast.AST) -> bool:
            if isinstance(node, ast.Name):
                return node.id == root
            if isinstance(node, ast.Subscript):
                return False
            if isinstance(node, ast.Attribute):
                return exposes_root(node.value)
            if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                return any(exposes_root(item) for item in node.elts)
            if isinstance(node, ast.Dict):
                return any(exposes_root(item) for item in (*node.keys, *node.values) if item is not None)
            if isinstance(node, ast.Starred):
                return exposes_root(node.value)
            return False

        def safe_call(node: ast.Call) -> bool:
            shape = ast.dump(node, annotate_fields=True, include_attributes=False).encode("utf-8")
            return hashlib.sha256(shape).hexdigest() in _REVIEWED_SAFE_GOVERNED_CALLS

        def unsafe_receiver_or_escape(node: ast.AST) -> bool:
            if isinstance(node, ast.Attribute) and exposes_root(node.value):
                parent = parents.get(id(node))
                return not (isinstance(parent, ast.Call) and parent.func is node and safe_call(parent))
            return isinstance(node, ast.Call) and any(
                exposes_root(argument) for argument in (*node.args, *(keyword.value for keyword in node.keywords))
            ) and not safe_call(node)

        approved_target: ast.Name | None = None
        for node in ast.walk(scope):
            if isinstance(node, ast.Assign) and node.value is value and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == root:
                approved_target = node.targets[0]
                break
            if isinstance(node, ast.AnnAssign) and node.value is value and isinstance(node.target, ast.Name) and node.target.id == root:
                approved_target = node.target
                break
        if approved_target is None:
            return False
        for node in ast.walk(scope):
            if isinstance(node, ast.Name) and node.id == root and isinstance(node.ctx, (ast.Store, ast.Del)) and node is not approved_target:
                return False
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                target = node.target if not isinstance(node, ast.Assign) else node.targets[0] if len(node.targets) == 1 else None
                base = cls._target_base(target) if target is not None else None
                if base is not None and base.id == root and target is not approved_target:
                    return False
                if isinstance(node.value, ast.Name) and node.value.id == root and target is not approved_target:
                    return False
            if unsafe_receiver_or_escape(node):
                return False
            if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                if any(item.id == root for item in ast.walk(node.target) if isinstance(item, ast.Name)):
                    return False
            if isinstance(node, (ast.With, ast.AsyncWith)):
                if any(item.optional_vars is not None and any(name.id == root for name in ast.walk(item.optional_vars) if isinstance(name, ast.Name)) for item in node.items):
                    return False
            if isinstance(node, ast.ExceptHandler) and node.name == root:
                return False
            if isinstance(node, (ast.Import, ast.ImportFrom)) and any((alias.asname or alias.name.split(".")[0]) == root for alias in node.names):
                return False
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == root:
                return False
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                arguments = node.args
                if any(argument.arg == root for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)):
                    return False
                if (arguments.vararg is not None and arguments.vararg.arg == root) or (arguments.kwarg is not None and arguments.kwarg.arg == root):
                    return False
        def module_nodes(node: ast.AST):
            yield node
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                    continue
                yield from module_nodes(child)

        approved_module_target = approved_target if scope is tree else None
        for node in module_nodes(tree):
            if isinstance(node, ast.Name) and node.id == root and isinstance(node.ctx, (ast.Store, ast.Del)) and node is not approved_module_target:
                return False
            if unsafe_receiver_or_escape(node):
                return False
        return True

    @classmethod
    def _mapping_origin_is_proved(cls, tree: ast.Module, name: str) -> bool:
        matches = [
            statement
            for statement in tree.body
            if isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and statement.targets[0].id == name
            and isinstance(statement.value, ast.Dict)
        ]
        return len(matches) == 1 and cls._scope_binding_is_proved(tree, tree, name, matches[0].value)

    @classmethod
    def _binding_fingerprint(
        cls,
        path: str,
        qualified_scope: str,
        bindings: Iterable[tuple[str, str, str, str]],
    ) -> str:
        return cls._fingerprint([path, qualified_scope, [list(binding) for binding in sorted(bindings)]])

    def _endpoint(
        self,
        path: str,
        tree: ast.Module,
        node: ast.AST,
        access: tuple[str, str],
    ) -> tuple[str, str, str, str, str, str] | None:
        root, field = access
        scope = self._scope(tree, node)
        qualified_scope = self._qualified_scope(scope)
        candidates = tuple(endpoint for endpoint in _ENDPOINTS if endpoint.path == path and endpoint.qualified_scope == qualified_scope and endpoint.root == root)
        if candidates and scope not in tree.body:
            raise _invalid()
        if not candidates:
            bindings = self._scope_bindings(scope)
            value = bindings.get(root)
            if value is not None and self._binding_kind(root, value) is not None and any(endpoint.path == path and endpoint.root == root for endpoint in _ENDPOINTS):
                raise _invalid()
            return None
        bindings = self._scope_bindings(scope)
        value = bindings.get(root)
        binding_kind = self._binding_kind(root, value) if value is not None else None
        endpoint = next((item for item in candidates if item.binding_kind == binding_kind), None)
        if endpoint is None or value is None or not self._scope_binding_is_proved(tree, scope, root, value):
            raise _invalid()
        if root == "specification" and not self._mapping_origin_is_proved(tree, "_PROFILE_SPECS"):
            raise _invalid()
        if root == "expected_identity" and not self._mapping_origin_is_proved(tree, "_PROFILES"):
            raise _invalid()
        family = _DOCUMENT_FIELDS[endpoint.document_kind].get(field)
        if family is None:
            return None
        return root, endpoint.document_kind, field, family, endpoint.binding_kind, ast.dump(value, annotate_fields=True, include_attributes=False)

    @staticmethod
    def _node_span(path: str, text: str, node: ast.AST) -> SourceSpan:
        starts = _offsets(text)
        start = _ast_offset(text, starts, node.lineno, node.col_offset)
        end = _ast_offset(text, starts, node.end_lineno, node.end_col_offset)
        return _span(path, text, (_Origin(start, end),))

    @staticmethod
    def _terminal_failure(node: ast.Compare, parents: dict[int, ast.AST]) -> bool:
        current: ast.AST = node
        while id(current) in parents:
            current = parents[id(current)]
            if isinstance(current, ast.If):
                return any(
                    isinstance(statement, ast.Raise)
                    or (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
                        and isinstance(statement.value.func, ast.Name) and statement.value.func.id == "_blocked")
                    for statement in current.body
                )
        return False

    def _governed_evidence(
        self,
        path: str,
        text: str,
        tree: ast.Module,
        node: ast.Compare,
        parents: dict[int, ast.AST],
    ) -> tuple[DynamicGovernedCheck | None, GovernedRelation | None] | None:
        if len(node.ops) != 1 or len(node.comparators) != 1:
            return None
        operator = "==" if isinstance(node.ops[0], ast.Eq) else "!=" if isinstance(node.ops[0], ast.NotEq) else None
        if operator is None:
            return None
        left_access = self._direct_access(node.left)
        right_access = self._direct_access(node.comparators[0])
        if left_access is None or right_access is None:
            return None
        left = self._endpoint(path, tree, node, left_access)
        right = self._endpoint(path, tree, node, right_access)
        if left is None or right is None:
            return None
        scope = self._scope(tree, node)
        binding_fingerprint = self._binding_fingerprint(
            path,
            self._qualified_scope(scope),
            {(left[0], left[1], left[4], left[5]), (right[0], right[1], right[4], right[5])},
        )
        syntax_fingerprint = self._fingerprint([left[0], left[2], operator, right[0], right[2]])
        span = self._node_span(path, text, node)
        if left[3] == right[3]:
            return (
                DynamicGovernedCheck(path, left[0], left[2], operator, right[0], right[2], syntax_fingerprint, span),
                None,
            )
        # A raw equality inside a terminal invalidity predicate represents the
        # accepted cross-family inequality relation, as approved for this baseline.
        relation_operator = "!=" if operator == "==" and self._terminal_failure(node, parents) else operator
        if relation_operator != "!=":
            return None
        return (
            None,
            GovernedRelation(
                path, left[0], left[1], left[2], left[3], relation_operator,
                right[0], right[1], right[2], right[3], "cross_family_consistency_guard",
                binding_fingerprint, syntax_fingerprint, span,
            ),
        )

    def _bindings(self, tree: ast.Module, tokens: tuple[_StringToken, ...], text: str) -> tuple[dict[str, _Binding], set[str], set[str]]:
        """Build one whole-module binding-event and conservative provenance graph."""
        def names(target: ast.AST | None) -> tuple[str, ...]:
            if isinstance(target, ast.Name):
                return (target.id,)
            if isinstance(target, (ast.Tuple, ast.List)):
                return tuple(name for item in target.elts for name in names(item))
            if isinstance(target, ast.Starred):
                return names(target.value)
            return ()

        events: dict[str, list[_BindingEvent]] = {}

        def record(target: ast.AST | None, value: ast.AST | None, top_level_simple: bool = False) -> None:
            for name in names(target):
                events.setdefault(name, []).append(_BindingEvent(value, top_level_simple))

        def mutation_base(target: ast.AST) -> ast.Name | None:
            current = target
            while isinstance(current, (ast.Attribute, ast.Subscript)):
                current = current.value
            return current if isinstance(current, ast.Name) else None

        top_level = {id(statement) for statement in tree.body}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    record(target, node.value, id(node) in top_level and len(node.targets) == 1 and isinstance(target, ast.Name))
                    base = mutation_base(target)
                    if base is not None and not isinstance(target, ast.Name):
                        record(base, node.value)
            elif isinstance(node, ast.AnnAssign):
                record(node.target, node.value, id(node) in top_level and isinstance(node.target, ast.Name) and node.value is not None)
                base = mutation_base(node.target)
                if base is not None and not isinstance(node.target, ast.Name):
                    record(base, node.value)
            elif isinstance(node, ast.AugAssign):
                record(node.target, node.value)
            elif isinstance(node, ast.NamedExpr):
                record(node.target, node.value)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                record(node.target, node.iter)
            elif isinstance(node, ast.comprehension):
                record(node.target, node.iter)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    record(item.optional_vars, item.context_expr)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                events.setdefault(node.name, []).append(_BindingEvent(None, False))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.name == "*":
                        for root in _OBJECTS:
                            events.setdefault(root, []).append(_BindingEvent(None, False))
                    else:
                        events.setdefault(alias.asname or alias.name.split(".")[0], []).append(_BindingEvent(None, False))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                events.setdefault(node.name, []).append(_BindingEvent(None, False))
                if isinstance(node, ast.ClassDef):
                    continue
                arguments = node.args
                positional = (*arguments.posonlyargs, *arguments.args)
                defaults = (None,) * (len(positional) - len(arguments.defaults)) + tuple(arguments.defaults)
                for argument, default in zip(positional, defaults):
                    events.setdefault(argument.arg, []).append(_BindingEvent(default, False))
                for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
                    events.setdefault(argument.arg, []).append(_BindingEvent(default, False))
                if arguments.vararg is not None:
                    events.setdefault(arguments.vararg.arg, []).append(_BindingEvent(None, False))
                if arguments.kwarg is not None:
                    events.setdefault(arguments.kwarg.arg, []).append(_BindingEvent(None, False))
            elif isinstance(node, ast.Lambda):
                arguments = node.args
                positional = (*arguments.posonlyargs, *arguments.args)
                defaults = (None,) * (len(positional) - len(arguments.defaults)) + tuple(arguments.defaults)
                for argument, default in zip(positional, defaults):
                    events.setdefault(argument.arg, []).append(_BindingEvent(default, False))
                for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
                    events.setdefault(argument.arg, []).append(_BindingEvent(default, False))
                if arguments.vararg is not None:
                    events.setdefault(arguments.vararg.arg, []).append(_BindingEvent(None, False))
                if arguments.kwarg is not None:
                    events.setdefault(arguments.kwarg.arg, []).append(_BindingEvent(None, False))
            elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
                events.setdefault(node.name, []).append(_BindingEvent(None, False))
            elif isinstance(node, ast.MatchMapping) and node.rest:
                events.setdefault(node.rest, []).append(_BindingEvent(None, False))
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Del):
                events.setdefault(node.id, []).append(_BindingEvent(None, False))

        # Every member Store/Del is a mutation event even when its enclosing
        # statement has no ordinary Name target (AugAssign, loops, deletes,
        # comprehensions, and future AST target-bearing forms included).
        mutation_bases: set[str] = set()
        for target in ast.walk(tree):
            if isinstance(target, (ast.Attribute, ast.Subscript)) and isinstance(target.ctx, (ast.Store, ast.Del)):
                base = mutation_base(target)
                if base is not None:
                    events.setdefault(base.id, []).append(_BindingEvent(None, False))
                    mutation_bases.add(base.id)

        invalid_roots = set(_OBJECTS).intersection(events)
        tainted: set[str] = set(mutation_bases)

        def expression_tainted(value: ast.AST | None) -> bool:
            if value is None:
                return False
            if _governed_like(value):
                return True
            if isinstance(value, ast.Name):
                return value.id in _OBJECTS or value.id in tainted
            return any(expression_tainted(child) for child in ast.iter_child_nodes(value))

        for _ in range(len(events) + 1):
            before = len(tainted)
            for name, name_events in events.items():
                if any(expression_tainted(event.value) for event in name_events):
                    tainted.add(name)
            if len(tainted) == before:
                break

        bindings: dict[str, _Binding] = {}
        invalid = set(events)
        for name, name_events in events.items():
            if len(name_events) != 1:
                continue
            event = name_events[0]
            if not event.top_level_simple or event.value is None:
                continue
            access = _raw_access(event.value)
            if access is not None:
                receiver = event.value.value if isinstance(event.value, ast.Subscript) else event.value.func.value
                if isinstance(receiver, ast.Name) and receiver.id not in invalid_roots:
                    bindings[name] = _Binding("alias", access)
                    invalid.discard(name)
                continue
            literal = self._literal(event.value, tokens, text)
            if literal is not None and not literal.dynamic:
                bindings[name] = _Binding("constant", literal)
                invalid.discard(name)
        invalid.update(invalid_roots)
        return bindings, invalid, tainted

    def _literal_observations(self, path: str, text: str, tokens: tuple[_StringToken, ...], tree: ast.Module) -> tuple[Observation, ...]:
        observations: set[Observation] = set()
        groups: list[list[_StringToken]] = []
        for token in tokens:
            if groups and text[groups[-1][-1].end : token.start].strip() == "":
                groups[-1].append(token)
            else:
                groups.append([token])
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and type(node.value) is str:
                literal = self._literal(node, tokens, text)
                if literal is not None and len(literal.origins) == len(str(literal.value)):
                    groups.append([_StringToken(0, 0, literal)])
        for group in groups:
            literal = _Literal(
                "".join(str(token.literal.value) for token in group),
                tuple(origin for token in group for origin in token.literal.origins),
                any(token.literal.dynamic for token in group),
            )
            if literal.dynamic:
                continue
            for start, end in _token_ranges(str(literal.value)):
                candidate = str(literal.value)[start:end]
                for spec in self._registry.family_specs:
                    observation = Observation(spec.family, candidate, _span(path, text, literal.origins[start:end]), "python")
                    if self._registry.classify(observation).code != "UNREGISTERED_IDENTITY":
                        observations.add(observation)
        return tuple(observations)

    def _literal(self, node: ast.AST, tokens: tuple[_StringToken, ...], text: str) -> _Literal | None:
        if isinstance(node, ast.Constant) and type(node.value) in (str, int, float):
            if type(node.value) is not str:
                starts = _offsets(text)
                start = _ast_offset(text, starts, node.lineno, node.col_offset)
                end = _ast_offset(text, starts, node.end_lineno, node.end_col_offset)
                return _Literal(node.value, tuple(_Origin(offset, offset + 1) for offset in range(start, end)))
            starts = _offsets(text)
            start = _ast_offset(text, starts, node.lineno, node.col_offset)
            end = _ast_offset(text, starts, node.end_lineno, node.end_col_offset)
            pieces = [token.literal for token in tokens if start <= token.start and token.end <= end]
            if not pieces:
                return None
            return _Literal("".join(str(piece.value) for piece in pieces), tuple(origin for piece in pieces for origin in piece.origins), any(piece.dynamic for piece in pieces))
        if isinstance(node, ast.JoinedStr):
            starts = _offsets(text)
            start = _ast_offset(text, starts, node.lineno, node.col_offset)
            end = _ast_offset(text, starts, node.end_lineno, node.end_col_offset)
            pieces = [token.literal for token in tokens if start <= token.start and token.end <= end]
            if len(pieces) != 1:
                return None
            return pieces[0]
        return None

    def _field(self, node: ast.AST, bindings: dict[str, _Binding], invalid: set[str]) -> str | None:
        direct = _raw_access(node)
        if direct is not None:
            return direct
        if isinstance(node, ast.Name):
            binding = bindings.get(node.id)
            if binding is not None and binding.kind == "alias":
                return str(binding.value)
        return None

    def _value(self, node: ast.AST, tokens: tuple[_StringToken, ...], text: str, bindings: dict[str, _Binding], invalid: set[str]) -> tuple[_Literal, ...] | None:
        if isinstance(node, ast.Name):
            if node.id in invalid:
                raise _invalid()
            binding = bindings.get(node.id)
            if binding is None or binding.kind != "constant":
                return None
            return (binding.value,)  # type: ignore[return-value]
        literal = self._literal(node, tokens, text)
        if literal is not None:
            return (literal,)
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            values: list[_Literal] = []
            for element in node.elts:
                item = self._literal(element, tokens, text)
                if item is None or item.dynamic:
                    return None
                values.append(item)
            return tuple(values)
        return None

    def _comparison_observations(self, path: str, text: str, node: ast.Compare, tokens: tuple[_StringToken, ...], bindings: dict[str, _Binding], invalid: set[str], invalid_governed: set[str]) -> tuple[Observation, ...]:
        def carries_provenance(value: ast.AST) -> bool:
            return any(isinstance(item, ast.Name) and (item.id in _GOVERNED_ROOTS or item.id in invalid_governed) for item in ast.walk(value))

        def attempted_access(value: ast.AST) -> bool:
            if _governed_like(value):
                return True
            if carries_provenance(value) and isinstance(value, (ast.Call, ast.Subscript, ast.Attribute)):
                keys = [item.value for item in ast.walk(value) if isinstance(item, ast.Constant) and type(item.value) is str]
                if any(key in _FIELDS for key in keys):
                    return True
                if isinstance(value, ast.Subscript) and not (isinstance(value.slice, ast.Constant) and type(value.slice.value) is str):
                    return True
                if isinstance(value, ast.Call) and any(not isinstance(argument, ast.Constant) for argument in value.args):
                    return True
            if isinstance(value, ast.Subscript) and not (isinstance(value.value, ast.Name) and value.value.id in _OBJECTS):
                return carries_provenance(value.value)
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and value.func.attr == "get" and not (isinstance(value.func.value, ast.Name) and value.func.value.id in _OBJECTS):
                return carries_provenance(value.func.value)
            return False

        if not any(
            attempted_access(candidate)
            or (isinstance(candidate, ast.Subscript) and isinstance(candidate.value, ast.Name) and candidate.value.id in invalid_governed)
            or (isinstance(candidate, ast.Name) and ((candidate.id in bindings and bindings[candidate.id].kind == "alias") or candidate.id in invalid_governed))
            for candidate in ast.walk(node)
        ):
            return ()
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise _invalid()
        if any(isinstance(candidate, ast.Name) and candidate.id in invalid_governed and candidate.id not in bindings for candidate in ast.walk(node)):
            raise _invalid()
        if any(isinstance(candidate, ast.Name) and candidate.id in _OBJECTS and candidate.id in invalid for candidate in ast.walk(node)):
            raise _invalid()
        if any(_governed_like(candidate) and _raw_access(candidate) is None for candidate in ast.walk(node)):
            raise _invalid()
        operation = node.ops[0]
        left_field = self._field(node.left, bindings, invalid)
        right_field = self._field(node.comparators[0], bindings, invalid)
        if isinstance(operation, (ast.Eq, ast.NotEq)):
            if left_field is not None and right_field is None:
                field, values = left_field, self._value(node.comparators[0], tokens, text, bindings, invalid)
            elif right_field is not None and left_field is None:
                field, values = right_field, self._value(node.left, tokens, text, bindings, invalid)
            else:
                raise _invalid()
        elif isinstance(operation, ast.In) and left_field is not None and right_field is None:
            field, values = left_field, self._value(node.comparators[0], tokens, text, bindings, invalid)
            if not isinstance(node.comparators[0], (ast.Tuple, ast.List, ast.Set)):
                raise _invalid()
        else:
            raise _invalid()
        if values is None:
            raise _invalid()
        observations: list[Observation] = []
        for literal in values:
            if literal.dynamic or not literal.origins:
                raise _invalid()
            value = str(literal.value)
            observations.append(Observation(field, value, _span(path, text, literal.origins), "python"))
        return tuple(observations)

    @staticmethod
    def _sort_key(observation: Observation) -> tuple[object, ...]:
        span = observation.span
        return (span.path, span.start_line, span.start_column, span.end_line, span.end_column, observation.family, observation.value)

    @staticmethod
    def _dynamic_sort_key(guard: DynamicGovernedCheck) -> tuple[object, ...]:
        span = guard.span
        return (
            guard.path, guard.left_root, guard.left_field, guard.operator,
            guard.right_root, guard.right_field, guard.syntax_fingerprint,
            span.start_line, span.start_column, span.end_line, span.end_column,
        )

    @staticmethod
    def _relation_sort_key(relation: GovernedRelation) -> tuple[object, ...]:
        span = relation.span
        return (
            relation.path, relation.left_root, relation.left_document_kind, relation.left_field, relation.left_family,
            relation.operator, relation.right_root, relation.right_document_kind, relation.right_field, relation.right_family,
            relation.relation_kind, relation.binding_fingerprint, relation.syntax_fingerprint,
            span.start_line, span.start_column, span.end_line, span.end_column,
        )
