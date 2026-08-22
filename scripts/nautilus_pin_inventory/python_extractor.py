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
    ('scripts/materialize_nautilus_runtime_closure.py', '_build_native_entry_guard@741', '016de145605a8b4e6e620b6c2aab552f7e10a8f044421214c8f65d1c2b1f5fc5'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_build_native_entry_guard@741', '154d1b51f9775171b1e4f1c35a687ca34d53b84c007299a38812ccf6540627ca'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_build_native_entry_guard@741', '7158633db0b161cb82b6bc9d352542fe240baa63009e1c0f2a0dc013d9b0cf9c'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_build_native_entry_guard@741', 'dda5a0d59abc1a5365829cd3cb56ef961f32574821d751a1e3f3e485ffef3a0e'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_build_native_entry_guard@741', 'e167c905bad8d07d62415818a89c7f7778641c2f702882d552e9fd61ae4d22b8'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_build_output_manifest@1049', '989df1fbb35e8fd0290df878d5ef104db9c9dd1233f903d0711004f1e4293eb0'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_copy_file@919', 'd3c181fd7d5085d97a6621d2effa638d5a2b1feccd52880d80bece2a6f9e78f4'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_load_local_tool@320', '7cc5b89beb61219049bbb48c790c6fb0a15bf54ad8e04de0cf6be199b7422332'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_load_local_tool@320', 'd6830491aaec14115a636d8aa67b31ca11fbcc8b6bc618f31009acd12782e3ba'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_publish_noreplace@975', '08bf0a2cfaf983653789ace7953d23aa831e083634327a5cc3bbea1ca35d7c7e'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_publish_noreplace@975', '7acd751419bbb0f6ca0e4c5aca706a16442a1ad9237147f16b13da5ffa79de49'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_publish_noreplace@975', '8087ffb4004feff8ed6ad4464f3e95abc109d2b9aac9755442be2ce38bda4bfd'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_publish_noreplace@975', 'ac42d7ef7e19d6f91b4edae17dea1029bccd002e537b5b03b720658c6a974b40'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_publish_noreplace@975', 'b08c4dc59c17c2f2c828a82af49e92fa3c96ad1ad3473dd2fd87a9a482691918'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_publish_noreplace@975', 'f30138856eeb719e3c813a4c106e9f8a9e170e1f7242b412dee54fe632885874'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_read_file@200', '0bd8096830fe425c2ab8da2c8242830badda884e8dd4599278dfbf78ad65e9ca'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_read_file@200', '3c462ae4456bee6afcb405b687ef92d44cfd64da91f071fb173e77f921e3ee87'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_read_file@200', '5e19ac7147e886624f9a1f386beb339eff430468682d89b74430bb5053d7db1b'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_read_file@200', '6a07a8159e12a29ddb45bcf435dc7177bb94e6f4eda34ab6d976a2556c74fe01'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_read_private_build_output@233', 'bdc565d250867fa759c6ef5c7abec064afc93f15ae8af8f9b67401bd1cf971de'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_read_private_build_output@233', 'db289b8db78323a4c51619aca58b15fa1198ef15c3791085a1f5554dc1ec41e2'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_renameat2_noreplace@939', '1cf08fa3e2743af205c70caf4d9215c8bd579be1df355d2d8139f8f70578316f'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_require_sha256@296', '4e56c9faf300ead840db4248ff786d1f6d211da4ba51c4451255d69a29519a54'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_safe_relative@302', 'a866ae84b924cac381fd5ee194f9a7d0a565605d3d603ed8120dd0f3354d05d2'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_safe_target@311', '2dc438c49081d4a44eb1005a7210005314afcfa47658037dfca989d67ddfefaf'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_safe_target@311', '62cd82ba1395959897cc113a1a7ef402ab8afcf31b18dba941f01ba0c1847fc3'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_sealed_directory@277', '7228c37940db664afcedf7c607a0cc4249bdff1db18e43811d1622f188b2eb0a'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_sealed_directory@277', '8e6ca0160ae3150b3c7a7dab3debf6a6af85993376333f932a2baea6c515017b'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_sealed_directory@277', 'ceb451275369517d2de059c8063b19438dc6c5707e30310a6628cfb9650a47a1'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_sealed_directory@277', 'e6a9de709c3d95152e5d6e4e3bd39de50258a01ac055fd427489039cc1ada46d'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_artifact_bytes@656', '042939634f6eee124e3261bc8b2aa34dc73636aa36574ce082d2e0a0ff63d26f'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_artifact_bytes@656', '114acb4f802a79aaa217afecfb4e3f444169cd1a7c1d9fefa2e1b5ebcdab62cd'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_artifact_bytes@656', '17b9f93014bae0e0d9fe279699de218722e58388290fe7565d50df574eb73602'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_artifact_bytes@656', '1ea2a93660a88e439c9dc07cd322981b5c4d1961080c8a0525fbc08c09e97b92'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_artifact_bytes@656', '273a8c1e2bd130f99774fc00894408ddc567cf971fb7327e41531968141b289e'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_artifact_bytes@656', '33572115312f0ddb78c0023ba49290bb6159db188232adcc0aaa16ab63a46307'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_artifact_bytes@656', '5c4c91860c17a3d8cf1960ad444c8a8b5e827faad1efac4413914bd6e9e8e56c'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_artifact_bytes@656', '992dc7301231f0f7f471dd8106693c153f382424dad4cbfba37573cc32b1f70e'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_artifact_bytes@656', 'a38f27c8015b9f3d32f6319c2200fb1420c4aafd0cf3d9cb0765c163be94036d'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_artifact_bytes@656', 'b2bf44b27d265b0deee0843122dd1e88f18f264cbd0d1b7bb21e8e1d41c435e5'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', '0e0ff16f3bd05aa64bfb4c4b21d2574e46a3f94ca8354392ba6e9dd6de65c78b'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', '1cf44efd076d3f7b4ce5dcf62a78c4c6dad387ff0f1fbc50002ceb1aad9a7c03'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', '41f7650803773acae766cb73e1a61335b6056b5357ba51902f94bc7561d70391'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', '4f8737f0c3b2088236e7eebd4c7a1e6766e9074a36957173809eca96151b93b5'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', '63d9daa093a0312ad75947e962ac3c49b1ac357d73ffbde39261f5a347ffbaaf'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', '6e5fe959e630f82fa6a19a3e161d1dac9bc4364b8f1364cc5f89da774b88a32b'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', '75c7404b1d613cf005df3250cca878a4eec7fc6c031c3ffe6b7a881df7f8516f'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', '776ef37b4008c80521e2bc846c470f01a53bf6ed868558a01568c0bbd30b0141'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', '882cd0bce288130227043fef0f519541501bfa0ae9c7a5cea74d9bb8300aba63'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', '8c0015462a516604ff95951bf894cac10a5582eb93f6a81eef69481ddac50289'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', '901c2b1fc3a0e14c79950d7ddaede4ff7cad30304825129de2d82bef02c1e7bd'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', 'a7c40d71afe2de2ff961f100cf48dc176669e89a1aa2a5358605629beb6318ca'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', 'bcc57823f5d452cd0f0072eff7fb32b2dc084c6e490fcf35e574c481220843a6'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', 'cae9cbbcb495a77e8b20f42fffa34014dff1238cae7e810d48773696082a3c07'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', 'd1a556e9d82dd41eb3b15122dbd814680734d5b5242bc8fb5ade543402284cde'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', 'd8dbd702a73f775b3ad67a084cdea5e6f35471ab048ec3fa33750ab8751dcd97'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_bytes@566', 'e7bf2329c93ab69506ee6cb974974650ea720701049e3440be3343308eb69da6'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_path_inventory@546', 'd4b38afb0f7762e1148f8e708e453bd09cd0338898c31901433fb2a1137c5ddc'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_base_runtime_path_inventory@546', 'd6f5d0e921fef4db8ae0e824c0b1f8f6c298faa11941e7f114ffcce5d7a6e623'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', '0da7e0a696af9b1c27c24653df796a066517922ef627b9e314ad17588e57562c'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', '2e10b1de82aeb8f00dc50a4febcf9bb197ccef396f07d416473df65a6442676b'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', '41d0e50e8cdaf9838127898957c95a9e450a4546000c198bb444e0cacd413101'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', '49575985a96ed03776ba1055b8654c332e84f04221d1f7679c41ea5e005cee95'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', '5cd9925ae16b42dc15156a37a0ad6ea9bba60e95eb73596cf71a04e06b970fcc'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', '5f615cb8b5f3045c7667ec82d352c572d7f0a0f03955d7aaa477277aaf6f1aa3'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', '8f4b897e9d7bc9d9aff2c9e448d62183424ea1646b3ffb0ba44baff0b0cd3631'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', '9833350c53b937e65f4656ce430af82eba3e4a184bcf65906a84f2a593e93880'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', 'a5fbb89835d19feaa93ad1f8508ddd5be66ad62298dffd9b1dd5430bbc43686c'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', 'a8da4ec474a36e6bed054ee38547e2b497ef20024ca7f66c2d2e6fc676e98cdd'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', 'c9c39d8955f0f79f315e6e837a22b057b9e15e79fe9508cd2d9aa29127a58851'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', 'ea0c9b4516b6db0e6fa7874c16c90d1cd4ec400e847b6688ca8f9c6ff9372d57'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_native_guard_policy@356', 'ea1253a6d4d20d34a00f5a90903c7cd552eb21befa7ee0fa990c0ac7b449507c'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', '0d805c024f9f9d4c6dc44adf984b5b3ec0b4939160d3e0983829684ca9d8372f'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', '2071ab54393bd9fa47a39b50e12b547c4a8d1f2d482a1f5deb1129a57632879f'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', '2b0b40f9164f63b807c600d035a35293dc1ad0daca2c82d54f7d6e94140c1122'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', '3499c04e0c7db39cfef31b8cb75da0740d05db1ad86cee9555a78440f6679cb8'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', '3963f68c4498f428c0bfba88e972c0fd5f91008d0d050e2fa0a8740eff440d8f'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', '4c7e10291f59278c978a4660772782b43990a515561824df17db9189dba2e197'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', '4d43aba6cf1a049d48741f9f87fc588d4301701b12bef63bc69f8c8d1d5a3c6f'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', '4e10068e6c0c3b5e17238c864220155573bbe6853fb3d52153d310268c5829e6'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', '5f14ed952db869a5e888b41d646d869cecf785464f4fdb5af3947c646cf5972b'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', '5f5f812ce09db05917283c4c3e9aca84743333f048a186822ea93a9c79d9b383'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', '63b894100ce7e3ad7df0f41cf4dcfbfcd33e1507dd88af550d720d5ecdcbb72c'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', '6d90f3e241b4cebadbae0034df4f62bcd4fe8ef21b6bebdde8ad2ab45af5ccda'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', '960d8b181dfcfd21128a78bab0646059bd0aceda68da9dd3e8deb778c0511441'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', 'af7fccaee4245c8c65d4933e0aefc922d77a3e2cf2c6c6f2c121c7e8dbb3afea'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', 'b06b29c43b539d9e4ca193da2aa0aaccb711996bf8155d72abe0ad8a9b49afa2'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', 'b970839f87811c1196ecee595ef4684244029b9d6fbc056bd1fd744e52a080b7'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', 'c5b0068e6ac3c719ddbb2b558b86374b418ed72d54b3acf936abaaf1cea9bf5e'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', 'f43cf4032013cd081f844afbe369bfe3714baabc023a077a828989911b7821f3'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_verify_native_guard_toolchains@691', '442e80a56e856d49357696e57cf5b46d4d106bf70526de607cd28dd0ef4468f4'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_verify_native_guard_toolchains@691', 'cc21e12908f5feb0bfc2fdbfb2cd85c2058294215a657e6bd9e441d95ba1099d'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '1dd8bb35abbc2b39a41b7439f8e16af5e9e01fbb684baeecefd628e2ad1be910'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '2a6c2c1189f1f1f15b6349c42e9084198f994335fd97bb396155fb8d9bbe0fc1'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '2ba62d5d093566ded19df6a86fb35bff2527ee7c33d780030b0e55d27589f177'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '31e1d7e85aaede0e41f6bc73f9e5383a57a1b30eff341a357d233f2f2895931b'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '325ad64f483219026aeccb4e275da1c37fe99c59d2ed9e0fc8d93c325515362b'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '43a9a6cb1eeb59f33bc2f9c4f91328ca74cd7f4af8e622b7d02dd0fb766dc6c8'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '5a2a509d15a2eec3fb9f2a2f249b2e6e032cf8040bcf0cc564768bc3985d0e89'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '5a7c147518ead342b5264605fc17586309c00198adb8c91c2723fdb8764f7df0'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '5d67cf19a81d67f34a9217978b4258a9242e6ae68ad48569df25e179813926c7'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '5dd90f2003d405eee719219e74c7e0495dde5b9d4fc1683dd0e01d0964b69345'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '645e94d993d63e74c89e4cd0c028ae8a7307ecf86ba3fd03fe48918b5bf2a5ad'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '75be71a8cfcfd2e5ae46f96baee2efeb112cf8420d443f7598c5c9a14af34796'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '7a7955220c0f92d2f8632faad00b5248257ec229cd944b7d5b582db353f6e836'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '947e4a76ede80486956d54f11d29ed8cdebafdd126b2e468421525883073823e'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', '9fa1dcad53c63a6436eec85689e41ac8b263201ccb7239867263faf1d586bf94'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', 'a46e45c48283df4f3b925bf8f4583c2c03a29d653aba4e81438e4c179c8efb49'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', 'a72d031fdb5f714654977e92f52724bfd0bfd2e385bcece74fd6a5757444b300'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', 'b12f9d0a24feac327644aa5a0d4e322e9973e0526f6506b730d240b237b45823'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', 'c19e5670bc7a15a3c970371826cf63ec7f965ad923a8e3d27766cea3d1cdcfa4'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', 'ccbe01ad560803f380eee9141e5f2c4141ac361c40dcdd86c042f96d07d76934'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', 'd0098081f5d7490e4ada5acb2490726330bbf46fd3b24caea723f017425a77ba'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'materialize_runtime_closure@1084', 'db68bec105cfdfcab20fe8c505e663543b05d412a233f3a37738cff09ed8c0a4'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'read_base_file@523', '0557c9bfb8133dd148ddb7c8f1a5faee4c63368aa0c42aca8d1f4b2befcc3b94'),
    ('scripts/materialize_nautilus_runtime_closure.py', 'read_base_file@523', '266c806ff2fe90fe18dff30054f444b4cae35fbd464e84e64ffe250581315894'),
    ('services/job_worker/nautilus_closure.py', '_closure_digest@343', '1a1458119bc72f026bbdd5d1bff23e85a48d96b2b8ce9220e6b6b271cc6a91de'),
    ('services/job_worker/nautilus_closure.py', '_closure_digest@343', '41eb0ac4a9788ff395a796af0837c95dad84e6863b86b657298c2e71d7680400'),
    ('services/job_worker/nautilus_closure.py', '_closure_digest@343', '72f121c8dfcc818d51a6089cef491d3306eba1c518ad34a033dd2cc5490c89cb'),
    ('services/job_worker/nautilus_closure.py', '_closure_digest@343', '752470ef6fe9ac063996c5a705f061bd9f6e05ad65b6ea224bdd7d7d8d6c034a'),
    ('services/job_worker/nautilus_closure.py', '_closure_digest@343', 'b5fce19a2eb1947d450a66384ec6c1e78d143c6271acc22a93d28926b53660a7'),
    ('services/job_worker/nautilus_closure.py', '_ensure_external_private_directory@178', '1f9a685b4feeeffaa8cb5f48bd017b6978a23f02de64a6fa122e478592308987'),
    ('services/job_worker/nautilus_closure.py', '_ensure_external_private_directory@178', '6b34faa18c710cb03de7885bdf61ad7a30d351e25f230b0842f66c70aeb0fedd'),
    ('services/job_worker/nautilus_closure.py', '_ensure_external_private_directory@178', 'e30c4764b7801422f9e999203b050d001778d2d3028d04096b24bc74d0489b2f'),
    ('services/job_worker/nautilus_closure.py', '_ensure_external_private_directory@178', 'f5ed503095f22e7646f7a81a367b6dd54a8711716169b085edea18da117902e6'),
    ('services/job_worker/nautilus_closure.py', '_manifest_files@256', '0d73bd1a5c89c73f651f49b713598f241148ed5a56906f10bda25a85b1744a11'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', '04e2515105866f80bcd8c39a0a26b7035db991f6825dd350fab7dbc84923d7a5'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', '1cd1d58d3c25452cac575afde7125ed019cf42032cb1984241b32cbd9297e889'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', '21d4f5967fe8a67d58a5e6551b7478ef1b0682872a3b820792c1240f9464e14f'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', '2e4c7ca220e6b891d704e328a7705d07d07608057d9702265bcc6d12e7fa565b'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', '30f5701b7bf96807ec2c8912d66286bcb5713c3c0b662b9116e0b0c19fc6a8ff'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', '3c58521aaf2dbfa667834ff5c72224531872c918f3ffb30c5490bdaa968fb743'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', '3f8d4330f724dd3e358de9d092339a44606035408894dfac74d338d9d21e1d5e'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', '871c0613b25aa327cb0b816b7c8cd13b46b0e661e1b771e4adeaeda3acc864ea'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', '913a55e6b5a6de702a29f75e769c9188cbffd5cc49f8871bae4d94645492dc65'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', '93ac44254fd3c1765ba7807fbe3e1418754a082151c4f0626aa033388a894fbb'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', 'a0dc46ed6d1611b442698e944977f6f96d68b952179f5cae6eaa1851421dc0e2'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', 'aa512faf28c23412cc781690684107e41c70f9d59933ca602132543b62276d1e'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', 'c4474ce5674a00bd8d9115ebb0ea969aff07c4a77c94bdf6b703248d2564b6b9'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', 'e5607eabaaf60ebc1ac15ff733b5fbdc0719a4582b7ae624ae43dc48942b63fd'),
    ('services/job_worker/nautilus_closure.py', '_native_entry_guard@421', 'fd8e57221755e1fa0f85b21084ddcfcd730d5aef00cc049ba6e2c3f93ba855e3'),
    ('services/job_worker/nautilus_closure.py', '_safe_relative@227', 'a19c1c9a81264e79ac7447e71bef5c470455f552bb441810c47a74d2163f54aa'),
    ('services/job_worker/nautilus_closure.py', '_safe_target@236', '79b3bcaac565b275639a1dc5589aa495865bbc373fdadaec2eca5d696bf12a40'),
    ('services/job_worker/nautilus_closure.py', '_sealed_file@200', '12de974b53aaa9cdc24c9a792737e025517bde7e92f153baf715c0040b4603e4'),
    ('services/job_worker/nautilus_closure.py', '_sealed_file@200', 'f6778240d750b515ff5c510c8fedb5d9ebd5b191d75ed4a95c32b89a70f49cbb'),
    ('services/job_worker/nautilus_closure.py', '_sha256_path@155', '51f9447b5aed7985e6a9116d32f6a80b85aef3205d43c4e51fbaffb284ea659b'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '093b0d92012c26307c5b858f8a3e862e87076b00cb36e028c83898ba77a9d34c'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '24a372111e25a2f9a678dfce86b1203ae9240a67e4a49608e30ad67df8f390a4'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '30505239bc4a52804f41a7dd6770f144e7b9f3fbe02c74628c1cb1a6b785c2e5'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '368aeeed24048c52cb449f9375309e38b86e156d00fa33d7bde8dbf3e44fac6a'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '3bfbc67611be4ff906cf274d3ffd49a408790ec78383f18570bc5be6ea1f3921'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '3ebfedf917d5ec81ecec134e4b4f4853503f3ef1ea9a63f280e22c3f1b6644ca'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '3ed904f9e5400c51da99a43cc349e3e02ac06e28884844ef38a9e997bab2add8'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '475c1f4de904b4f5c72a1de3649f921dee7116e13f77d753ffc7ece95008d1ae'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '52719ddc24598feb531f3b738792d290013dfdfb24c48a656f35a599b981cc68'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '59856bd44fb2350c3ea7589e5a4d450518b095775c6c0fd8d451d4cf5414cf35'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '5e066eef1a23056322591c6a1029a0f10eb9e4769ba4f763b86e19d71773c0d1'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '67cac3990027cffe0c595abb77aaaadec80ad01888881f827e7b567ac2920a88'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '6fc76b482136ffc17db11349e6bd68ea48b1fe1b5aa6321637ee5d3f0a3b8765'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '736962ec80e4b1ee501e813b708d7927dbb459fd6c1a2a3014adf04ed56a1164'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '75351155f98dd241f6d0221b4371227212f9f0f91b7e3aae4dd028717702bc19'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '7cf8dc0e0fe5a70917d218a8c0585f2b969ac9c67bed1c479bfa6feea23fde55'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '7ffc6b3703e6d356e02c7f2ef0335ce9b56928f5d97a2c0f63bdba77ee9959f4'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '83e89971512a38b8d1c18509d1eb07ec09cfb7bef4875b2f09bd4af65bd2f31a'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '84cf04de4e9ed23ffd2f2e89c77ad448e25dfbf91b6cac36533defcf7c797594'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '880486eb87e5f326159333b509f123885d0b40147e9bcf643176a8e8564f1016'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '8a2887d2d45c3d546f900094a055eda76d92a98b0e02157fabf442d3ff387a6a'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '8e5f21dbf06cdbe73aaa3886e8de109f26f13c31a03d2d7a7e35b4c0053160ef'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '9463864dc03e888074af91aefc49eddf07bda06353af627263af5bc9def0cc26'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', '9a7cedcbfbd6f160135929012253d97f5589b0f4a0498d03eba2f9fd8022e274'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'a0bc332b363a45d903962ccb42745e8b73c1588b9fe168cb0c4e667758d77b37'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'a33462293c7d4881a134ecc7e8d58690d70d67412660f0732704a3bb198a99a9'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'a51ce554395a0a728367101fdbc836ad5e480df06c9073bb8b543e86d9098123'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'afcb53545dc3fe966946a098e857925a89c6f6de9c40530a1adcd999a1c666da'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'bdfb2ce7ef53c4139f14fc6c22f6c00f6468b2949d99ae4a8561780222e4fb96'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'c039adc0f67edbd52f3786e1b6f2a3570f2f907f0679ba876b08569be625c829'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'c430805de839b08167aecabc6dcf58f1a4ddc382246a6d2f2763e289a749d98e'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'ca5861824ec7da09cd9ec7277496dd3470b5b1454361d0246fcc4b1ef21fd480'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'cb4b64c7e6901cecc8cdc4bbe3b95698c032e75a833d3193c56b13bf82e0f07b'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'cdfd4134aa847873b9581d026dedea45127e0d104b13d8210c52072c1ed8d171'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'ddf648adf78d59947d50387c4e558fae86142437e330b70ee8854990d069c783'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'de7ffdaed63459b58bd5c8da258988c3718fead09fe79b731de2a6f676053f56'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'ea3a63738ccf559ecccf3b791a683206390aa152f9d74e264c0762f28566e154'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'ebdad61ed937992d15c81a6d937387d9be5e8c725d280cd93f029c8d4aa23881'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'ec4f3b6c6fd1f9c74d05ff633773772fe6493a55208d8525bdeba40532b5441b'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'f36f4915b920b0ffbb380b5ecc12119ee91cc2741da7c92c40fea45e40ac262e'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'f3b27f08f5a8b054b4a7a30cd4d723fc07de5949bc986fa81492b0cf4c961c21'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'f64c845eeb882c6ac18798a41b8591006d8d34fe8c3347ff2cf6a5b69aa3f6d9'),
})
_REVIEWED_SAFE_GOVERNED_CALLS = frozenset({
    ('scripts/materialize_nautilus_runtime_closure.py', '<module>@1', '_PROFILE_SPECS', 'f1548753b1c763d74a93c280e8a291dea1e5c75016f9e75366a8e1b85496d767', 'mapping:values'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', '_PROFILE_SPECS', 'a7a4b0a6713b0dacb5d0bcd0c797934660f9691cf9cb1ed172f030e3eed98cfd', 'mapping:get'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', 'policy', '029b5fc92e8685fb67bb6017c001e730ceaaa4d6c23ff7a4f8da56ca1c14f821', 'builtin:set'),
    ('scripts/materialize_nautilus_runtime_closure.py', '_validate_policy_bytes@422', 'policy', '834878470b061543b78c924f1ba844ced7d5a0486a5701b1b2afbe828aaa11d9', 'mapping:get'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'closure_manifest', '06fe0af04294b39060dbefc2de73565316a04ee1c18d70baddb829e9cda22827', 'builtin:set'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'closure_manifest', '13b2898ed6f51f1807758fb02f4dc795ebe62a8688190cb45c8c4407b6ffe058', 'builtin:set'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'closure_manifest', '17bcbd15ce370a4f0a377ae73793e4001dd7f652ed9b08236c8befa472f3ae8b', 'mapping:get'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'closure_manifest', '2e6b675272a0ba4522d555129dc7a90560c873fb9a95d1d8e7f410cdc4ce4446', 'mapping:get'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'closure_manifest', '3c116172c290c937b12293dd334eec0a3068568761060d8e4266baa51ba4b568', 'mapping:get'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'closure_manifest', '3ef630d26c2948e81cd943f090f8f51260cbbb8612d64aa365f58b043dc6d495', 'builtin:set'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'closure_manifest', '55f3a791d3d56c6e676e0c196a0116876e9a860131dad0c53c9ec5b77ff4ec49', 'mapping:get'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'closure_manifest', '715dcb4ef6a264694771dda01e8c9d33d076c2bae40b3ee65a34c3eb98bdf8a7', 'mapping:get'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'closure_manifest', '7283f805633123e0e1e54ae8661f0ad88a84d8e148058582e85e097cbf373a93', 'mapping:get'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'closure_manifest', '82c53d0240395fe24ae5c910e945159599a7bd1e02c99ba29f6e5fc39c4ed588', 'mapping:get'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'closure_manifest', '8b7b78bdbc791281229cdb5d28f7ee52cb6d88bd56c06278d22672906cd5453a', 'mapping:get'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'closure_manifest', '8ec262a4fb8ca4faf988f7616725d91f7c93dbbda1fcd6d8dc301237a912282d', 'mapping:get'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'closure_manifest', '99ae1cbef8c6b4e8fbdaafe713722a34e335c229577fc385818ee432062cbcce', 'mapping:get'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'closure_manifest', '9d75f6374cb09ca17fd388fe16e8a3a779bcba916edc0245732ce0a73ff0b95f', 'builtin:set'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'closure_manifest', 'a496d491308ba6e43b5878a5b4ea49c0b5ff137ec8c2d0eb3956d30404d4816b', 'builtin:set'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'closure_manifest', 'aa6f139f2bdee16d44891d7c98bce82b1b6461e90ca02cdd0d4ae8c69209970d', 'mapping:get'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'closure_manifest', 'b08ca7c4daddb4d9d5866b640d3072d185ea2aed2d556041238b6459ec1bce03', 'direct:_closure_digest:485689d5aa06104eaba46d5057b3e0c4f3a17a2c7d443c936d44638632c4b172'),
    ('services/job_worker/nautilus_closure.py', 'attest_nautilus_backtest_closure@515', 'closure_manifest', 'd3ff385a9e5bfe182f32c3acf44b8583d90c5124adeb0a2084de71c38117e744', 'builtin:set'),
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
                if self._reviewed_non_governed_comparison(path, tree, node, parents):
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
    def _receipt_fingerprint(cls, node: ast.AST, parents: dict[int, ast.AST], scope: ast.AST) -> str:
        ancestors: list[tuple[object, ...]] = []
        current = node
        while current is not scope:
            parent = parents.get(id(current))
            if parent is None:
                return ""
            ancestors.append((type(parent).__name__, getattr(parent, "lineno", 0), getattr(parent, "col_offset", 0), getattr(parent, "end_lineno", 0), getattr(parent, "end_col_offset", 0)))
            current = parent
        payload = (
            ast.dump(node, annotate_fields=True, include_attributes=False),
            getattr(node, "lineno", 0), getattr(node, "col_offset", 0), getattr(node, "end_lineno", 0), getattr(node, "end_col_offset", 0),
            tuple(ancestors),
        )
        return hashlib.sha256(cls._canonical(payload)).hexdigest()

    @classmethod
    def _reviewed_non_governed_comparison(cls, path: str, tree: ast.Module, node: ast.Compare, parents: dict[int, ast.AST]) -> bool:
        scope = cls._scope(tree, node)
        return (path, cls._qualified_scope(scope), cls._receipt_fingerprint(node, parents, scope)) in _REVIEWED_NON_GOVERNED_COMPARISONS

    @staticmethod
    def _target_base(target: ast.AST) -> ast.Name | None:
        while isinstance(target, (ast.Attribute, ast.Subscript)):
            target = target.value
        return target if isinstance(target, ast.Name) else None

    @classmethod
    def _scope_binding_is_proved(cls, path: str, tree: ast.Module, scope: ast.AST, root: str, value: ast.AST) -> bool:
        parents = {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

        def target_names(node: ast.AST) -> tuple[str, ...]:
            if isinstance(node, ast.Name):
                return (node.id,)
            if isinstance(node, (ast.Tuple, ast.List)):
                return tuple(name for item in node.elts for name in target_names(item))
            if isinstance(node, ast.Starred):
                return target_names(node.value)
            return ()

        aliases = {root}

        def module_nodes(node: ast.AST):
            yield node
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                    continue
                yield from module_nodes(child)

        alias_nodes = tuple(module_nodes(tree)) if scope is tree else tuple(ast.walk(scope))
        proof_nodes = alias_nodes

        def exposes_root(node: ast.AST) -> bool:
            if isinstance(node, ast.Name):
                return node.id in aliases
            if isinstance(node, ast.Subscript):
                return False
            if isinstance(node, ast.Call):
                return False
            return any(exposes_root(child) for child in ast.iter_child_nodes(node))

        for _ in range(len(alias_nodes) + 1):
            before = len(aliases)
            for candidate in alias_nodes:
                if isinstance(candidate, ast.Assign) and exposes_root(candidate.value):
                    for target in candidate.targets:
                        aliases.update(target_names(target))
                elif isinstance(candidate, ast.AnnAssign) and candidate.value is not None and exposes_root(candidate.value):
                    aliases.update(target_names(candidate.target))
                elif isinstance(candidate, ast.NamedExpr) and exposes_root(candidate.value):
                    aliases.update(target_names(candidate.target))
            if len(aliases) == before:
                break

        def governed_origin(node: ast.AST) -> bool:
            if isinstance(node, ast.Name):
                return node.id in aliases
            if isinstance(node, ast.Call):
                return isinstance(node.func, ast.Attribute) and governed_origin(node.func.value)
            return any(governed_origin(child) for child in ast.iter_child_nodes(node))

        def callee_authority(node: ast.Call) -> str | None:
            if isinstance(node.func, ast.Name) and node.func.id == "set":
                if any(isinstance(candidate, ast.Name) and candidate.id == "set" and isinstance(candidate.ctx, (ast.Store, ast.Del)) for candidate in ast.walk(tree)):
                    return None
                return "builtin:set"
            if isinstance(node.func, ast.Name) and node.func.id == "_closure_digest":
                definitions = [
                    candidate for candidate in tree.body
                    if isinstance(candidate, ast.FunctionDef) and candidate.name == "_closure_digest"
                ]
                if len(definitions) != 1 or any(
                    isinstance(candidate, ast.Name) and candidate.id == "_closure_digest" and isinstance(candidate.ctx, (ast.Store, ast.Del))
                    for candidate in ast.walk(tree)
                ):
                    return None
                return "direct:_closure_digest:" + hashlib.sha256(
                    ast.dump(definitions[0], annotate_fields=True, include_attributes=False).encode("utf-8")
                ).hexdigest()
            if isinstance(node.func, ast.Attribute) and node.func.attr in {"get", "values"} and governed_origin(node.func.value):
                return f"mapping:{node.func.attr}"
            return None

        def safe_call(node: ast.Call) -> bool:
            authority = callee_authority(node)
            if authority is None:
                return False
            call_scope = cls._scope(tree, node)
            return (
                path,
                cls._qualified_scope(call_scope),
                root,
                cls._receipt_fingerprint(node, parents, call_scope),
                authority,
            ) in _REVIEWED_SAFE_GOVERNED_CALLS

        def unsafe_receiver_or_escape(node: ast.AST) -> bool:
            if isinstance(node, ast.Attribute) and governed_origin(node.value):
                parent = parents.get(id(node))
                return not (isinstance(parent, ast.Call) and parent.func is node and safe_call(parent))
            return isinstance(node, ast.Call) and any(
                exposes_root(argument) for argument in (*node.args, *(keyword.value for keyword in node.keywords))
            ) and not safe_call(node)

        approved_target: ast.Name | None = None
        for node in proof_nodes:
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
        approved_module_target = approved_target if scope is tree else None
        for node in module_nodes(tree):
            if isinstance(node, ast.Name) and node.id == root and isinstance(node.ctx, (ast.Store, ast.Del)) and node is not approved_module_target:
                return False
            if unsafe_receiver_or_escape(node):
                return False
        return True

    @classmethod
    def _mapping_origin_is_proved(cls, path: str, tree: ast.Module, name: str) -> bool:
        matches = [
            statement
            for statement in tree.body
            if isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and statement.targets[0].id == name
            and isinstance(statement.value, ast.Dict)
        ]
        return len(matches) == 1 and cls._scope_binding_is_proved(path, tree, tree, name, matches[0].value)

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
        if endpoint is None or value is None or not self._scope_binding_is_proved(path, tree, scope, root, value):
            raise _invalid()
        if root == "specification" and not self._mapping_origin_is_proved(path, tree, "_PROFILE_SPECS"):
            raise _invalid()
        if root == "expected_identity" and not self._mapping_origin_is_proved(path, tree, "_PROFILES"):
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
