"""Independent P1-U00 acceptance identities copied from the approved brief.

This file intentionally contains literals only. It must never import the
production pin-inventory package or derive expectations from its registry.
"""

from __future__ import annotations

ROLLBACK_IDENTITIES = {
    "engine_version": {"1.227.0", "v1.227.0"},
    "upstream_commit": {"280ae1762df51a492a4ce71506a40b5c8706def5"},
    "tag_object": {"0ccb5b55879c072a6e07fc7cbe5297c53c378107"},
    "rust": {"1.95.0"},
    "cython": {"3.2.4"},
    "setuptools": {"82", "82.0.1"},
    "closure_schema": {"6"},
    "rollback_sha256": {
        "a00d3ab0c5b2ba1e4a4ac4c9af70f5b3fe30717d9b42a328e51696e3894a45e2",
        "083652294183947a352d1443ed0245311bf7ee5a716b66ccc21e814be25851ed",
        "f707cbe27b183ba598c31f1b3b6ec67e36f36e878c4228d3fef80741efb81b28",
        "105579383ea3c5e44104bbe162ab78380f7abb5654e15ac3b600beee54ed93d2",
        "ff2e7753974c7b163bd890f9913dbfbb630f80195708ab67d537d72939e0c56b",
        "0a7693f27a384925698dde2818abd70b894524bae341a62de0ef8f17500d108b",
        "7d3cc69b340536ee6c0e74f4c6954c8a6ed19121df1836a1fab0aad4e43c4f79",
        "69cb87568361ccd6324550fb3823956c64e073b4cf09e674d7eb0883f844c044",
        "18c9ba4af073ae953e0115f577423348b6d454c158da59cbcbd3c9e34a22856f",
        "14d4fd990dccfdbb8b6dfe964a04ae9e80fefb30914cf433de1bc503b8ad03fa",
        "b143564cf3ad63b4ca01afb9a27e7496c9b1c6ff1f3c46cf10b6c4a047545d20",
        "746df241937f6e791f30d66f2b70d50c88c451d6e6575fd903a46ea63e6c3ae2",
        "151b1570623253295ae36ea4b0933ad1f051fa56277ac9d1f54edcedc2c60c9a",
        "c78158a9539332fec665b019236c7d61e530cd2a343c5f6a9f60cde55d297d18",
        "78af5dc64867adbe81b8b825230aabbac2d25b289971ad301dc3998f09f5abe3",
        "ab04b77042fb351a541764054e2bac7259097c749f6ff930c3fc68ef631d592c",
        "2b17f496472473b746e9ac2cf96971b8999e7c94f796580b17c32310372f61a3",
    },
    "generation": {
        "nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c",
        "runtime-closure-v12-r12-simulation",
        "runtime-closure-v13-paper-compatibility",
    },
    "profile": {"zero-order", "execution-simulation", "paper-compatibility"},
    "semantic_profile": {
        "nautilus-execution-simulation-v1",
        "nautilus-execution-simulation-v2",
        "nautilus-paper-compatibility-v1",
    },
    "validator": {
        "nautilus-backtest-result-v1",
        "nautilus-backtest-simulation-result-v1",
        "nautilus-paper-compatibility-result-v1",
    },
    "selected_source": {
        "1683f1324826b78a715f017a7749fe3d1f7b37f4",
        "a25053355abcfece9b7d5c524f4a3d3c06ce727aec8224012ef9b683240fd880",
    },
}

CANDIDATE_CONTEXT_IDENTITIES = {
    "engine_version": {"1.231.0", "v1.231.0"},
    "upstream_commit": {"27a8e54e7ac3c57d6cbf8891f0283dfbaee97317"},
    "tag_object": {"d3e1685e979925d7b0ffacd1b3f442547686e18f"},
    "sdist_sha256": {"142dde40e77339745aa5fe6bcbb3de5624cee087f526879da00f127df077530f"},
    "wheel_sha256": {"8c438e95c275a13df0c0ddb7012c462708b5e99ff3612e36a1b7bd49ab39c216"},
}
